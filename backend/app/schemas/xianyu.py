import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PRODUCT_ID_PATTERN = re.compile(r"^[0-9]{6,20}$")
ALLOWED_HOSTS = {"goofish.com", "www.goofish.com"}
SELLER_WORKBENCH_HOST = "seller.goofish.com"
BRIDGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,80}$")

SellerModule = Literal[
    "消息",
    "数据总览",
    "商品数据",
    "商品发布",
    "商品管理",
    "订单管理",
    "退款管理",
    "评价管理",
    "退货地址",
]
BridgeOperation = Literal[
    "OPEN_MODULE",
    "CAPTURE_OVERVIEW",
    "CAPTURE_PRODUCT_METRICS",
    "CAPTURE_CONVERSATIONS",
    "PREFILL_PRODUCT",
    "PUBLISH_PRODUCT",
    "SEND_REPLY",
]
BridgeExecutionPhase = Literal[
    "PREPARING",
    "READY_TO_SUBMIT",
    "SUBMITTING",
    "SUBMITTED",
]


class ManualXianyuProductSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_product_id: str = Field(min_length=6, max_length=20)
    title: str = Field(min_length=2, max_length=500)
    price: Decimal = Field(ge=0, le=Decimal("99999999.99"), max_digits=10, decimal_places=2)
    status: Literal["ACTIVE", "SOLD", "PAUSED", "REMOVED"] = "ACTIVE"
    source_url: str = Field(min_length=20, max_length=1000)

    @field_validator("external_product_id")
    @classmethod
    def validate_product_id(cls, value):
        value = value.strip()
        if not PRODUCT_ID_PATTERN.fullmatch(value):
            raise ValueError("闲鱼商品 ID 必须为 6–20 位数字")
        return value

    @field_validator("title")
    @classmethod
    def validate_title(cls, value):
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("商品标题包含无效控制字符")
        return value

    @model_validator(mode="after")
    def validate_source_url(self):
        parsed = urlparse(self.source_url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError("商品链接必须来自闲鱼 HTTPS 页面")
        if parsed.path.rstrip("/") != "/item":
            raise ValueError("商品链接必须指向闲鱼商品详情页")
        query_id = (parse_qs(parsed.query).get("id") or [None])[0]
        if query_id != self.external_product_id:
            raise ValueError("商品链接中的 ID 与 external_product_id 不一致")
        return self


class ManualXianyuSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str = Field(min_length=1, max_length=160)
    source_url: str = Field(min_length=20, max_length=1000)
    captured_at: datetime
    declared_active_count: int | None = Field(default=None, ge=0, le=100_000)
    declared_sold_count: int | None = Field(default=None, ge=0, le=1_000_000)
    snapshot_complete: bool = False
    products: list[ManualXianyuProductSnapshot] = Field(min_length=1, max_length=100)

    @field_validator("nickname")
    @classmethod
    def validate_nickname(cls, value):
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("闲鱼昵称包含无效控制字符")
        return value

    @field_validator("source_url")
    @classmethod
    def validate_profile_url(cls, value):
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_HOSTS
            or parsed.path.rstrip("/") != "/personal"
        ):
            raise ValueError("快照来源必须是闲鱼个人页")
        return value

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value):
        if value.tzinfo is None:
            raise ValueError("captured_at 必须包含时区")
        now = datetime.now(UTC)
        normalized = value.astimezone(UTC)
        if normalized > now + timedelta(minutes=5):
            raise ValueError("captured_at 不能来自未来")
        if normalized < now - timedelta(hours=24):
            raise ValueError("网页快照超过 24 小时，请重新导出")
        return normalized

    @model_validator(mode="after")
    def validate_products(self):
        product_ids = [item.external_product_id for item in self.products]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("网页快照包含重复商品 ID")
        active_count = sum(item.status == "ACTIVE" for item in self.products)
        if self.declared_active_count is not None and active_count > self.declared_active_count:
            raise ValueError("快照中的在售商品数超过页面声明数量")
        if (
            self.snapshot_complete
            and self.declared_active_count is not None
            and active_count != self.declared_active_count
        ):
            raise ValueError("完整快照的在售商品数与页面声明数量不一致")
        return self


class ManualXianyuSnapshotResult(BaseModel):
    sync_run_id: int
    account_id: int
    products_seen: int
    products_created: int
    products_updated: int
    products_removed: int
    snapshots_written: int
    snapshot_complete: bool


class ManualXianyuPublicationRequest(ManualXianyuProductSnapshot):
    """Verified result of a browser-assisted manual publication."""

    product_id: int = Field(gt=0)
    product_sku_id: int = Field(gt=0)
    draft_id: int | None = Field(default=None, gt=0)
    nickname: str = Field(min_length=1, max_length=160)
    captured_at: datetime
    status: Literal["ACTIVE", "PAUSED"] = "ACTIVE"

    @field_validator("nickname")
    @classmethod
    def validate_publication_nickname(cls, value):
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("闲鱼昵称包含无效控制字符")
        return value

    @field_validator("captured_at")
    @classmethod
    def validate_publication_captured_at(cls, value):
        if value.tzinfo is None:
            raise ValueError("captured_at 必须包含时区")
        now = datetime.now(UTC)
        normalized = value.astimezone(UTC)
        if normalized > now + timedelta(minutes=5):
            raise ValueError("captured_at 不能来自未来")
        if normalized < now - timedelta(hours=24):
            raise ValueError("人工发布结果超过 24 小时，请重新核验")
        return normalized


class ManualXianyuPublicationResult(BaseModel):
    account_id: int
    product_id: int
    product_sku_id: int
    xianyu_product_id: int
    external_product_id: str
    status: str
    created: bool
    idempotent: bool
    draft_id: int | None = None
    draft_reconciled: bool = False
    browser_action_id: int | None = None
    browser_task_reconciled: bool = False
    launch_id: int | None = None
    launch_reconciled: bool = False


class XianyuWebhookEvent(BaseModel):
    """Normalized contract for an authorized TOP/message-service gateway."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=8, max_length=200)
    event_type: Literal[
        "ACCOUNT_UPSERT",
        "PRODUCT_UPSERT",
        "CONVERSATION_UPSERT",
        "MESSAGE_UPSERT",
        "ORDER_UPSERT",
    ]
    occurred_at: datetime
    account_external_id: str = Field(min_length=1, max_length=160)
    data: dict

    @field_validator("event_id", "account_external_id")
    @classmethod
    def validate_identifiers(cls, value):
        normalized = value.strip()
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("事件标识包含无效字符")
        return normalized

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value):
        if value.tzinfo is None:
            raise ValueError("occurred_at 必须包含时区")
        normalized = value.astimezone(UTC)
        now = datetime.now(UTC)
        if normalized > now + timedelta(minutes=5):
            raise ValueError("occurred_at 不能来自未来")
        if normalized < now - timedelta(days=30):
            raise ValueError("拒绝处理超过 30 天的历史事件")
        return normalized

    @model_validator(mode="after")
    def validate_event_data(self):
        required = {
            "ACCOUNT_UPSERT": {"nickname", "status"},
            "PRODUCT_UPSERT": {"external_product_id", "title", "price", "status"},
            "CONVERSATION_UPSERT": {
                "external_conversation_id",
                "customer_id_masked",
            },
            "MESSAGE_UPSERT": {
                "external_conversation_id",
                "external_message_id",
                "direction",
                "role",
                "content",
            },
            "ORDER_UPSERT": {"external_order_id", "status"},
        }[self.event_type]
        missing = sorted(
            field
            for field in required
            if field not in self.data
            or self.data[field] is None
            or (isinstance(self.data[field], str) and not self.data[field].strip())
        )
        if missing:
            raise ValueError(f"{self.event_type} 缺少字段: {', '.join(missing)}")
        for field in (
            "external_product_id",
            "external_conversation_id",
            "external_message_id",
            "external_order_id",
            "customer_id_masked",
        ):
            if field in self.data and len(str(self.data[field])) > 160:
                raise ValueError(f"{field} 超过 160 个字符")
        if self.event_type == "MESSAGE_UPSERT":
            if str(self.data["direction"]).upper() not in {"INBOUND", "OUTBOUND"}:
                raise ValueError("消息 direction 必须为 INBOUND 或 OUTBOUND")
            if str(self.data["role"]).upper() not in {"BUYER", "SELLER", "SYSTEM"}:
                raise ValueError("消息 role 无效")
        encoded = str(self.data)
        if len(encoded) > 100_000:
            raise ValueError("事件数据过大")
        return self


class SuggestionSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestion_id: int = Field(gt=0)
    confirm: bool = True


def _validate_bridge_id(value: str) -> str:
    normalized = value.strip()
    if not BRIDGE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("bridge_id 仅允许字母、数字、点、下划线和连字符")
    return normalized


def _validate_workbench_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != SELLER_WORKBENCH_HOST:
        raise ValueError("浏览器代理只允许上报闲鱼商家工作台地址")
    if parsed.username or parsed.password:
        raise ValueError("工作台地址不得包含认证信息")
    return value


class BrowserBridgeTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: BridgeOperation
    module: SellerModule | None = None
    draft_id: int | None = Field(default=None, gt=0)
    conversation_id: int | None = Field(default=None, gt=0)
    suggestion_id: int | None = Field(default=None, gt=0)
    idempotency_key: str = Field(min_length=12, max_length=180)
    confirm: bool = False

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value):
        normalized = value.strip()
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("幂等键包含无效字符")
        return normalized

    @model_validator(mode="after")
    def validate_operation_fields(self):
        if self.operation == "OPEN_MODULE" and self.module is None:
            raise ValueError("打开工作台模块时必须指定 module")
        if self.operation != "OPEN_MODULE" and self.module is not None:
            raise ValueError("当前操作不接受 module")
        if self.operation in {"PREFILL_PRODUCT", "PUBLISH_PRODUCT"}:
            if self.draft_id is None:
                raise ValueError("商品浏览器任务必须指定 draft_id")
            if not self.confirm:
                raise ValueError("商品浏览器任务必须由操作员明确确认")
        elif self.draft_id is not None:
            raise ValueError("当前操作不接受 draft_id")
        if self.operation == "SEND_REPLY":
            if self.conversation_id is None or self.suggestion_id is None:
                raise ValueError("浏览器回复任务必须指定 conversation_id 和 suggestion_id")
            if not self.confirm:
                raise ValueError("浏览器回复任务必须由操作员明确确认")
        elif self.conversation_id is not None or self.suggestion_id is not None:
            raise ValueError("当前操作不接受会话或回复建议 ID")
        return self


class BrowserBridgeAgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bridge_id: str = Field(min_length=3, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    capabilities: list[BridgeOperation | Literal["CAPTURE_MARKET_PRICES"]] = Field(
        min_length=1, max_length=12
    )
    current_url: str | None = Field(default=None, max_length=1000)
    last_error: str | None = Field(default=None, max_length=500)

    _normalize_bridge_id = field_validator("bridge_id")(_validate_bridge_id)
    _validate_current_url = field_validator("current_url")(_validate_workbench_url)

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("capabilities 不能重复")
        return value


class BrowserBridgeClaimRequest(BrowserBridgeAgentState):
    pass


class BrowserBridgeTaskProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bridge_id: str = Field(min_length=3, max_length=80)
    phase: BridgeExecutionPhase
    current_url: str | None = Field(default=None, max_length=1000)
    form_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    _normalize_bridge_id = field_validator("bridge_id")(_validate_bridge_id)
    _validate_current_url = field_validator("current_url")(_validate_workbench_url)


class BrowserProductMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_product_id: str = Field(pattern=r"^[0-9]{6,20}$")
    listing_title: str = Field(min_length=1, max_length=500)
    listing_price: Decimal = Field(ge=0, le=Decimal("99999999.99"))
    exposures: int = Field(ge=0, le=1_000_000_000)
    exposed_users: int = Field(ge=0, le=1_000_000_000)
    views: int = Field(ge=0, le=1_000_000_000)
    viewers: int = Field(ge=0, le=1_000_000_000)
    inquiries: int = Field(ge=0, le=1_000_000_000)
    paid_users: int = Field(ge=0, le=1_000_000_000)
    paid_orders: int = Field(ge=0, le=1_000_000_000)
    paid_amount: Decimal = Field(ge=0, le=Decimal("999999999999.99"))
    browse_pay_conversion: Decimal | None = Field(default=None, ge=0, le=1)
    refund_requested_users: int = Field(ge=0, le=1_000_000_000)
    refund_requested_orders: int = Field(ge=0, le=1_000_000_000)
    refund_requested_amount: Decimal = Field(ge=0, le=Decimal("999999999999.99"))
    refund_success_users: int = Field(ge=0, le=1_000_000_000)
    refund_success_orders: int = Field(ge=0, le=1_000_000_000)
    refund_success_amount: Decimal = Field(ge=0, le=Decimal("999999999999.99"))

    @model_validator(mode="after")
    def validate_metric_consistency(self):
        if self.exposures > 0 and self.views > self.exposures:
            raise ValueError("浏览次数不能大于曝光次数")
        return self


class BrowserChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_message_id: str = Field(pattern=r"^browser-msg-[a-f0-9]{64}$")
    direction: Literal["INBOUND", "OUTBOUND"]
    role: Literal["BUYER", "SELLER"]
    content: str = Field(min_length=1, max_length=10_000)
    sent_at: datetime
    timestamp_source: Literal["PLATFORM_VISIBLE", "CAPTURE_OBSERVED"]

    @field_validator("content")
    @classmethod
    def validate_content(cls, value):
        normalized = value.strip()
        if not normalized or any(ord(character) < 9 for character in normalized):
            raise ValueError("浏览器消息内容无效")
        return normalized

    @field_validator("sent_at")
    @classmethod
    def validate_sent_at(cls, value):
        if value.tzinfo is None:
            raise ValueError("浏览器消息时间必须包含时区")
        now = datetime.now(UTC)
        normalized = value.astimezone(UTC)
        if normalized > now + timedelta(minutes=5):
            raise ValueError("浏览器消息时间不能来自未来")
        if normalized < now - timedelta(days=3650):
            raise ValueError("浏览器消息时间超出允许范围")
        return normalized


class BrowserConversationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_conversation_id: str = Field(pattern=r"^browser-conv-[a-f0-9]{64}$")
    customer_id_masked: str = Field(pattern=r"^buyer-[a-f0-9]{24}$")
    customer_name_masked: str = Field(min_length=1, max_length=160)
    external_product_id: str | None = Field(default=None, pattern=r"^[0-9]{6,20}$")
    last_message_at: datetime
    messages: list[BrowserChatMessage] = Field(min_length=1, max_length=200)

    @field_validator("customer_name_masked")
    @classmethod
    def validate_customer_name(cls, value):
        normalized = value.strip()
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("买家显示名无效")
        return normalized

    @field_validator("last_message_at")
    @classmethod
    def validate_last_message_at(cls, value):
        if value.tzinfo is None:
            raise ValueError("会话时间必须包含时区")
        return value


class BrowserBridgeTaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bridge_id: str = Field(min_length=3, max_length=80)
    status: Literal["SUCCEEDED", "MANUAL_REQUIRED", "FAILED"]
    current_url: str | None = Field(default=None, max_length=1000)
    page_title: str | None = Field(default=None, max_length=300)
    module: SellerModule | None = None
    modules: list[SellerModule] = Field(default_factory=list, max_length=20)
    filled_fields: list[Literal["title", "description", "price", "category", "images"]] = Field(
        default_factory=list, max_length=10
    )
    missing_fields: list[Literal["title", "description", "price", "category", "images"]] = Field(
        default_factory=list, max_length=10
    )
    risk_detected: bool = False
    submission_attempted: bool = False
    uploaded_images: int = Field(default=0, ge=0, le=9)
    form_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    external_product_id: str | None = Field(default=None, pattern=r"^[0-9]{6,20}$")
    published_url: str | None = Field(default=None, max_length=1000)
    success_evidence: (
        Literal[
            "SUCCESS_TEXT",
            "ITEM_URL",
            "ITEM_URL_AFTER_NAVIGATION",
            "MANAGEMENT_ROW",
            "MESSAGE_APPEARED",
        ]
        | None
    ) = None
    metric_date: date | None = None
    metrics: list[BrowserProductMetric] = Field(default_factory=list, max_length=500)
    metrics_duplicate_rows_skipped: int = Field(default=0, ge=0, le=500)
    metrics_pages: int = Field(default=0, ge=0, le=25)
    captured_at: datetime | None = None
    conversations: list[BrowserConversationSnapshot] = Field(default_factory=list, max_length=50)
    external_conversation_id: str | None = Field(
        default=None, pattern=r"^browser-conv-[a-f0-9]{64}$"
    )
    source_message_id: str | None = Field(default=None, pattern=r"^browser-msg-[a-f0-9]{64}$")
    sent_message_id: str | None = Field(default=None, pattern=r"^browser-msg-[a-f0-9]{64}$")
    reply_sent: bool = False
    manual_reason: str | None = Field(default=None, max_length=500)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,100}$")

    _normalize_bridge_id = field_validator("bridge_id")(_validate_bridge_id)
    _validate_current_url = field_validator("current_url")(_validate_workbench_url)

    @field_validator("published_url")
    @classmethod
    def validate_published_url(cls, value):
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError("发布结果链接必须来自闲鱼 HTTPS 页面")
        return value

    @model_validator(mode="after")
    def validate_result(self):
        if self.risk_detected and self.status != "MANUAL_REQUIRED":
            raise ValueError("检测到风控时必须转人工")
        if self.status == "MANUAL_REQUIRED" and not self.manual_reason:
            raise ValueError("转人工结果必须说明原因")
        if self.status == "FAILED" and not self.error_code:
            raise ValueError("失败结果必须提供 error_code")
        if set(self.filled_fields) & set(self.missing_fields):
            raise ValueError("同一字段不能同时标记为已填写和缺失")
        if bool(self.metric_date) != bool(self.metrics):
            raise ValueError("商品经营数据必须同时包含 metric_date 和 metrics")
        if bool(self.captured_at) != bool(self.conversations):
            raise ValueError("浏览器会话必须同时包含 captured_at 和 conversations")
        if self.reply_sent and (
            not self.submission_attempted
            or self.success_evidence != "MESSAGE_APPEARED"
            or not self.external_conversation_id
            or not self.source_message_id
            or not self.sent_message_id
        ):
            raise ValueError("回复成功结果缺少可验证的会话和消息证据")
        return self
