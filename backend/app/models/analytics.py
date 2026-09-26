from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

MONEY = Numeric(14, 2)
RATIO = Numeric(10, 6)


class XianyuProductDailyMetric(TimestampMixin, Base):
    __tablename__ = "xianyu_product_daily_metrics"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "external_product_id",
            "metric_date",
            name="uq_xianyu_product_daily_metric",
        ),
        Index("ix_xianyu_metrics_account_date", "account_id", "metric_date"),
        Index("ix_xianyu_metrics_product_date", "external_product_id", "metric_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    xianyu_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("xianyu_products.id", ondelete="SET NULL")
    )
    external_product_id: Mapped[str] = mapped_column(String(160))
    metric_date: Mapped[date] = mapped_column(Date)
    listing_title: Mapped[str] = mapped_column(String(500))
    listing_price: Mapped[Decimal] = mapped_column(MONEY)
    listing_days: Mapped[int] = mapped_column(Integer, default=0)
    listed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    category_path: Mapped[list] = mapped_column(JSON, default=list)
    exposures: Mapped[int] = mapped_column(Integer, default=0)
    exposed_users: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    viewers: Mapped[int] = mapped_column(Integer, default=0)
    inquiries: Mapped[int] = mapped_column(Integer, default=0)
    paid_users: Mapped[int] = mapped_column(Integer, default=0)
    paid_orders: Mapped[int] = mapped_column(Integer, default=0)
    paid_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    browse_pay_conversion: Mapped[Decimal | None] = mapped_column(RATIO)
    refund_requested_users: Mapped[int] = mapped_column(Integer, default=0)
    refund_requested_orders: Mapped[int] = mapped_column(Integer, default=0)
    refund_requested_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    refund_success_users: Mapped[int] = mapped_column(Integer, default=0)
    refund_success_orders: Mapped[int] = mapped_column(Integer, default=0)
    refund_success_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    source: Mapped[str] = mapped_column(String(80), default="seller-workbench-xlsx")
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    imported_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
