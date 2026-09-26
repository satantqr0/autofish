from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTOFISH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AutoFish"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://autofish:autofish@localhost:5432/autofish"
    database_pool_size: int = Field(default=10, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=50)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=120)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)
    database_statement_timeout_ms: int = Field(default=120000, ge=1000, le=900000)
    database_lock_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    # Automation jobs may legitimately wait up to 180 seconds for one model
    # request and are bounded by a 960-second Celery hard limit.  Keep the
    # database guard above that process limit so PostgreSQL does not terminate
    # a valid job while it is waiting on an authorized external adapter.
    database_idle_transaction_timeout_ms: int = Field(
        default=1020000, ge=1000, le=1200000
    )
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = Field(default="development-only-change-me-32-chars")
    credential_encryption_secret: str | None = None
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 480
    admin_username: str = "admin"
    admin_password: str = "change-me-before-production"
    cors_origins: str = "http://localhost:18180"
    seed_demo: bool = False
    log_level: str = "INFO"
    adapters_enabled: bool = False
    adapter_write_enabled: bool = False
    supplier_adapter: str = "disabled"
    supplier_gateway_url: str | None = None
    supplier_gateway_token: str | None = None
    supplier_access_key: str | None = None
    supplier_access_key_file: str | None = None
    supplier_cli_command: str | None = None
    supplier_shopkeeper_cli_command: str | None = None
    supplier_product_find_cli_command: str | None = None
    supplier_cli_timeout_seconds: int = 60
    xianyu_adapter: str = "disabled"
    market_collector_url: str | None = None
    market_collector_token: str | None = None
    xianyu_gateway_url: str | None = None
    xianyu_gateway_token: str | None = None
    xianyu_cli_command: str | None = None
    xianyu_cookie_path: str | None = None
    xianyu_cli_timeout_seconds: int = 60
    xianyu_top_app_key: str | None = None
    xianyu_top_app_secret: str | None = None
    xianyu_top_app_secret_file: str | None = None
    xianyu_top_session: str | None = None
    xianyu_top_session_file: str | None = None
    xianyu_top_endpoint: str = "https://eco.taobao.com/router/rest"
    xianyu_webhook_token: str | None = None
    xianyu_webhook_token_file: str | None = None
    xianyu_browser_bridge_token: str | None = None
    xianyu_browser_bridge_token_file: str | None = None
    # Hard gate for final submission through the local Chrome bridge.  Keeping
    # this separate from the official adapter switch allows installations
    # without a Xianyu enterprise API to use the browser executor deliberately.
    xianyu_browser_publish_enabled: bool = False
    # Independent fail-closed gate for unattended browser conversation capture
    # and AI customer-service replies.  It must never inherit the publish gate.
    xianyu_browser_customer_service_enabled: bool = False
    automation_scheduler_enabled: bool = False
    automation_message_interval_seconds: int = 120
    automation_order_interval_seconds: int = 180
    automation_supplier_interval_seconds: int = 900
    automation_publish_interval_seconds: int = 300
    automation_scheduler_batch_size: int = 50
    asset_root: str = "/app/assets"
    login_max_attempts: int = 5
    login_window_seconds: int = 900

    @property
    def cors_origin_list(self):
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]

    @property
    def data_mode(self):
        if self.seed_demo:
            return "DEMO"
        if self.adapters_enabled and self.adapter_write_enabled:
            return "CONTROLLED_WRITE_LIVE"
        if self.adapters_enabled:
            return "READ_ONLY_LIVE"
        return "LIVE_READY"

    def assert_production_safe(self):
        if self.environment.strip().lower() != "production":
            return
        unsafe_fields = []
        jwt_secret = self.jwt_secret.strip()
        admin_password = self.admin_password.strip()
        admin_character_classes = sum(
            (
                any(character.islower() for character in admin_password),
                any(character.isupper() for character in admin_password),
                any(character.isdigit() for character in admin_password),
                any(not character.isalnum() for character in admin_password),
            )
        )
        if (
            len(jwt_secret) < 32
            or jwt_secret == "development-only-change-me-32-chars"
            or jwt_secret.startswith("replace-with-")
        ):
            unsafe_fields.append("AUTOFISH_JWT_SECRET")
        if (
            len(admin_password) < 12
            or admin_password == "change-me-before-production"
            or admin_password.startswith("replace-with-")
            or admin_character_classes < 3
        ):
            unsafe_fields.append("AUTOFISH_ADMIN_PASSWORD")
        encryption_secret = (self.credential_encryption_secret or "").strip()
        if encryption_secret and (
            len(encryption_secret) < 32
            or encryption_secret.startswith("replace-with-")
        ):
            unsafe_fields.append("AUTOFISH_CREDENTIAL_ENCRYPTION_SECRET")
        if (
            "autofish:autofish@localhost" in self.database_url
            or "replace-with-" in self.database_url
        ):
            unsafe_fields.append("AUTOFISH_DATABASE_URL")
        if self.seed_demo:
            unsafe_fields.append("AUTOFISH_SEED_DEMO")
        if unsafe_fields:
            names = ", ".join(unsafe_fields)
            raise RuntimeError(f"unsafe production configuration: {names}")


@lru_cache
def get_settings():
    return Settings()
