import base64
import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AIProviderConfig, AIRuntimeSetting

PROVIDER_DEFAULTS = {
    "openai": {
        "display_name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "text_model": "gpt-5.6-luna",
        "vision_model": "gpt-5.6-luna",
        "image_model": "gpt-image-2",
        "allowed_hosts": {"api.openai.com"},
        "capabilities": {
            "text": True,
            "vision": True,
            "image_generation": True,
            "tools": True,
            "structured_output": True,
        },
    },
    "deepseek": {
        "display_name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "text_model": "deepseek-v4-flash",
        "vision_model": None,
        "image_model": None,
        "allowed_hosts": {"api.deepseek.com"},
        "capabilities": {
            "text": True,
            "vision": False,
            "image_generation": False,
            "tools": True,
            "structured_output": True,
        },
    },
    "qwen": {
        "display_name": "阿里云百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "text_model": "qwen3.7-plus",
        "vision_model": "qwen3.7-plus",
        "image_model": "qwen-image-plus",
        "allowed_hosts": {
            "dashscope.aliyuncs.com",
            "token-plan.cn-beijing.maas.aliyuncs.com",
        },
        "capabilities": {
            "text": True,
            "vision": True,
            "image_generation": True,
            "tools": True,
            "structured_output": True,
        },
    },
}

DEFAULT_TASK_ROUTING = {
    "customer_service": "openai",
    "product_copy": "openai",
    "vision_quality": "openai",
    "image_generation": "openai",
    "batch_text": "deepseek",
    "risk_summary": "openai",
}


def _fernet() -> Fernet:
    settings = get_settings()
    secret = settings.credential_encryption_secret or settings.jwt_secret
    digest = hashlib.sha256(f"autofish-ai-credentials:{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_api_key(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise ValueError("stored API key cannot be decrypted") from exc


def mask_api_key(value: str) -> str:
    if len(value) <= 8:
        return f"••••{value[-2:]}"
    return f"{value[:3]}••••••{value[-4:]}"


def normalize_base_url(provider: str, value: str) -> str:
    defaults = PROVIDER_DEFAULTS.get(provider)
    if defaults is None:
        raise ValueError("unsupported provider")
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Base URL 必须使用 HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL 不能包含凭据、查询参数或片段")
    if parsed.hostname.lower() not in defaults["allowed_hosts"]:
        raise ValueError("Base URL 域名不在该服务商允许列表")
    if parsed.port not in {None, 443}:
        raise ValueError("Base URL 仅允许 HTTPS 默认端口")
    path = (parsed.path or "").rstrip("/")
    return urlunsplit(("https", parsed.netloc.lower(), path, "", ""))


def ensure_ai_settings(db) -> tuple[list[AIProviderConfig], AIRuntimeSetting, bool]:
    providers = db.scalars(select(AIProviderConfig)).all()
    existing = {item.provider for item in providers}
    created = False
    for provider, defaults in PROVIDER_DEFAULTS.items():
        if provider in existing:
            continue
        config = AIProviderConfig(
            provider=provider,
            display_name=defaults["display_name"],
            base_url=defaults["base_url"],
            text_model=defaults["text_model"],
            vision_model=defaults["vision_model"],
            image_model=defaults["image_model"],
            capabilities=defaults["capabilities"],
        )
        db.add(config)
        providers.append(config)
        created = True

    runtime = db.get(AIRuntimeSetting, 1)
    if runtime is None:
        runtime = AIRuntimeSetting(
            id=1,
            enabled=False,
            primary_provider="openai",
            fallback_provider="deepseek",
            monthly_budget_cny=Decimal("100.00"),
            temperature=Decimal("0.200"),
            max_output_tokens=1200,
            request_timeout_seconds=30,
            task_routing=DEFAULT_TASK_ROUTING.copy(),
        )
        db.add(runtime)
        created = True
    db.flush()
    providers.sort(key=lambda item: list(PROVIDER_DEFAULTS).index(item.provider))
    return providers, runtime, created


def serialize_provider(config: AIProviderConfig) -> dict:
    return {
        "provider": config.provider,
        "display_name": config.display_name,
        "base_url": config.base_url,
        "text_model": config.text_model,
        "vision_model": config.vision_model,
        "image_model": config.image_model,
        "api_key_configured": config.is_configured,
        "api_key_hint": config.api_key_hint,
        "last_test_status": config.last_test_status,
        "last_test_message": config.last_test_message,
        "last_tested_at": config.last_tested_at,
        "capabilities": config.capabilities,
        "updated_at": config.updated_at,
    }


def serialize_runtime(runtime: AIRuntimeSetting) -> dict:
    return {
        "enabled": runtime.enabled,
        "primary_provider": runtime.primary_provider,
        "fallback_provider": runtime.fallback_provider,
        "monthly_budget_cny": str(runtime.monthly_budget_cny),
        "temperature": str(runtime.temperature),
        "max_output_tokens": runtime.max_output_tokens,
        "request_timeout_seconds": runtime.request_timeout_seconds,
        "task_routing": runtime.task_routing,
        "updated_at": runtime.updated_at,
    }


def test_provider_connection(config: AIProviderConfig, timeout_seconds: int) -> tuple[bool, str]:
    if not config.api_key_encrypted:
        return False, "请先保存 API Key"
    try:
        api_key = decrypt_api_key(config.api_key_encrypted)
    except ValueError:
        return False, "已保存密钥无法解密，请重新录入"
    try:
        response = httpx.get(
            f"{config.base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=min(max(timeout_seconds, 5), 30),
            follow_redirects=False,
        )
    except httpx.TimeoutException:
        return False, "连接超时，请检查 NAS 网络和服务地址"
    except httpx.HTTPError:
        return False, "无法连接服务商，请检查 NAS 网络和服务地址"
    if response.status_code in {401, 403}:
        return False, "API Key 无效或没有访问权限"
    if response.status_code == 429:
        return False, "服务商返回限流或账户额度不足"
    if response.status_code != 200:
        return False, f"服务商返回 HTTP {response.status_code}"

    # `/models` can remain available when an account is in arrears.  Exercise
    # the configured text model with a tiny request so the settings page
    # reflects actual inference readiness instead of authentication alone.
    try:
        response = httpx.post(
            f"{config.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.text_model,
                "messages": [{"role": "user", "content": "只回复 OK"}],
                "temperature": 0,
                "max_tokens": 8,
            },
            timeout=min(max(timeout_seconds, 5), 30),
            follow_redirects=False,
        )
    except httpx.TimeoutException:
        return False, "模型推理测试超时，请检查 NAS 网络或提高超时设置"
    except httpx.HTTPError:
        return False, "模型推理接口连接失败"
    if response.status_code == 200:
        return True, "密钥有效，实际模型推理调用成功"
    if response.status_code in {401, 403}:
        return False, "API Key 无效、无模型权限或套餐不可用"
    if response.status_code == 429:
        return False, "服务商返回限流或账户额度不足"
    if response.status_code == 400:
        try:
            error_code = str((response.json().get("error") or {}).get("code") or "")
        except (ValueError, AttributeError):
            error_code = ""
        if error_code.lower() in {"arrearage", "insufficient_balance", "quota_exceeded"}:
            return False, "账户欠费、余额不足或套餐额度不可用"
    return False, f"实际模型推理返回 HTTP {response.status_code}"


def apply_test_result(config: AIProviderConfig, success: bool, message: str) -> None:
    config.last_test_status = "SUCCESS" if success else "FAILED"
    config.last_test_message = message[:500]
    config.last_tested_at = datetime.now(UTC)
