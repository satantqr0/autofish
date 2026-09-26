import base64
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from PIL import Image
from pydantic import BaseModel, ValidationError

from app.models import AIProviderConfig, AIRuntimeSetting
from app.services.ai_settings import decrypt_api_key, ensure_ai_settings


class AIInferenceError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


@dataclass(frozen=True)
class InferenceResult:
    data: Any
    provider: str
    model: str
    request_id: str | None
    usage: dict


def _extract_json(value: str) -> dict:
    text = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise AIInferenceError("INVALID_AI_JSON", "模型没有返回有效 JSON") from exc
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as nested:
            raise AIInferenceError("INVALID_AI_JSON", "模型没有返回有效 JSON") from nested
    if not isinstance(parsed, dict):
        raise AIInferenceError("INVALID_AI_JSON", "模型返回值不是 JSON 对象")
    return parsed


def _configured_providers(
    db, task: str, capability: str
) -> tuple[list[AIProviderConfig], AIRuntimeSetting]:
    providers, runtime, created = ensure_ai_settings(db)
    if created:
        db.flush()
    if not runtime.enabled:
        raise AIInferenceError("AI_RUNTIME_DISABLED", "大模型总开关未启用")
    provider_name = (runtime.task_routing or {}).get(task) or runtime.primary_provider
    if provider_name == "human":
        raise AIInferenceError("AI_ROUTE_REQUIRES_HUMAN", f"{task} 路由被设置为人工")
    names = [provider_name]
    if runtime.fallback_provider and runtime.fallback_provider not in names:
        names.append(runtime.fallback_provider)
    candidates = []
    for name in names:
        provider = next((item for item in providers if item.provider == name), None)
        if (
            provider is None
            or not provider.is_configured
            or not provider.api_key_encrypted
            or provider.last_test_status != "SUCCESS"
            or not (provider.capabilities or {}).get(capability)
        ):
            continue
        model = {
            "text": provider.text_model,
            "vision": provider.vision_model,
            "image_generation": provider.image_model,
        }.get(capability)
        if model:
            candidates.append(provider)
    if not candidates:
        raise AIInferenceError(
            "AI_PROVIDER_NOT_READY",
            f"{task} 没有已配置、已测试且支持 {capability} 的模型服务商",
        )
    return candidates, runtime


def _auth_headers(provider: AIProviderConfig) -> dict[str, str]:
    try:
        api_key = decrypt_api_key(provider.api_key_encrypted or "")
    except ValueError as exc:
        raise AIInferenceError("AI_KEY_DECRYPT_FAILED", "模型密钥无法解密，请重新保存") from exc
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _post_json(
    provider,
    runtime,
    url: str,
    payload: dict,
    *,
    timeout_seconds: float | None = None,
) -> tuple[dict, str | None]:
    configured_timeout = min(max(runtime.request_timeout_seconds, 5), 180)
    request_timeout = configured_timeout
    if timeout_seconds is not None:
        request_timeout = max(0.1, min(configured_timeout, timeout_seconds))
    try:
        response = httpx.post(
            url,
            headers=_auth_headers(provider),
            json=payload,
            timeout=request_timeout,
            follow_redirects=False,
        )
    except httpx.TimeoutException as exc:
        raise AIInferenceError("AI_TIMEOUT", "模型调用超时", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise AIInferenceError("AI_NETWORK_ERROR", "模型服务网络不可用", retryable=True) from exc
    request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
    if response.status_code == 429:
        raise AIInferenceError("AI_RATE_LIMITED", "模型服务限流或额度不足", retryable=True)
    if response.status_code in {401, 403}:
        raise AIInferenceError("AI_AUTH_OR_QUOTA", "模型密钥无权调用所选模型或免费额度已耗尽")
    if response.status_code >= 500:
        raise AIInferenceError("AI_UPSTREAM_ERROR", "模型服务暂时不可用", retryable=True)
    if response.status_code >= 400:
        raise AIInferenceError(
            "AI_REQUEST_REJECTED", f"模型服务拒绝请求（HTTP {response.status_code}）"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise AIInferenceError("AI_INVALID_RESPONSE", "模型服务没有返回 JSON") from exc
    if not isinstance(body, dict):
        raise AIInferenceError("AI_INVALID_RESPONSE", "模型服务响应格式异常")
    return body, request_id or body.get("request_id")


def chat_json[SchemaT: BaseModel](
    db,
    *,
    task: str,
    messages: list[dict],
    schema: type[SchemaT],
    model_kind: str = "text",
) -> InferenceResult:
    capability = "vision" if model_kind == "vision" else "text"
    providers, runtime = _configured_providers(db, task, capability)
    total_timeout = min(max(runtime.request_timeout_seconds * 2, 30), 120)
    deadline = time.monotonic() + total_timeout
    last_error: Exception | None = None
    for provider_index, provider in enumerate(providers):
        model = provider.vision_model if model_kind == "vision" else provider.text_model
        request_messages = list(messages)
        for attempt in range(1, 4):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AIInferenceError(
                    "AI_DEADLINE_EXCEEDED",
                    "模型调用超过本次任务总时限，请稍后重试",
                    retryable=True,
                ) from last_error
            try:
                body, request_id = _post_json(
                    provider,
                    runtime,
                    f"{provider.base_url.rstrip('/')}/chat/completions",
                    {
                        "model": model,
                        "messages": request_messages,
                        "temperature": float(runtime.temperature),
                        "max_tokens": runtime.max_output_tokens,
                        "response_format": {"type": "json_object"},
                    },
                    timeout_seconds=remaining,
                )
            except AIInferenceError as exc:
                last_error = exc
                if provider_index + 1 < len(providers):
                    break
                raise
            content: str | None = None
            validation_detail: Any = "响应缺少 choices[0].message.content"
            try:
                content = body["choices"][0]["message"]["content"]
                parsed = _extract_json(content)
                validated = schema.model_validate(parsed)
            except AIInferenceError as exc:
                if exc.code != "INVALID_AI_JSON":
                    raise
                last_error = exc
                validation_detail = exc.safe_message
            except ValidationError as exc:
                last_error = exc
                validation_detail = exc.errors(include_url=False)
            except (KeyError, IndexError, TypeError) as exc:
                last_error = exc
            else:
                return InferenceResult(
                    data=validated,
                    provider=provider.provider,
                    model=model or "",
                    request_id=request_id,
                    usage=body.get("usage") or {},
                )
            if attempt < 3:
                if content:
                    request_messages.append({"role": "assistant", "content": content[:12000]})
                request_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上次 JSON 未通过结构校验，请严格按下面的 JSON Schema 修正"
                            "全部字段类型和"
                            "必填项，不要解释，只返回一个 JSON 对象。"
                            "\nSchema: "
                            f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
                            "\n校验错误: "
                            f"{json.dumps(validation_detail, ensure_ascii=False, default=str)}"
                        ),
                    }
                )
    raise AIInferenceError(
        "AI_SCHEMA_VALIDATION_FAILED", "模型连续三次未通过结构校验"
    ) from last_error


def image_data_uri(path: Path) -> str:
    try:
        with Image.open(path) as image:
            image_format = (image.format or "").upper()
    except Exception as exc:
        raise AIInferenceError("INVALID_SOURCE_IMAGE", "来源图片无法读取") from exc
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(image_format)
    if not mime:
        raise AIInferenceError("UNSUPPORTED_SOURCE_IMAGE", "来源图片格式不支持")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _qwen_multimodal_endpoint(base_url: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            "/api/v1/services/aigc/multimodal-generation/generation",
            "",
            "",
        )
    )


def generate_reference_image(
    db,
    *,
    source_path: Path,
    prompt: str,
    size: str = "1024*1024",
    seed: int | None = None,
) -> InferenceResult:
    providers, runtime = _configured_providers(
        db, "image_generation", "image_generation"
    )
    provider = next(
        (
            item
            for item in providers
            if item.provider == "qwen"
            and (item.image_model or "").startswith(
                ("qwen-image-2.0", "qwen-image-edit", "wan2.7-image")
            )
        ),
        None,
    )
    if provider is None:
        raise AIInferenceError(
            "REFERENCE_IMAGE_EDIT_UNSUPPORTED",
            "当前图片模型不支持已验证的参考图编辑路径",
        )
    model = provider.image_model or ""
    parameters: dict[str, Any] = {"n": 1, "watermark": False}
    if model.startswith("wan2.7-image"):
        # Wan 2.7 uses the multimodal generation endpoint too, but its
        # documented image-edit parameters differ from Qwen Image.  Token Plan
        # exposes Wan 2.7 with the symbolic 2K size and rejects Qwen-only
        # prompt extension / negative prompt fields.
        parameters["size"] = "2K" if size == "1024*1024" else size
    else:
        parameters.update(
            {
                "prompt_extend": True,
                "size": size,
                "negative_prompt": (
                    "改变商品结构，改变颜色，增加配件，改变尺寸比例，品牌标志，水印，文字，"
                    "人物，手，低清晰度，畸变，悬浮，虚构功能"
                ),
            }
        )
    if seed is not None:
        parameters["seed"] = seed
    body, request_id = _post_json(
        provider,
        runtime,
        _qwen_multimodal_endpoint(provider.base_url),
        {
            "model": model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": image_data_uri(source_path)},
                            {"text": prompt[:5000]},
                        ],
                    }
                ]
            },
            "parameters": parameters,
        },
    )
    urls: list[str] = []
    for choice in (body.get("output") or {}).get("choices") or []:
        for item in (choice.get("message") or {}).get("content") or []:
            if isinstance(item, dict) and item.get("image"):
                urls.append(str(item["image"]))
    for item in (body.get("output") or {}).get("results") or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
    if not urls:
        raise AIInferenceError("AI_IMAGE_MISSING", "图片模型没有返回可下载图片")
    return InferenceResult(
        data=urls,
        provider=provider.provider,
        model=model,
        request_id=request_id,
        usage=body.get("usage") or {},
    )
