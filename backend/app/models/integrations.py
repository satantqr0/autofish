from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    DateTime,
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

from app.models.base import Base, TimestampMixin

MONEY = Numeric(14, 2)
SCORE = Numeric(6, 2)


class IntegrationSyncRun(TimestampMixin, Base):
    __tablename__ = "integration_sync_runs"
    __table_args__ = (
        Index("ix_integration_runs_platform_started", "platform", "started_at"),
        Index("ix_integration_runs_status_started", "status", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(40))
    adapter_name: Mapped[str] = mapped_column(String(100))
    adapter_version: Mapped[str] = mapped_column(String(80), default="unknown")
    operation: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), default="RUNNING")
    requested_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_written: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    safe_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    snapshots = relationship("ExternalSnapshot", back_populates="sync_run")


class ExternalSnapshot(Base):
    __tablename__ = "external_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "object_type",
            "external_id",
            "payload_hash",
            name="uq_external_snapshot_identity_hash",
        ),
        Index("ix_external_snapshot_object_fetched", "platform", "object_type", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("integration_sync_runs.id", ondelete="SET NULL")
    )
    platform: Mapped[str] = mapped_column(String(40))
    object_type: Mapped[str] = mapped_column(String(60))
    external_id: Mapped[str] = mapped_column(String(200))
    adapter_name: Mapped[str] = mapped_column(String(100))
    adapter_version: Mapped[str] = mapped_column(String(80), default="unknown")
    payload_hash: Mapped[str] = mapped_column(String(64))
    normalized_data: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sync_run = relationship("IntegrationSyncRun", back_populates="snapshots")


class SourcingCandidate(TimestampMixin, Base):
    __tablename__ = "sourcing_candidates"
    __table_args__ = (
        UniqueConstraint("adapter_name", "external_product_id", name="uq_sourcing_candidate"),
        Index("ix_sourcing_candidates_status_score", "status", "score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("external_snapshots.id", ondelete="SET NULL")
    )
    adapter_name: Mapped[str] = mapped_column(String(100))
    adapter_version: Mapped[str] = mapped_column(String(80), default="unknown")
    external_product_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(1000))
    image_url: Mapped[str | None] = mapped_column(String(1000))
    category: Mapped[str | None] = mapped_column(String(200), index=True)
    external_supplier_id: Mapped[str | None] = mapped_column(String(200))
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    minimum_price: Mapped[Decimal | None] = mapped_column(MONEY)
    maximum_price: Mapped[Decimal | None] = mapped_column(MONEY)
    sku_count: Mapped[int] = mapped_column(Integer, default=0)
    stock: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[Decimal | None] = mapped_column(SCORE, index=True)
    status: Mapped[str] = mapped_column(String(40), default="DISCOVERED")
    normalized_data: Mapped[dict] = mapped_column(JSON, default=dict)
    last_fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
