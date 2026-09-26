from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AutomationControlUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    reason: str = Field(min_length=2, max_length=500)
    mode: Literal["REVIEW", "AUTOMATIC"] | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=1000)
    min_interval_seconds: int | None = Field(default=None, ge=1, le=86400)
    failure_threshold: int | None = Field(default=None, ge=1, le=20)


class AutomationJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    idempotency_key: str = Field(min_length=8, max_length=180)
    payload: dict = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=5, le=900)
