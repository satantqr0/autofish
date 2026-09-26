from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, OrderStatus, TimestampMixin

MONEY = Numeric(14, 2)


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_account_status_created", "account_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    external_order_id: Mapped[str] = mapped_column(String(160), unique=True)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, native_enum=False, length=40), default=OrderStatus.NEW
    )
    revenue: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    expected_profit: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    actual_profit: Mapped[Decimal | None] = mapped_column(MONEY)
    buyer_name_encrypted: Mapped[str | None] = mapped_column(Text)
    buyer_phone_encrypted: Mapped[str | None] = mapped_column(Text)
    buyer_address_encrypted: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items = relationship("OrderItem", back_populates="order")
    supplier_orders = relationship("SupplierOrder", back_populates="order")


class OrderItem(TimestampMixin, Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    product_sku_id: Mapped[int] = mapped_column(ForeignKey("product_skus.id", ondelete="RESTRICT"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_sale_price: Mapped[Decimal] = mapped_column(MONEY)
    supplier_cost_snapshot: Mapped[Decimal] = mapped_column(MONEY)
    shipping_snapshot: Mapped[Decimal] = mapped_column(MONEY)
    platform_fee_snapshot: Mapped[Decimal] = mapped_column(MONEY)
    reserve_snapshot: Mapped[Decimal] = mapped_column(MONEY)

    order = relationship("Order", back_populates="items")


class SupplierOrder(TimestampMixin, Base):
    __tablename__ = "supplier_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"))
    external_order_id: Mapped[str | None] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(40), default="PURCHASE_TASK")
    cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    shipping_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    payload_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    order = relationship("Order", back_populates="supplier_orders")
    shipments = relationship("Shipment", back_populates="supplier_order")


class Shipment(TimestampMixin, Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_order_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_orders.id", ondelete="RESTRICT")
    )
    carrier: Mapped[str | None] = mapped_column(String(100))
    tracking_number_encrypted: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="PENDING")
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    supplier_order = relationship("SupplierOrder", back_populates="shipments")


class AfterSale(TimestampMixin, Base):
    __tablename__ = "after_sales"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), default="MANUAL_REQUIRED")
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
    reason: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
