from adapters.base import AdapterHealth, AdapterResult, AdapterStatus
from adapters.http_gateway import JsonGatewayClient
from adapters.runner import CliFailure
from adapters.supplier.shopkeeper_1688 import _failure_result
from adapters.xianyu.port import (
    ChatSnapshot,
    ConversationMessageSnapshot,
    MessageSnapshot,
    OrderSnapshot,
    PublishRequest,
    SendMessageRequest,
    ShipOrderRequest,
    UpdateProductRequest,
    XianyuAccountSnapshot,
    XianyuProductSnapshot,
)


class JsonGatewayXianyuAdapter:
    source = "xianyu-json-gateway"
    source_version = "1.0"

    def __init__(self, base_url=None, token=None, *, timeout_seconds=30):
        self.client = JsonGatewayClient(base_url, token, timeout_seconds=timeout_seconds)

    async def _request(self, method, path, **kwargs):
        try:
            return await self.client.request(method, path, **kwargs)
        except CliFailure as exc:
            return _failure_result(self.source, self.source_version, exc)

    async def _result(self, method, path, model, *, list_key=None, **request_kwargs):
        payload = await self._request(method, path, **request_kwargs)
        if isinstance(payload, AdapterResult):
            return payload
        if list_key:
            data = [model.model_validate(item) for item in payload.get(list_key, [])]
        else:
            data = model.model_validate(payload.get("data") or payload)
        return AdapterResult(
            status=AdapterStatus.OK,
            data=data,
            source=self.source,
            source_version=str(payload.get("version") or self.source_version),
        )

    async def health(self):
        if not self.client.available:
            return AdapterResult[AdapterHealth](
                status=AdapterStatus.AUTH_REQUIRED,
                data=AdapterHealth(available=False, authenticated=False, read_only=True),
                source=self.source,
                source_version=self.source_version,
                error_code="GATEWAY_NOT_CONFIGURED",
                safe_message="闲鱼 JSON Gateway 尚未配置",
            )
        return await self._result("GET", "/health", AdapterHealth)

    async def get_account(self):
        return await self._result("GET", "/account", XianyuAccountSnapshot)

    async def list_products(self):
        return await self._result("GET", "/products", XianyuProductSnapshot, list_key="items")

    async def get_product(self, external_product_id):
        return await self._result("GET", f"/products/{external_product_id}", XianyuProductSnapshot)

    async def list_chats(self):
        return await self._result("GET", "/conversations", ChatSnapshot, list_key="items")

    async def list_messages(self, external_conversation_id):
        return await self._result(
            "GET",
            f"/conversations/{external_conversation_id}/messages",
            ConversationMessageSnapshot,
            list_key="items",
        )

    async def get_orders(self):
        return await self._result("GET", "/orders", OrderSnapshot, list_key="items")

    async def publish_product(self, request: PublishRequest):
        return await self._result(
            "POST",
            "/products",
            XianyuProductSnapshot,
            json=request.model_dump(mode="json"),
            headers={"Idempotency-Key": request.idempotency_key},
        )

    async def update_product(self, request: UpdateProductRequest):
        return await self._result(
            "PATCH",
            f"/products/{request.external_product_id}",
            XianyuProductSnapshot,
            json=request.model_dump(mode="json"),
            headers={"Idempotency-Key": request.idempotency_key},
        )

    async def pause_product(self, external_product_id):
        payload = await self._request("POST", f"/products/{external_product_id}/pause")
        if isinstance(payload, AdapterResult):
            return payload
        return AdapterResult(
            status=AdapterStatus.OK,
            data=None,
            source=self.source,
            source_version=str(payload.get("version") or self.source_version),
        )

    async def delete_product(self, external_product_id):
        payload = await self._request("DELETE", f"/products/{external_product_id}")
        if isinstance(payload, AdapterResult):
            return payload
        return AdapterResult(
            status=AdapterStatus.OK,
            data=None,
            source=self.source,
            source_version=str(payload.get("version") or self.source_version),
        )

    async def send_message(self, request: SendMessageRequest):
        return await self._result(
            "POST",
            f"/conversations/{request.external_conversation_id}/messages",
            MessageSnapshot,
            json=request.model_dump(mode="json"),
            headers={"Idempotency-Key": request.idempotency_key},
        )

    async def ship_order(self, request: ShipOrderRequest):
        return await self._result(
            "POST",
            f"/orders/{request.external_order_id}/ship",
            OrderSnapshot,
            json=request.model_dump(mode="json"),
            headers={"Idempotency-Key": request.idempotency_key},
        )

    async def watch_messages(self):
        if False:
            yield None
        raise NotImplementedError("Gateway 事件应通过 webhook 或独立 SSE consumer 接入")
