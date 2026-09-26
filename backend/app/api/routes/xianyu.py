import asyncio
import hmac
from datetime import UTC, date, datetime
from decimal import Decimal

from adapters.base import AdapterStatus
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_operator
from app.core.config import get_settings
from app.core.database import get_db
from app.models import (
    Account,
    AIDecision,
    Conversation,
    Customer,
    Message,
    Order,
    OrderStatus,
    User,
    XianyuProduct,
)
from app.schemas.xianyu import (
    BrowserBridgeAgentState,
    BrowserBridgeClaimRequest,
    BrowserBridgeTaskCreate,
    BrowserBridgeTaskProgress,
    BrowserBridgeTaskResult,
    ManualXianyuPublicationRequest,
    ManualXianyuPublicationResult,
    ManualXianyuSnapshotRequest,
    ManualXianyuSnapshotResult,
    SuggestionSendRequest,
    XianyuWebhookEvent,
)
from app.services.adapter_factory import get_xianyu_adapter, get_xianyu_webhook_token
from app.services.audit import write_audit
from app.services.catalog import mask_identifier
from app.services.clawhub import serialize_action
from app.services.customer_service import (
    generate_reply_suggestion,
    send_suggestion,
    serialize_suggestion,
)
from app.services.integrations import (
    finish_sync_run,
    sanitize_payload,
    save_snapshot,
    snapshot_hash,
    start_sync_run,
)
from app.services.xianyu_browser_bridge import (
    bridge_overview,
    bridge_token_matches,
    cancel_bridge_task,
    claim_bridge_task,
    configured_bridge_token,
    create_bridge_task,
    finish_bridge_task,
    heartbeat_agent,
    record_bridge_progress,
    resolve_bridge_task_asset,
    serialize_bridge_agent,
    serialize_bridge_task,
)
from app.services.xianyu_events import ingest_xianyu_event
from app.services.xianyu_manual import (
    ingest_manual_xianyu_snapshot,
    reconcile_manual_xianyu_publication,
)
from app.services.xianyu_metrics import import_seller_workbench_xlsx, metrics_overview

router = APIRouter(prefix="/xianyu", tags=["xianyu"])

ORDER_STATUS_MAP = {
    "new": OrderStatus.NEW,
    "待付款": OrderStatus.NEW,
    "paid": OrderStatus.PAID,
    "已付款": OrderStatus.PAID,
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


def _account_for_user(db, user):
    return db.scalar(
        select(Account)
        .where(Account.platform == "XIANYU", Account.owner_user_id == user.id)
        .order_by(Account.id)
    )


def _conversation_for_user(db: Session, user: User, conversation_id: int):
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        return None
    account = db.get(Account, conversation.account_id)
    if account is None or account.owner_user_id != user.id:
        return None
    return conversation


def require_browser_bridge(
    token: str | None = Header(default=None, alias="X-AutoFish-Bridge-Token"),
):
    if configured_bridge_token() is None:
        raise HTTPException(status_code=503, detail="浏览器代理令牌尚未配置")
    if not bridge_token_matches(token):
        raise HTTPException(status_code=401, detail="浏览器代理令牌无效")
    return True


@router.get("/browser-bridge")
def browser_bridge_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return bridge_overview(db, user=user)


@router.post("/browser-bridge/tasks", status_code=201)
def browser_bridge_task_create(
    payload: BrowserBridgeTaskCreate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        item = create_bridge_task(
            db,
            payload=payload,
            user=user,
            correlation_id=request.state.correlation_id,
        )
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return serialize_bridge_task(item)


@router.post("/browser-bridge/tasks/{task_id}/cancel")
def browser_bridge_task_cancel(
    task_id: int,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        item = cancel_bridge_task(
            db,
            task_id=task_id,
            user=user,
            correlation_id=request.state.correlation_id,
        )
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_bridge_task(item)


@router.post("/browser-bridge/agent/heartbeat")
def browser_bridge_heartbeat(
    payload: BrowserBridgeAgentState,
    _authorized: bool = Depends(require_browser_bridge),
    db: Session = Depends(get_db),
):
    return serialize_bridge_agent(heartbeat_agent(db, payload))


@router.post("/browser-bridge/agent/claim")
def browser_bridge_claim(
    payload: BrowserBridgeClaimRequest,
    _authorized: bool = Depends(require_browser_bridge),
    db: Session = Depends(get_db),
):
    item = claim_bridge_task(db, payload)
    return {
        "task": serialize_bridge_task(item, include_payload=True) if item else None,
        "poll_after_seconds": 5,
    }


@router.post("/browser-bridge/agent/tasks/{task_id}/result")
def browser_bridge_result(
    task_id: int,
    payload: BrowserBridgeTaskResult,
    request: Request,
    _authorized: bool = Depends(require_browser_bridge),
    db: Session = Depends(get_db),
):
    try:
        item = finish_bridge_task(
            db,
            task_id=task_id,
            payload=payload,
            correlation_id=request.state.correlation_id,
        )
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_bridge_task(item)


@router.post("/browser-bridge/agent/tasks/{task_id}/progress")
def browser_bridge_progress(
    task_id: int,
    payload: BrowserBridgeTaskProgress,
    request: Request,
    _authorized: bool = Depends(require_browser_bridge),
    db: Session = Depends(get_db),
):
    try:
        item = record_bridge_progress(
            db,
            task_id=task_id,
            payload=payload,
            correlation_id=request.state.correlation_id,
        )
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_bridge_task(item)


@router.get("/browser-bridge/agent/tasks/{task_id}/assets/{ordinal}")
def browser_bridge_asset(
    task_id: int,
    ordinal: int,
    bridge_id: str | None = Header(default=None, alias="X-AutoFish-Bridge-Id"),
    _authorized: bool = Depends(require_browser_bridge),
    db: Session = Depends(get_db),
):
    if not bridge_id:
        raise HTTPException(status_code=401, detail="浏览器代理 ID 缺失")
    try:
        path, descriptor = resolve_bridge_task_asset(
            db,
            task_id=task_id,
            bridge_id=bridge_id,
            ordinal=ordinal,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type=descriptor["mime_type"],
        filename=descriptor["filename"],
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-AutoFish-Asset-SHA256": descriptor["sha256"],
        },
    )


@router.post("/webhooks/gateway", status_code=202)
def ingest_gateway_event(
    payload: XianyuWebhookEvent,
    request: Request,
    authorization: str | None = Header(default=None),
    webhook_token_header: str | None = Header(default=None, alias="X-AutoFish-Webhook-Token"),
    db: Session = Depends(get_db),
):
    configured_token = get_xianyu_webhook_token()
    provided_token = webhook_token_header
    if authorization and authorization.casefold().startswith("bearer "):
        provided_token = authorization[7:].strip()
    if not configured_token:
        raise HTTPException(status_code=503, detail="闲鱼 Gateway webhook 尚未配置")
    if not provided_token or not hmac.compare_digest(configured_token, provided_token):
        raise HTTPException(status_code=401, detail="webhook token 无效")
    try:
        return ingest_xianyu_event(
            db,
            event=payload,
            correlation_id=request.state.correlation_id,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/conversations/{conversation_id}")
def conversation_detail(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _conversation_for_user(db, user, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at, Message.id)
        .limit(200)
    ).all()
    suggestions = db.scalars(
        select(AIDecision)
        .where(
            AIDecision.conversation_id == conversation.id,
            AIDecision.agent == "CUSTOMER_ROUTER",
        )
        .order_by(AIDecision.created_at.desc(), AIDecision.id.desc())
        .limit(20)
    ).all()
    return {
        "id": conversation.id,
        "status": conversation.status,
        "manual_mode": conversation.manual_mode,
        "manual_reason": conversation.manual_reason,
        "messages": [
            {
                "id": item.id,
                "direction": item.direction,
                "role": item.role,
                "content": item.content,
                "content_type": item.content_type,
                "sent_by_ai": item.sent_by_ai,
                "created_at": item.created_at,
            }
            for item in messages
        ],
        "suggestions": [serialize_suggestion(item) for item in suggestions],
    }


@router.post("/conversations/{conversation_id}/suggestion")
def create_conversation_suggestion(
    conversation_id: int,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    if _conversation_for_user(db, user, conversation_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        return serialize_suggestion(generate_reply_suggestion(db, conversation_id))
    except (LookupError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/conversations/{conversation_id}/send-suggestion")
async def send_conversation_suggestion(
    conversation_id: int,
    payload: SuggestionSendRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    conversation = _conversation_for_user(db, user, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    suggestion = db.get(AIDecision, payload.suggestion_id)
    if suggestion is None or suggestion.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="回复建议不存在")
    bridge = bridge_overview(db, user=user)
    browser_reply_ready = any(
        agent["status"] == "ONLINE" and "SEND_REPLY" in agent["capabilities"]
        for agent in bridge["agents"]
    )
    browser_conversation = conversation.external_conversation_id.startswith("browser-conv-")
    if browser_reply_ready and browser_conversation:
        try:
            task = create_bridge_task(
                db,
                payload=BrowserBridgeTaskCreate(
                    operation="SEND_REPLY",
                    conversation_id=conversation_id,
                    suggestion_id=suggestion.id,
                    idempotency_key=f"browser:reply:{suggestion.input_context_hash}",
                    confirm=payload.confirm,
                ),
                user=user,
                correlation_id=request.state.correlation_id,
            )
        except (LookupError, ValueError) as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return serialize_bridge_task(task)
    try:
        action = await send_suggestion(
            db,
            suggestion=suggestion,
            user_id=user.id,
            correlation_id=request.state.correlation_id,
            confirm=payload.confirm,
        )
    except (LookupError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return serialize_action(action)


@router.get("")
def overview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    accounts = db.scalars(
        select(Account).where(
            Account.platform == "XIANYU",
            Account.owner_user_id == user.id,
        )
    ).all()
    account_ids = [item.id for item in accounts]
    products = db.scalars(
        select(XianyuProduct)
        .where(XianyuProduct.account_id.in_(account_ids))
        .order_by(XianyuProduct.last_synced_at.desc())
        .limit(200)
    ).all()
    conversations = db.scalars(
        select(Conversation)
        .where(Conversation.account_id.in_(account_ids))
        .order_by(Conversation.last_message_at.desc())
        .limit(200)
    ).all()
    orders = db.scalars(
        select(Order)
        .where(Order.account_id.in_(account_ids))
        .order_by(Order.created_at.desc())
        .limit(200)
    ).all()
    message_counts = dict(
        db.execute(
            select(Message.conversation_id, func.count(Message.id)).group_by(
                Message.conversation_id
            )
        ).all()
    )
    return {
        "accounts": [
            {
                "id": item.id,
                "nickname": item.nickname,
                "external_account_id": mask_identifier(item.external_account_id),
                "status": item.status,
                "is_enabled": item.is_enabled,
                "last_synced_at": item.last_synced_at,
                "source": (item.raw_snapshot or {}).get("ingest_method", "adapter"),
            }
            for item in accounts
        ],
        "products": [
            {
                "id": item.id,
                "external_product_id": mask_identifier(item.external_product_id),
                "title": item.published_title,
                "price": str(item.published_price) if item.published_price is not None else None,
                "status": item.status,
                "last_synced_at": item.last_synced_at,
                "source_url": (item.raw_snapshot or {}).get("source_url"),
                "source": (item.raw_snapshot or {}).get("ingest_method", "adapter"),
            }
            for item in products
        ],
        "conversations": [
            {
                "id": item.id,
                "external_conversation_id": mask_identifier(item.external_conversation_id),
                "customer_name": item.customer.display_name_masked or "买家",
                "status": item.status,
                "manual_mode": item.manual_mode,
                "last_message_at": item.last_message_at,
                "message_count": message_counts.get(item.id, 0),
            }
            for item in conversations
        ],
        "orders": [
            {
                "id": item.id,
                "external_order_id": mask_identifier(item.external_order_id),
                "status": item.status.value,
                "revenue": str(item.revenue),
                "expected_profit": str(item.expected_profit),
                "actual_profit": (
                    str(item.actual_profit) if item.actual_profit is not None else None
                ),
                "paid_at": item.paid_at,
                "last_synced_at": item.last_synced_at,
            }
            for item in orders
        ],
    }


@router.post("/metrics/import", status_code=201)
async def import_metrics_report(
    metric_date: date,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    content_type = (request.headers.get("content-type") or "").split(";", maxsplit=1)[0]
    if content_type not in {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    }:
        raise HTTPException(status_code=415, detail="请上传闲鱼经营罗盘导出的 XLSX 文件")
    content = await request.body()
    try:
        return import_seller_workbench_xlsx(
            db,
            content=content,
            metric_date=metric_date,
            user=user,
            correlation_id=request.state.correlation_id,
        )
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/metrics/overview")
def get_metrics_overview(
    days: int = 7,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return metrics_overview(db, user=user, days=days)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/manual-snapshot",
    response_model=ManualXianyuSnapshotResult,
    status_code=201,
)
def ingest_manual_snapshot(
    payload: ManualXianyuSnapshotRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    return ingest_manual_xianyu_snapshot(
        db,
        payload=payload,
        user=user,
        correlation_id=request.state.correlation_id,
    )


@router.post(
    "/manual-publications",
    response_model=ManualXianyuPublicationResult,
    status_code=201,
)
def reconcile_manual_publication(
    payload: ManualXianyuPublicationRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        return reconcile_manual_xianyu_publication(
            db,
            payload=payload,
            user=user,
            correlation_id=request.state.correlation_id,
        )
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/sync")
async def sync_read_only(
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    if not get_settings().adapters_enabled:
        raise HTTPException(status_code=409, detail="真实 Adapter 总开关未启用")
    adapter = get_xianyu_adapter()
    account_result = await adapter.get_account()
    if account_result.status != AdapterStatus.OK or not account_result.data:
        raise HTTPException(
            status_code=401,
            detail=account_result.safe_message or "闲鱼账号授权不可用",
        )
    account_data = account_result.data
    run = start_sync_run(
        db,
        platform="XIANYU",
        adapter_name=account_result.source,
        adapter_version=account_result.source_version,
        operation="SYNC_READ_ONLY",
        requested_by_user_id=user.id,
        correlation_id=request.state.correlation_id,
    )
    normalized_account = account_data.model_dump(mode="json")
    save_snapshot(
        db,
        run=run,
        platform="XIANYU",
        object_type="ACCOUNT",
        external_id=account_data.external_account_id,
        adapter_name=account_result.source,
        adapter_version=account_result.source_version,
        normalized_data=normalized_account,
        raw_payload=account_data.raw_payload or normalized_account,
    )
    account = db.scalar(
        select(Account).where(
            Account.platform == "XIANYU",
            Account.external_account_id == account_data.external_account_id,
            Account.owner_user_id == user.id,
        )
    )
    if account is None:
        account = Account(
            platform="XIANYU",
            external_account_id=account_data.external_account_id,
            nickname=account_data.nickname,
            owner_user_id=user.id,
        )
        db.add(account)
        db.flush()
    account.nickname = account_data.nickname
    account.status = account_data.status
    account.is_enabled = True
    account.raw_snapshot = sanitize_payload(account_data.raw_payload)
    account.snapshot_hash = snapshot_hash(account.raw_snapshot)
    account.last_synced_at = datetime.now(UTC)

    product_result, chat_result, order_result = await asyncio.gather(
        adapter.list_products(),
        adapter.list_chats(),
        adapter.get_orders(),
    )
    results = [product_result, chat_result, order_result]
    fatal = [
        result
        for result in results[:2]
        if result.status not in {AdapterStatus.OK, AdapterStatus.UNSUPPORTED}
    ]
    if fatal:
        first = fatal[0]
        finish_sync_run(
            run,
            status="FAILED",
            error_code=first.error_code,
            message=first.safe_message,
        )
        db.commit()
        raise HTTPException(status_code=503, detail=first.safe_message or "闲鱼同步失败")

    written = 1
    for item in product_result.data or []:
        normalized = item.model_dump(mode="json")
        snapshot, created = save_snapshot(
            db,
            run=run,
            platform="XIANYU",
            object_type="PRODUCT",
            external_id=item.external_product_id,
            adapter_name=product_result.source,
            adapter_version=product_result.source_version,
            normalized_data=normalized,
            raw_payload=item.raw_payload or normalized,
        )
        row = db.scalar(
            select(XianyuProduct).where(
                XianyuProduct.account_id == account.id,
                XianyuProduct.external_product_id == item.external_product_id,
            )
        )
        if row is None:
            row = XianyuProduct(
                account_id=account.id,
                product_id=None,
                external_product_id=item.external_product_id,
            )
            db.add(row)
        row.status = item.status
        row.published_title = item.title
        row.published_price = Decimal(item.price.amount)
        row.raw_snapshot = sanitize_payload(item.raw_payload or normalized)
        row.snapshot_hash = snapshot.payload_hash
        row.last_synced_at = datetime.now(UTC)
        written += int(created)

    for item in chat_result.data or []:
        normalized = item.model_dump(mode="json")
        _, created = save_snapshot(
            db,
            run=run,
            platform="XIANYU",
            object_type="CONVERSATION",
            external_id=item.external_conversation_id,
            adapter_name=chat_result.source,
            adapter_version=chat_result.source_version,
            normalized_data=normalized,
            raw_payload=item.raw_payload or normalized,
        )
        customer = db.scalar(
            select(Customer).where(
                Customer.platform == "XIANYU",
                Customer.external_customer_id == item.customer_id_masked,
            )
        )
        if customer is None:
            customer = Customer(
                platform="XIANYU",
                external_customer_id=item.customer_id_masked,
                display_name_masked=item.customer_name_masked,
            )
            db.add(customer)
            db.flush()
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.account_id == account.id,
                Conversation.external_conversation_id == item.external_conversation_id,
            )
        )
        if conversation is None:
            conversation = Conversation(
                account_id=account.id,
                customer_id=customer.id,
                external_conversation_id=item.external_conversation_id,
            )
            db.add(conversation)
        conversation.last_message_at = _parse_datetime(item.last_message_at)
        written += int(created)

    if order_result.status == AdapterStatus.OK:
        for item in order_result.data or []:
            normalized = item.model_dump(mode="json")
            snapshot, created = save_snapshot(
                db,
                run=run,
                platform="XIANYU",
                object_type="ORDER",
                external_id=item.external_order_id,
                adapter_name=order_result.source,
                adapter_version=order_result.source_version,
                normalized_data=normalized,
                raw_payload=item.raw_payload or normalized,
            )
            order = db.scalar(
                select(Order).where(Order.external_order_id == item.external_order_id)
            )
            if order is None:
                order = Order(
                    account_id=account.id,
                    external_order_id=item.external_order_id,
                )
                db.add(order)
            order.status = ORDER_STATUS_MAP.get(item.status.casefold(), OrderStatus.MANUAL_REQUIRED)
            order.revenue = Decimal(item.amount.amount) if item.amount else Decimal("0")
            order.paid_at = _parse_datetime(item.paid_at)
            order.completed_at = _parse_datetime(item.completed_at)
            order.raw_snapshot = sanitize_payload(item.raw_payload or normalized)
            order.snapshot_hash = snapshot.payload_hash
            order.last_synced_at = datetime.now(UTC)
            written += int(created)

    seen = 1 + len(product_result.data or []) + len(chat_result.data or [])
    if order_result.status == AdapterStatus.OK:
        seen += len(order_result.data or [])
    finish_sync_run(run, status="SUCCEEDED", items_seen=seen, items_written=written)
    write_audit(
        db,
        action="XIANYU_READ_ONLY_SYNC_COMPLETED",
        entity_type="INTEGRATION_SYNC_RUN",
        entity_id=run.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={
            "products": len(product_result.data or []),
            "conversations": len(chat_result.data or []),
            "orders": (
                len(order_result.data or []) if order_result.status == AdapterStatus.OK else 0
            ),
            "orders_supported": order_result.status != AdapterStatus.UNSUPPORTED,
        },
        correlation_id=request.state.correlation_id,
    )
    db.commit()
    return {
        "sync_run_id": run.id,
        "products": len(product_result.data or []),
        "conversations": len(chat_result.data or []),
        "orders": len(order_result.data or []) if order_result.status == AdapterStatus.OK else 0,
        "orders_supported": order_result.status != AdapterStatus.UNSUPPORTED,
    }


@router.post("/conversations/{conversation_id}/sync")
async def sync_conversation_messages(
    conversation_id: int,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    account = db.get(Account, conversation.account_id)
    if account is None or account.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not get_settings().adapters_enabled:
        raise HTTPException(status_code=409, detail="真实 Adapter 总开关未启用")
    adapter = get_xianyu_adapter()
    await adapter.get_account()
    result = await adapter.list_messages(conversation.external_conversation_id)
    if result.status != AdapterStatus.OK:
        raise HTTPException(status_code=503, detail=result.safe_message or "消息同步失败")
    run = start_sync_run(
        db,
        platform="XIANYU",
        adapter_name=result.source,
        adapter_version=result.source_version,
        operation="SYNC_MESSAGES",
        requested_by_user_id=user.id,
        correlation_id=request.state.correlation_id,
    )
    created_count = 0
    for item in result.data or []:
        normalized = item.model_dump(mode="json")
        save_snapshot(
            db,
            run=run,
            platform="XIANYU",
            object_type="MESSAGE",
            external_id=item.external_message_id,
            adapter_name=result.source,
            adapter_version=result.source_version,
            normalized_data=normalized,
            raw_payload=item.raw_payload or normalized,
        )
        exists = db.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.external_message_id == item.external_message_id,
            )
        )
        if exists:
            continue
        db.add(
            Message(
                conversation_id=conversation.id,
                external_message_id=item.external_message_id,
                direction=item.direction,
                role=item.role,
                content=item.content,
                content_type=item.content_type,
                metadata_json={"adapter": result.source},
                created_at=_parse_datetime(item.sent_at) or datetime.now(UTC),
            )
        )
        created_count += 1
    finish_sync_run(
        run,
        status="SUCCEEDED",
        items_seen=len(result.data or []),
        items_written=created_count,
    )
    write_audit(
        db,
        action="XIANYU_MESSAGES_SYNCED",
        entity_type="CONVERSATION",
        entity_id=conversation.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={"seen": len(result.data or []), "created": created_count},
        correlation_id=request.state.correlation_id,
    )
    db.commit()
    return {"seen": len(result.data or []), "created": created_count}
