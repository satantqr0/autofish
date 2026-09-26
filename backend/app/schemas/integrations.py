import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ALI_IMAGE_HOST = "alicdn.com"
PRODUCT_PATH_PATTERN = re.compile(r"^/offer/([0-9]{6,20})\.html$")


def extract_1688_product_id(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "detail.1688.com"
        or parsed.username
        or parsed.password
    ):
        raise ValueError("来源链接必须是 1688 商品详情 HTTPS 地址")
    matched = PRODUCT_PATH_PATTERN.fullmatch(parsed.path.rstrip("/"))
    if matched is None:
        raise ValueError("来源链接必须指向 1688 商品详情页")
    return matched.group(1)


def _clean_evidence_text(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(ord(character) < 32 for character in normalized):
        raise ValueError("核验字段包含空值或无效控制字符")
    return normalized


class SupplierFeedSku(BaseModel):
    external_sku_id: str = Field(min_length=1, max_length=200)
    spec: dict = Field(default_factory=dict)
    price: Decimal = Field(ge=0)
    shipping: Decimal | None = Field(default=None, ge=0)
    stock: int = Field(ge=0)
    raw_payload: dict = Field(default_factory=dict)


class SourcingScoreInput(BaseModel):
    profit_space: Decimal = Field(ge=0, le=100)
    after_sales_safety: Decimal = Field(ge=0, le=100)
    fault_safety: Decimal = Field(ge=0, le=100)
    compatibility_safety: Decimal = Field(ge=0, le=100)
    transport_safety: Decimal = Field(ge=0, le=100)
    stock_stability: Decimal = Field(ge=0, le=100)
    price_stability: Decimal = Field(ge=0, le=100)
    verticality: Decimal = Field(ge=0, le=100)


class SupplierFeedProduct(BaseModel):
    external_product_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=2, max_length=500)
    category: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=1000)
    image_url: str | None = Field(default=None, max_length=1000)
    external_supplier_id: str | None = Field(default=None, min_length=1, max_length=200)
    supplier_name: str = Field(min_length=1, max_length=200)
    skus: list[SupplierFeedSku] = Field(min_length=1, max_length=200)
    stats: dict = Field(default_factory=dict)
    score_inputs: SourcingScoreInput | None = None
    raw_payload: dict = Field(default_factory=dict)


class SupplierFeedRequest(BaseModel):
    adapter_name: str = Field(default="authorized-json-feed", min_length=1, max_length=100)
    adapter_version: str = Field(default="1.0", min_length=1, max_length=80)
    items: list[SupplierFeedProduct] = Field(min_length=1, max_length=100)


class SourcingSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=20, ge=1, le=20)
    filters: dict = Field(default_factory=dict)


class CandidateImportRequest(BaseModel):
    external_sku_id: str | None = Field(default=None, max_length=200)
    internal_code: str = Field(min_length=1, max_length=100)
    sku_code: str = Field(min_length=1, max_length=120)
    listing_title: str | None = Field(default=None, min_length=2, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    platform_fee: Decimal = Field(default=Decimal("0"), ge=0)
    after_sales_reserve: Decimal = Field(default=Decimal("0"), ge=0)
    minimum_profit: Decimal = Field(gt=0)
    target_profit: Decimal = Field(gt=0)
    negotiation_margin: Decimal = Field(default=Decimal("0"), ge=0)


class ManualVerifiedSupplierSku(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_sku_id: str = Field(min_length=1, max_length=200)
    spec: dict = Field(default_factory=dict)
    price: Decimal = Field(
        ge=0,
        le=Decimal("99999999.99"),
        max_digits=10,
        decimal_places=2,
    )
    shipping: Decimal = Field(
        ge=0,
        le=Decimal("99999999.99"),
        max_digits=10,
        decimal_places=2,
    )
    stock: int = Field(ge=0, le=100_000_000)

    _validate_sku_id = field_validator("external_sku_id")(_clean_evidence_text)

    @field_validator("spec")
    @classmethod
    def validate_spec(cls, value):
        if len(json.dumps(value, ensure_ascii=False, default=str)) > 5_000:
            raise ValueError("SKU 规格数据超过 5000 字符")
        return value


class CandidateManualVerificationRequest(BaseModel):
    """Recently captured facts from a 1688 detail page or authorized official API."""

    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(min_length=30, max_length=1000)
    captured_at: datetime
    category: str = Field(min_length=1, max_length=200)
    external_supplier_id: str | None = Field(default=None, min_length=1, max_length=200)
    supplier_name: str = Field(min_length=1, max_length=200)
    image_url: str = Field(min_length=20, max_length=1000)
    skus: list[ManualVerifiedSupplierSku] = Field(min_length=1, max_length=200)
    evidence_note: str = Field(min_length=5, max_length=1000)
    evidence_method: Literal[
        "OPERATOR_1688_DETAIL_PAGE",
        "AUTHORIZED_1688_API",
    ] = "OPERATOR_1688_DETAIL_PAGE"

    _validate_text = field_validator(
        "category",
        "supplier_name",
        "evidence_note",
    )(_clean_evidence_text)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value):
        extract_1688_product_id(value)
        return value

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, value):
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username
            or parsed.password
            or not (hostname == ALI_IMAGE_HOST or hostname.endswith(f".{ALI_IMAGE_HOST}"))
        ):
            raise ValueError("商品图片必须来自阿里 CDN 的 HTTPS 公网地址")
        return value

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value):
        if value.tzinfo is None:
            raise ValueError("captured_at 必须包含时区")
        now = datetime.now(UTC)
        normalized = value.astimezone(UTC)
        if normalized > now + timedelta(minutes=5):
            raise ValueError("captured_at 不能来自未来")
        if normalized < now - timedelta(hours=24):
            raise ValueError("供应证据超过 24 小时，请重新核验")
        return normalized

    @model_validator(mode="after")
    def validate_unique_skus(self):
        sku_ids = [item.external_sku_id for item in self.skus]
        if len(sku_ids) != len(set(sku_ids)):
            raise ValueError("核验数据包含重复 SKU ID")
        return self
