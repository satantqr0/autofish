import re

from adapters.base import AdapterHealth, AdapterResult, AdapterStatus, Money
from adapters.runner import CliFailure, JsonCliRunner
from adapters.supplier.shopkeeper_1688 import _failure_result
from adapters.xianyu.port import (
    ChatSnapshot,
    ConversationMessageSnapshot,
    MessageSnapshot,
    PublishRequest,
    SendMessageRequest,
    UpdateProductRequest,
    XianyuAccountSnapshot,
    XianyuProductSnapshot,
)

PRICE_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")


def _money(value):
    match = PRICE_PATTERN.search(str(value or ""))
    return Money(amount=match.group(0) if match else "0")


def _truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "ok", "valid"}
    return bool(value)


class GoofishCliAdapter:
    """Process adapter for a separately installed Apache-2.0 goofish CLI.

    Reads are always available when authenticated. Writes are exposed to the core
    policy engine but are never called merely because this adapter is configured.
    """

    source = "goofish-cli"
    source_version = "external"

    def __init__(self, command=None, *, cookie_path=None, timeout_seconds=60):
        self.runner = JsonCliRunner(
            command,
            timeout_seconds=timeout_seconds,
            environment={
                "GOOFISH_COOKIES_PATH": cookie_path,
                "GOOFISH_NO_CHROME_BOOTSTRAP": "1",
            },
        )
        self.account_id = None

    async def _run(self, *arguments):
        try:
            return await self.runner.run(*arguments, "--format", "json")
        except CliFailure as exc:
            return _failure_result(self.source, self.source_version, exc)

    async def health(self):
        if not self.runner.available:
            return AdapterResult[AdapterHealth](
                status=AdapterStatus.AUTH_REQUIRED,
                data=AdapterHealth(
                    available=False,
                    authenticated=False,
                    read_only=False,
                    details={
                        "capabilities": [
                            "account",
                            "products",
                            "conversations",
                            "messages",
                        ],
                        "orders": False,
                        "writes": ["publish", "remove", "send_message"],
                    },
                ),
                source=self.source,
                source_version=self.source_version,
                error_code="COMMAND_NOT_CONFIGURED",
                safe_message="闲鱼只读 CLI 或授权文件尚未配置",
            )
        payload = await self._run("auth", "status")
        if isinstance(payload, AdapterResult):
            payload.data = AdapterHealth(
                available=True, authenticated=False, read_only=False
            )
            return payload
        authenticated = _truthy(payload.get("valid"))
        return AdapterResult[AdapterHealth](
            status=AdapterStatus.OK if authenticated else AdapterStatus.AUTH_REQUIRED,
            data=AdapterHealth(
                available=True,
                authenticated=authenticated,
                read_only=False,
                details={
                    "capabilities": [
                        "account",
                        "products",
                        "conversations",
                        "messages",
                    ],
                    "orders": False,
                    "writes": ["publish", "remove", "send_message"],
                },
            ),
            source=self.source,
            source_version=self.source_version,
            error_code=None if authenticated else "AUTH_REQUIRED",
            safe_message=None if authenticated else "闲鱼授权无效或已过期",
        )

    async def get_account(self):
        payload = await self._run("auth", "status")
        if isinstance(payload, AdapterResult):
            return payload
        external_id = str(payload.get("unb") or "")
        self.account_id = external_id or None
        if not external_id:
            return AdapterResult(
                status=AdapterStatus.AUTH_REQUIRED,
                source=self.source,
                source_version=self.source_version,
                error_code="AUTH_REQUIRED",
                safe_message="闲鱼授权中缺少账号标识",
            )
        return AdapterResult(
            status=AdapterStatus.OK,
            data=XianyuAccountSnapshot(
                external_account_id=external_id,
                nickname=str(
                    payload.get("nick") or payload.get("tracknick") or "闲鱼账号"
                ),
                status="CONNECTED" if _truthy(payload.get("valid")) else "EXPIRED",
                raw_payload=payload,
            ),
            source=self.source,
            source_version=self.source_version,
        )

    async def search_market(self, query: str):
        payload = await self._run("search", "items", "--query", query, "--limit", "50")
        if isinstance(payload, AdapterResult):
            return payload
        items = payload.get("items")
        if not isinstance(items, list):
            return AdapterResult(
                status=AdapterStatus.PERMANENT_ERROR,
                source=self.source,
                safe_message="市场搜索返回格式不正确",
            )
        return AdapterResult(status=AdapterStatus.OK, data=items, source=self.source)

    async def list_products(self):
        payload = await self._run("item", "list", "--limit", "100")
        if isinstance(payload, AdapterResult):
            return payload
        items = payload.get("items") or []
        products = [
            XianyuProductSnapshot(
                external_product_id=str(item.get("item_id") or item.get("id") or ""),
                title=str(item.get("title") or "未命名闲鱼商品"),
                price=_money(item.get("price")),
                status=str(item.get("status") or "UNKNOWN"),
                image_urls=[item["image_url"]] if item.get("image_url") else [],
                raw_payload=item,
            )
            for item in items
            if item.get("item_id") or item.get("id")
        ]
        return AdapterResult(
            status=AdapterStatus.OK,
            data=products,
            source=self.source,
            source_version=self.source_version,
        )

    async def get_product(self, external_product_id):
        payload = await self._run("item", "get", str(external_product_id))
        if isinstance(payload, AdapterResult):
            return payload
        item = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        return AdapterResult(
            status=AdapterStatus.OK,
            data=XianyuProductSnapshot(
                external_product_id=str(
                    item.get("item_id") or item.get("id") or external_product_id
                ),
                title=str(item.get("title") or "未命名闲鱼商品"),
                price=_money(item.get("price")),
                status=str(item.get("status") or "UNKNOWN"),
                image_urls=item.get("image_urls") or item.get("images") or [],
                raw_payload=item,
            ),
            source=self.source,
            source_version=self.source_version,
        )

    async def list_chats(self):
        payload = await self._run("message", "list-chats", "--fetch-num", "100")
        if isinstance(payload, AdapterResult):
            return payload
        sessions = payload.get("sessions") or payload.get("items") or []
        chats = [
            ChatSnapshot(
                external_conversation_id=str(item.get("session_id") or ""),
                customer_id_masked=str(item.get("peer_user_id") or "unknown"),
                customer_name_masked=item.get("peer_nick") or None,
                unread=int(item.get("unread") or 0),
                last_message=item.get("last_msg") or None,
                last_message_at=str(item.get("ts") or "") or None,
                external_product_id=str(item.get("item_id") or "") or None,
                raw_payload=item,
            )
            for item in sessions
            if item.get("session_id")
        ]
        return AdapterResult(
            status=AdapterStatus.OK,
            data=chats,
            source=self.source,
            source_version=self.source_version,
        )

    async def list_messages(self, external_conversation_id):
        payload = await self._run("message", "history", str(external_conversation_id))
        if isinstance(payload, AdapterResult):
            return payload
        rows = payload.get("messages") or payload.get("items") or []
        messages = []
        for index, item in enumerate(rows):
            sender = str(item.get("send_user_id") or item.get("sender_id") or "")
            outbound = bool(self.account_id and sender == self.account_id)
            messages.append(
                ConversationMessageSnapshot(
                    external_message_id=str(
                        item.get("msg_id")
                        or item.get("message_id")
                        or f"{external_conversation_id}:{index}"
                    ),
                    external_conversation_id=str(external_conversation_id),
                    direction="OUTBOUND" if outbound else "INBOUND",
                    role="SELLER" if outbound else "BUYER",
                    content=str(item.get("message") or item.get("send_message") or ""),
                    content_type=str(item.get("content_type") or "TEXT"),
                    sent_at=str(item.get("ts") or item.get("timestamp") or "") or None,
                    raw_payload=item,
                )
            )
        return AdapterResult(
            status=AdapterStatus.OK,
            data=messages,
            source=self.source,
            source_version=self.source_version,
        )

    async def get_orders(self):
        return AdapterResult(
            status=AdapterStatus.UNSUPPORTED,
            source=self.source,
            source_version=self.source_version,
            error_code="ORDER_API_UNAVAILABLE",
            safe_message="当前 goofish CLI 未提供订单读取能力",
        )

    async def publish_product(self, request: PublishRequest):
        remote_images = [ref for ref in request.image_refs if "://" in ref]
        if remote_images:
            return AdapterResult(
                status=AdapterStatus.MANUAL_REQUIRED,
                source=self.source,
                source_version=self.source_version,
                error_code="REMOTE_IMAGES_REQUIRE_GATEWAY",
                safe_message="兼容 CLI 发布需要已安全落盘的本地图片，请改用隔离 Gateway 暂存图片",
            )
        arguments = [
            "item",
            "publish",
            "--title",
            request.title,
            "--desc",
            request.description,
            "--images",
            ",".join(request.image_refs),
            "--price",
            request.price.amount,
            "--delivery",
            request.delivery,
        ]
        if request.original_price:
            arguments.extend(["--original-price", request.original_price.amount])
        if request.post_price:
            arguments.extend(["--post-price", request.post_price.amount])
        if not request.can_self_pickup:
            arguments.extend(["--no-can-self-pickup"])
        payload = await self._run(*arguments)
        if isinstance(payload, AdapterResult):
            return payload
        if not _truthy(payload.get("ok")):
            return AdapterResult(
                status=AdapterStatus.PERMANENT_ERROR,
                source=self.source,
                source_version=self.source_version,
                error_code="PUBLISH_REJECTED",
                safe_message="闲鱼未确认商品发布成功",
            )
        external_product_id = str(payload.get("item_id") or "")
        if not external_product_id:
            return AdapterResult(
                status=AdapterStatus.MANUAL_REQUIRED,
                source=self.source,
                source_version=self.source_version,
                error_code="PUBLISH_UNCONFIRMED",
                safe_message="闲鱼响应未返回商品 ID，禁止自动重试以避免重复上架",
            )
        return AdapterResult(
            status=AdapterStatus.OK,
            data=XianyuProductSnapshot(
                external_product_id=external_product_id,
                title=request.title,
                price=request.price,
                status="ACTIVE",
                image_urls=[],
                raw_payload=payload,
            ),
            source=self.source,
            source_version=self.source_version,
        )

    async def update_product(self, _request: UpdateProductRequest):
        return AdapterResult(
            status=AdapterStatus.UNSUPPORTED,
            source=self.source,
            source_version=self.source_version,
            error_code="UPDATE_API_UNAVAILABLE",
            safe_message="当前兼容 CLI 尚未提供商品编辑或改价能力",
        )

    async def pause_product(self, external_product_id):
        return await self.delete_product(external_product_id)

    async def delete_product(self, external_product_id):
        payload = await self._run("item", "delete", str(external_product_id))
        if isinstance(payload, AdapterResult):
            return payload
        if not _truthy(payload.get("ok")):
            return AdapterResult(
                status=AdapterStatus.PERMANENT_ERROR,
                source=self.source,
                source_version=self.source_version,
                error_code="REMOVE_REJECTED",
                safe_message=str(payload.get("message") or "闲鱼未确认商品下架成功"),
            )
        return AdapterResult(
            status=AdapterStatus.OK,
            data=None,
            source=self.source,
            source_version=self.source_version,
        )

    async def send_message(self, request: SendMessageRequest):
        payload = await self._run(
            "message",
            "send",
            request.external_conversation_id,
            request.recipient_id,
            "--text",
            request.text,
        )
        if isinstance(payload, AdapterResult):
            return payload
        if not _truthy(payload.get("ok")):
            return AdapterResult(
                status=AdapterStatus.PERMANENT_ERROR,
                source=self.source,
                source_version=self.source_version,
                error_code="MESSAGE_REJECTED",
                safe_message="闲鱼未确认消息发送成功",
            )
        return AdapterResult(
            status=AdapterStatus.OK,
            data=MessageSnapshot(
                external_message_id=str(payload.get("mid") or request.idempotency_key),
                status="SENT",
            ),
            source=self.source,
            source_version=self.source_version,
        )

    async def ship_order(self, _request):
        return AdapterResult(
            status=AdapterStatus.UNSUPPORTED,
            source=self.source,
            source_version=self.source_version,
            error_code="ORDER_API_UNAVAILABLE",
            safe_message="当前兼容 CLI 尚未提供订单发货能力",
        )

    async def watch_messages(self):
        if False:
            yield None
        raise NotImplementedError("message watch 应由隔离 Gateway 的常驻进程提供")
