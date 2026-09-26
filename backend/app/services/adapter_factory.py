from functools import lru_cache
from pathlib import Path

from adapters.supplier.hybrid_1688 import Hybrid1688Adapter
from adapters.supplier.json_gateway import JsonGatewaySupplierAdapter
from adapters.supplier.product_find_1688 import ProductFind1688Adapter
from adapters.supplier.shopkeeper_1688 import Shopkeeper1688Adapter
from adapters.xianyu.goofish_cli import GoofishCliAdapter
from adapters.xianyu.json_gateway import JsonGatewayXianyuAdapter
from adapters.xianyu.market_gateway import PublicMarketAdapter
from adapters.xianyu.top_api import TopXianyuAdapter

from app.core.config import get_settings

MAX_SECRET_BYTES = 16 * 1024


def get_market_adapter():
    settings = get_settings()
    if settings.market_collector_url:
        return PublicMarketAdapter(settings.market_collector_url, settings.market_collector_token)
    return get_xianyu_adapter()


def _read_secret(path: str | None, fallback: str | None = None):
    """Read a small mounted secret without ever logging its value."""
    if not path:
        return fallback
    try:
        secret_path = Path(path)
        if secret_path.stat().st_size > MAX_SECRET_BYTES:
            return fallback
        value = secret_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return fallback
    return value or fallback


@lru_cache
def get_supplier_adapter():
    settings = get_settings()
    if settings.supplier_adapter == "json_gateway":
        return JsonGatewaySupplierAdapter(
            settings.supplier_gateway_url,
            settings.supplier_gateway_token,
            timeout_seconds=settings.supplier_cli_timeout_seconds,
        )
    if settings.supplier_adapter == "shopkeeper_cli":
        return Shopkeeper1688Adapter(
            settings.supplier_cli_command,
            access_key=_read_secret(
                settings.supplier_access_key_file,
                settings.supplier_access_key,
            ),
            timeout_seconds=settings.supplier_cli_timeout_seconds,
        )
    if settings.supplier_adapter == "product_find_cli":
        return ProductFind1688Adapter(
            settings.supplier_cli_command,
            access_key=_read_secret(
                settings.supplier_access_key_file,
                settings.supplier_access_key,
            ),
            timeout_seconds=settings.supplier_cli_timeout_seconds,
        )
    if settings.supplier_adapter == "hybrid_cli":
        access_key = _read_secret(
            settings.supplier_access_key_file,
            settings.supplier_access_key,
        )
        return Hybrid1688Adapter(
            settings.supplier_shopkeeper_cli_command,
            settings.supplier_product_find_cli_command,
            access_key=access_key,
            timeout_seconds=settings.supplier_cli_timeout_seconds,
        )
    return Shopkeeper1688Adapter()


@lru_cache
def get_supplier_detail_adapter():
    """Return the audited detail reader even when Product Find is primary."""
    settings = get_settings()
    command = settings.supplier_shopkeeper_cli_command
    if not command and settings.supplier_adapter == "shopkeeper_cli":
        command = settings.supplier_cli_command
    return Shopkeeper1688Adapter(
        command,
        access_key=_read_secret(
            settings.supplier_access_key_file,
            settings.supplier_access_key,
        ),
        timeout_seconds=settings.supplier_cli_timeout_seconds,
    )


@lru_cache
def get_xianyu_adapter():
    settings = get_settings()
    if settings.xianyu_adapter == "json_gateway":
        return JsonGatewayXianyuAdapter(
            settings.xianyu_gateway_url,
            settings.xianyu_gateway_token,
            timeout_seconds=settings.xianyu_cli_timeout_seconds,
        )
    if settings.xianyu_adapter == "goofish_cli":
        return GoofishCliAdapter(
            settings.xianyu_cli_command,
            cookie_path=settings.xianyu_cookie_path,
            timeout_seconds=settings.xianyu_cli_timeout_seconds,
        )
    if settings.xianyu_adapter == "top_api":
        return TopXianyuAdapter(
            settings.xianyu_top_app_key,
            _read_secret(
                settings.xianyu_top_app_secret_file,
                settings.xianyu_top_app_secret,
            ),
            _read_secret(
                settings.xianyu_top_session_file,
                settings.xianyu_top_session,
            ),
            endpoint=settings.xianyu_top_endpoint,
            timeout_seconds=settings.xianyu_cli_timeout_seconds,
        )
    return GoofishCliAdapter()


def get_xianyu_webhook_token():
    settings = get_settings()
    return _read_secret(
        settings.xianyu_webhook_token_file,
        settings.xianyu_webhook_token,
    )
