import hashlib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.models import (
    Account,
    Conversation,
    Customer,
    ExternalSnapshot,
    Message,
    Order,
    OrderStatus,
    User,
    XianyuProduct,
)
from app.services.audit import write_audit
from app.services.integrations import (
    finish_sync_run,
    sanitize_payload,
    save_snapshot,
    snapshot_hash,
    start_sync_run,
)

ADAPTER_NAME = "xianyu-normalized-webhook"
ADAPTER_VERSION = "1.0"

ORDER_STATUS_MAP = {
    "new": OrderStatus.NEW,
    "待付款": OrderStatus.NEW,
    "paid": OrderStatus.PAID,
    "已付款": OrderStatus.PAID,
    "purchased": OrderStatus.PURCHASED,
    "supplier_shipped": OrderStatus.SUPPLIER_SHIPPED,
    "shipped": OrderStatus.XIANYU_SHIPPED,
    "已发货": OrderStatus.XIANYU_SHIPPED,
    "delivered": OrderStatus.DELIVERED,
    "已签收": OrderStatus.DELIVERED,
    "completed": OrderStatus.COMPLETED,
    "已完成": OrderStatus.COMPLETED,
    "refund": OrderStatus.REFUND_PENDING,
    "退款中": OrderStatus.REFUND_PENDING,
    "dispute": OrderStatus.DISPUTE,
    "纠纷": OrderStatus.DISPUTE,
}


def _decimal(value, *, default="0") -> Decimal:
    try:
        result = Decimal(str(value if value is not None else default))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("事件金额格式无效") from exc
    if result < 0 or result > Decimal("99999999.99"):
        raise ValueError("事件金额超出允许范围")
    return result


def _parse_datetime(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            return datetime.fromtimestamp(number, tz=UTC)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, TypeError, OSError):
        return None


def _order_status(value) -> OrderStatus:
    text = str(value or "").strip()
    try:
        return OrderStatus(text.upper())
    except ValueError:
        return ORDER_STATUS_MAP.get(text.casefold(), OrderStatus.MANUAL_REQUIRED)


def _account(db, external_id: str, data: dict) -> Account:
    account = db.scalar(
        select(Account).where(
            Account.platform == "XIANYU",
            Account.external_account_id == external_id,
        )
    )
    if account is None:
        owner = db.scalar(
            select(User).where(User.role == "admin", User.is_active.is_(True)).order_by(User.id)
        )
        account = Account(
            platform="XIANYU",
            external_account_id=external_id,
            nickname=str(data.get("nickname") or "闲鱼授权账号")[:160],
            owner_user_id=owner.id if owner else None,
        )
        db.add(account)
        db.flush()
    account.nickname = str(data.get("nickname") or account.nickname)[:160]
    account_status = data.get("account_status")
    if "nickname" in data:
        account_status = account_status or data.get("status")
    account.status = str(account_status or account.status or "CONNECTED")[:40]
    account.is_enabled = True
    account.last_synced_at = datetime.now(UTC)
    return account


def _fallback_customer_id(external_conversation_id: str) -> str:
    digest = hashlib.sha256(external_conversation_id.encode()).hexdigest()[:24]
    return f"unknown-{digest}"


def _conversation(db, account: Account, data: dict) -> Conversation:
    external_id = str(data["external_conversation_id"])[:160]
    customer_external_id = str(
        data.get("customer_id_masked") or _fallback_customer_id(external_id)
    )[:160]
    customer = db.scalar(
        select(Customer).where(
            Customer.platform == "XIANYU",
            Customer.external_customer_id == customer_external_id,
        )
    )
    if customer is None:
        customer = Customer(
            platform="XIANYU",
            external_customer_id=customer_external_id,
            display_name_masked=str(data.get("customer_name_masked") or "")[:160] or None,
        )
        db.add(customer)
        db.flush()
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.account_id == account.id,
            Conversation.external_conversation_id == external_id,
        )
    )
    if conversation is None:
        conversation = Conversation(
            account_id=account.id,
            customer_id=customer.id,
            external_conversation_id=external_id,
        )
        db.add(conversation)
        db.flush()
    external_product_id = str(data.get("external_product_id") or "")
    if external_product_id:
        product = db.scalar(
            select(XianyuProduct).where(
                XianyuProduct.account_id == account.id,
                XianyuProduct.external_product_id == external_product_id,
            )
        )
        conversation.product_id = product.product_id if product else None
    conversation.status = str(data.get("conversation_status") or "OPEN")[:40]
    conversation.last_message_at = _parse_datetime(data.get("last_message_at"))
    return conversation


def ingest_xianyu_event(db, *, event, correlation_id: str) -> dict:
    existing = db.scalar(
        select(ExternalSnapshot).where(
            ExternalSnapshot.platform == "XIANYU",
            ExternalSnapshot.object_type == "GATEWAY_EVENT",
            ExternalSnapshot.external_id == event.event_id,
        )
    )
    if existing:
        return {"accepted": True, "duplicate": True, "event_id": event.event_id}

    normalized = event.model_dump(mode="json")
    run = start_sync_run(
        db,
        platform="XIANYU",
        adapter_name=ADAPTER_NAME,
        adapter_version=ADAPTER_VERSION,
        operation=event.event_type,
        requested_by_user_id=None,
        correlation_id=correlation_id,
    )
    save_snapshot(
        db,
        run=run,
        platform="XIANYU",
        object_type="GATEWAY_EVENT",
        external_id=event.event_id,
        adapter_name=ADAPTER_NAME,
        adapter_version=ADAPTER_VERSION,
        normalized_data=sanitize_payload(normalized),
        raw_payload=normalized,
    )
    data = event.data
    account = _account(db, event.account_external_id, data)
    now = event.occurred_at

    if event.event_type == "ACCOUNT_UPSERT":
        account.raw_snapshot = sanitize_payload(data)
        account.snapshot_hash = snapshot_hash(account.raw_snapshot)
        account.last_synced_at = now
    elif event.event_type == "PRODUCT_UPSERT":
        external_id = str(data["external_product_id"])[:160]
        row = db.scalar(
            select(XianyuProduct).where(
                XianyuProduct.account_id == account.id,
                XianyuProduct.external_product_id == external_id,
            )
        )
        if row is None:
            row = XianyuProduct(account_id=account.id, external_product_id=external_id)
            db.add(row)
        row.published_title = str(data["title"])[:500]
        row.published_price = _decimal(data["price"])
        row.status = str(data["status"])[:40]
        row.raw_snapshot = sanitize_payload(data)
        row.snapshot_hash = snapshot_hash(row.raw_snapshot)
        row.last_synced_at = now
    elif event.event_type == "CONVERSATION_UPSERT":
        conversation = _conversation(db, account, data)
        conversation.last_message_at = _parse_datetime(data.get("last_message_at")) or now
    elif event.event_type == "MESSAGE_UPSERT":
        conversation = _conversation(db, account, data)
        external_message_id = str(data["external_message_id"])[:160]
        exists = db.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.external_message_id == external_message_id,
            )
        )
        if exists is None:
            db.add(
                Message(
                    conversation_id=conversation.id,
                    external_message_id=external_message_id,
                    direction=str(data["direction"]).upper()[:20],
                    role=str(data["role"]).upper()[:20],
                    content=str(data["content"])[:10_000],
                    content_type=str(data.get("content_type") or "TEXT")[:40],
                    metadata_json={"event_id": event.event_id, "adapter": ADAPTER_NAME},
                    created_at=_parse_datetime(data.get("sent_at")) or now,
                )
            )
        conversation.last_message_at = _parse_datetime(data.get("sent_at")) or now
    elif event.event_type == "ORDER_UPSERT":
        external_order_id = str(data["external_order_id"])[:160]
        order = db.scalar(select(Order).where(Order.external_order_id == external_order_id))
        if order is None:
            order = Order(account_id=account.id, external_order_id=external_order_id)
            db.add(order)
        order.status = _order_status(data["status"])
        order.revenue = _decimal(data.get("amount"))
        order.paid_at = _parse_datetime(data.get("paid_at"))
        order.completed_at = _parse_datetime(data.get("completed_at"))
        order.raw_snapshot = sanitize_payload(data)
        order.snapshot_hash = snapshot_hash(order.raw_snapshot)
        order.last_synced_at = now

    finish_sync_run(run, status="SUCCEEDED", items_seen=1, items_written=1)
    write_audit(
        db,
        action="XIANYU_GATEWAY_EVENT_INGESTED",
        entity_type="GATEWAY_EVENT",
        entity_id=event.event_id,
        actor_type="SYSTEM",
        after_data={"event_type": event.event_type, "account_id": account.id},
        correlation_id=correlation_id,
    )
    db.commit()
    return {"accepted": True, "duplicate": False, "event_id": event.event_id}
