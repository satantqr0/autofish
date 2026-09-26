from types import SimpleNamespace

from PIL import Image
from pydantic import BaseModel

import app.services.ai_inference as inference


class BooleanResult(BaseModel):
    passed: bool


def test_chat_json_self_repairs_schema_output(monkeypatch):
    provider = SimpleNamespace(
        provider="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        text_model="qwen-flash",
        vision_model="qwen3-vl-flash",
    )
    runtime = SimpleNamespace(temperature=0.2, max_output_tokens=512, request_timeout_seconds=30)
    requests = []

    monkeypatch.setattr(
        inference,
        "_configured_providers",
        lambda *_args, **_kwargs: ([provider], runtime),
    )

    def fake_post(_provider, _runtime, _url, payload, *, timeout_seconds=None):
        requests.append(payload)
        assert timeout_seconds is not None
        content = '{"wrong_field": true}' if len(requests) == 1 else '{"passed": true}'
        return {"choices": [{"message": {"content": content}}]}, f"request-{len(requests)}"

    monkeypatch.setattr(inference, "_post_json", fake_post)

    result = inference.chat_json(
        None,
        task="vision_quality",
        messages=[{"role": "user", "content": "check"}],
        schema=BooleanResult,
        model_kind="vision",
    )

    assert result.data.passed is True
    assert result.request_id == "request-2"
    assert len(requests) == 2
    assert "JSON Schema" in requests[1]["messages"][-1]["content"]


def test_chat_json_uses_configured_fallback_after_primary_failure(monkeypatch):
    primary = SimpleNamespace(
        provider="openai",
        base_url="https://api.openai.com/v1",
        text_model="gpt-primary",
        vision_model="gpt-primary-vision",
    )
    fallback = SimpleNamespace(
        provider="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        text_model="qwen-flash",
        vision_model="qwen3-vl-flash",
    )
    runtime = SimpleNamespace(temperature=0.2, max_output_tokens=512, request_timeout_seconds=30)
    calls = []

    monkeypatch.setattr(
        inference,
        "_configured_providers",
        lambda *_args, **_kwargs: ([primary, fallback], runtime),
    )

    def fake_post(provider, _runtime, _url, _payload, *, timeout_seconds=None):
        calls.append(provider.provider)
        assert timeout_seconds is not None
        if provider.provider == "openai":
            raise inference.AIInferenceError(
                "AI_UPSTREAM_ERROR", "模型服务暂时不可用", retryable=True
            )
        return {
            "choices": [{"message": {"content": '{"passed": true}'}}]
        }, "fallback-request"

    monkeypatch.setattr(inference, "_post_json", fake_post)

    result = inference.chat_json(
        None,
        task="vision_quality",
        messages=[{"role": "user", "content": "check"}],
        schema=BooleanResult,
        model_kind="vision",
    )

    assert calls == ["openai", "qwen"]
    assert result.provider == "qwen"
    assert result.request_id == "fallback-request"


def test_wan_token_plan_reference_edit_uses_documented_parameters(monkeypatch, tmp_path):
    provider = SimpleNamespace(
        provider="qwen",
        base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        image_model="wan2.7-image",
    )
    runtime = SimpleNamespace(request_timeout_seconds=120)
    source = tmp_path / "source.png"
    Image.new("RGB", (32, 32), (240, 240, 240)).save(source)
    captured = {}

    monkeypatch.setattr(
        inference,
        "_configured_providers",
        lambda *_args, **_kwargs: ([provider], runtime),
    )

    def fake_post(_provider, _runtime, url, payload, **_kwargs):
        captured.update({"url": url, "payload": payload})
        return {
            "output": {
                "choices": [
                    {"message": {"content": [{"image": "https://example.com/result.png"}]}}
                ]
            },
            "usage": {"image_count": 1},
        }, "wan-request"

    monkeypatch.setattr(inference, "_post_json", fake_post)

    result = inference.generate_reference_image(
        None,
        source_path=source,
        prompt="保持商品结构并优化背景",
    )

    assert result.model == "wan2.7-image"
    assert result.data == ["https://example.com/result.png"]
    assert captured["url"] == (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/"
        "api/v1/services/aigc/multimodal-generation/generation"
    )
    parameters = captured["payload"]["parameters"]
    assert parameters == {"n": 1, "watermark": False, "size": "2K"}


def test_chat_json_stops_when_total_deadline_is_exhausted(monkeypatch):
    provider = SimpleNamespace(
        provider="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        text_model="qwen-flash",
        vision_model="qwen3-vl-flash",
    )
    runtime = SimpleNamespace(temperature=0.2, max_output_tokens=512, request_timeout_seconds=30)
    clock = iter((10.0, 10.0, 71.0))

    monkeypatch.setattr(
        inference,
        "_configured_providers",
        lambda *_args, **_kwargs: ([provider], runtime),
    )
    monkeypatch.setattr(inference.time, "monotonic", lambda: next(clock))

    calls = 0

    def invalid_response(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"choices": [{"message": {"content": "not-json"}}]}, "request-1"

    monkeypatch.setattr(inference, "_post_json", invalid_response)

    try:
        inference.chat_json(
            None,
            task="customer_service",
            messages=[{"role": "user", "content": "check"}],
            schema=BooleanResult,
        )
    except inference.AIInferenceError as exc:
        assert exc.code == "AI_DEADLINE_EXCEEDED"
    else:
        raise AssertionError("deadline exhaustion must fail closed")
    assert calls == 1
