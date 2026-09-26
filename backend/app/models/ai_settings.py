from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AIProviderConfig(TimestampMixin, Base):
    __tablename__ = "ai_provider_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(500))
    text_model: Mapped[str] = mapped_column(String(160))
    vision_model: Mapped[str | None] = mapped_column(String(160))
    image_model: Mapped[str | None] = mapped_column(String(160))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    api_key_hint: Mapped[str | None] = mapped_column(String(40))
    is_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    last_test_status: Mapped[str] = mapped_column(String(30), default="NOT_TESTED")
    last_test_message: Mapped[str | None] = mapped_column(String(500))
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class AIRuntimeSetting(TimestampMixin, Base):
    __tablename__ = "ai_runtime_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    primary_provider: Mapped[str] = mapped_column(String(40), default="openai")
    fallback_provider: Mapped[str | None] = mapped_column(String(40), default="deepseek")
    monthly_budget_cny: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("100.00"))
    temperature: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.200"))
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=1200)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    task_routing: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
