from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

MONEY = Numeric(14, 2)
SCORE = Numeric(6, 2)


class SupplierDiscoveryJob(TimestampMixin, Base):
    __tablename__ = "supplier_discovery_jobs"
    __table_args__ = (Index("ix_supplier_discovery_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[str] = mapped_column(String(300))
    sourcing_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("sourcing_candidates.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(100), default="local-fact-pool")
    status: Mapped[str] = mapped_column(String(40), default="RUNNING")
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    error_code: Mapped[str | None] = mapped_column(String(100))
    safe_message: Mapped[str | None] = mapped_column(Text)


class SupplierCandidate(TimestampMixin, Base):
    __tablename__ = "supplier_candidates"
    __table_args__ = (
        UniqueConstraint("source", "external_supplier_id", name="uq_supplier_candidate_source"),
        Index("ix_supplier_candidates_status_score", "status", "suitability_score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_supplier_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(100))
    source_version: Mapped[str] = mapped_column(String(80), default="unknown")
    region: Mapped[str | None] = mapped_column(String(160))
    industry: Mapped[str | None] = mapped_column(String(200))
    factory_tags: Mapped[list] = mapped_column(JSON, default=list)
    source_product_count: Mapped[int] = mapped_column(Integer, default=0)
    suitability_score: Mapped[Decimal] = mapped_column(SCORE, default=Decimal("0"))
    response_speed_score: Mapped[Decimal | None] = mapped_column(SCORE)
    delivery_score: Mapped[Decimal | None] = mapped_column(SCORE)
    status: Mapped[str] = mapped_column(String(40), default="DISCOVERED")
    raw_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupplierProductMatch(TimestampMixin, Base):
    __tablename__ = "supplier_product_matches"
    __table_args__ = (
        UniqueConstraint(
            "sourcing_candidate_id",
            "supplier_candidate_id",
            "external_product_id",
            name="uq_supplier_product_match",
        ),
        Index("ix_supplier_matches_candidate_priority", "sourcing_candidate_id", "priority"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sourcing_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("sourcing_candidates.id", ondelete="CASCADE")
    )
    supplier_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_candidates.id", ondelete="CASCADE")
    )
    external_product_id: Mapped[str] = mapped_column(String(200))
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY)
    stock: Mapped[int | None] = mapped_column(Integer)
    minimum_order_quantity: Mapped[int | None] = mapped_column(Integer)
    shipping_terms: Mapped[dict] = mapped_column(JSON, default=dict)
    match_score: Mapped[Decimal] = mapped_column(SCORE, default=Decimal("0"))
    priority: Mapped[int] = mapped_column(Integer, default=1)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40), default="ACTIVE")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class SupplierReliabilityScore(Base):
    __tablename__ = "supplier_reliability_scores"
    __table_args__ = (
        Index("ix_supplier_reliability_created", "supplier_candidate_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_candidates.id", ondelete="CASCADE")
    )
    total_score: Mapped[Decimal] = mapped_column(SCORE)
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    engine_version: Mapped[str] = mapped_column(String(30), default="1.0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlatformActionRecord(TimestampMixin, Base):
    __tablename__ = "platform_action_records"
    __table_args__ = (Index("ix_platform_actions_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    action_type: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[int] = mapped_column(Integer)
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    preview: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_result: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="PREVIEWED")
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str | None] = mapped_column(String(100))
    correlation_id: Mapped[str | None] = mapped_column(String(80), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrowserBridgeAgent(TimestampMixin, Base):
    __tablename__ = "browser_bridge_agents"
    __table_args__ = (Index("ix_browser_bridge_agents_last_seen", "last_seen_at"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="ONLINE")
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    current_url: Mapped[str | None] = mapped_column(String(1000))
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProductEvaluation(Base):
    __tablename__ = "product_evaluations"
    __table_args__ = (
        Index("ix_product_evaluations_product_created", "product_id", "created_at"),
        Index("ix_product_evaluations_candidate_created", "sourcing_candidate_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    sourcing_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("sourcing_candidates.id", ondelete="CASCADE")
    )
    stage: Mapped[str] = mapped_column(String(40))
    grade: Mapped[str] = mapped_column(String(10), index=True)
    total_score: Mapped[Decimal] = mapped_column(SCORE)
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    engine_version: Mapped[str] = mapped_column(String(30), default="2.0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProductDiagnosis(Base):
    __tablename__ = "product_diagnoses"
    __table_args__ = (Index("ix_product_diagnoses_severity_created", "severity", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    sourcing_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("sourcing_candidates.id", ondelete="CASCADE")
    )
    diagnosis: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    recommended_actions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="OPEN")
    engine_version: Mapped[str] = mapped_column(String(30), default="1.0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class XianyuDraft(TimestampMixin, Base):
    __tablename__ = "xianyu_drafts"
    __table_args__ = (
        UniqueConstraint("product_id", "input_hash", name="uq_xianyu_draft_product_hash"),
        Index("ix_xianyu_drafts_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(MONEY)
    category: Mapped[str | None] = mapped_column(String(200))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    images: Mapped[list] = mapped_column(JSON, default=list)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    pipeline_trace: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(40), default="DRAFT")
    input_hash: Mapped[str] = mapped_column(String(64))
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class SupplierInquiry(TimestampMixin, Base):
    __tablename__ = "supplier_inquiries"
    __table_args__ = (Index("ix_supplier_inquiries_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("supplier_candidates.id", ondelete="SET NULL")
    )
    sourcing_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("sourcing_candidates.id", ondelete="SET NULL")
    )
    topic: Mapped[str] = mapped_column(String(300))
    questions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(40), default="MANUAL_REQUIRED")
    upstream_task_id: Mapped[str | None] = mapped_column(String(200))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)


class SupplierInquiryMessage(Base):
    __tablename__ = "supplier_inquiry_messages"
    __table_args__ = (Index("ix_inquiry_messages_inquiry_created", "inquiry_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    inquiry_id: Mapped[int] = mapped_column(ForeignKey("supplier_inquiries.id", ondelete="CASCADE"))
    direction: Mapped[str] = mapped_column(String(20))
    author_type: Mapped[str] = mapped_column(String(30), default="OPERATOR")
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
