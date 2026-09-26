import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    Account,
    AIDecision,
    AutomationControl,
    AutomationScope,
    AutonomousLaunch,
    BrowserBridgeAgent,
    Conversation,
    Customer,
    ManualTask,
    Message,
    PlatformActionRecord,
    Product,
    User,
    XianyuDraft,
    XianyuProduct,
)
from app.services.audit import write_audit
from app.services.catalog import product_query_options
from app.services.integrations import finish_sync_run, start_sync_run
from app.services.publication_guard import assert_product_publishable, publication_copy_blockers
from app.services.xianyu_metrics import ingest_browser_metrics

BRIDGE_PROVIDER = "seller-browser-bridge"
LEASE_SECONDS = 300
MAX_ATTEMPTS = 3
MAX_CLAIM_SCAN = 20
ACTION_BY_OPERATION = {
    "OPEN_MODULE": "BROWSER_OPEN_MODULE",
    "CAPTURE_OVERVIEW": "BROWSER_CAPTURE_OVERVIEW",
    "CAPTURE_PRODUCT_METRICS": "BROWSER_CAPTURE_PRODUCT_METRICS",
    "CAPTURE_CONVERSATIONS": "BROWSER_CAPTURE_CONVERSATIONS",
    "PREFILL_PRODUCT": "BROWSER_PREFILL_PRODUCT",
    "PUBLISH_PRODUCT": "BROWSER_PUBLISH_PRODUCT",
    "SEND_REPLY": "BROWSER_SEND_REPLY",
}
OPERATION_BY_ACTION = {value: key for key, value in ACTION_BY_OPERATION.items()}
MODULE_LABELS = {
    "消息",
    "数据总览",
    "商品数据",
    "商品发布",
    "商品管理",
    "订单管理",
    "退款管理",
    "评价管理",
    "退货地址",
}
PUBLISH_PHASES = {
    "PREPARING": 1,
    "READY_TO_SUBMIT": 2,
    "SUBMITTING": 3,
    "SUBMITTED": 4,
}


def _publishable_draft(db: Session, draft_id: int) -> XianyuDraft:
    draft = db.get(XianyuDraft, draft_id)
    if draft is None:
        raise LookupError("闲鱼草稿不存在")
    product = db.scalar(
        select(Product).where(Product.id == draft.product_id).options(*product_query_options())
    )
    if product is None:
        raise LookupError("闲鱼草稿缺少关联商品")
    assert_product_publishable(product)
    copy_blockers = publication_copy_blockers(draft.title, draft.description)
    if copy_blockers:
        raise ValueError("闲鱼发布文案校验失败：" + "；".join(copy_blockers))
    return draft


def _audited_asset_manifest(db: Session, draft: XianyuDraft) -> tuple[AutonomousLaunch, list[dict]]:
    """Return the launch and its final audited asset snapshot."""
    launch = db.scalar(
        select(AutonomousLaunch)
        .where(AutonomousLaunch.draft_id == draft.id)
        .order_by(AutonomousLaunch.id.desc())
    )
    if launch is None:
        raise ValueError("自动发布要求草稿来自自主上架流水线")
    manifest = list(launch.asset_manifest or [])
    attributes = draft.attributes or {}
    draft_manifest = attributes.get("asset_manifest")
    if (
        attributes.get("autonomous_launch_id") == launch.id
        and isinstance(draft_manifest, list)
    ):
        # The draft captures the final, audited manifest at build time. Prefer it
        # when an ORM expiration restored an earlier source-only launch snapshot.
        manifest = list(draft_manifest)
    return launch, manifest


def _verified_assets(db: Session, draft: XianyuDraft) -> list[dict]:
    """Return only locally generated assets that passed the vision QA gate."""
    _launch, manifest = _audited_asset_manifest(db, draft)
    root = Path(get_settings().asset_root).expanduser().resolve()
    assets: list[dict] = []
    for entry in manifest:
        if entry.get("kind") != "generated" or not (entry.get("qa") or {}).get("passed"):
            continue
        try:
            path = (root / str(entry["relative_path"])).resolve()
        except (KeyError, TypeError, OSError):
            continue
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"精品图文件缺失：{entry.get('filename') or 'unknown'}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry.get("sha256"):
            raise ValueError(f"精品图完整性校验失败：{entry.get('filename') or 'unknown'}")
        assets.append(
            {
                "ordinal": len(assets),
                "filename": str(entry.get("filename") or path.name),
                "mime_type": str(entry.get("mime_type") or "image/png"),
                "sha256": digest,
                "bytes": path.stat().st_size,
            }
        )
        if len(assets) >= 9:
            break
    if not assets:
        raise ValueError("草稿没有通过视觉质检的本地精品图")
    return assets


def _publish_controls_ready(db: Session) -> tuple[bool, list[str]]:
    controls = db.scalars(
        select(AutomationControl).where(
            AutomationControl.scope.in_([AutomationScope.GLOBAL, AutomationScope.PUBLISH])
        )
    ).all()
    by_scope = {item.scope: item for item in controls}
    blockers: list[str] = []
    for scope, label in (
        (AutomationScope.GLOBAL, "全局"),
        (AutomationScope.PUBLISH, "发布"),
    ):
        control = by_scope.get(scope)
        if control is None or not control.enabled:
            blockers.append(f"{label}自动化开关未启用")
    publish = by_scope.get(AutomationScope.PUBLISH)
    if publish is None or publish.mode != "AUTOMATIC":
        blockers.append("发布自动化未设置为 AUTOMATIC")
    return not blockers, blockers


def automatic_customer_service_blockers(
    db: Session,
    *,
    enforce_action_limits: bool,
) -> list[str]:
    """Return fail-closed blockers for unattended browser customer service.

    The independent hard gate and bridge credential are checked here so both
    worker-side creation and browser-side claim use the same policy.  Capture
    tasks only need the switch/control checks; SEND_REPLY additionally observes
    the configured action interval and daily limits.
    """

    settings = get_settings()
    blockers: list[str] = []
    if not bool(getattr(settings, "xianyu_browser_customer_service_enabled", False)):
        blockers.append("AUTOFISH_XIANYU_BROWSER_CUSTOMER_SERVICE_ENABLED=false")
    if configured_bridge_token() is None:
        blockers.append("浏览器桥接令牌未配置或不可读取")

    controls = db.scalars(
        select(AutomationControl).where(
            AutomationControl.scope.in_([
                AutomationScope.GLOBAL,
                AutomationScope.CUSTOMER_SERVICE,
            ])
        )
    ).all()
    by_scope = {item.scope: item for item in controls}
    now = datetime.now(UTC)
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for scope, label in (
        (AutomationScope.GLOBAL, "全局"),
        (AutomationScope.CUSTOMER_SERVICE, "客服"),
    ):
        control = by_scope.get(scope)
        if control is None or not control.enabled:
            blockers.append(f"{label}自动化开关未启用")
            continue
        cooldown = _utc(control.cooldown_until) if control.cooldown_until else None
        if cooldown and cooldown > now:
            blockers.append(f"{label}自动化熔断至 {cooldown.isoformat()}")
        if scope == AutomationScope.CUSTOMER_SERVICE and control.mode != "AUTOMATIC":
            blockers.append("客服自动化未设置为 AUTOMATIC")
        if not enforce_action_limits:
            continue
        last_executed = _utc(control.last_executed_at) if control.last_executed_at else None
        if (
            last_executed
            and (now - last_executed).total_seconds() < control.min_interval_seconds
        ):
            blockers.append(f"{label}动作最小间隔尚未满足")
        if scope == AutomationScope.GLOBAL:
            count = db.scalar(
                select(func.count(PlatformActionRecord.id)).where(
                    PlatformActionRecord.status == "SUCCEEDED",
                    PlatformActionRecord.executed_at >= since,
                )
            )
        else:
            count = db.scalar(
                select(func.count(PlatformActionRecord.id)).where(
                    PlatformActionRecord.action_type.in_([
                        ACTION_BY_OPERATION["SEND_REPLY"],
                        "SEND_REPLY",
                    ]),
                    PlatformActionRecord.status == "SUCCEEDED",
                    PlatformActionRecord.executed_at >= since,
                )
            )
        if int(count or 0) >= control.daily_limit:
            blockers.append(f"{label}今日动作上限已达到")
    return list(dict.fromkeys(blockers))


def _build_product_payload(db: Session, draft: XianyuDraft, *, publish: bool) -> dict:
    request_payload = {
        "operation": "PUBLISH_PRODUCT" if publish else "PREFILL_PRODUCT",
        "module": "商品发布",
        "draft_id": draft.id,
        "draft_input_hash": draft.input_hash,
        "title": draft.title,
        "description": draft.description,
        "price": str(draft.price),
        "category": draft.category,
    }
    if publish:
        request_payload["images"] = _verified_assets(db, draft)
        request_payload["submit"] = True
    else:
        request_payload["images"] = [
            value
            for value in (draft.images or [])
            if isinstance(value, str) and value.startswith("https://")
        ][:9]
        request_payload["submit"] = False
    return request_payload


def _build_reply_payload(
    db: Session,
    *,
    conversation_id: int,
    suggestion_id: int,
    user: User,
    automatic: bool = False,
) -> tuple[Conversation, dict]:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise LookupError("客服会话不存在")
    account = db.get(Account, conversation.account_id)
    if account is None or account.owner_user_id != user.id:
        raise LookupError("客服会话不存在")
    if conversation.manual_mode:
        raise ValueError("客服会话已切换人工接管模式")
    suggestion = db.get(AIDecision, suggestion_id)
    if suggestion is None or suggestion.conversation_id != conversation.id:
        raise LookupError("回复建议不存在")
    decision = suggestion.decision or {}
    if decision.get("requires_human") or not decision.get("reply"):
        raise ValueError("该建议必须人工处理，不能通过浏览器发送")
    if automatic and not decision.get("auto_eligible"):
        raise ValueError("该建议未通过自动回复事实门禁")
    latest = db.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    source_message_id = decision.get("external_message_id")
    if (
        latest is None
        or latest.direction != "INBOUND"
        or not source_message_id
        or decision.get("source_message_id") != latest.id
        or latest.external_message_id != source_message_id
    ):
        raise ValueError("会话最新消息已变化，请重新同步并生成回复建议")
    expected_input_hash = hashlib.sha256(
        json.dumps(
            {
                "conversation_id": conversation.id,
                "message_id": latest.id,
                "external_message_id": latest.external_message_id,
                "content": latest.content,
                "product_id": conversation.product_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    if not hmac.compare_digest(suggestion.input_context_hash, expected_input_hash):
        raise ValueError("回复建议指纹与当前会话事实不一致，请重新生成")
    return conversation, {
        "operation": "SEND_REPLY",
        "conversation_id": conversation.id,
        "suggestion_id": suggestion.id,
        "external_conversation_id": conversation.external_conversation_id,
        "source_message_id": source_message_id,
        "reply": str(decision["reply"])[:500],
        "suggestion_input_hash": suggestion.input_context_hash,
    }


def _reply_form_fingerprint(payload: dict) -> str:
    """Match the Chrome bridge fingerprint for the exact reply form state.

    The browser bridge computes SHA-256 over ``JSON.stringify`` of these fields
    before the trusted click, then only reports ``MESSAGE_APPEARED`` after the
    newly observed outbound bubble has the same reply text.  Recomputing that
    fingerprint here binds a successful result to the queued conversation,
    source message, and reply instead of trusting three unrelated result fields.
    """
    encoded = json.dumps(
        {
            "external_conversation_id": str(payload.get("external_conversation_id") or ""),
            "source_message_id": str(payload.get("source_message_id") or ""),
            "reply": str(payload.get("reply") or ""),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _reply_success_evidence_issue(item: PlatformActionRecord, payload) -> tuple[str, str] | None:
    """Return a safe failure code when reply success evidence is not task-bound."""
    expected = item.request_payload or {}
    if payload.external_conversation_id != expected.get("external_conversation_id"):
        return (
            "REPLY_CONVERSATION_EVIDENCE_MISMATCH",
            "回复结果中的会话标识与服务端任务不一致；为防止错发，任务已转人工核对",
        )
    if payload.source_message_id != expected.get("source_message_id"):
        return (
            "REPLY_SOURCE_MESSAGE_EVIDENCE_MISMATCH",
            "回复结果中的买家消息标识与服务端任务不一致；为防止答非所问，任务已转人工核对",
        )
    if not payload.form_fingerprint or not hmac.compare_digest(
        payload.form_fingerprint,
        _reply_form_fingerprint(expected),
    ):
        return (
            "REPLY_TEXT_EVIDENCE_MISMATCH",
            "回复结果未能证明实际发送文本与已确认建议一致；任务已转人工核对",
        )
    return None


def configured_bridge_token() -> str | None:
    settings = get_settings()
    if settings.xianyu_browser_bridge_token:
        return settings.xianyu_browser_bridge_token.strip() or None
    if settings.xianyu_browser_bridge_token_file:
        path = Path(settings.xianyu_browser_bridge_token_file)
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except (OSError, UnicodeError):
            return None
    return None


def bridge_token_matches(provided: str | None) -> bool:
    configured = configured_bridge_token()
    return bool(configured and provided and hmac.compare_digest(configured, provided))


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def serialize_bridge_agent(item: BrowserBridgeAgent, *, now: datetime | None = None) -> dict:
    reference = now or datetime.now(UTC)
    online = _utc(item.last_seen_at) >= reference - timedelta(seconds=90)
    return {
        "bridge_id": item.id,
        "name": item.name,
        "version": item.version,
        "status": "ONLINE" if online else "OFFLINE",
        "capabilities": item.capabilities,
        "current_url": item.current_url,
        "last_error": item.last_error,
        "last_seen_at": item.last_seen_at,
    }


def serialize_bridge_task(item: PlatformActionRecord, *, include_payload: bool = False) -> dict:
    data = {
        "id": item.id,
        "operation": OPERATION_BY_ACTION.get(item.action_type, item.action_type),
        "action_type": item.action_type,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "status": item.status,
        "preview": item.preview,
        "execution_result": item.execution_result,
        "attempts": item.attempts,
        "requires_confirmation": item.requires_confirmation,
        "error_code": item.error_code,
        "error_message": item.error_message,
        "lease_owner": item.lease_owner,
        "lease_expires_at": item.lease_expires_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_payload:
        data["payload"] = item.request_payload
    return data


def create_bridge_task(
    db: Session,
    *,
    payload,
    user,
    correlation_id: str,
    automatic: bool = False,
) -> PlatformActionRecord:
    action_type = ACTION_BY_OPERATION[payload.operation]
    existing = db.scalar(
        select(PlatformActionRecord).where(
            PlatformActionRecord.idempotency_key == payload.idempotency_key
        )
    )
    if existing is not None:
        if existing.requested_by_user_id != user.id or existing.provider != BRIDGE_PROVIDER:
            raise ValueError("幂等键已被其他任务使用")
        if existing.action_type != action_type or bool(existing.automatic) != automatic:
            raise ValueError("幂等键对应的浏览器任务类型不一致")
        if automatic and payload.operation == "CAPTURE_CONVERSATIONS":
            blockers = automatic_customer_service_blockers(
                db,
                enforce_action_limits=False,
            )
            if blockers:
                raise ValueError("；".join(blockers))
        if automatic and payload.operation == "SEND_REPLY":
            blockers = automatic_customer_service_blockers(
                db,
                enforce_action_limits=False,
            )
            if blockers:
                raise ValueError("；".join(blockers))
            _conversation, current_payload = _build_reply_payload(
                db,
                conversation_id=payload.conversation_id,
                suggestion_id=payload.suggestion_id,
                user=user,
                automatic=True,
            )
            if any(
                current_payload.get(key) != (existing.request_payload or {}).get(key)
                for key in (
                    "conversation_id",
                    "suggestion_id",
                    "external_conversation_id",
                    "source_message_id",
                    "reply",
                    "suggestion_input_hash",
                )
            ):
                raise ValueError("幂等回复任务与当前会话或建议不一致")
        return existing

    target_type = "SELLER_WORKBENCH"
    target_id = 0
    request_payload: dict = {"operation": payload.operation}
    warnings = ["浏览器代理仅执行服务端固定动作，不接收任意脚本或选择器"]
    requires_confirmation = False

    if payload.operation == "OPEN_MODULE":
        if payload.module not in MODULE_LABELS:
            raise ValueError("不支持的工作台模块")
        request_payload["module"] = payload.module
    elif payload.operation == "CAPTURE_OVERVIEW":
        request_payload["capture"] = "MODULE_AVAILABILITY_ONLY"
    elif payload.operation == "CAPTURE_PRODUCT_METRICS":
        metric_date = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
        request_payload.update(
            {
                "module": "商品数据",
                "capture": "PRODUCT_METRICS_NEAR_1_DAY",
                "metric_date": metric_date.isoformat(),
            }
        )
        warnings.extend(
            [
                "只读取商品数据页近1天可见经营指标，不读取 Cookie 或买家隐私",
                "登录失效、风控提示、日期不符或表格结构变化时立即停止",
            ]
        )
    elif payload.operation == "CAPTURE_CONVERSATIONS":
        if automatic:
            blockers = automatic_customer_service_blockers(
                db,
                enforce_action_limits=False,
            )
            if blockers:
                raise ValueError("；".join(blockers))
        request_payload.update(
            {
                "module": "消息",
                "capture": "VISIBLE_CONVERSATIONS_AND_MESSAGES",
                "max_conversations": 20,
            }
        )
        warnings.extend(
            [
                "只读取当前账号消息页可见会话和文本消息，不读取 Cookie、Token 或联系人通讯录",
                "验证码、登录失效、页面结构变化或买家身份无法核验时立即停止",
            ]
        )
    elif payload.operation in {"PREFILL_PRODUCT", "PUBLISH_PRODUCT"}:
        draft = db.get(XianyuDraft, payload.draft_id)
        if draft is None or draft.created_by_user_id != user.id:
            raise LookupError("闲鱼草稿不存在")
        draft = _publishable_draft(db, draft.id)
        if not (draft.validation or {}).get("passed"):
            raise ValueError("草稿校验未通过，不能发送到浏览器")
        if draft.status == "PUBLISHED":
            raise ValueError("已发布草稿不能再次执行浏览器任务")
        publish = payload.operation == "PUBLISH_PRODUCT"
        if publish and draft.status != "REVIEW_READY":
            raise ValueError("只有 REVIEW_READY 草稿可以自动发布")
        if publish and not get_settings().xianyu_browser_publish_enabled:
            raise ValueError("AUTOFISH_XIANYU_BROWSER_PUBLISH_ENABLED=false")
        if publish and automatic:
            ready, blockers = _publish_controls_ready(db)
            if not ready:
                raise ValueError("；".join(blockers))
        target_type = "XIANYU_DRAFT"
        target_id = draft.id
        request_payload = _build_product_payload(db, draft, publish=publish)
        requires_confirmation = not automatic
        if publish:
            warnings.extend(
                [
                    "代理会上传已通过事实质检的本地精品图并提交发布",
                    "遇到登录失效、验证码、风控、字段不匹配或结果不明确时立即转人工",
                    "进入 SUBMITTING 后租约超时不会自动重试，以防重复发布",
                ]
            )
        else:
            warnings.extend(
                [
                    "只预填标题、描述和价格；类目与图片由操作员核对",
                    "预填完成后必须在闲鱼页面人工确认发布",
                ]
            )
    elif payload.operation == "SEND_REPLY":
        if automatic:
            blockers = automatic_customer_service_blockers(
                db,
                enforce_action_limits=False,
            )
            if blockers:
                raise ValueError("；".join(blockers))
        conversation, request_payload = _build_reply_payload(
            db,
            conversation_id=payload.conversation_id,
            suggestion_id=payload.suggestion_id,
            user=user,
            automatic=automatic,
        )
        target_type = "CONVERSATION"
        target_id = conversation.id
        requires_confirmation = not automatic
        warnings.extend(
            [
                "发送前会重新核对会话指纹、最新买家消息和回复文本",
                "只允许向已同步的目标会话发送这一条已确认建议",
                "提交后结果不明确时立即停止，禁止自动重试，避免重复回复",
            ]
        )

    item = PlatformActionRecord(
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        request_payload=request_payload,
        preview={
            "executable": True,
            "blockers": [],
            "warnings": warnings,
            "expected_changes": {
                "browser_only": True,
                "external_submission": payload.operation in {"PUBLISH_PRODUCT", "SEND_REPLY"},
            },
            "scope": "BROWSER_BRIDGE",
            "mode": "AUTOMATIC" if automatic else "OPERATOR_CONFIRMED",
        },
        status="QUEUED",
        requires_confirmation=requires_confirmation,
        automatic=automatic,
        provider=BRIDGE_PROVIDER,
        correlation_id=correlation_id,
        idempotency_key=payload.idempotency_key,
        requested_by_user_id=user.id,
        approved_by_user_id=user.id if payload.confirm and not automatic else None,
        approved_at=datetime.now(UTC) if payload.confirm and not automatic else None,
    )
    db.add(item)
    db.flush()
    write_audit(
        db,
        action="XIANYU_BROWSER_TASK_QUEUED",
        entity_type="PLATFORM_ACTION",
        entity_id=item.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={
            "operation": payload.operation,
            "target_type": target_type,
            "target_id": target_id,
            "external_submission": payload.operation in {"PUBLISH_PRODUCT", "SEND_REPLY"},
            "automatic": automatic,
        },
        correlation_id=correlation_id,
    )
    db.commit()
    db.refresh(item)
    return item


def create_automatic_publish_task(
    db: Session,
    *,
    draft_id: int,
    user,
    idempotency_key: str,
    correlation_id: str,
) -> PlatformActionRecord:
    # HTTP callers cannot set ``automatic``.  Only the scheduler reaches this
    # helper after the GLOBAL/PUBLISH policy checks in create_bridge_task.
    from app.schemas.xianyu import BrowserBridgeTaskCreate

    return create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="PUBLISH_PRODUCT",
            draft_id=draft_id,
            idempotency_key=idempotency_key,
            confirm=True,
        ),
        user=user,
        correlation_id=correlation_id,
        automatic=True,
    )


def create_automatic_metrics_task(
    db: Session,
    *,
    user,
    idempotency_key: str,
    correlation_id: str,
) -> PlatformActionRecord:
    from app.schemas.xianyu import BrowserBridgeTaskCreate

    return create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="CAPTURE_PRODUCT_METRICS",
            idempotency_key=idempotency_key,
        ),
        user=user,
        correlation_id=correlation_id,
        automatic=True,
    )


def create_automatic_conversation_capture_task(
    db: Session,
    *,
    user,
    idempotency_key: str,
    correlation_id: str,
) -> PlatformActionRecord:
    from app.schemas.xianyu import BrowserBridgeTaskCreate

    return create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="CAPTURE_CONVERSATIONS",
            idempotency_key=idempotency_key,
        ),
        user=user,
        correlation_id=correlation_id,
        automatic=True,
    )


def create_automatic_reply_task(
    db: Session,
    *,
    conversation_id: int,
    suggestion_id: int,
    user,
    idempotency_key: str,
    correlation_id: str,
) -> PlatformActionRecord:
    from app.schemas.xianyu import BrowserBridgeTaskCreate

    return create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="SEND_REPLY",
            conversation_id=conversation_id,
            suggestion_id=suggestion_id,
            idempotency_key=idempotency_key,
            # The internal helper is the only path allowed to set automatic=True;
            # schema confirmation prevents HTTP callers from bypassing review.
            confirm=True,
        ),
        user=user,
        correlation_id=correlation_id,
        automatic=True,
    )


def heartbeat_agent(db: Session, payload) -> BrowserBridgeAgent:
    now = datetime.now(UTC)
    item = db.get(BrowserBridgeAgent, payload.bridge_id)
    if item is None:
        item = BrowserBridgeAgent(
            id=payload.bridge_id,
            name=payload.name,
            version=payload.version,
            last_seen_at=now,
        )
        db.add(item)
    item.name = payload.name
    item.version = payload.version
    item.status = "ONLINE"
    item.capabilities = list(payload.capabilities)
    item.current_url = payload.current_url
    item.last_error = payload.last_error
    item.last_seen_at = now
    db.commit()
    db.refresh(item)
    return item


def _browser_chat_account(db: Session, owner: User) -> Account:
    account = db.scalar(
        select(Account)
        .where(Account.platform == "XIANYU", Account.owner_user_id == owner.id)
        .order_by(Account.last_synced_at.desc(), Account.id.desc())
    )
    if account is None:
        digest = hashlib.sha256(f"browser-chat:{owner.id}".encode()).hexdigest()[:24]
        account = Account(
            platform="XIANYU",
            external_account_id=f"browser-chat:{digest}",
            nickname="闲鱼浏览器账号",
            owner_user_id=owner.id,
        )
        db.add(account)
        db.flush()
    account.status = "BROWSER_CONNECTED"
    account.is_enabled = True
    account.last_synced_at = datetime.now(UTC)
    account.raw_snapshot = {
        **(account.raw_snapshot or {}),
        "ingest_method": "browser-bridge-chat",
    }
    return account


def _ingest_browser_conversations(
    db: Session,
    *,
    owner: User,
    conversations,
    captured_at: datetime,
    correlation_id: str,
) -> dict:
    browser_account = _browser_chat_account(db, owner)
    run = start_sync_run(
        db,
        platform="XIANYU",
        adapter_name=BRIDGE_PROVIDER,
        adapter_version="0.5.0",
        operation="CAPTURE_CONVERSATIONS",
        requested_by_user_id=owner.id,
        correlation_id=correlation_id,
    )
    conversations_written = 0
    conversations_verified = 0
    conversations_unverified = 0
    messages_seen = 0
    messages_created = 0
    for record in conversations:
        if not record.external_product_id:
            conversations_unverified += 1
            continue
        product = db.scalar(
            select(XianyuProduct)
            .join(Account, Account.id == XianyuProduct.account_id)
            .where(
                Account.owner_user_id == owner.id,
                XianyuProduct.external_product_id == record.external_product_id,
            )
            .order_by(XianyuProduct.last_synced_at.desc(), XianyuProduct.id.desc())
        )
        if product is None:
            conversations_unverified += 1
            continue
        account = db.get(Account, product.account_id)
        if account is None:
            conversations_unverified += 1
            continue
        conversations_verified += 1
        customer = db.scalar(
            select(Customer).where(
                Customer.platform == "XIANYU",
                Customer.external_customer_id == record.customer_id_masked,
            )
        )
        if customer is None:
            customer = Customer(
                platform="XIANYU",
                external_customer_id=record.customer_id_masked,
                display_name_masked=record.customer_name_masked,
            )
            db.add(customer)
            db.flush()
        else:
            customer.display_name_masked = record.customer_name_masked
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.account_id == account.id,
                Conversation.external_conversation_id == record.external_conversation_id,
            )
        )
        if conversation is None:
            conversation = Conversation(
                account_id=account.id,
                customer_id=customer.id,
                external_conversation_id=record.external_conversation_id,
                status="OPEN",
            )
            db.add(conversation)
            db.flush()
            conversations_written += 1
        conversation.product_id = product.product_id
        conversation.last_message_at = record.last_message_at
        for message in record.messages:
            messages_seen += 1
            exists = db.scalar(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.external_message_id == message.external_message_id,
                )
            )
            if exists is not None:
                continue
            db.add(
                Message(
                    conversation_id=conversation.id,
                    external_message_id=message.external_message_id,
                    direction=message.direction,
                    role=message.role,
                    content=message.content,
                    content_type="TEXT",
                    metadata_json={
                        "adapter": BRIDGE_PROVIDER,
                        "captured_at": captured_at.isoformat(),
                        "timestamp_source": message.timestamp_source,
                    },
                    created_at=message.sent_at,
                )
            )
            messages_created += 1
    finish_sync_run(
        run,
        status="SUCCEEDED",
        items_seen=messages_seen,
        items_written=messages_created,
    )
    return {
        "account_id": browser_account.id,
        "conversations_seen": len(conversations),
        "conversations_verified": conversations_verified,
        "conversations_unverified": conversations_unverified,
        "conversations_created": conversations_written,
        "messages_seen": messages_seen,
        "messages_created": messages_created,
        "sync_run_id": run.id,
    }


def _record_browser_reply(
    db: Session,
    *,
    item: PlatformActionRecord,
    sent_message_id: str,
) -> None:
    conversation = db.get(Conversation, item.target_id)
    if conversation is None:
        raise ValueError("浏览器回复任务关联会话不存在")
    exists = db.scalar(
        select(Message).where(
            Message.conversation_id == conversation.id,
            Message.external_message_id == sent_message_id,
        )
    )
    now = datetime.now(UTC)
    if exists is None:
        db.add(
            Message(
                conversation_id=conversation.id,
                external_message_id=sent_message_id,
                direction="OUTBOUND",
                role="SELLER",
                content=str((item.request_payload or {}).get("reply") or "")[:10_000],
                content_type="TEXT",
                metadata_json={"adapter": BRIDGE_PROVIDER, "platform_action_id": item.id},
                sent_by_ai=True,
                created_at=now,
            )
        )
    conversation.last_message_at = now


def _manual_task(db: Session, item: PlatformActionRecord, reason: str) -> None:
    existing = db.scalar(
        select(ManualTask).where(
            ManualTask.entity_type == "PLATFORM_ACTION",
            ManualTask.entity_id == str(item.id),
            ManualTask.status == "OPEN",
        )
    )
    if existing is None:
        db.add(
            ManualTask(
                type="BROWSER_CONFIRMATION",
                title=f"闲鱼工作台任务 #{item.id} 需要人工处理",
                reason=reason,
                status="OPEN",
                priority=80,
                entity_type="PLATFORM_ACTION",
                entity_id=str(item.id),
            )
        )


def _update_customer_service_controls_after_reply(
    db: Session,
    *,
    succeeded: bool,
    reason: str | None,
) -> None:
    now = datetime.now(UTC)
    controls = db.scalars(
        select(AutomationControl).where(
            AutomationControl.scope.in_([
                AutomationScope.GLOBAL,
                AutomationScope.CUSTOMER_SERVICE,
            ])
        )
    ).all()
    for control in controls:
        control.last_executed_at = now
        if succeeded:
            control.consecutive_failures = 0
            control.stopped_reason = None
            continue
        control.consecutive_failures += 1
        control.stopped_reason = (reason or "浏览器自动回复失败")[:500]
        if control.consecutive_failures >= control.failure_threshold:
            control.cooldown_until = now + timedelta(minutes=15)


def record_bridge_progress(
    db: Session,
    *,
    task_id: int,
    payload,
    correlation_id: str,
) -> PlatformActionRecord:
    item = db.get(PlatformActionRecord, task_id)
    if item is None or item.provider != BRIDGE_PROVIDER:
        raise LookupError("浏览器任务不存在")
    if item.status != "RUNNING" or item.lease_owner != payload.bridge_id:
        raise ValueError("浏览器任务租约无效")
    if item.action_type not in {
        ACTION_BY_OPERATION["PUBLISH_PRODUCT"],
        ACTION_BY_OPERATION["SEND_REPLY"],
    }:
        raise ValueError("只有外部提交任务可以上报执行阶段")
    current = (item.execution_result or {}).get("phase")
    if current and PUBLISH_PHASES[payload.phase] < PUBLISH_PHASES.get(current, 0):
        raise ValueError("外部提交阶段不得回退")
    now = datetime.now(UTC)
    item.execution_result = {
        **(item.execution_result or {}),
        "phase": payload.phase,
        "phase_updated_at": now.isoformat(),
        "current_url": payload.current_url,
        "form_fingerprint": payload.form_fingerprint,
    }
    item.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    write_audit(
        db,
        action=(
            "XIANYU_BROWSER_REPLY_PROGRESS"
            if item.action_type == ACTION_BY_OPERATION["SEND_REPLY"]
            else "XIANYU_BROWSER_PUBLISH_PROGRESS"
        ),
        entity_type="PLATFORM_ACTION",
        entity_id=item.id,
        actor_type="BROWSER_BRIDGE",
        after_data={"phase": payload.phase},
        result="RUNNING",
        correlation_id=correlation_id,
    )
    db.commit()
    db.refresh(item)
    return item


def resolve_bridge_task_asset(
    db: Session,
    *,
    task_id: int,
    bridge_id: str,
    ordinal: int,
) -> tuple[Path, dict]:
    item = db.get(PlatformActionRecord, task_id)
    if item is None or item.provider != BRIDGE_PROVIDER:
        raise LookupError("浏览器任务不存在")
    if item.status != "RUNNING" or item.lease_owner != bridge_id:
        raise ValueError("浏览器任务租约无效")
    if item.action_type != ACTION_BY_OPERATION["PUBLISH_PRODUCT"]:
        raise ValueError("当前任务不包含发布图片")
    assets = (item.request_payload or {}).get("images") or []
    descriptor = next(
        (entry for entry in assets if int(entry.get("ordinal", -1)) == ordinal),
        None,
    )
    if descriptor is None:
        raise LookupError("发布图片不存在")
    draft = db.get(XianyuDraft, item.target_id)
    if draft is None:
        raise LookupError("发布图片关联草稿不存在")
    _launch, audited_manifest = _audited_asset_manifest(db, draft)
    manifest = next(
        (
            entry
            for entry in audited_manifest
            if entry.get("kind") == "generated"
            and (entry.get("qa") or {}).get("passed")
            and entry.get("filename") == descriptor.get("filename")
            and entry.get("sha256") == descriptor.get("sha256")
        ),
        None,
    )
    if manifest is None:
        raise LookupError("发布图片未通过事实质检")
    root = Path(get_settings().asset_root).expanduser().resolve()
    path = (root / str(manifest["relative_path"])).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise LookupError("发布图片文件不存在")
    if hashlib.sha256(path.read_bytes()).hexdigest() != descriptor["sha256"]:
        raise ValueError("发布图片完整性校验失败")
    return path, descriptor


def _requeue_expired_leases(db: Session, now: datetime) -> None:
    expired = db.scalars(
        select(PlatformActionRecord).where(
            PlatformActionRecord.provider == BRIDGE_PROVIDER,
            PlatformActionRecord.status == "RUNNING",
            PlatformActionRecord.lease_expires_at.is_not(None),
            PlatformActionRecord.lease_expires_at < now,
        )
    ).all()
    for item in expired:
        phase = (item.execution_result or {}).get("phase")
        item.lease_owner = None
        item.lease_expires_at = None
        if phase in {"SUBMITTING", "SUBMITTED"}:
            item.status = "MANUAL_REQUIRED"
            is_reply = item.action_type == ACTION_BY_OPERATION["SEND_REPLY"]
            item.error_code = "REPLY_RESULT_AMBIGUOUS" if is_reply else "PUBLISH_RESULT_AMBIGUOUS"
            item.error_message = (
                "回复提交阶段连接中断；为防止重复回复，任务不会自动重试"
                if is_reply
                else "发布提交阶段连接中断；为防止重复上架，任务不会自动重试"
            )
            _manual_task(db, item, item.error_message)
        elif item.attempts >= MAX_ATTEMPTS:
            item.status = "MANUAL_REQUIRED"
            item.error_code = "BRIDGE_LEASE_EXHAUSTED"
            item.error_message = "浏览器代理连续三次未在租约内返回结果"
            _manual_task(db, item, item.error_message)
        else:
            item.status = "QUEUED"
            item.error_code = "BRIDGE_LEASE_EXPIRED"
            item.error_message = "上一次浏览器代理租约超时，任务已重新排队"
    if expired:
        db.flush()


def claim_bridge_task(db: Session, payload) -> PlatformActionRecord | None:
    now = datetime.now(UTC)
    heartbeat_agent(db, payload)
    _requeue_expired_leases(db, now)
    supported_actions = [
        ACTION_BY_OPERATION[operation]
        for operation in payload.capabilities
        if operation in ACTION_BY_OPERATION
    ]
    if not supported_actions:
        db.commit()
        return None

    for _attempt in range(MAX_CLAIM_SCAN):
        task_id = db.scalar(
            select(PlatformActionRecord.id)
            .where(
                PlatformActionRecord.provider == BRIDGE_PROVIDER,
                PlatformActionRecord.status == "QUEUED",
                PlatformActionRecord.attempts < MAX_ATTEMPTS,
                PlatformActionRecord.action_type.in_(supported_actions),
            )
            .order_by(PlatformActionRecord.created_at, PlatformActionRecord.id)
            .limit(1)
        )
        if task_id is None:
            db.commit()
            return None
        task = db.get(PlatformActionRecord, task_id)
        if task is None or task.status != "QUEUED":
            db.rollback()
            continue
        if (
            task.automatic
            and task.action_type == ACTION_BY_OPERATION["CAPTURE_CONVERSATIONS"]
        ):
            blockers = automatic_customer_service_blockers(
                db,
                enforce_action_limits=False,
            )
            if blockers:
                task.status = "BLOCKED"
                task.error_code = "AUTOMATIC_CUSTOMER_SERVICE_POLICY_BLOCKED"
                task.error_message = "；".join(blockers)
                task.preview = {
                    **(task.preview or {}),
                    "executable": False,
                    "blockers": list(
                        dict.fromkeys([
                            *(task.preview or {}).get("blockers", []),
                            *blockers,
                        ])
                    ),
                }
                write_audit(
                    db,
                    action="XIANYU_BROWSER_TASK_BLOCKED",
                    entity_type="PLATFORM_ACTION",
                    entity_id=task.id,
                    actor_type="SYSTEM",
                    after_data={
                        "operation": "CAPTURE_CONVERSATIONS",
                        "error_code": task.error_code,
                    },
                    result="BLOCKED",
                    correlation_id="browser-bridge-claim",
                )
                db.commit()
                continue
        if task.action_type in {
            ACTION_BY_OPERATION["PREFILL_PRODUCT"],
            ACTION_BY_OPERATION["PUBLISH_PRODUCT"],
        }:
            try:
                draft = _publishable_draft(db, task.target_id)
                if task.action_type == ACTION_BY_OPERATION["PUBLISH_PRODUCT"]:
                    if not get_settings().xianyu_browser_publish_enabled:
                        raise ValueError("AUTOFISH_XIANYU_BROWSER_PUBLISH_ENABLED=false")
                    if draft.status != "REVIEW_READY":
                        raise ValueError("发布草稿已不处于 REVIEW_READY 状态")
                    if (task.request_payload or {}).get("draft_input_hash") != draft.input_hash:
                        raise ValueError("发布草稿在排队后发生变更")
                    _verified_assets(db, draft)
            except (LookupError, ValueError) as exc:
                task.status = "BLOCKED"
                task.error_code = "PUBLICATION_PROVENANCE_BLOCKED"
                task.error_message = str(exc)
                task.preview = {
                    **(task.preview or {}),
                    "executable": False,
                    "blockers": list(
                        dict.fromkeys([*(task.preview or {}).get("blockers", []), str(exc)])
                    ),
                }
                write_audit(
                    db,
                    action="XIANYU_BROWSER_TASK_BLOCKED",
                    entity_type="PLATFORM_ACTION",
                    entity_id=task.id,
                    actor_type="SYSTEM",
                    after_data={
                        "operation": OPERATION_BY_ACTION.get(task.action_type),
                        "error_code": task.error_code,
                    },
                    result="BLOCKED",
                    correlation_id="browser-bridge-claim",
                )
                db.commit()
                continue
        if task.action_type == ACTION_BY_OPERATION["SEND_REPLY"]:
            try:
                if task.automatic:
                    blockers = automatic_customer_service_blockers(
                        db,
                        enforce_action_limits=True,
                    )
                    if blockers:
                        deferred_limit = all(
                            blocker.endswith("动作最小间隔尚未满足")
                            or blocker.endswith("今日动作上限已达到")
                            for blocker in blockers
                        )
                        if deferred_limit:
                            db.commit()
                            return None
                        raise ValueError("；".join(blockers))
                owner = db.get(User, task.requested_by_user_id)
                if owner is None or not owner.is_active:
                    raise ValueError("浏览器回复任务缺少有效操作账号")
                conversation, current_payload = _build_reply_payload(
                    db,
                    conversation_id=int((task.request_payload or {}).get("conversation_id") or 0),
                    suggestion_id=int((task.request_payload or {}).get("suggestion_id") or 0),
                    user=owner,
                    automatic=bool(task.automatic),
                )
                if conversation.id != task.target_id or any(
                    current_payload.get(key) != (task.request_payload or {}).get(key)
                    for key in (
                        "conversation_id",
                        "suggestion_id",
                        "external_conversation_id",
                        "source_message_id",
                        "reply",
                        "suggestion_input_hash",
                    )
                ):
                    raise ValueError("回复建议或最新买家消息已变化，请重新生成")
            except (LookupError, ValueError) as exc:
                task.status = "BLOCKED"
                task.error_code = (
                    "AUTOMATIC_REPLY_REVALIDATION_BLOCKED"
                    if task.automatic
                    else "STALE_REPLY_BLOCKED"
                )
                task.error_message = str(exc)
                task.preview = {
                    **(task.preview or {}),
                    "executable": False,
                    "blockers": list(
                        dict.fromkeys([*(task.preview or {}).get("blockers", []), str(exc)])
                    ),
                }
                _manual_task(db, task, task.error_message)
                write_audit(
                    db,
                    action="XIANYU_BROWSER_TASK_BLOCKED",
                    entity_type="PLATFORM_ACTION",
                    entity_id=task.id,
                    actor_type="SYSTEM",
                    after_data={"operation": "SEND_REPLY", "error_code": task.error_code},
                    result="BLOCKED",
                    correlation_id="browser-bridge-claim",
                )
                db.commit()
                continue
        claimed = db.execute(
            update(PlatformActionRecord)
            .where(
                PlatformActionRecord.id == task_id,
                PlatformActionRecord.status == "QUEUED",
            )
            .values(
                status="RUNNING",
                attempts=PlatformActionRecord.attempts + 1,
                lease_owner=payload.bridge_id,
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                error_code=None,
                error_message=None,
            )
        )
        if claimed.rowcount == 1:
            db.commit()
            return db.get(PlatformActionRecord, task_id)
        db.rollback()
    return None


def finish_bridge_task(
    db: Session,
    *,
    task_id: int,
    payload,
    correlation_id: str,
) -> PlatformActionRecord:
    item = db.get(PlatformActionRecord, task_id)
    if item is None or item.provider != BRIDGE_PROVIDER:
        raise LookupError("浏览器任务不存在")
    if item.status != "RUNNING":
        raise ValueError("浏览器任务当前不可提交结果")
    if item.lease_owner != payload.bridge_id:
        raise ValueError("浏览器任务租约不属于当前代理")

    is_publish = item.action_type == ACTION_BY_OPERATION["PUBLISH_PRODUCT"]
    is_reply = item.action_type == ACTION_BY_OPERATION["SEND_REPLY"]
    is_external_submission = is_publish or is_reply
    if is_publish and payload.status == "SUCCEEDED":
        if not payload.submission_attempted or not payload.success_evidence:
            raise ValueError("外部提交成功结果缺少可验证证据")
    result_status = payload.status
    result_error_code = payload.error_code
    result_reason = payload.manual_reason
    if is_reply and payload.status == "SUCCEEDED":
        evidence_issue = None
        if (
            not payload.submission_attempted
            or not payload.reply_sent
            or payload.success_evidence != "MESSAGE_APPEARED"
            or not payload.sent_message_id
        ):
            evidence_issue = (
                "REPLY_SUCCESS_EVIDENCE_MISSING",
                "回复成功结果缺少可验证的消息气泡证据；任务已转人工核对",
            )
        else:
            evidence_issue = _reply_success_evidence_issue(item, payload)
        if evidence_issue is not None:
            result_status = "MANUAL_REQUIRED"
            result_error_code, result_reason = evidence_issue
    if is_external_submission and payload.submission_attempted and payload.status != "SUCCEEDED":
        result_status = "MANUAL_REQUIRED"
        result_error_code = result_error_code or (
            "REPLY_RESULT_AMBIGUOUS" if is_reply else "PUBLISH_RESULT_AMBIGUOUS"
        )
        result_reason = result_reason or (
            "已尝试发送但未确认回复结果；为防止重复回复已停止重试"
            if is_reply
            else "已尝试提交但未确认发布结果；为防止重复上架已停止重试"
        )
    if is_reply and item.automatic and result_status == "FAILED":
        result_status = "MANUAL_REQUIRED"
        result_error_code = result_error_code or "AUTOMATIC_REPLY_FAILED"
        result_reason = result_reason or "浏览器自动回复未执行成功，已转人工处理"

    payload_result = payload.model_dump(
        mode="json", exclude={"bridge_id", "metrics", "conversations"}
    )
    result = {
        **(item.execution_result or {}),
        **payload_result,
    }
    result["received_at"] = datetime.now(UTC).isoformat()
    result["status"] = result_status
    result["error_code"] = result_error_code
    result["manual_reason"] = result_reason
    if is_reply:
        result["reply_evidence_verified"] = result_status == "SUCCEEDED"
    if is_external_submission and result_status == "SUCCEEDED":
        result["phase"] = "SUBMITTED"
    item.execution_result = result
    item.status = result_status
    item.error_code = result_error_code
    item.error_message = result_reason
    item.lease_owner = None
    item.lease_expires_at = None
    if result_status == "SUCCEEDED":
        item.executed_at = datetime.now(UTC)
        if item.action_type == ACTION_BY_OPERATION["CAPTURE_PRODUCT_METRICS"]:
            expected_date = (item.request_payload or {}).get("metric_date")
            if payload.metric_date is None or payload.metric_date.isoformat() != expected_date:
                raise ValueError("浏览器日报日期与服务端任务日期不一致")
            if not payload.metrics:
                raise ValueError("浏览器日报没有商品数据")
            owner = db.get(User, item.requested_by_user_id)
            if owner is None or not owner.is_active:
                raise ValueError("浏览器日报任务缺少有效账号")
            metrics_import = ingest_browser_metrics(
                db,
                records=[metric.model_dump(mode="json") for metric in payload.metrics],
                metric_date=payload.metric_date,
                user=owner,
                correlation_id=correlation_id,
            )
            result = {**result, "metrics_import": metrics_import}
            item.execution_result = result
        if item.action_type == ACTION_BY_OPERATION["CAPTURE_CONVERSATIONS"]:
            owner = db.get(User, item.requested_by_user_id)
            if owner is None or not owner.is_active:
                raise ValueError("浏览器会话采集任务缺少有效账号")
            if payload.captured_at is None or not payload.conversations:
                raise ValueError("浏览器会话采集结果为空")
            conversation_import = _ingest_browser_conversations(
                db,
                owner=owner,
                conversations=payload.conversations,
                captured_at=payload.captured_at,
                correlation_id=correlation_id,
            )
            result = {**result, "conversation_import": conversation_import}
            item.execution_result = result
        if is_reply:
            _record_browser_reply(
                db,
                item=item,
                sent_message_id=str(payload.sent_message_id),
            )
        if is_publish:
            draft = db.get(XianyuDraft, item.target_id)
            if draft is None:
                raise ValueError("发布任务关联草稿不存在")
            draft.status = "PUBLISHED"
            launch = db.scalar(
                select(AutonomousLaunch)
                .where(AutonomousLaunch.draft_id == draft.id)
                .order_by(AutonomousLaunch.id.desc())
            )
            if launch is not None:
                launch.status = "PUBLISHED"
                launch.current_stage = "XIANYU_PUBLISHED"
                launch.finished_at = datetime.now(UTC)
            controls = db.scalars(
                select(AutomationControl).where(
                    AutomationControl.scope.in_([
                        AutomationScope.GLOBAL,
                        AutomationScope.PUBLISH,
                    ])
                )
            ).all()
            for control in controls:
                control.last_executed_at = datetime.now(UTC)
                control.consecutive_failures = 0
    elif result_status == "MANUAL_REQUIRED":
        _manual_task(db, item, result_reason or "浏览器任务需要人工核对")

    if is_reply and item.automatic:
        _update_customer_service_controls_after_reply(
            db,
            succeeded=result_status == "SUCCEEDED",
            reason=result_reason,
        )

    agent = db.get(BrowserBridgeAgent, payload.bridge_id)
    if agent is not None:
        agent.last_seen_at = datetime.now(UTC)
        agent.current_url = payload.current_url
        agent.last_error = result_reason if result_status != "SUCCEEDED" else None

    write_audit(
        db,
        action="XIANYU_BROWSER_TASK_RESULT",
        entity_type="PLATFORM_ACTION",
        entity_id=item.id,
        actor_type="BROWSER_BRIDGE",
        after_data={
            "status": result_status,
            "operation": OPERATION_BY_ACTION.get(item.action_type),
            "filled_fields": list(payload.filled_fields),
            "missing_fields": list(payload.missing_fields),
            "risk_detected": payload.risk_detected,
            "submission_attempted": payload.submission_attempted,
            "success_evidence": payload.success_evidence,
            "external_product_id": payload.external_product_id,
            "metric_date": payload.metric_date.isoformat() if payload.metric_date else None,
            "metric_rows": len(payload.metrics),
            "metrics_duplicate_rows_skipped": payload.metrics_duplicate_rows_skipped,
            "metrics_pages": payload.metrics_pages,
            "conversation_rows": len(payload.conversations),
            "reply_sent": payload.reply_sent,
            "external_conversation_id": payload.external_conversation_id,
            "source_message_id": payload.source_message_id,
            "reply_evidence_verified": bool(
                is_reply and result_status == "SUCCEEDED"
            ),
            "error_code": result_error_code,
        },
        result=result_status,
        correlation_id=correlation_id,
    )
    db.commit()
    db.refresh(item)
    return item


def cancel_bridge_task(
    db: Session,
    *,
    task_id: int,
    user,
    correlation_id: str,
) -> PlatformActionRecord:
    item = db.get(PlatformActionRecord, task_id)
    if item is None or item.provider != BRIDGE_PROVIDER or item.requested_by_user_id != user.id:
        raise LookupError("浏览器任务不存在")
    if item.status != "QUEUED":
        raise ValueError("只有尚未领取的任务可以取消")
    item.status = "CANCELLED"
    write_audit(
        db,
        action="XIANYU_BROWSER_TASK_CANCELLED",
        entity_type="PLATFORM_ACTION",
        entity_id=item.id,
        actor_user_id=user.id,
        actor_type="USER",
        correlation_id=correlation_id,
    )
    db.commit()
    db.refresh(item)
    return item


def bridge_overview(db: Session, *, user) -> dict:
    agents = db.scalars(
        select(BrowserBridgeAgent).order_by(BrowserBridgeAgent.last_seen_at.desc()).limit(20)
    ).all()
    tasks = db.scalars(
        select(PlatformActionRecord)
        .where(
            PlatformActionRecord.provider == BRIDGE_PROVIDER,
            PlatformActionRecord.requested_by_user_id == user.id,
        )
        .order_by(PlatformActionRecord.created_at.desc(), PlatformActionRecord.id.desc())
        .limit(100)
    ).all()
    now = datetime.now(UTC)
    publish_enabled = bool(getattr(get_settings(), "xianyu_browser_publish_enabled", False))
    customer_service_enabled = bool(
        getattr(get_settings(), "xianyu_browser_customer_service_enabled", False)
    )
    automatic_ready, automatic_blockers = _publish_controls_ready(db)
    customer_service_blockers = automatic_customer_service_blockers(
        db,
        enforce_action_limits=True,
    )
    automatic_customer_service_ready = not customer_service_blockers
    return {
        "configured": configured_bridge_token() is not None,
        "agents": [serialize_bridge_agent(item, now=now) for item in agents],
        "tasks": [serialize_bridge_task(item) for item in tasks],
        "safety": {
            "external_submission": publish_enabled or customer_service_enabled,
            "publish_submission_enabled": publish_enabled,
            "customer_service_submission_enabled": customer_service_enabled,
            "arbitrary_scripts": False,
            "cookies_exported": False,
            "human_confirmation_required": not (
                (publish_enabled and automatic_ready)
                or automatic_customer_service_ready
            ),
            "automatic_publish_ready": publish_enabled and automatic_ready,
            "automatic_publish_blockers": (
                []
                if publish_enabled and automatic_ready
                else (["浏览器最终发布硬开关未启用"] if not publish_enabled else [])
                + automatic_blockers
            ),
            "automatic_customer_service_ready": automatic_customer_service_ready,
            "automatic_customer_service_blockers": customer_service_blockers,
            "lease_seconds": LEASE_SECONDS,
            "max_attempts": MAX_ATTEMPTS,
        },
    }
