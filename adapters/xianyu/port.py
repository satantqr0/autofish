from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, Field

from adapters.base import AdapterHealth, AdapterResult, Money


class XianyuAccountSnapshot(BaseModel):
    external_account_id: str
    nickname: str
    status: str
    raw_payload: dict = Field(default_factory=dict)


class XianyuProductSnapshot(BaseModel):
    external_product_id: str
    title: str
    price: Money
    status: str
    image_urls: list[str] = Field(default_factory=list)
    raw_payload: dict = Field(default_factory=dict)


class PublishRequest(BaseModel):
    internal_product_id: int
    title: str
    description: str
    price: Money
    image_refs: list[str]
    idempotency_key: str
    original_price: Money | None = None
    delivery: str = "无需邮寄"
    post_price: Money | None = None
    can_self_pickup: bool = True
    platform_payload: dict = Field(default_factory=dict)


class UpdateProductRequest(BaseModel):
    external_product_id: str
    fields: dict
    idempotency_key: str
    platform_payload: dict = Field(default_factory=dict)


class MessageEvent(BaseModel):
    event: str
    external_conversation_id: str
    external_message_id: str | None = None
    payload: dict = Field(default_factory=dict)


class ChatSnapshot(BaseModel):
    external_conversation_id: str
    customer_id_masked: str
    unread: int = 0
    customer_name_masked: str | None = None
    last_message: str | None = None
    last_message_at: str | None = None
    external_product_id: str | None = None
    raw_payload: dict = Field(default_factory=dict)


class ConversationMessageSnapshot(BaseModel):
    external_message_id: str
    external_conversation_id: str
    direction: str
    role: str
    content: str
    content_type: str = "TEXT"
    sent_at: str | None = None
    raw_payload: dict = Field(default_factory=dict)


class SendMessageRequest(BaseModel):
    external_conversation_id: str
    recipient_id: str
    text: str
    idempotency_key: str


class MessageSnapshot(BaseModel):
    external_message_id: str
    status: str


class OrderSnapshot(BaseModel):
    external_order_id: str
    status: str
    amount: Money | None = None
    buyer_id_masked: str | None = None
    paid_at: str | None = None
    completed_at: str | None = None
    raw_payload: dict = Field(default_factory=dict)


class ShipOrderRequest(BaseModel):
    external_order_id: str
    carrier: str
    tracking_number_ref: str
    idempotency_key: str
    logistics_code: str | None = None
    sender_name: str | None = None
    sender_phone: str | None = None
    sender_address: str | None = None
    sender_division_id: int | None = None
    platform_payload: dict = Field(default_factory=dict)


class XianyuAdapter(Protocol):
    async def search_market(self, query: str) -> AdapterResult[list[dict]]: ...
    async def health(self) -> AdapterResult[AdapterHealth]: ...
    async def get_account(self) -> AdapterResult[XianyuAccountSnapshot]: ...
    async def get_product(
        self, external_product_id: str
    ) -> AdapterResult[XianyuProductSnapshot]: ...
    async def list_products(self) -> AdapterResult[list[XianyuProductSnapshot]]: ...
    async def publish_product(
        self, request: PublishRequest
    ) -> AdapterResult[XianyuProductSnapshot]: ...
    async def update_product(
        self, request: UpdateProductRequest
    ) -> AdapterResult[XianyuProductSnapshot]: ...
    async def pause_product(self, external_product_id: str) -> AdapterResult[None]: ...
    async def delete_product(self, external_product_id: str) -> AdapterResult[None]: ...
    def watch_messages(self) -> AsyncIterator[MessageEvent]: ...
    async def list_chats(self) -> AdapterResult[list[ChatSnapshot]]: ...
    async def list_messages(
        self, external_conversation_id: str
    ) -> AdapterResult[list[ConversationMessageSnapshot]]: ...
    async def send_message(self, request: SendMessageRequest) -> AdapterResult[MessageSnapshot]: ...
    async def get_orders(self) -> AdapterResult[list[OrderSnapshot]]: ...
    async def ship_order(self, request: ShipOrderRequest) -> AdapterResult[OrderSnapshot]: ...
