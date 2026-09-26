from typing import Protocol

from pydantic import BaseModel, Field

from adapters.base import AdapterHealth, AdapterResult, Money


class SourcingQuery(BaseModel):
    query: str
    limit: int = Field(default=20, ge=1, le=20)
    filters: dict = Field(default_factory=dict)


class SupplierSkuSnapshot(BaseModel):
    external_sku_id: str
    spec: dict = Field(default_factory=dict)
    price: Money | None = None
    shipping: Money | None = None
    stock: int | None = None
    raw_payload: dict = Field(default_factory=dict)


class SupplierProductCandidate(BaseModel):
    external_product_id: str
    title: str
    price: Money | None = None
    url: str | None = None
    image_url: str | None = None
    category: str | None = None
    external_supplier_id: str | None = None
    supplier_name: str | None = None
    skus: list[SupplierSkuSnapshot] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    raw_payload: dict = Field(default_factory=dict)


class SupplierProductDetail(BaseModel):
    external_product_id: str
    title: str
    category: str | None = None
    url: str | None = None
    images: list[str] = Field(default_factory=list)
    external_supplier_id: str | None = None
    supplier_name: str | None = None
    raw_content: str | None = None
    attributes: dict = Field(default_factory=dict)
    skus: list["SupplierSkuSnapshot"] = Field(default_factory=list)
    raw_payload: dict = Field(default_factory=dict)


SupplierProductDetail.model_rebuild()


class StockSnapshot(BaseModel):
    stock: int
    status: str


class SupplierSnapshot(BaseModel):
    external_supplier_id: str
    name: str
    metadata: dict = Field(default_factory=dict)


class SupplierInsight(BaseModel):
    kind: str
    query: str | None = None
    summary: str | None = None
    items: list[dict] = Field(default_factory=list)
    raw_payload: dict = Field(default_factory=dict)


class SupplierOrderRequest(BaseModel):
    product_sku_id: int
    quantity: int = Field(ge=1)
    encrypted_address_ref: str
    idempotency_key: str


class SupplierOrderSnapshot(BaseModel):
    external_order_id: str
    status: str
    amount: Money | None = None


class TrackingSnapshot(BaseModel):
    carrier: str | None = None
    tracking_number_masked: str | None = None
    status: str


class SupplierAdapter(Protocol):
    async def health(self) -> AdapterResult[AdapterHealth]: ...
    async def search_products(
        self, query: SourcingQuery
    ) -> AdapterResult[list[SupplierProductCandidate]]: ...
    async def get_product(
        self, external_product_id: str
    ) -> AdapterResult[SupplierProductDetail]: ...
    async def get_skus(
        self, external_product_id: str
    ) -> AdapterResult[list[SupplierSkuSnapshot]]: ...
    async def get_price(self, external_sku_id: str) -> AdapterResult[Money]: ...
    async def get_stock(self, external_sku_id: str) -> AdapterResult[StockSnapshot]: ...
    async def get_supplier(self, external_supplier_id: str) -> AdapterResult[SupplierSnapshot]: ...
    async def search_image(
        self, source_url: str, limit: int = 20
    ) -> AdapterResult[list[SupplierProductCandidate]]: ...
    async def search_link(
        self, source_url: str, limit: int = 20
    ) -> AdapterResult[list[SupplierProductCandidate]]: ...
    async def get_trends(self, query: str) -> AdapterResult[SupplierInsight]: ...
    async def get_opportunities(
        self, category: str | None = None
    ) -> AdapterResult[SupplierInsight]: ...
    async def create_order(
        self, request: SupplierOrderRequest
    ) -> AdapterResult[SupplierOrderSnapshot]: ...
    async def get_order(self, external_order_id: str) -> AdapterResult[SupplierOrderSnapshot]: ...
    async def get_tracking(self, external_order_id: str) -> AdapterResult[TrackingSnapshot]: ...
    async def cancel_order(
        self, external_order_id: str
    ) -> AdapterResult[SupplierOrderSnapshot]: ...
