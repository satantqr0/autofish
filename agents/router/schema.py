from enum import Enum

from pydantic import BaseModel, Field


class Intent(str, Enum):
    PRODUCT_QUERY = "PRODUCT_QUERY"
    COMPATIBILITY = "COMPATIBILITY"
    PRICE_QUERY = "PRICE_QUERY"
    NEGOTIATION = "NEGOTIATION"
    STOCK = "STOCK"
    SHIPPING = "SHIPPING"
    LOGISTICS = "LOGISTICS"
    ORDER_STATUS = "ORDER_STATUS"
    USAGE = "USAGE"
    AFTERSALES = "AFTERSALES"
    REFUND = "REFUND"
    COMPLAINT = "COMPLAINT"
    UNKNOWN = "UNKNOWN"


class RouterDecision(BaseModel):
    schema_version: str = "1.0"
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    requires_human: bool
    reason_code: str
