import asyncio

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.models import User
from app.services.adapter_factory import get_supplier_adapter, get_xianyu_adapter

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _serialize(name, configured, result, *, write_enabled=False):
    health = result.data
    adapter_read_only = True if health is None else health.read_only
    return {
        "name": name,
        "configured_provider": configured,
        "status": result.status.value,
        "available": bool(health and health.available),
        "authenticated": bool(health and health.authenticated),
        "read_only": adapter_read_only,
        "write_enabled": bool(write_enabled and health and not health.read_only),
        "source": result.source,
        "source_version": result.source_version,
        "capabilities": health.details if health else {},
        "error_code": result.error_code,
        "message": result.safe_message,
    }


@router.get("/status")
async def integration_status(_user: User = Depends(get_current_user)):
    settings = get_settings()
    supplier_result, xianyu_result = await asyncio.gather(
        get_supplier_adapter().health(),
        get_xianyu_adapter().health(),
    )
    return {
        "global_enabled": settings.adapters_enabled,
        "write_enabled": settings.adapter_write_enabled,
        "data_mode": settings.data_mode,
        "supplier": _serialize("1688 / 供应商", settings.supplier_adapter, supplier_result),
        "xianyu": _serialize(
            "闲鱼",
            settings.xianyu_adapter,
            xianyu_result,
            write_enabled=settings.adapters_enabled and settings.adapter_write_enabled,
        ),
    }
