from decimal import Decimal

from pydantic import BaseModel, Field, SecretStr, field_validator

SUPPORTED_PROVIDERS = {"openai", "deepseek", "qwen"}


class AIProviderUpdate(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    text_model: str = Field(min_length=2, max_length=160)
    vision_model: str | None = Field(default=None, max_length=160)
    image_model: str | None = Field(default=None, max_length=160)
    api_key: SecretStr | None = None

    @field_validator("vision_model", "image_model")
    @classmethod
    def empty_to_none(cls, value: str | None):
        return value.strip() or None if value is not None else None


class AIRuntimeUpdate(BaseModel):
    enabled: bool
    primary_provider: str
    fallback_provider: str | None = None
    monthly_budget_cny: Decimal = Field(ge=Decimal("1"), le=Decimal("100000"))
    temperature: Decimal = Field(ge=Decimal("0"), le=Decimal("2"))
    max_output_tokens: int = Field(ge=64, le=32768)
    request_timeout_seconds: int = Field(ge=5, le=180)
    task_routing: dict[str, str] = Field(default_factory=dict)

    @field_validator("primary_provider")
    @classmethod
    def valid_primary(cls, value: str):
        if value not in SUPPORTED_PROVIDERS:
            raise ValueError("unsupported primary provider")
        return value

    @field_validator("fallback_provider")
    @classmethod
    def valid_fallback(cls, value: str | None):
        if value is not None and value not in SUPPORTED_PROVIDERS:
            raise ValueError("unsupported fallback provider")
        return value

    @field_validator("task_routing")
    @classmethod
    def valid_routes(cls, value: dict[str, str]):
        invalid = set(value.values()) - SUPPORTED_PROVIDERS - {"human"}
        if invalid:
            raise ValueError("task routing contains unsupported provider")
        return value
