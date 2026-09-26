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

from app.models.base import AutomationScope, Base, JobStatus, JobType, TimestampMixin


class AIDecision(Base):
    __tablename__ = "ai_decisions"
    __table_args__ = (Index("ix_ai_decisions_agent_created", "agent", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(String(60))
    schema_version: Mapped[str] = mapped_column(String(20))
    input_context_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    reason_summary: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT")
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    sourcing_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("sourcing_candidates.id", ondelete="SET NULL")
    )
    autonomous_launch_id: Mapped[int | None] = mapped_column(
        ForeignKey("autonomous_launches.id", ondelete="SET NULL")
    )
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AutomationControl(TimestampMixin, Base):
    __tablename__ = "automation_controls"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[AutomationScope] = mapped_column(
        Enum(AutomationScope, native_enum=False, length=40), unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(20), default="REVIEW")
    daily_limit: Mapped[int] = mapped_column(Integer, default=20)
    min_interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    failure_threshold: Mapped[int] = mapped_column(Integer, default=3)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_reason: Mapped[str | None] = mapped_column(String(500))
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )


class AutomationJob(TimestampMixin, Base):
    __tablename__ = "automation_jobs"
    __table_args__ = (
        Index("ix_jobs_status_schedule", "status", "scheduled_at"),
        Index("ix_jobs_status_lease_expiry", "status", "lease_expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[JobType] = mapped_column(Enum(JobType, native_enum=False, length=50), index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=40), default=JobStatus.PENDING
    )
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    manual_tasks = relationship("ManualTask", back_populates="automation_job")
    adapter_calls = relationship("AdapterCall", back_populates="automation_job")


class ManualTask(TimestampMixin, Base):
    __tablename__ = "manual_tasks"
    __table_args__ = (Index("ix_manual_tasks_status_priority", "status", "priority"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    automation_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("automation_jobs.id", ondelete="RESTRICT")
    )
    type: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="OPEN")
    priority: Mapped[int] = mapped_column(Integer, default=50)
    entity_type: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    resolution: Mapped[str | None] = mapped_column(Text)

    automation_job = relationship("AutomationJob", back_populates="manual_tasks")


class AdapterCall(Base):
    __tablename__ = "adapter_calls"
    __table_args__ = (Index("ix_adapter_calls_adapter_created", "adapter", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    automation_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("automation_jobs.id", ondelete="RESTRICT")
    )
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    adapter: Mapped[str] = mapped_column(String(100))
    adapter_version: Mapped[str | None] = mapped_column(String(80))
    operation: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40))
    duration_ms: Mapped[int] = mapped_column(Integer)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    raw_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    safe_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    automation_job = relationship("AutomationJob", back_populates="adapter_calls")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity_created", "entity_type", "entity_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    actor_type: Mapped[str] = mapped_column(String(30), default="SYSTEM")
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    before_data: Mapped[dict] = mapped_column(JSON, default=dict)
    after_data: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[str] = mapped_column(String(40))
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PublicationTask(TimestampMixin, Base):
    __tablename__ = "publication_tasks"
    __table_args__ = (
        UniqueConstraint("product_id", "status", name="uq_publication_product_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(40), default="MANUAL_REVIEW")
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    requested_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    review_note: Mapped[str | None] = mapped_column(Text)


class AutonomousLaunch(TimestampMixin, Base):
    """A resumable, audited candidate-to-Xianyu-draft pipeline."""

    __tablename__ = "autonomous_launches"
    __table_args__ = (Index("ix_autonomous_launches_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    automation_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("automation_jobs.id", ondelete="SET NULL"), unique=True
    )
    sourcing_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("sourcing_candidates.id", ondelete="SET NULL")
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("xianyu_drafts.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    current_stage: Mapped[str] = mapped_column(String(60), default="QUEUED")
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    selection: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_copy: Mapped[dict] = mapped_column(JSON, default=dict)
    asset_manifest: Mapped[list] = mapped_column(JSON, default=list)
    vision_qa: Mapped[dict] = mapped_column(JSON, default=dict)
    stages: Mapped[list] = mapped_column(JSON, default=list)
    blockers: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
