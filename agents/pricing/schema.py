from decimal import Decimal

from pydantic import BaseModel, Field


class PricingSuggestion(BaseModel):
    schema_version: str = "1.0"
    suggested_price: Decimal = Field(gt=0)
    reply: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)
    requires_human: bool = False
