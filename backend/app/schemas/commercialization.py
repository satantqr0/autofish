from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CommercialProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_name: str = Field(min_length=2, max_length=120)
    customer_name: str | None = Field(default=None, max_length=160)
    customer_type: Literal["INDIVIDUAL_OPERATOR", "SOLE_PROPRIETOR", "COMPANY"]
    plan: Literal["PILOT", "STANDARD", "PRO"]

    @field_validator("installation_name")
    @classmethod
    def normalize_installation_name(cls, value):
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("安装名称不能为空")
        return normalized

    @field_validator("customer_name")
    @classmethod
    def normalize_customer_name(cls, value):
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class CommercialAcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_owned_by_customer: Literal[True]
    data_stays_customer_controlled: Literal[True]
    no_credential_custody: Literal[True]
    no_revenue_guarantee_acknowledged: Literal[True]
    prohibited_automation_acknowledged: Literal[True]
    regulatory_obligations_acknowledged: Literal[True]


class CommercialEvidenceUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: date
    qualified_leads: int = Field(ge=0, le=100000)
    product_demos: int = Field(ge=0, le=100000)
    paid_new_customers: int = Field(ge=0, le=100000)
    active_customers: int = Field(ge=0, le=100000)
    retained_30d_customers: int = Field(ge=0, le=100000)
    refunded_customers: int = Field(ge=0, le=100000)
    revenue_cny: Decimal = Field(ge=0, le=Decimal("999999999.99"), decimal_places=2)
    delivery_hours: Decimal = Field(ge=0, le=Decimal("999999.99"), decimal_places=2)
    support_hours: Decimal = Field(ge=0, le=Decimal("999999.99"), decimal_places=2)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value):
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def validate_period(self):
        if self.period_start.day != 1:
            raise ValueError("商业证据月份必须使用当月第一天")
        if self.retained_30d_customers > self.active_customers:
            raise ValueError("30 天留存客户不能超过当月活跃客户")
        return self
