from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MarketClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bridge_id: str = Field(pattern=r"^[a-zA-Z0-9:_-]{3,80}$")


class MarketItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(pattern=r"^\d{5,30}$")
    title: str = Field(min_length=1, max_length=2000)
    price: str = Field(pattern=r"^\d{1,7}(\.\d{1,2})?$")
    category_id: str = Field(default="", pattern=r"^\d{0,30}$")
    url: str = Field(max_length=300)


class MarketCapture(MarketClaim):
    run_id: int
    lease_token: str = Field(min_length=32, max_length=100)
    query: str = Field(min_length=1, max_length=40)
    status: Literal["OK", "AUTH_REQUIRED", "MANUAL_REQUIRED", "TRANSIENT_ERROR"]
    captured_at: datetime
    page_url: str = Field(max_length=500)
    items: list[MarketItem] = Field(default_factory=list, max_length=50)
    message: str = Field(default="", max_length=300)
