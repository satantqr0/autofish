from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CommercialDeployment(TimestampMixin, Base):
    """Single-tenant commercial delivery profile.

    AutoFish deliberately does not model hosted customer accounts or remote
    credential custody.  One row describes the customer-owned installation.
    """

    __tablename__ = "commercial_deployments"

    id: Mapped[int] = mapped_column(primary_key=True)
    edition: Mapped[str] = mapped_column(String(40), default="PRIVATE_DEPLOYMENT")
    installation_name: Mapped[str] = mapped_column(String(120), default="AutoFish 私有部署")
    customer_name: Mapped[str | None] = mapped_column(String(160))
    customer_type: Mapped[str] = mapped_column(String(40), default="INDIVIDUAL_OPERATOR")
    plan: Mapped[str] = mapped_column(String(30), default="PILOT")
    deployment_mode: Mapped[str] = mapped_column(String(60), default="CUSTOMER_OWNED_SINGLE_TENANT")

    account_owned_by_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    data_stays_customer_controlled: Mapped[bool] = mapped_column(Boolean, default=False)
    no_credential_custody: Mapped[bool] = mapped_column(Boolean, default=False)
    no_revenue_guarantee_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    prohibited_automation_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    regulatory_obligations_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)

    terms_version: Mapped[str | None] = mapped_column(String(30))
    privacy_version: Mapped[str | None] = mapped_column(String(30))
    compliance_version: Mapped[str | None] = mapped_column(String(30))
    accepted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommercialEvidencePeriod(TimestampMixin, Base):
    """Aggregate, non-personal commercial pilot evidence for one calendar month."""

    __tablename__ = "commercial_evidence_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, unique=True, index=True)
    qualified_leads: Mapped[int] = mapped_column(Integer, default=0)
    product_demos: Mapped[int] = mapped_column(Integer, default=0)
    paid_new_customers: Mapped[int] = mapped_column(Integer, default=0)
    active_customers: Mapped[int] = mapped_column(Integer, default=0)
    retained_30d_customers: Mapped[int] = mapped_column(Integer, default=0)
    refunded_customers: Mapped[int] = mapped_column(Integer, default=0)
    revenue_cny: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    delivery_hours: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"))
    support_hours: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
