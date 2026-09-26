import hashlib
import json
import time
from datetime import UTC, datetime

from adapters.base import AdapterResult, AdapterStatus
from sqlalchemy import select

from app.models import (
    Account,
    AdapterCall,
    AutomationJob,
    Conversation,
    JobType,
    ProductPriceHistory,
    ProductSKU,
    ProductStockHistory,
    ProductSupplierLink,
    SupplierSKU,
)
from app.schemas.xianyu import XianyuWebhookEvent
from app.services.adapter_factory import get_supplier_adapter, get_xianyu_adapter
from app.services.clawhub import execute_action, preview_action
from app.services.customer_service import generate_reply_suggestion, send_suggestion
from app.services.pricing import PricingInput, calculate_pricing
from app.services.xianyu_events import ingest_xianyu_event


class RuntimeAdapterError(RuntimeError):
    def __init__(self, result: AdapterResult):
        super().__init__(result.safe_message or result.error_code or result.status.value)
        self.result = result


def _adapter_call(db, job: AutomationJob, operation: str, result, started: float):
    db.add(
        AdapterCall(
            automation_job_id=job.id,
            correlation_id=job.correlation_id,
            adapter=result.source,
            adapter_version=result.source_version,
            operation=operation,
            status=result.status.value,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=result.error_code,
            raw_snapshot_hash=result.raw_snapshot_hash,
            safe_summary={"retryable": result.retryable},
        )
    )


def _require_ok(result):
    if result.status != AdapterStatus.OK:
        raise RuntimeAdapterError(result)
    return result.data


def _event_id(kind: str, external_id: str, payload: dict) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f"poll:{kind}:{external_id}:{digest}"


def _event(kind, account_external_id, external_id, data):
    return XianyuWebhookEvent(
        event_id=_event_id(kind, external_id, data),
        event_type=kind,
        occurred_at=datetime.now(UTC),
        account_external_id=account_external_id,
        data=data,
    )


async def sync_xianyu_job(db, job: AutomationJob) -> dict:
    adapter = get_xianyu_adapter()
    started = time.monotonic()
    account_result = await adapter.get_account()
    _adapter_call(db, job, "GET_ACCOUNT", account_result, started)
    account = _require_ok(account_result)
    account_data = {
        "nickname": account.nickname,
        "status": account.status,
    }
    ingest_xianyu_event(
        db,
        event=_event(
            "ACCOUNT_UPSERT",
            account.external_account_id,
            account.external_account_id,
            account_data,
        ),
        correlation_id=job.correlation_id,
    )

    if job.type == JobType.SYNC_ORDERS:
        started = time.monotonic()
        result = await adapter.get_orders()
        _adapter_call(db, job, "GET_ORDERS", result, started)
        if result.status == AdapterStatus.UNSUPPORTED:
            db.commit()
            return {"orders_supported": False, "orders": 0}
        orders = _require_ok(result) or []
        for item in orders:
            data = {
                "external_order_id": item.external_order_id,
                "status": item.status,
                "amount": item.amount.amount if item.amount else "0",
                "paid_at": item.paid_at,
                "completed_at": item.completed_at,
            }
            ingest_xianyu_event(
                db,
                event=_event(
                    "ORDER_UPSERT",
                    account.external_account_id,
                    item.external_order_id,
                    data,
                ),
                correlation_id=job.correlation_id,
            )
        return {"orders_supported": True, "orders": len(orders)}

    started = time.monotonic()
    chat_result = await adapter.list_chats()
    _adapter_call(db, job, "LIST_CHATS", chat_result, started)
    chats = _require_ok(chat_result) or []
    message_count = 0
    suggestion_count = 0
    automatic_replies = 0
    for chat in chats[:50]:
        data = {
            "external_conversation_id": chat.external_conversation_id,
            "customer_id_masked": chat.customer_id_masked,
            "customer_name_masked": chat.customer_name_masked,
            "external_product_id": chat.external_product_id,
            "last_message_at": chat.last_message_at,
        }
        ingest_xianyu_event(
            db,
            event=_event(
                "CONVERSATION_UPSERT",
                account.external_account_id,
                chat.external_conversation_id,
                data,
            ),
            correlation_id=job.correlation_id,
        )
        started = time.monotonic()
        result = await adapter.list_messages(chat.external_conversation_id)
        _adapter_call(db, job, "LIST_MESSAGES", result, started)
        messages = _require_ok(result) or []
        for message in messages:
            message_data = {
                "external_conversation_id": message.external_conversation_id,
                "external_message_id": message.external_message_id,
                "customer_id_masked": chat.customer_id_masked,
                "direction": message.direction,
                "role": message.role,
                "content": message.content,
                "content_type": message.content_type,
                "sent_at": message.sent_at,
            }
            ingest_xianyu_event(
                db,
                event=_event(
                    "MESSAGE_UPSERT",
                    account.external_account_id,
                    message.external_message_id,
                    message_data,
                ),
                correlation_id=job.correlation_id,
            )
        message_count += len(messages)
        account_row = db.scalar(
            select(Account).where(
                Account.platform == "XIANYU",
                Account.external_account_id == account.external_account_id,
            )
        )
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.account_id == account_row.id,
                Conversation.external_conversation_id == chat.external_conversation_id,
            )
        )
        try:
            suggestion = generate_reply_suggestion(db, conversation.id)
        except ValueError:
            continue
        suggestion_count += 1
        if suggestion.decision.get("auto_eligible"):
            action = await send_suggestion(
                db,
                suggestion=suggestion,
                user_id=int(job.payload.get("_requested_by_user_id") or 1),
                correlation_id=job.correlation_id,
                confirm=False,
            )
            automatic_replies += int(action.status == "SUCCEEDED")
    return {
        "conversations": len(chats[:50]),
        "messages": message_count,
        "suggestions": suggestion_count,
        "automatic_replies": automatic_replies,
    }


def _supplier_link(db, product_sku_id: int) -> tuple[ProductSKU, SupplierSKU]:
    product_sku = db.get(ProductSKU, product_sku_id)
    if product_sku is None:
        raise ValueError("商品 SKU 不存在")
    link = db.scalar(
        select(ProductSupplierLink)
        .where(
            ProductSupplierLink.product_sku_id == product_sku_id,
            ProductSupplierLink.is_enabled.is_(True),
        )
        .order_by(ProductSupplierLink.priority, ProductSupplierLink.id)
    )
    if link is None:
        raise ValueError("商品 SKU 没有启用的供应商链接")
    supplier_sku = db.get(SupplierSKU, link.supplier_sku_id)
    return product_sku, supplier_sku


async def _preview_repricing_action(db, job, product_sku, action_type, payload):
    payload_digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:16]
    action = preview_action(
        db,
        action_type=action_type,
        target_type="PRODUCT",
        target_id=product_sku.product_id,
        payload=payload,
        idempotency_key=(f"auto:product:{product_sku.product_id}:{action_type}:{payload_digest}"),
        user_id=int(job.payload.get("_requested_by_user_id") or 1),
        correlation_id=job.correlation_id,
    )
    if action.status == "PREVIEWED" and not action.requires_confirmation:
        action = await execute_action(
            db,
            action_id=action.id,
            confirm=False,
            user_id=int(job.payload.get("_requested_by_user_id") or 1),
            correlation_id=job.correlation_id,
        )
    return {"platform_action_id": action.id, "platform_action_status": action.status}


async def sync_supplier_job(db, job: AutomationJob) -> dict:
    product_sku_id = int(job.payload.get("product_sku_id") or 0)
    if product_sku_id <= 0:
        raise ValueError("供应链同步任务缺少 product_sku_id")
    product_sku, supplier_sku = _supplier_link(db, product_sku_id)
    adapter = get_supplier_adapter()
    now = datetime.now(UTC)

    if job.type == JobType.SYNC_SUPPLIER_PRICE:
        started = time.monotonic()
        result = await adapter.get_price(supplier_sku.external_sku_id)
        _adapter_call(db, job, "GET_PRICE", result, started)
        money = _require_ok(result)
        new_price = type(product_sku.supplier_cost)(money.amount)
        supplier_sku.unit_price = new_price
        supplier_sku.last_checked_at = now
        pricing = calculate_pricing(
            PricingInput(
                supplier_cost=new_price,
                supplier_shipping=product_sku.supplier_shipping,
                platform_fee=product_sku.platform_fee,
                after_sales_reserve=product_sku.after_sales_reserve,
                minimum_profit=product_sku.minimum_profit,
                target_profit=product_sku.target_profit,
                negotiation_margin=product_sku.negotiation_margin,
            )
        )
        product_sku.supplier_cost = new_price
        product_sku.final_cost = pricing.final_cost
        product_sku.minimum_sale_price = pricing.minimum_sale_price
        product_sku.recommended_price = pricing.recommended_price
        product_sku.expected_profit = pricing.expected_profit
        product_sku.last_checked_at = now
        db.add(
            ProductPriceHistory(
                product_sku_id=product_sku.id,
                supplier_sku_id=supplier_sku.id,
                supplier_cost=new_price,
                shipping_cost=product_sku.supplier_shipping,
                recommended_price=pricing.recommended_price,
                source=result.source,
                snapshot_hash=result.raw_snapshot_hash,
            )
        )
        db.commit()
        summary = {
            "product_sku_id": product_sku.id,
            "supplier_cost": str(new_price),
            "minimum_sale_price": str(pricing.minimum_sale_price),
            "recommended_price": str(pricing.recommended_price),
        }
        if product_sku.current_sale_price != pricing.recommended_price:
            summary.update(
                await _preview_repricing_action(
                    db,
                    job,
                    product_sku,
                    "UPDATE_PRICE",
                    {"price": str(pricing.recommended_price)},
                )
            )
        return summary

    started = time.monotonic()
    result = await adapter.get_stock(supplier_sku.external_sku_id)
    _adapter_call(db, job, "GET_STOCK", result, started)
    stock = _require_ok(result)
    supplier_sku.stock = stock.stock
    supplier_sku.stock_status = stock.status
    supplier_sku.last_checked_at = now
    product_sku.stock = stock.stock
    product_sku.last_checked_at = now
    db.add(
        ProductStockHistory(
            product_sku_id=product_sku.id,
            supplier_sku_id=supplier_sku.id,
            stock=stock.stock,
            stock_status=stock.status,
            source=result.source,
            snapshot_hash=result.raw_snapshot_hash,
        )
    )
    db.commit()
    summary = {"product_sku_id": product_sku.id, "stock": stock.stock, "status": stock.status}
    if stock.stock <= 0:
        summary.update(
            await _preview_repricing_action(
                db,
                job,
                product_sku,
                "REMOVE_LISTING",
                {},
            )
        )
    return summary


async def execute_platform_job(db, job: AutomationJob):
    mappings = {
        JobType.PUBLISH_PRODUCT: (
            "PUBLISH_XIANYU",
            str(job.payload.get("target_type") or "XIANYU_DRAFT"),
            int(job.payload.get("target_id") or job.payload.get("draft_id") or 0),
        ),
        JobType.REPLY_MESSAGE: (
            "SEND_REPLY",
            "CONVERSATION",
            int(job.payload.get("conversation_id") or 0),
        ),
        JobType.CREATE_PURCHASE: (
            "CREATE_PURCHASE",
            "ORDER",
            int(job.payload.get("order_id") or 0),
        ),
        JobType.SYNC_LOGISTICS: (
            "FILL_TRACKING",
            "ORDER",
            int(job.payload.get("order_id") or 0),
        ),
    }
    action_type, target_type, target_id = mappings[job.type]
    if target_id <= 0:
        raise ValueError(f"{job.type.value} 缺少动作目标 ID")
    public_payload = {
        key: value for key, value in job.payload.items() if not str(key).startswith("_")
    }
    action = preview_action(
        db,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        payload=public_payload,
        idempotency_key=f"job:{job.idempotency_key}",
        user_id=int(job.payload.get("_requested_by_user_id") or 1),
        correlation_id=job.correlation_id,
    )
    if action.status == "PREVIEWED" and not action.requires_confirmation:
        action = await execute_action(
            db,
            action_id=action.id,
            confirm=False,
            user_id=int(job.payload.get("_requested_by_user_id") or 1),
            correlation_id=job.correlation_id,
        )
    return action
