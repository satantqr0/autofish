from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class AdapterStatus(str, Enum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    UNSUPPORTED = "UNSUPPORTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    PERMANENT_ERROR = "PERMANENT_ERROR"


class AdapterResult(BaseModel, Generic[T]):
    status: AdapterStatus
    data: T | None = None
    source: str
    source_version: str = "unknown"
    raw_snapshot_hash: str | None = None
    retryable: bool = False
    error_code: str | None = None
    safe_message: str | None = None


class AdapterHealth(BaseModel):
    available: bool
    authenticated: bool
    read_only: bool = True
    details: dict = Field(default_factory=dict)


class Money(BaseModel):
    amount: str
    currency: str = "CNY"
