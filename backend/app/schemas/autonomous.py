from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class AutonomousLaunchCreate(BaseModel):
    candidate_id: int | None = Field(default=None, ge=1)
    query: str | None = Field(default=None, max_length=200)
    candidate_limit: int = Field(default=50, ge=1, le=200)
    image_count: int = Field(default=2, ge=1, le=4)
    dimension_image_required: bool = True
    platform_fee: Decimal = Field(default=Decimal("2.00"), ge=0, le=1000)
    after_sales_reserve: Decimal = Field(default=Decimal("3.00"), ge=0, le=1000)
    minimum_profit: Decimal = Field(default=Decimal("8.00"), gt=0, le=10000)
    target_profit: Decimal = Field(default=Decimal("14.00"), gt=0, le=10000)
    negotiation_margin: Decimal = Field(default=Decimal("2.00"), ge=0, le=1000)
    idempotency_key: str = Field(min_length=8, max_length=180)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str | None):
        return value.strip() or None if value is not None else None


class ListingCopyOutput(BaseModel):
    title: str = Field(min_length=2, max_length=30)
    description: str = Field(min_length=20, max_length=4500)
    image_prompts: list[str] = Field(min_length=1, max_length=4)
    factual_basis: list[str] = Field(min_length=1, max_length=20)

    @field_validator("title", "description")
    @classmethod
    def strip_copy(cls, value: str):
        return value.strip()

    @field_validator("image_prompts")
    @classmethod
    def strip_prompts(cls, values: list[str]):
        prompts = [value.strip() for value in values if value.strip()]
        if not prompts:
            raise ValueError("at least one image prompt is required")
        return prompts


class CopyQAOutput(BaseModel):
    factual_consistency: bool
    owner_voice: bool
    no_supplier_context: bool
    buyer_relevance: bool
    confidence: Decimal = Field(ge=0, le=1)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=30)
    blockers: list[str] = Field(default_factory=list, max_length=30)
    summary: str = Field(min_length=2, max_length=500)


class VisionQAOutput(BaseModel):
    structure_match: bool
    color_match: bool
    specification_consistent: bool
    no_added_accessories: bool
    no_misleading_text: bool
    premium_quality: bool
    source_visible_text: list[str] = Field(max_length=30)
    generated_visible_text: list[str] = Field(max_length=30)
    unreadable_text: bool
    confidence: Decimal = Field(ge=0, le=1)
    blockers: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(min_length=2, max_length=500)


class DimensionEvidenceOutput(BaseModel):
    available: bool
    variant_label: str | None = Field(default=None, max_length=80)
    measurements: list[str] = Field(default_factory=list, max_length=12)
    layout_instructions: list[str] = Field(default_factory=list, max_length=12)
    summary: str | None = Field(default=None, max_length=160)
    evidence_text: list[str] = Field(default_factory=list, max_length=30)
    confidence: Decimal = Field(ge=0, le=1)
    blockers: list[str] = Field(default_factory=list, max_length=20)
