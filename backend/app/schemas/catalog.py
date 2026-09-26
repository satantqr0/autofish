from decimal import Decimal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class ProductCreate(BaseModel):
    supplier_code: str = Field(min_length=1, max_length=80)
    supplier_name: str = Field(min_length=1, max_length=200)
    supplier_source_type: str = Field(default="MANUAL", max_length=50)
    external_product_id: str = Field(min_length=1, max_length=160)
    external_supplier_id: str | None = Field(default=None, min_length=1, max_length=160)
    supplier_url: str | None = Field(default=None, max_length=1000)
    external_sku_id: str = Field(min_length=1, max_length=160)
    internal_code: str = Field(min_length=1, max_length=100)
    sku_code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=2, max_length=500)
    category: str = Field(min_length=1, max_length=200)
    description: str | None = None
    images: list[str] = Field(default_factory=list, max_length=20)
    spec: dict = Field(default_factory=dict)
    compatibility: dict = Field(default_factory=dict)
    supplier_price: Decimal = Field(ge=0)
    shipping_cost: Decimal = Field(default=Decimal("0"), ge=0)
    platform_fee: Decimal = Field(default=Decimal("0"), ge=0)
    after_sales_reserve: Decimal = Field(default=Decimal("0"), ge=0)
    minimum_profit: Decimal = Field(gt=0)
    target_profit: Decimal = Field(gt=0)
    negotiation_margin: Decimal = Field(default=Decimal("0"), ge=0)
    stock: int = Field(default=0, ge=0)
    lifecycle: str = "CANDIDATE"
    after_sales_safety: Decimal = Field(default=Decimal("85"), ge=0, le=100)
    fault_safety: Decimal = Field(default=Decimal("85"), ge=0, le=100)
    compatibility_safety: Decimal = Field(default=Decimal("80"), ge=0, le=100)
    transport_safety: Decimal = Field(default=Decimal("85"), ge=0, le=100)
    stock_stability: Decimal = Field(default=Decimal("80"), ge=0, le=100)
    price_stability: Decimal = Field(default=Decimal("80"), ge=0, le=100)
    verticality: Decimal = Field(default=Decimal("90"), ge=0, le=100)

    @field_validator("images")
    @classmethod
    def validate_images(cls, values):
        for value in values:
            parsed = urlparse(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("商品图片必须使用无凭证的 HTTPS 公网地址")
        return values

    @field_validator("lifecycle")
    @classmethod
    def validate_lifecycle(cls, value):
        allowed = {"CANDIDATE", "TESTING", "ACTIVE", "WINNER", "DECLINING", "PAUSED", "REMOVED"}
        if value not in allowed:
            raise ValueError("invalid lifecycle")
        return value


class ProductImportRequest(BaseModel):
    items: list[ProductCreate] = Field(min_length=1, max_length=100)


class PublicationQueueRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
