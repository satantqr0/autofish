import hashlib
import hmac
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from adapters.base import AdapterHealth, AdapterResult, AdapterStatus, Money
from adapters.runner import CliFailure
from adapters.xianyu.port import (
    MessageSnapshot,
    OrderSnapshot,
    PublishRequest,
    SendMessageRequest,
    ShipOrderRequest,
    UpdateProductRequest,
    XianyuAccountSnapshot,
    XianyuProductSnapshot,
)

TOP_ENDPOINT = "https://eco.taobao.com/router/rest"


def _json_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sign_top_params(params: dict[str, str], app_secret: str) -> str:
    """Create the documented TOP HMAC-MD5 signature."""
    canonical = "".join(f"{key}{params[key]}" for key in sorted(params))
    return hmac.new(
        app_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.md5
    ).hexdigest().upper()


def _response_envelope(method: str, payload: dict) -> dict:
    key = f"{method.replace('.', '_')}_response"
    return payload.get(key) or payload


def _nested(value: Any, *keys: str):
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first(value: Any, *keys: str):
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, ""):
                return candidate
        for child in value.values():
            candidate = _first(child, *keys)
            if candidate not in (None, ""):
                return candidate
    elif isinstance(value, list):
        for child in value:
            candidate = _first(child, *keys)
            if candidate not in (None, ""):
                return candidate
    return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "ok", "success", "authorized"}
    return bool(value)


class TopApiClient:
    def __init__(
        self,
        app_key: str | None,
        app_secret: str | None,
        session_key: str | None,
        *,
        endpoint: str = TOP_ENDPOINT,
        timeout_seconds: int = 30,
    ):
        self.app_key = app_key or ""
        self.app_secret = app_secret or ""
        self.session_key = session_key or ""
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    @property
    def available(self):
        return bool(self.app_key and self.app_secret and self.session_key)

    async def call(self, method: str, **api_params) -> dict:
        if not self.available:
            raise CliFailure("TOP_NOT_CONFIGURED", "闲鱼 TOP AppKey、AppSecret 或 Session 未配置")
        params = {
            "method": method,
            "app_key": self.app_key,
            "session": self.session_key,
            "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
            "format": "json",
            "v": "2.0",
            "sign_method": "hmac",
            "simplify": "true",
        }
        params.update(
            {key: _json_value(value) for key, value in api_params.items() if value is not None}
        )
        params["sign"] = sign_top_params(params, self.app_secret)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(self.endpoint, data=params)
        except httpx.TimeoutException as exc:
            raise CliFailure("TIMEOUT", "闲鱼 TOP API 调用超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise CliFailure("NETWORK_ERROR", "闲鱼 TOP API 暂时不可用", retryable=True) from exc
        if response.status_code in {401, 403}:
            raise CliFailure("AUTH_REQUIRED", "闲鱼 TOP 用户授权无效或已过期")
        if response.status_code == 429:
            raise CliFailure("RATE_LIMITED", "闲鱼 TOP API 已限流", retryable=True)
        if response.status_code in {408, 425} or response.status_code >= 500:
            raise CliFailure(
                "UPSTREAM_HTTP_ERROR",
                f"闲鱼 TOP API 暂时不可用（HTTP {response.status_code}）",
                retryable=True,
            )
        if response.status_code >= 400:
            raise CliFailure(
                "REQUEST_REJECTED",
                f"闲鱼 TOP API 拒绝请求（HTTP {response.status_code}）",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CliFailure("INVALID_JSON", "闲鱼 TOP API 未返回有效 JSON") from exc
        error = payload.get("error_response") or {}
        if error:
            code = str(error.get("code") or error.get("sub_code") or "TOP_ERROR")
            message = str(error.get("sub_msg") or error.get("msg") or "TOP API 拒绝请求")
            lowered = f"{code} {message}".casefold()
            if any(token in lowered for token in ("session", "authorize", "invalid-session")):
                raise CliFailure("AUTH_REQUIRED", "闲鱼 TOP 用户授权无效或已过期")
            if any(token in lowered for token in ("flow", "limit", "traffic")):
                raise CliFailure("RATE_LIMITED", "闲鱼 TOP API 已限流", retryable=True)
            raise CliFailure(code[:100], f"闲鱼 TOP API 拒绝请求：{message}")
        return _response_envelope(method, payload)


class TopXianyuAdapter:
    """Official TOP adapter for business profiles explicitly authorized by Xianyu."""

    source = "xianyu-top-api"
    source_version = "2.0"

    def __init__(self, app_key=None, app_secret=None, session_key=None, **kwargs):
        self.client = TopApiClient(app_key, app_secret, session_key, **kwargs)

    def _failure(self, exc: CliFailure, *, health=False):
        status = {
            "AUTH_REQUIRED": AdapterStatus.AUTH_REQUIRED,
            "TOP_NOT_CONFIGURED": AdapterStatus.AUTH_REQUIRED,
            "RATE_LIMITED": AdapterStatus.RATE_LIMITED,
        }.get(
            exc.code,
            AdapterStatus.TRANSIENT_ERROR if exc.retryable else AdapterStatus.PERMANENT_ERROR,
        )
        data = None
        if health:
            data = AdapterHealth(
                available=self.client.available,
                authenticated=False,
                read_only=False,
                details={"official": True},
            )
        return AdapterResult(
            status=status,
            data=data,
            source=self.source,
            source_version=self.source_version,
            retryable=exc.retryable,
            error_code=exc.code,
            safe_message=exc.safe_message,
        )

    async def health(self):
        if not self.client.available:
            return self._failure(
                CliFailure("TOP_NOT_CONFIGURED", "闲鱼 TOP AppKey、AppSecret 或 Session 未配置"),
                health=True,
            )
        try:
            payload = await self.client.call("alibaba.idle.user.permit.query")
        except CliFailure as exc:
            return self._failure(exc, health=True)
        permitted = _as_bool(_first(payload, "success", "permit", "authorized"))
        return AdapterResult(
            status=AdapterStatus.OK if permitted else AdapterStatus.AUTH_REQUIRED,
            data=AdapterHealth(
                available=True,
                authenticated=permitted,
                read_only=False,
                details={
                    "official": True,
                    "authorization_verified": permitted,
                    "writes": ["publish", "update", "remove", "ship_order"],
                    "messages": False,
                },
            ),
            source=self.source,
            source_version=self.source_version,
            error_code=None if permitted else "TOP_PERMISSION_MISSING",
            safe_message=None if permitted else "TOP 应用尚未获得对应闲鱼业务权限",
        )

    async def get_account(self):
        try:
            payload = await self.client.call("alibaba.idle.isv.user.info")
        except CliFailure as exc:
            return self._failure(exc)
        external_id = str(_first(payload, "user_id", "seller_id", "encryption_seller_id") or "")
        if not external_id:
            return AdapterResult(
                status=AdapterStatus.PERMANENT_ERROR,
                source=self.source,
                source_version=self.source_version,
                error_code="TOP_ACCOUNT_SHAPE_CHANGED",
                safe_message="TOP 用户信息响应缺少账号标识",
            )
        return AdapterResult(
            status=AdapterStatus.OK,
            data=XianyuAccountSnapshot(
                external_account_id=external_id,
                nickname=str(_first(payload, "nick", "nickname", "user_name") or "闲鱼授权账号"),
                status="CONNECTED",
                raw_payload=payload,
            ),
            source=self.source,
            source_version=self.source_version,
        )

    @staticmethod
    def _product(item: dict) -> XianyuProductSnapshot:
        images = item.get("img_urls") or item.get("image_urls") or []
        if isinstance(images, dict):
            images = images.get("string") or []
        return XianyuProductSnapshot(
            external_product_id=str(item.get("item_id") or item.get("id") or ""),
            title=str(item.get("title") or "未命名闲鱼商品"),
            price=Money(amount=str(item.get("reserve_price") or item.get("price") or "0")),
            status=str(item.get("status") or "UNKNOWN"),
            image_urls=[str(value) for value in images if value],
            raw_payload=item,
        )

    async def list_products(self):
        items = []
        try:
            for page in range(1, 6):
                payload = await self.client.call(
                    "alibaba.idle.item.user.publishitems",
                    param_item_page_query={"page_no": page, "page_size": 20, "status": ["0"]},
                )
                result = payload.get("result") or payload
                rows = (
                    _nested(result, "item_list", "idle_item_api_do")
                    or result.get("item_list")
                    or []
                )
                if isinstance(rows, dict):
                    rows = rows.get("idle_item_api_do") or []
                items.extend(self._product(row) for row in rows if isinstance(row, dict))
                if not result.get("next_page"):
                    break
        except CliFailure as exc:
            return self._failure(exc)
        return AdapterResult(
            status=AdapterStatus.OK,
            data=items,
            source=self.source,
            source_version=self.source_version,
        )

    async def get_product(self, external_product_id):
        try:
            payload = await self.client.call(
                "alibaba.idle.isv.item.query",
                param={"item_id": str(external_product_id), "need_sku": True},
            )
        except CliFailure as exc:
            return self._failure(exc)
        item = payload.get("result") or payload.get("module") or payload
        return AdapterResult(
            status=AdapterStatus.OK,
            data=self._product(item),
            source=self.source,
            source_version=self.source_version,
        )

    async def publish_product(self, request: PublishRequest):
        item_param = request.platform_payload.get("item_param")
        if not isinstance(item_param, dict):
            return AdapterResult(
                status=AdapterStatus.MANUAL_REQUIRED,
                source=self.source,
                source_version=self.source_version,
                error_code="TOP_BUSINESS_PAYLOAD_REQUIRED",
                safe_message="官方发布需要与已获批闲鱼业务类型匹配的 item_param 和媒体 ID",
            )
        try:
            payload = await self.client.call("alibaba.idle.isv.item.publish", item_param=item_param)
        except CliFailure as exc:
            return self._failure(exc)
        item_id = str(_first(payload, "item_id", "itemId") or "")
        if not item_id:
            return AdapterResult(
                status=AdapterStatus.PERMANENT_ERROR,
                source=self.source,
                source_version=self.source_version,
                error_code="TOP_PUBLISH_UNCONFIRMED",
                safe_message="TOP 响应未确认发布后的商品 ID",
            )
        return AdapterResult(
            status=AdapterStatus.OK,
            data=XianyuProductSnapshot(
                external_product_id=item_id,
                title=request.title,
                price=request.price,
                status="ACTIVE",
                raw_payload=payload,
            ),
            source=self.source,
            source_version=self.source_version,
        )

    async def update_product(self, request: UpdateProductRequest):
        item_param = request.platform_payload.get("item_param")
        if not isinstance(item_param, dict):
            return AdapterResult(
                status=AdapterStatus.MANUAL_REQUIRED,
                source=self.source,
                source_version=self.source_version,
                error_code="TOP_BUSINESS_PAYLOAD_REQUIRED",
                safe_message="官方商品编辑需要与已获批业务类型匹配的 item_param",
            )
        try:
            payload = await self.client.call("alibaba.idle.isv.item.edit", item_param=item_param)
        except CliFailure as exc:
            return self._failure(exc)
        return AdapterResult(
            status=AdapterStatus.OK,
            data=XianyuProductSnapshot(
                external_product_id=request.external_product_id,
                title=str(request.fields.get("title") or "已编辑商品"),
                price=Money(amount=str(request.fields.get("price") or "0")),
                status="ACTIVE",
                raw_payload=payload,
            ),
            source=self.source,
            source_version=self.source_version,
        )

    async def pause_product(self, external_product_id):
        try:
            await self.client.call(
                "alibaba.idle.isv.item.downshelf", param={"item_id": str(external_product_id)}
            )
        except CliFailure as exc:
            return self._failure(exc)
        return AdapterResult(
            status=AdapterStatus.OK,
            data=None,
            source=self.source,
            source_version=self.source_version,
        )

    async def delete_product(self, external_product_id):
        return await self.pause_product(external_product_id)

    async def get_orders(self):
        return AdapterResult(
            status=AdapterStatus.UNSUPPORTED,
            source=self.source,
            source_version=self.source_version,
            error_code="TOP_ORDER_LIST_REQUIRES_EVENTS",
            safe_message="TOP 订单查询需要订单号；订单列表应通过官方消息或 Gateway webhook 同步",
        )

    async def ship_order(self, request: ShipOrderRequest):
        payload = {
            "biz_order_id": request.external_order_id,
            "logistics_company": request.carrier,
            "ship_mail_no": request.platform_payload.get("ship_mail_no"),
            "sender_phone": request.sender_phone,
            "sender_address": request.sender_address,
            "sender_name": request.sender_name,
            "sender_divisionid": request.sender_division_id,
            "lc_code": request.logistics_code,
        }
        if not payload["ship_mail_no"]:
            return AdapterResult(
                status=AdapterStatus.MANUAL_REQUIRED,
                source=self.source,
                source_version=self.source_version,
                error_code="TRACKING_SECRET_UNRESOLVED",
                safe_message="正式发货前需由受信 Gateway 将物流单号引用解析为实际单号",
            )
        try:
            response = await self.client.call("alibaba.idle.isv.order.ship", **payload)
        except CliFailure as exc:
            return self._failure(exc)
        return AdapterResult(
            status=AdapterStatus.OK,
            data=OrderSnapshot(
                external_order_id=request.external_order_id,
                status="XIANYU_SHIPPED",
                raw_payload=response,
            ),
            source=self.source,
            source_version=self.source_version,
        )

    async def send_message(self, _request: SendMessageRequest):
        return AdapterResult[MessageSnapshot](
            status=AdapterStatus.UNSUPPORTED,
            source=self.source,
            source_version=self.source_version,
            error_code="TOP_GENERIC_IM_UNAVAILABLE",
            safe_message="当前官方权限未提供通用闲鱼私信发送接口",
        )

    async def list_chats(self):
        return AdapterResult(
            status=AdapterStatus.UNSUPPORTED,
            source=self.source,
            source_version=self.source_version,
            error_code="TOP_GENERIC_IM_UNAVAILABLE",
            safe_message="当前官方权限未提供通用闲鱼私信会话接口",
        )

    async def list_messages(self, _external_conversation_id):
        return await self.list_chats()

    async def watch_messages(self):
        if False:
            yield None
        raise NotImplementedError("TOP 事件应通过消息服务推送到规范化 webhook")
