from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_operator
from app.core.database import get_db
from app.models import AIProviderConfig, User
from app.schemas.ai_settings import SUPPORTED_PROVIDERS, AIProviderUpdate, AIRuntimeUpdate
from app.services.ai_settings import (
    apply_test_result,
    encrypt_api_key,
    ensure_ai_settings,
    mask_api_key,
    normalize_base_url,
    serialize_provider,
    serialize_runtime,
    test_provider_connection,
)
from app.services.audit import write_audit

router = APIRouter(prefix="/ai-settings", tags=["ai-settings"])


def _provider_or_404(db: Session, provider: str) -> AIProviderConfig:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail="unknown AI provider")
    config = db.scalar(select(AIProviderConfig).where(AIProviderConfig.provider == provider))
    if config is None:
        ensure_ai_settings(db)
        config = db.scalar(select(AIProviderConfig).where(AIProviderConfig.provider == provider))
    if config is None:
        raise HTTPException(status_code=404, detail="AI provider not found")
    return config


@router.get("")
def get_ai_settings(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    providers, runtime, created = ensure_ai_settings(db)
    if created:
        db.commit()
        for provider in providers:
            db.refresh(provider)
        db.refresh(runtime)
    return {
        "providers": [serialize_provider(provider) for provider in providers],
        "runtime": serialize_runtime(runtime),
        "security": {
            "api_keys_encrypted": True,
            "api_keys_returned": False,
            "platform_automation_affected": False,
            "activation_requires_test": True,
        },
    }


@router.put("/providers/{provider}")
def update_ai_provider(
    provider: str,
    payload: AIProviderUpdate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    config = _provider_or_404(db, provider)
    before = serialize_provider(config)
    try:
        config.base_url = normalize_base_url(provider, payload.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    config.text_model = payload.text_model.strip()
    config.vision_model = payload.vision_model
    config.image_model = payload.image_model
    if payload.api_key is not None:
        api_key = payload.api_key.get_secret_value().strip()
        if len(api_key) < 8:
            raise HTTPException(status_code=422, detail="API Key 长度不足")
        config.api_key_encrypted = encrypt_api_key(api_key)
        config.api_key_hint = mask_api_key(api_key)
        config.is_configured = True
    config.last_test_status = "NOT_TESTED"
    config.last_test_message = "配置已更新，请重新测试连接"
    config.last_tested_at = None
    config.updated_by_user_id = user.id
    after = serialize_provider(config)
    write_audit(
        db,
        action="AI_PROVIDER_CONFIG_UPDATED",
        entity_type="AI_PROVIDER_CONFIG",
        entity_id=provider,
        actor_user_id=user.id,
        actor_type="USER",
        before_data={key: value for key, value in before.items() if key != "updated_at"},
        after_data={key: value for key, value in after.items() if key != "updated_at"},
        correlation_id=request.state.correlation_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    db.refresh(config)
    return serialize_provider(config)


@router.delete("/providers/{provider}/api-key")
def clear_ai_provider_key(
    provider: str,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    config = _provider_or_404(db, provider)
    config.api_key_encrypted = None
    config.api_key_hint = None
    config.is_configured = False
    config.last_test_status = "NOT_TESTED"
    config.last_test_message = "API Key 已清除"
    config.last_tested_at = None
    config.updated_by_user_id = user.id
    write_audit(
        db,
        action="AI_PROVIDER_KEY_CLEARED",
        entity_type="AI_PROVIDER_CONFIG",
        entity_id=provider,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={"api_key_configured": False},
        correlation_id=request.state.correlation_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    db.refresh(config)
    return serialize_provider(config)


@router.post("/providers/{provider}/test")
def test_ai_provider(
    provider: str,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    providers, runtime, created = ensure_ai_settings(db)
    if created:
        db.flush()
    config = next((item for item in providers if item.provider == provider), None)
    if config is None:
        raise HTTPException(status_code=404, detail="AI provider not found")
    success, message = test_provider_connection(config, runtime.request_timeout_seconds)
    apply_test_result(config, success, message)
    write_audit(
        db,
        action="AI_PROVIDER_CONNECTION_TESTED",
        entity_type="AI_PROVIDER_CONFIG",
        entity_id=provider,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={"success": success, "message": message},
        result="SUCCESS" if success else "FAILED",
        correlation_id=request.state.correlation_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    db.refresh(config)
    return serialize_provider(config)


@router.patch("/runtime")
def update_ai_runtime(
    payload: AIRuntimeUpdate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    providers, runtime, created = ensure_ai_settings(db)
    if created:
        db.flush()
    primary = next((item for item in providers if item.provider == payload.primary_provider), None)
    if payload.enabled and (
        primary is None or not primary.is_configured or primary.last_test_status != "SUCCESS"
    ):
        raise HTTPException(status_code=409, detail="主服务商必须先保存密钥并通过连接测试")
    before = serialize_runtime(runtime)
    runtime.enabled = payload.enabled
    runtime.primary_provider = payload.primary_provider
    runtime.fallback_provider = payload.fallback_provider
    runtime.monthly_budget_cny = payload.monthly_budget_cny
    runtime.temperature = payload.temperature
    runtime.max_output_tokens = payload.max_output_tokens
    runtime.request_timeout_seconds = payload.request_timeout_seconds
    runtime.task_routing = payload.task_routing
    runtime.updated_by_user_id = user.id
    write_audit(
        db,
        action="AI_RUNTIME_SETTINGS_UPDATED",
        entity_type="AI_RUNTIME_SETTING",
        entity_id=runtime.id,
        actor_user_id=user.id,
        actor_type="USER",
        before_data={key: value for key, value in before.items() if key != "updated_at"},
        after_data={
            key: value for key, value in serialize_runtime(runtime).items() if key != "updated_at"
        },
        correlation_id=request.state.correlation_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    db.refresh(runtime)
    return serialize_runtime(runtime)
