from adapters.base import AdapterHealth, AdapterResult, AdapterStatus, Money
from adapters.http_gateway import JsonGatewayClient
from adapters.runner import CliFailure
from adapters.supplier.port import (
    SourcingQuery,
    StockSnapshot,
    SupplierProductCandidate,
    SupplierProductDetail,
    SupplierSkuSnapshot,
    SupplierSnapshot,
)
from adapters.supplier.shopkeeper_1688 import _failure_result


class JsonGatewaySupplierAdapter:
    """Canonical read-only contract for an authorized supplier integration gateway."""

    source = "supplier-json-gateway"
    source_version = "1.0"

    def __init__(self, base_url=None, token=None, *, timeout_seconds=30):
        self.client = JsonGatewayClient(base_url, token, timeout_seconds=timeout_seconds)

    async def _request(self, method, path, **kwargs):
        try:
            return await self.client.request(method, path, **kwargs)
        except CliFailure as exc:
            return _failure_result(self.source, self.source_version, exc)

    async def health(self):
        if not self.client.available:
            return AdapterResult[AdapterHealth](
                status=AdapterStatus.AUTH_REQUIRED,
                data=AdapterHealth(available=False, authenticated=False, read_only=True),
                source=self.source,
                source_version=self.source_version,
                error_code="GATEWAY_NOT_CONFIGURED",
                safe_message="供应商 JSON Gateway 尚未配置",
            )
        payload = await self._request("GET", "/health")
        if isinstance(payload, AdapterResult):
            payload.data = AdapterHealth(available=True, authenticated=False, read_only=True)
            return payload
        health = AdapterHealth.model_validate(payload.get("data") or payload)
        health.read_only = True
        return AdapterResult(
            status=AdapterStatus.OK if health.authenticated else AdapterStatus.AUTH_REQUIRED,
            data=health,
            source=self.source,
            source_version=str(payload.get("version") or self.source_version),
        )

    async def search_products(self, query: SourcingQuery):
        payload = await self._request("POST", "/products/search", json=query.model_dump())
        if isinstance(payload, AdapterResult):
            return payload
        items = [SupplierProductCandidate.model_validate(item) for item in payload.get("items", [])]
        return AdapterResult(
            status=AdapterStatus.OK,
            data=items[: query.limit],
            source=self.source,
            source_version=str(payload.get("version") or self.source_version),
        )

    async def get_product(self, external_product_id):
        payload = await self._request("GET", f"/products/{external_product_id}")
        if isinstance(payload, AdapterResult):
            return payload
        return AdapterResult(
            status=AdapterStatus.OK,
            data=SupplierProductDetail.model_validate(payload.get("data") or payload),
            source=self.source,
            source_version=str(payload.get("version") or self.source_version),
        )

    async def get_skus(self, external_product_id):
        payload = await self._request("GET", f"/products/{external_product_id}/skus")
        if isinstance(payload, AdapterResult):
            return payload
        return AdapterResult(
            status=AdapterStatus.OK,
            data=[SupplierSkuSnapshot.model_validate(item) for item in payload.get("items", [])],
            source=self.source,
            source_version=str(payload.get("version") or self.source_version),
        )

    async def get_price(self, external_sku_id):
        payload = await self._request("GET", f"/skus/{external_sku_id}/price")
        if isinstance(payload, AdapterResult):
            return payload
        return AdapterResult(
            status=AdapterStatus.OK,
            data=Money.model_validate(payload.get("data") or payload),
            source=self.source,
            source_version=self.source_version,
        )

    async def get_stock(self, external_sku_id):
        payload = await self._request("GET", f"/skus/{external_sku_id}/stock")
        if isinstance(payload, AdapterResult):
            return payload
        return AdapterResult(
            status=AdapterStatus.OK,
            data=StockSnapshot.model_validate(payload.get("data") or payload),
            source=self.source,
            source_version=self.source_version,
        )

    async def get_supplier(self, external_supplier_id):
        payload = await self._request("GET", f"/suppliers/{external_supplier_id}")
        if isinstance(payload, AdapterResult):
            return payload
        return AdapterResult(
            status=AdapterStatus.OK,
            data=SupplierSnapshot.model_validate(payload.get("data") or payload),
            source=self.source,
            source_version=self.source_version,
        )

    def __getattr__(self, _name):
        raise NotImplementedError("Supplier gateway write operations are disabled")
