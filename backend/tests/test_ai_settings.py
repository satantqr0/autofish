from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import AIProviderConfig, AuditLog, Base
from app.services.ai_settings import (
    decrypt_api_key,
    encrypt_api_key,
    ensure_ai_settings,
    mask_api_key,
    normalize_base_url,
    serialize_provider,
)
from app.services.ai_settings import (
    test_provider_connection as check_provider_connection,
)
from app.services.audit import write_audit


def test_api_key_is_encrypted_and_only_mask_is_serialized():
    plain = "sk-test-secret-value-1234"
    encrypted = encrypt_api_key(plain)

    assert plain not in encrypted
    assert decrypt_api_key(encrypted) == plain
    assert mask_api_key(plain) == "sk-••••••1234"


def test_defaults_create_three_providers_and_disabled_runtime():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        providers, runtime, created = ensure_ai_settings(db)
        db.commit()

        assert created is True
        assert [item.provider for item in providers] == ["openai", "deepseek", "qwen"]
        assert runtime.enabled is False
        assert runtime.primary_provider == "openai"
        assert runtime.task_routing["image_generation"] == "openai"
        assert db.scalar(select(AIProviderConfig).where(AIProviderConfig.provider == "qwen"))


def test_serialized_provider_never_returns_encrypted_key():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        providers, _, _ = ensure_ai_settings(db)
        provider = providers[0]
        provider.api_key_encrypted = encrypt_api_key("sk-private-secret")
        provider.api_key_hint = "sk-••••••cret"
        provider.is_configured = True
        db.flush()

        payload = serialize_provider(provider)

        assert "api_key_encrypted" not in payload
        assert payload["api_key_configured"] is True
        assert payload["api_key_hint"] == "sk-••••••cret"


def test_base_url_is_https_and_provider_allowlisted():
    assert normalize_base_url("openai", "https://api.openai.com/v1/") == (
        "https://api.openai.com/v1"
    )
    assert normalize_base_url(
        "qwen",
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/",
    ) == "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"

    for invalid in (
        "http://api.openai.com/v1",
        "https://example.com/v1",
        "https://user:pass@api.openai.com/v1",
        "https://api.openai.com:8443/v1",
    ):
        try:
            normalize_base_url("openai", invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid URL: {invalid}")


def test_connection_requires_configured_key_without_network_call():
    config = AIProviderConfig(
        provider="openai",
        display_name="OpenAI",
        base_url="https://api.openai.com/v1",
        text_model="gpt-5.6-luna",
        capabilities={},
    )

    success, message = check_provider_connection(config, 10)

    assert success is False
    assert message == "请先保存 API Key"


def test_connection_requires_successful_inference_not_only_model_list(monkeypatch):
    config = AIProviderConfig(
        provider="qwen",
        display_name="阿里云百炼",
        base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        text_model="qwen3.7-plus",
        api_key_encrypted=encrypt_api_key("sk-test-token-plan-key"),
        is_configured=True,
        capabilities={"text": True},
    )

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    monkeypatch.setattr(
        "app.services.ai_settings.httpx.get",
        lambda *_args, **_kwargs: FakeResponse(200, {"data": []}),
    )
    monkeypatch.setattr(
        "app.services.ai_settings.httpx.post",
        lambda *_args, **_kwargs: FakeResponse(
            400, {"error": {"code": "Arrearage"}}
        ),
    )

    success, message = check_provider_connection(config, 10)

    assert success is False
    assert message == "账户欠费、余额不足或套餐额度不可用"


def test_connection_passes_after_real_inference_probe(monkeypatch):
    config = AIProviderConfig(
        provider="qwen",
        display_name="阿里云百炼",
        base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        text_model="qwen3.7-plus",
        api_key_encrypted=encrypt_api_key("sk-test-token-plan-key"),
        is_configured=True,
        capabilities={"text": True},
    )

    class FakeResponse:
        status_code = 200

        def json(self):
            return {}

    monkeypatch.setattr(
        "app.services.ai_settings.httpx.get",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    monkeypatch.setattr(
        "app.services.ai_settings.httpx.post",
        lambda *_args, **_kwargs: FakeResponse(),
    )

    success, message = check_provider_connection(config, 10)

    assert success is True
    assert message == "密钥有效，实际模型推理调用成功"


def test_audit_payload_serializes_datetime_and_decimal_values():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tested_at = datetime(2026, 8, 17, 1, 30, tzinfo=UTC)

    with Session(engine) as db:
        write_audit(
            db,
            action="AI_PROVIDER_CONFIG_UPDATED",
            entity_type="AI_PROVIDER_CONFIG",
            entity_id="qwen",
            before_data={"last_tested_at": tested_at, "budget": Decimal("100.50")},
        )
        db.commit()
        entry = db.scalar(select(AuditLog))

        assert entry is not None
        assert entry.before_data == {
            "last_tested_at": "2026-08-17T01:30:00+00:00",
            "budget": 100.5,
        }
