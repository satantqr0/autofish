from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Lifecycle, TimestampMixin, XianyuStatus

MONEY = Numeric(14, 2)
SCORE = Numeric(6, 2)
RATIO = Numeric(7, 4)


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(50), default="MANUAL")
    base_url: Mapped[str | None] = mapped_column(String(500))
    credential_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(40), default="ACTIVE")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    products = relationship("SupplierProduct", back_populates="supplier")


class SupplierProduct(TimestampMixin, Base):
    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("supplier_id", "external_product_id", name="uq_supplier_product_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"))
    external_product_id: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(1000))
    category: Mapped[str] = mapped_column(String(200))
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    external_supplier_id: Mapped[str | None] = mapped_column(String(160))
    raw_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(40), default="ACTIVE")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    supplier = relationship("Supplier", back_populates="products")
    skus = relationship("SupplierSKU", back_populates="supplier_product")


class SupplierSKU(TimestampMixin, Base):
    __tablename__ = "supplier_skus"
    __table_args__ = (
        UniqueConstraint("supplier_product_id", "external_sku_id", name="uq_supplier_sku_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_product_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_products.id", ondelete="RESTRICT")
    )
    external_sku_id: Mapped[str] = mapped_column(String(160))
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    shipping_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    stock_status: Mapped[str] = mapped_column(String(40), default="IN_STOCK")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    supplier_product = relationship("SupplierProduct", back_populates="skus")
    product_links = relationship("ProductSupplierLink", back_populates="supplier_sku")


class Product(TimestampMixin, Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    internal_code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    images: Mapped[list] = mapped_column(JSON, default=list)
    compatibility: Mapped[dict] = mapped_column(JSON, default=dict)
    lifecycle: Mapped[Lifecycle] = mapped_column(
        Enum(Lifecycle, native_enum=False, length=30), default=Lifecycle.CANDIDATE, index=True
    )
    xianyu_status: Mapped[XianyuStatus] = mapped_column(
        Enum(XianyuStatus, native_enum=False, length=30), default=XianyuStatus.UNPUBLISHED
    )
    excluded_reason: Mapped[str | None] = mapped_column(String(500))

    skus = relationship("ProductSKU", back_populates="product")
    scores = relationship("ProductScore", back_populates="product")


class ProductSKU(TimestampMixin, Base):
    __tablename__ = "product_skus"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    sku_code: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    supplier_cost: Mapped[Decimal] = mapped_column(MONEY)
    supplier_shipping: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    platform_fee: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    after_sales_reserve: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    minimum_profit: Mapped[Decimal] = mapped_column(MONEY)
    target_profit: Mapped[Decimal] = mapped_column(MONEY)
    negotiation_margin: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    final_cost: Mapped[Decimal] = mapped_column(MONEY)
    minimum_sale_price: Mapped[Decimal] = mapped_column(MONEY)
    recommended_price: Mapped[Decimal] = mapped_column(MONEY)
    current_sale_price: Mapped[Decimal | None] = mapped_column(MONEY)
    expected_profit: Mapped[Decimal] = mapped_column(MONEY)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[Decimal] = mapped_column(SCORE, default=Decimal("0"), index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product = relationship("Product", back_populates="skus")
    supplier_links = relationship("ProductSupplierLink", back_populates="product_sku")


class ProductSupplierLink(TimestampMixin, Base):
    __tablename__ = "product_supplier_links"
    __table_args__ = (
        UniqueConstraint("product_sku_id", "supplier_sku_id", name="uq_product_supplier_link"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_sku_id: Mapped[int] = mapped_column(ForeignKey("product_skus.id", ondelete="RESTRICT"))
    supplier_sku_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_skus.id", ondelete="RESTRICT")
    )
    priority: Mapped[int] = mapped_column(Integer, default=1)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    product_sku = relationship("ProductSKU", back_populates="supplier_links")
    supplier_sku = relationship("SupplierSKU", back_populates="product_links")


class ProductPriceHistory(Base):
    __tablename__ = "product_price_history"
    __table_args__ = (Index("ix_price_history_sku_checked", "product_sku_id", "checked_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_sku_id: Mapped[int] = mapped_column(ForeignKey("product_skus.id", ondelete="RESTRICT"))
    supplier_sku_id: Mapped[int | None] = mapped_column(
        ForeignKey("supplier_skus.id", ondelete="RESTRICT")
    )
    supplier_cost: Mapped[Decimal] = mapped_column(MONEY)
    shipping_cost: Mapped[Decimal] = mapped_column(MONEY)
    recommended_price: Mapped[Decimal] = mapped_column(MONEY)
    source: Mapped[str] = mapped_column(String(80))
    snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProductStockHistory(Base):
    __tablename__ = "product_stock_history"
    __table_args__ = (Index("ix_stock_history_sku_checked", "product_sku_id", "checked_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_sku_id: Mapped[int] = mapped_column(ForeignKey("product_skus.id", ondelete="RESTRICT"))
    supplier_sku_id: Mapped[int | None] = mapped_column(
        ForeignKey("supplier_skus.id", ondelete="RESTRICT")
    )
    stock: Mapped[int] = mapped_column(Integer)
    stock_status: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(80))
    snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PricingRule(TimestampMixin, Base):
    __tablename__ = "pricing_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str | None] = mapped_column(String(200), index=True)
    platform_fee_rate: Mapped[Decimal] = mapped_column(RATIO, default=Decimal("0"))
    fixed_platform_fee: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    after_sales_reserve: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    minimum_profit: Mapped[Decimal] = mapped_column(MONEY)
    negotiation_margin: Mapped[Decimal] = mapped_column(MONEY)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductScore(Base):
    __tablename__ = "product_scores"
    __table_args__ = (Index("ix_product_scores_product_created", "product_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    profit_space: Mapped[Decimal] = mapped_column(SCORE)
    after_sales_safety: Mapped[Decimal] = mapped_column(SCORE)
    fault_safety: Mapped[Decimal] = mapped_column(SCORE)
    compatibility_safety: Mapped[Decimal] = mapped_column(SCORE)
    transport_safety: Mapped[Decimal] = mapped_column(SCORE)
    stock_stability: Mapped[Decimal] = mapped_column(SCORE)
    price_stability: Mapped[Decimal] = mapped_column(SCORE)
    verticality: Mapped[Decimal] = mapped_column(SCORE)
    total_score: Mapped[Decimal] = mapped_column(SCORE, index=True)
    weights: Mapped[dict] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(30), default="1.0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    product = relationship("Product", back_populates="scores")


class XianyuProduct(TimestampMixin, Base):
    __tablename__ = "xianyu_products"
    __table_args__ = (
        UniqueConstraint("account_id", "external_product_id", name="uq_xianyu_product_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    external_product_id: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), default="DRAFT")
    published_title: Mapped[str | None] = mapped_column(String(500))
    published_price: Mapped[Decimal | None] = mapped_column(MONEY)
    raw_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
