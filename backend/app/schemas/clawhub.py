import ipaddress
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_public_https_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("仅允许不含认证信息的 HTTPS 公网地址")
    hostname = parsed.hostname.casefold()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValueError("不允许本地地址")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value
    if not address.is_global:
        raise ValueError("不允许内网、回环或保留地址")
    return value


class DiscoveryRequest(StrictModel):
    mode: Literal["TEXT", "IMAGE", "LINK"] = "TEXT"
    query: str | None = Field(default=None, min_length=2, max_length=200)
    source_url: str | None = Field(default=None, max_length=1000)
    limit: int = Field(default=20, ge=1, le=20)
    filters: dict = Field(default_factory=dict)

    _validate_source_url = field_validator("source_url")(_validate_public_https_url)

    @model_validator(mode="after")
    def validate_mode_input(self):
        if self.mode == "TEXT" and not self.query:
            raise ValueError("文本发现需要 query")
        if self.mode in {"IMAGE", "LINK"} and not self.source_url:
            raise ValueError("图片或链接发现需要 source_url")
        return self


class CompareRequest(StrictModel):
    candidate_ids: list[int] = Field(min_length=2, max_length=20)

    @field_validator("candidate_ids")
    @classmethod
    def unique_ids(cls, value: list[int]):
        if any(item <= 0 for item in value):
            raise ValueError("候选 ID 必须为正整数")
        if len(set(value)) != len(value):
            raise ValueError("候选 ID 不能重复")
        return value


class InsightRequest(StrictModel):
    kind: Literal["TREND", "OPPORTUNITIES"]
    query: str | None = Field(default=None, min_length=2, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_input(self):
        if self.kind == "TREND" and not self.query:
            raise ValueError("趋势洞察需要 query")
        return self


class SupplierDiscoveryRequest(StrictModel):
    sourcing_candidate_id: int | None = Field(default=None, gt=0)
    query: str | None = Field(default=None, min_length=2, max_length=200)
    limit: int = Field(default=10, ge=1, le=30)

    @model_validator(mode="after")
    def validate_target(self):
        if self.sourcing_candidate_id is None and not self.query:
            raise ValueError("sourcing_candidate_id 与 query 至少填写一项")
        return self


class ActionPreviewRequest(StrictModel):
    action_type: Literal[
        "PUBLISH_XIANYU",
        "UPDATE_PRICE",
        "REMOVE_LISTING",
        "SEND_REPLY",
        "CREATE_PURCHASE",
        "FILL_TRACKING",
        "CANCEL_ORDER",
        "REFUND_ORDER",
        "CHANGE_SUPPLIER",
    ]
    target_type: Literal["PRODUCT", "XIANYU_DRAFT", "ORDER", "CONVERSATION"]
    target_id: int = Field(gt=0)
    payload: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=180)


class ActionExecuteRequest(StrictModel):
    confirm: bool


class EvaluationRequest(StrictModel):
    product_id: int | None = Field(default=None, gt=0)
    sourcing_candidate_id: int | None = Field(default=None, gt=0)
    stage: Literal["PRE_LAUNCH", "POST_LAUNCH"] = "PRE_LAUNCH"
    metrics: dict[str, Decimal] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_target_and_metrics(self):
        if (self.product_id is None) == (self.sourcing_candidate_id is None):
            raise ValueError("product_id 与 sourcing_candidate_id 必须且只能填写一项")
        for key, value in self.metrics.items():
            if value < 0 or value > 100:
                raise ValueError(f"指标 {key} 必须在 0 到 100 之间")
        return self


class DiagnosisRequest(StrictModel):
    product_id: int | None = Field(default=None, gt=0)
    sourcing_candidate_id: int | None = Field(default=None, gt=0)
    metrics: dict[str, Decimal] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_target_and_metrics(self):
        if (self.product_id is None) == (self.sourcing_candidate_id is None):
            raise ValueError("product_id 与 sourcing_candidate_id 必须且只能填写一项")
        for key, value in self.metrics.items():
            if value < 0 or value > 100:
                raise ValueError(f"指标 {key} 必须在 0 到 100 之间")
        return self


class DraftCreateRequest(StrictModel):
    product_id: int = Field(gt=0)
    target_category: str | None = Field(default=None, min_length=1, max_length=200)
    title_mode: Literal["RULE_ONLY"] = "RULE_ONLY"
    price: Decimal | None = Field(default=None, gt=0)


class InquiryCreateRequest(StrictModel):
    supplier_candidate_id: int | None = Field(default=None, gt=0)
    sourcing_candidate_id: int | None = Field(default=None, gt=0)
    topic: str = Field(min_length=2, max_length=300)
    questions: list[str] = Field(min_length=1, max_length=12)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=180)

    @field_validator("questions")
    @classmethod
    def validate_questions(cls, value: list[str]):
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 500 for item in normalized):
            raise ValueError("每个问题长度必须为 1 到 500 个字符")
        return normalized

    @model_validator(mode="after")
    def validate_target(self):
        if self.supplier_candidate_id is None and self.sourcing_candidate_id is None:
            raise ValueError("至少指定供应商候选或选品候选")
        return self


class InquiryResultRequest(StrictModel):
    status: Literal["REPLIED", "FAILED", "TIMEOUT"]
    result: dict = Field(default_factory=dict)
    message: str | None = Field(default=None, max_length=5000)
