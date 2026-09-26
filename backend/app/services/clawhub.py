import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from adapters.base import AdapterResult, AdapterStatus, Money
from adapters.xianyu.port import (
    PublishRequest,
    SendMessageRequest,
    ShipOrderRequest,
    UpdateProductRequest,
)
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    Account,
    AdapterCall,
    AutomationControl,
    AutomationScope,
    Conversation,
    ManualTask,
    Message,
    Order,
    OrderStatus,
    PlatformActionRecord,
    Product,
    ProductDiagnosis,
    ProductEvaluation,
    ProductScore,
    SourcingCandidate,
    SupplierCandidate,
    SupplierDiscoveryJob,
    SupplierInquiry,
    SupplierInquiryMessage,
    SupplierProductMatch,
    SupplierReliabilityScore,
    User,
    XianyuDraft,
    XianyuProduct,
    XianyuStatus,
)
from app.services.adapter_factory import get_xianyu_adapter
from app.services.audit import write_audit
from app.services.catalog import product_query_options
from app.services.publication_guard import (
    SELLER_SERVICE_COPY,
    assert_product_publishable,
    publication_copy_blockers,
    publication_provenance_blockers,
    sanitize_publication_source_text,
)


def _hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _decimal(value, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _score(value) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), _decimal(value)))


def _grade(total: Decimal) -> str:
    if total >= 85:
        return "S"
    if total >= 70:
        return "A"
    if total >= 55:
        return "B"
    return "C"


def serialize_supplier(item: SupplierCandidate) -> dict:
    return {
        "id": item.id,
        "external_supplier_id": item.external_supplier_id,
        "name": item.name,
        "source": item.source,
        "source_version": item.source_version,
        "region": item.region,
        "industry": item.industry,
        "factory_tags": item.factory_tags,
        "source_product_count": item.source_product_count,
        "suitability_score": str(item.suitability_score),
        "response_speed_score": (
            str(item.response_speed_score) if item.response_speed_score is not None else None
        ),
        "delivery_score": str(item.delivery_score) if item.delivery_score is not None else None,
        "status": item.status,
        "last_verified_at": item.last_verified_at,
    }


def serialize_action(item: PlatformActionRecord) -> dict:
    return {
        "id": item.id,
        "action_type": item.action_type,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "preview": item.preview,
        "status": item.status,
        "requires_confirmation": item.requires_confirmation,
        "automatic": item.automatic,
        "provider": item.provider,
        "attempts": item.attempts,
        "execution_result": item.execution_result,
        "error_code": item.error_code,
        "error_message": item.error_message,
        "correlation_id": item.correlation_id,
        "idempotency_key": item.idempotency_key,
        "approved_at": item.approved_at,
        "executed_at": item.executed_at,
        "created_at": item.created_at,
    }


def serialize_evaluation(item: ProductEvaluation) -> dict:
    return {
        "id": item.id,
        "product_id": item.product_id,
        "sourcing_candidate_id": item.sourcing_candidate_id,
        "stage": item.stage,
        "grade": item.grade,
        "total_score": str(item.total_score),
        "dimensions": item.dimensions,
        "evidence": item.evidence,
        "engine_version": item.engine_version,
        "created_at": item.created_at,
    }


def serialize_diagnosis(item: ProductDiagnosis) -> dict:
    return {
        "id": item.id,
        "product_id": item.product_id,
        "sourcing_candidate_id": item.sourcing_candidate_id,
        "diagnosis": item.diagnosis,
        "severity": item.severity,
        "evidence": item.evidence,
        "recommended_actions": item.recommended_actions,
        "status": item.status,
        "created_at": item.created_at,
    }


def serialize_draft(item: XianyuDraft) -> dict:
    return {
        "id": item.id,
        "product_id": item.product_id,
        "version": item.version,
        "title": item.title,
        "description": item.description,
        "price": str(item.price),
        "category": item.category,
        "attributes": item.attributes,
        "images": item.images,
        "validation": item.validation,
        "pipeline_trace": item.pipeline_trace,
        "status": item.status,
        "created_at": item.created_at,
    }


def serialize_inquiry(item: SupplierInquiry) -> dict:
    return {
        "id": item.id,
        "supplier_candidate_id": item.supplier_candidate_id,
        "sourcing_candidate_id": item.sourcing_candidate_id,
        "topic": item.topic,
        "questions": item.questions,
        "status": item.status,
        "upstream_task_id": item.upstream_task_id,
        "result": item.result,
        "due_at": item.due_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def compare_candidates(db: Session, candidate_ids: list[int]) -> dict:
    items = db.scalars(
        select(SourcingCandidate).where(SourcingCandidate.id.in_(candidate_ids))
    ).all()
    if len(items) != len(candidate_ids):
        found = {item.id for item in items}
        missing = sorted(set(candidate_ids) - found)
        raise LookupError(f"选品候选不存在: {missing}")

    def rank(item: SourcingCandidate):
        completeness = sum(
            (
                item.minimum_price is not None,
                item.stock is not None,
                bool(item.external_supplier_id),
                bool(item.category),
                item.sku_count > 0,
            )
        )
        return (
            _decimal(item.score),
            completeness,
            -_decimal(item.minimum_price, Decimal("999999")),
        )

    ordered = sorted(items, key=rank, reverse=True)
    rows = []
    for index, item in enumerate(ordered, start=1):
        rows.append(
            {
                "rank": index,
                "candidate_id": item.id,
                "title": item.title,
                "supplier_name": item.supplier_name,
                "minimum_price": (
                    str(item.minimum_price) if item.minimum_price is not None else None
                ),
                "maximum_price": (
                    str(item.maximum_price) if item.maximum_price is not None else None
                ),
                "stock": item.stock,
                "sku_count": item.sku_count,
                "score": str(item.score) if item.score is not None else None,
                "status": item.status,
                "evidence_completeness": sum(
                    (
                        item.minimum_price is not None,
                        item.stock is not None,
                        bool(item.external_supplier_id),
                        bool(item.category),
                        item.sku_count > 0,
                    )
                )
                * 20,
                "recommended": index == 1 and item.status != "EXCLUDED",
            }
        )
    return {"items": rows, "recommended_candidate_id": rows[0]["candidate_id"]}


def _candidate_suitability(item: SourcingCandidate) -> Decimal:
    if item.score is not None:
        return _score(item.score)
    return Decimal(
        (25 if item.minimum_price is not None else 0)
        + (20 if item.stock is not None else 0)
        + (20 if item.sku_count > 0 else 0)
        + (20 if item.external_supplier_id and item.supplier_name else 0)
        + (15 if item.category else 0)
    )


def discover_suppliers(
    db: Session,
    *,
    sourcing_candidate_id: int | None,
    query: str | None,
    limit: int,
    user_id: int,
    correlation_id: str,
) -> dict:
    base = db.get(SourcingCandidate, sourcing_candidate_id) if sourcing_candidate_id else None
    if sourcing_candidate_id and base is None:
        raise LookupError("选品候选不存在")
    search_term = query or (base.title if base else "")
    job = SupplierDiscoveryJob(
        query=search_term[:300],
        sourcing_candidate_id=sourcing_candidate_id,
        source="local-fact-pool",
        status="RUNNING",
        requested_by_user_id=user_id,
        correlation_id=correlation_id,
    )
    db.add(job)
    db.flush()

    statement = select(SourcingCandidate).where(
        SourcingCandidate.external_supplier_id.is_not(None),
        SourcingCandidate.supplier_name.is_not(None),
    )
    if base and base.category:
        statement = statement.where(SourcingCandidate.category == base.category)
    elif query:
        statement = statement.where(SourcingCandidate.title.ilike(f"%{query}%"))
    found = db.scalars(
        statement.order_by(SourcingCandidate.score.desc().nullslast()).limit(limit * 3)
    ).all()

    suppliers: list[SupplierCandidate] = []
    seen: set[tuple[str, str]] = set()
    for candidate in found:
        key = (candidate.adapter_name, str(candidate.external_supplier_id))
        if key in seen:
            continue
        seen.add(key)
        normalized = candidate.normalized_data or {}
        stats = normalized.get("stats") or {}
        factory_tags = stats.get("factory_tags") or stats.get("tags") or []
        if not isinstance(factory_tags, list):
            factory_tags = []
        item = db.scalar(
            select(SupplierCandidate).where(
                SupplierCandidate.source == candidate.adapter_name,
                SupplierCandidate.external_supplier_id == str(candidate.external_supplier_id),
            )
        )
        suitability = _candidate_suitability(candidate)
        count = db.scalar(
            select(func.count(SourcingCandidate.id)).where(
                SourcingCandidate.adapter_name == candidate.adapter_name,
                SourcingCandidate.external_supplier_id == candidate.external_supplier_id,
            )
        )
        if item is None:
            item = SupplierCandidate(
                external_supplier_id=str(candidate.external_supplier_id),
                name=str(candidate.supplier_name),
                source=candidate.adapter_name,
            )
            db.add(item)
        item.name = str(candidate.supplier_name)
        item.source_version = candidate.adapter_version
        item.region = stats.get("region")
        item.industry = candidate.category
        item.factory_tags = factory_tags
        item.source_product_count = int(count or 0)
        item.suitability_score = suitability
        item.raw_snapshot = {
            "source_candidate_id": candidate.id,
            "evidence_fields": sorted(stats.keys()),
        }
        item.last_verified_at = candidate.last_fetched_at
        db.flush()
        reliability = db.scalar(
            select(SupplierReliabilityScore).where(
                SupplierReliabilityScore.supplier_candidate_id == item.id,
                SupplierReliabilityScore.engine_version == "1.0",
            )
        )
        dimensions = {
            "catalog_evidence": str(suitability),
            "response_speed": None,
            "delivery": None,
        }
        if reliability is None:
            db.add(
                SupplierReliabilityScore(
                    supplier_candidate_id=item.id,
                    total_score=suitability,
                    dimensions=dimensions,
                    evidence={
                        "source_candidate_id": candidate.id,
                        "unverified": ["response_speed", "delivery"],
                    },
                )
            )
        target_id = sourcing_candidate_id or candidate.id
        match = db.scalar(
            select(SupplierProductMatch).where(
                SupplierProductMatch.sourcing_candidate_id == target_id,
                SupplierProductMatch.supplier_candidate_id == item.id,
                SupplierProductMatch.external_product_id == candidate.external_product_id,
            )
        )
        if match is None:
            match = SupplierProductMatch(
                sourcing_candidate_id=target_id,
                supplier_candidate_id=item.id,
                external_product_id=candidate.external_product_id,
            )
            db.add(match)
        match.unit_price = candidate.minimum_price
        match.stock = candidate.stock
        match.match_score = suitability
        match.priority = len(suppliers) + 1
        match.is_primary = len(suppliers) == 0
        match.evidence = {
            "source_candidate_id": candidate.id,
            "adapter": candidate.adapter_name,
            "last_fetched_at": candidate.last_fetched_at.isoformat(),
        }
        suppliers.append(item)
        if len(suppliers) >= limit:
            break

    job.status = "SUCCEEDED"
    job.result_count = len(suppliers)
    write_audit(
        db,
        action="SUPPLIER_DISCOVERY_COMPLETED",
        entity_type="SUPPLIER_DISCOVERY_JOB",
        entity_id=job.id,
        actor_user_id=user_id,
        actor_type="USER",
        after_data={"result_count": len(suppliers), "source": "local-fact-pool"},
        correlation_id=correlation_id,
    )
    db.commit()
    return {
        "job_id": job.id,
        "source": "local-fact-pool",
        "items": [serialize_supplier(item) for item in suppliers],
    }


def _target_exists(db: Session, target_type: str, target_id: int) -> bool:
    models = {
        "PRODUCT": Product,
        "XIANYU_DRAFT": XianyuDraft,
        "ORDER": Order,
        "CONVERSATION": Conversation,
    }
    model = models.get(target_type)
    return bool(model and db.get(model, target_id) is not None)


ACTION_SCOPE = {
    "PUBLISH_XIANYU": AutomationScope.PUBLISH,
    "UPDATE_PRICE": AutomationScope.REPRICING,
    "REMOVE_LISTING": AutomationScope.REPRICING,
    "SEND_REPLY": AutomationScope.CUSTOMER_SERVICE,
    "CREATE_PURCHASE": AutomationScope.PURCHASE,
    "FILL_TRACKING": AutomationScope.LOGISTICS,
    "CANCEL_ORDER": AutomationScope.PURCHASE,
    "REFUND_ORDER": AutomationScope.PURCHASE,
    "CHANGE_SUPPLIER": AutomationScope.PURCHASE,
}
EXTERNAL_WRITE_ACTIONS = {
    "PUBLISH_XIANYU",
    "UPDATE_PRICE",
    "REMOVE_LISTING",
    "SEND_REPLY",
    "FILL_TRACKING",
}
HUMAN_ONLY_ACTIONS = {
    "CREATE_PURCHASE": "1688 下单与支付尚无已授权的交易 Adapter",
    "CANCEL_ORDER": "取消订单必须由人工核对买卖双方状态",
    "REFUND_ORDER": "退款和售后争议禁止无人值守执行",
    "CHANGE_SUPPLIER": "换供应商会改变成本、规格与履约承诺，必须人工复核",
}
TARGETS_BY_ACTION = {
    "PUBLISH_XIANYU": {"PRODUCT", "XIANYU_DRAFT"},
    "UPDATE_PRICE": {"PRODUCT"},
    "REMOVE_LISTING": {"PRODUCT"},
    "SEND_REPLY": {"CONVERSATION"},
    "CREATE_PURCHASE": {"ORDER"},
    "FILL_TRACKING": {"ORDER"},
    "CANCEL_ORDER": {"ORDER"},
    "REFUND_ORDER": {"ORDER"},
    "CHANGE_SUPPLIER": {"ORDER", "PRODUCT"},
}


def _target_owned_by_user(
    db: Session,
    *,
    action_type: str,
    target_type: str,
    target_id: int,
    user_id: int,
) -> bool:
    """Scope platform-bound targets to the user who owns their Xianyu account.

    Catalog products are shared until they acquire a user-owned draft or Xianyu
    listing.  Once platform state exists, actions must use the matching owner's
    draft/listing.  Conversation and order ownership always comes from Account.
    """
    if target_type == "XIANYU_DRAFT":
        return (
            db.scalar(
                select(XianyuDraft.id).where(
                    XianyuDraft.id == target_id,
                    XianyuDraft.created_by_user_id == user_id,
                )
            )
            is not None
        )
    if target_type == "CONVERSATION":
        return (
            db.scalar(
                select(Conversation.id)
                .join(Account, Account.id == Conversation.account_id)
                .where(
                    Conversation.id == target_id,
                    Account.owner_user_id == user_id,
                )
            )
            is not None
        )
    if target_type == "ORDER":
        return (
            db.scalar(
                select(Order.id)
                .join(Account, Account.id == Order.account_id)
                .where(Order.id == target_id, Account.owner_user_id == user_id)
            )
            is not None
        )
    if target_type != "PRODUCT":
        return False
    if action_type == "PUBLISH_XIANYU":
        any_draft = db.scalar(
            select(XianyuDraft.id).where(XianyuDraft.product_id == target_id).limit(1)
        )
        if any_draft is None:
            return True
        return (
            db.scalar(
                select(XianyuDraft.id)
                .where(
                    XianyuDraft.product_id == target_id,
                    XianyuDraft.created_by_user_id == user_id,
                )
                .limit(1)
            )
            is not None
        )
    if action_type in {"UPDATE_PRICE", "REMOVE_LISTING"}:
        any_listing = db.scalar(
            select(XianyuProduct.id).where(XianyuProduct.product_id == target_id).limit(1)
        )
        if any_listing is None:
            return True
        return (
            db.scalar(
                select(XianyuProduct.id)
                .join(Account, Account.id == XianyuProduct.account_id)
                .where(
                    XianyuProduct.product_id == target_id,
                    Account.owner_user_id == user_id,
                )
                .limit(1)
            )
            is not None
        )
    return True


def _admin_override_allowed(db: Session, *, user_id: int, explicit: bool) -> bool:
    if not explicit:
        return False
    actor = db.get(User, user_id)
    return bool(actor and actor.is_active and actor.role == "admin")


def _audit_admin_override(
    db: Session,
    *,
    item: PlatformActionRecord,
    actor_user_id: int,
    correlation_id: str,
    stage: str,
) -> None:
    write_audit(
        db,
        action="PLATFORM_ACTION_ADMIN_OVERRIDE",
        entity_type="PLATFORM_ACTION_RECORD",
        entity_id=item.id,
        actor_user_id=actor_user_id,
        actor_type="USER",
        after_data={
            "stage": stage,
            "requested_by_user_id": item.requested_by_user_id,
            "action_type": item.action_type,
        },
        correlation_id=correlation_id,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _action_product(db: Session, target_type: str, target_id: int) -> Product | None:
    if target_type == "PRODUCT":
        return db.scalar(
            select(Product).where(Product.id == target_id).options(*product_query_options())
        )
    if target_type == "XIANYU_DRAFT":
        draft = db.get(XianyuDraft, target_id)
        if draft:
            return db.scalar(
                select(Product)
                .where(Product.id == draft.product_id)
                .options(*product_query_options())
            )
    return None


def _latest_draft(
    db: Session,
    target_type: str,
    target_id: int,
    *,
    owner_user_id: int | None = None,
) -> XianyuDraft | None:
    if target_type == "XIANYU_DRAFT":
        query = select(XianyuDraft).where(XianyuDraft.id == target_id)
    else:
        query = select(XianyuDraft).where(XianyuDraft.product_id == target_id)
    if owner_user_id is not None:
        query = query.where(XianyuDraft.created_by_user_id == owner_user_id)
    return db.scalar(query.order_by(XianyuDraft.created_at.desc(), XianyuDraft.id.desc()))


def _xianyu_link(
    db: Session,
    product_id: int,
    *,
    owner_user_id: int | None = None,
) -> XianyuProduct | None:
    query = select(XianyuProduct).where(XianyuProduct.product_id == product_id)
    if owner_user_id is not None:
        query = query.join(Account, Account.id == XianyuProduct.account_id).where(
            Account.owner_user_id == owner_user_id
        )
    return db.scalar(query.order_by(XianyuProduct.updated_at.desc(), XianyuProduct.id.desc()))


def _action_validation(
    db: Session,
    *,
    action_type: str,
    target_type: str,
    target_id: int,
    payload: dict,
    owner_user_id: int,
) -> tuple[list[str], list[str], dict]:
    blockers: list[str] = []
    warnings: list[str] = []
    expected: dict = {"action": action_type, "target": f"{target_type}:{target_id}"}
    if target_type not in TARGETS_BY_ACTION[action_type]:
        blockers.append(f"{action_type} 不支持目标类型 {target_type}")
        return blockers, warnings, expected
    if action_type in HUMAN_ONLY_ACTIONS:
        blockers.append(HUMAN_ONLY_ACTIONS[action_type])
        return blockers, warnings, expected

    if action_type == "PUBLISH_XIANYU":
        product = _action_product(db, target_type, target_id)
        if product is None:
            blockers.append("发布目标缺少关联商品")
        else:
            blockers.extend(publication_provenance_blockers(product))
        draft = _latest_draft(
            db,
            target_type,
            target_id,
            owner_user_id=owner_user_id,
        )
        if draft is None:
            blockers.append("缺少闲鱼发布草稿")
        elif draft.status != "REVIEW_READY":
            blockers.append("发布草稿尚未通过风险校验")
        elif product is not None:
            blockers.extend(publication_copy_blockers(draft.title, draft.description))
            floor = max((sku.minimum_sale_price for sku in product.skus), default=None)
            if floor is not None and draft.price < floor:
                blockers.append("草稿价格低于商品最低安全售价")
            if not draft.images:
                warnings.append("发布草稿没有图片；Adapter 可能要求人工补图")
            expected.update({"draft_id": draft.id, "price": str(draft.price)})
    elif action_type == "UPDATE_PRICE":
        product = _action_product(db, target_type, target_id)
        price = _decimal(payload.get("price"), Decimal("-1"))
        if price <= 0:
            blockers.append("改价必须提供大于 0 的 price")
        floor = max((sku.minimum_sale_price for sku in product.skus), default=None)
        if floor is None:
            blockers.append("商品缺少可核验的最低安全售价")
        elif price < floor:
            blockers.append(f"新价格低于最低安全售价 {floor}")
        if _xianyu_link(db, product.id, owner_user_id=owner_user_id) is None:
            blockers.append("商品尚未绑定闲鱼商品 ID")
        expected["price"] = str(price)
    elif action_type == "REMOVE_LISTING":
        product = _action_product(db, target_type, target_id)
        if _xianyu_link(db, product.id, owner_user_id=owner_user_id) is None:
            blockers.append("商品尚未绑定闲鱼商品 ID")
    elif action_type == "SEND_REPLY":
        conversation = db.get(Conversation, target_id)
        text = str(payload.get("text") or "").strip()
        recipient_id = str(payload.get("recipient_id") or "").strip()
        if conversation.manual_mode:
            blockers.append("会话已切换人工接管模式")
        if not text or len(text) > 2000:
            blockers.append("回复 text 长度必须为 1 到 2000 个字符")
        if not recipient_id:
            blockers.append("回复缺少 recipient_id")
        expected["message_length"] = len(text)
    elif action_type == "FILL_TRACKING":
        order = db.get(Order, target_id)
        if order.status not in {
            OrderStatus.PURCHASED,
            OrderStatus.SUPPLIER_SHIPPED,
            OrderStatus.LOGISTICS_ERROR,
        }:
            blockers.append(f"订单状态 {order.status} 不允许填写物流")
        for field in ("carrier", "tracking_number_ref"):
            if not str(payload.get(field) or "").strip():
                blockers.append(f"发货缺少 {field}")
        expected["external_order_id"] = order.external_order_id
    return blockers, warnings, expected


def _policy_controls(
    db: Session, scope: AutomationScope
) -> tuple[AutomationControl | None, AutomationControl | None]:
    controls = db.scalars(
        select(AutomationControl).where(
            AutomationControl.scope.in_([AutomationScope.GLOBAL, scope])
        )
    ).all()
    by_scope = {item.scope: item for item in controls}
    return by_scope.get(AutomationScope.GLOBAL), by_scope.get(scope)


def _policy_blockers(
    db: Session,
    *,
    action_type: str,
    now: datetime,
) -> tuple[list[str], AutomationControl | None, AutomationControl | None]:
    scope = ACTION_SCOPE[action_type]
    global_control, scope_control = _policy_controls(db, scope)
    blockers = []
    if global_control is None or scope_control is None:
        blockers.append("自动化控制策略未初始化")
        return blockers, global_control, scope_control
    for control, label in ((global_control, "全局"), (scope_control, scope.value)):
        if not control.enabled:
            blockers.append(f"{label} 自动化开关未启用")
        cooldown = _as_utc(control.cooldown_until)
        if cooldown and cooldown > now:
            blockers.append(f"{label} 自动化熔断至 {cooldown.isoformat()}")
        last_executed = _as_utc(control.last_executed_at)
        if last_executed and (now - last_executed).total_seconds() < control.min_interval_seconds:
            blockers.append(f"{label} 动作最小间隔尚未满足")
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if control.scope == AutomationScope.GLOBAL:
            count = db.scalar(
                select(func.count(PlatformActionRecord.id)).where(
                    PlatformActionRecord.status == "SUCCEEDED",
                    PlatformActionRecord.executed_at >= since,
                )
            )
        else:
            action_types = [
                name for name, action_scope in ACTION_SCOPE.items() if action_scope == control.scope
            ]
            count = db.scalar(
                select(func.count(PlatformActionRecord.id)).where(
                    PlatformActionRecord.action_type.in_(action_types),
                    PlatformActionRecord.status == "SUCCEEDED",
                    PlatformActionRecord.executed_at >= since,
                )
            )
        if int(count or 0) >= control.daily_limit:
            blockers.append(f"{label} 今日动作上限已达到")
    return blockers, global_control, scope_control


def preview_action(
    db: Session,
    *,
    action_type: str,
    target_type: str,
    target_id: int,
    payload: dict,
    idempotency_key: str | None,
    user_id: int,
    correlation_id: str,
    allow_admin_override: bool = False,
) -> PlatformActionRecord:
    if not _target_exists(db, target_type, target_id):
        raise LookupError("动作目标不存在")
    key = idempotency_key or _hash(
        {
            "action_type": action_type,
            "target_type": target_type,
            "target_id": target_id,
            "payload": payload,
            "user_id": user_id,
        }
    )
    existing = db.scalar(
        select(PlatformActionRecord).where(PlatformActionRecord.idempotency_key == key)
    )
    if existing:
        if (
            existing.action_type != action_type
            or existing.target_type != target_type
            or existing.target_id != target_id
            or (existing.request_payload or {}) != payload
        ):
            raise ValueError("幂等键与现有动作请求不一致")
        if not _target_owned_by_user(
            db,
            action_type=existing.action_type,
            target_type=existing.target_type,
            target_id=existing.target_id,
            user_id=existing.requested_by_user_id,
        ):
            raise LookupError("动作目标不存在或无权访问")
        if existing.requested_by_user_id != user_id:
            if not _admin_override_allowed(
                db,
                user_id=user_id,
                explicit=allow_admin_override,
            ):
                raise LookupError("动作记录不存在")
            _audit_admin_override(
                db,
                item=existing,
                actor_user_id=user_id,
                correlation_id=correlation_id,
                stage="PREVIEW_IDEMPOTENT_READ",
            )
            db.commit()
        return existing
    if not _target_owned_by_user(
        db,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        user_id=user_id,
    ):
        raise LookupError("动作目标不存在或无权访问")
    settings = get_settings()
    now = datetime.now(UTC)
    blockers, warnings, expected_changes = _action_validation(
        db,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        owner_user_id=user_id,
    )
    policy_blockers, _global_control, scope_control = _policy_blockers(
        db, action_type=action_type, now=now
    )
    blockers.extend(policy_blockers)
    if not settings.adapters_enabled:
        blockers.append("AUTOFISH_ADAPTERS_ENABLED=false")
    if not settings.adapter_write_enabled:
        blockers.append("AUTOFISH_ADAPTER_WRITE_ENABLED=false")
    if settings.xianyu_adapter == "disabled":
        blockers.append("闲鱼写入 Adapter 尚未配置")
    executable = action_type in EXTERNAL_WRITE_ACTIONS and not blockers
    automatic = bool(executable and scope_control and scope_control.mode == "AUTOMATIC")
    preview = {
        "executable": executable,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": warnings,
        "expected_changes": expected_changes,
        "scope": ACTION_SCOPE[action_type].value,
        "mode": scope_control.mode if scope_control else "BLOCKED",
    }
    item = PlatformActionRecord(
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        request_payload=payload,
        preview=preview,
        status="PREVIEWED" if executable else "BLOCKED",
        requires_confirmation=not automatic,
        automatic=automatic,
        provider=settings.xianyu_adapter,
        correlation_id=correlation_id,
        idempotency_key=key,
        requested_by_user_id=user_id,
    )
    db.add(item)
    db.flush()
    write_audit(
        db,
        action="PLATFORM_ACTION_PREVIEWED",
        entity_type="PLATFORM_ACTION_RECORD",
        entity_id=item.id,
        actor_user_id=user_id,
        actor_type="USER",
        after_data={"status": item.status, "action_type": action_type},
        correlation_id=correlation_id,
    )
    db.commit()
    return item


def _manual_task(db: Session, item: PlatformActionRecord, reason: str) -> None:
    existing = db.scalar(
        select(ManualTask).where(
            ManualTask.entity_type == "PLATFORM_ACTION_RECORD",
            ManualTask.entity_id == str(item.id),
            ManualTask.status == "OPEN",
        )
    )
    if existing is None:
        db.add(
            ManualTask(
                type="PLATFORM_ACTION_REVIEW",
                title=f"平台动作需要人工处理：{item.action_type}",
                reason=reason[:2000],
                priority=90 if item.action_type in {"FILL_TRACKING", "SEND_REPLY"} else 75,
                entity_type="PLATFORM_ACTION_RECORD",
                entity_id=str(item.id),
            )
        )


def _record_adapter_call(
    db: Session,
    *,
    item: PlatformActionRecord,
    operation: str,
    result: AdapterResult,
    duration_ms: int,
) -> None:
    db.add(
        AdapterCall(
            correlation_id=item.correlation_id or "platform-action",
            adapter=result.source,
            adapter_version=result.source_version,
            operation=operation,
            status=result.status.value,
            duration_ms=duration_ms,
            error_code=result.error_code,
            raw_snapshot_hash=result.raw_snapshot_hash,
            safe_summary={
                "action_id": item.id,
                "action_type": item.action_type,
                "retryable": result.retryable,
            },
        )
    )


async def _ensure_account(db: Session, adapter, item: PlatformActionRecord) -> Account | None:
    account = db.scalar(
        select(Account)
        .where(
            Account.platform == "XIANYU",
            Account.is_enabled.is_(True),
            Account.owner_user_id == item.requested_by_user_id,
        )
        .order_by(Account.id)
    )
    if account:
        return account
    started = time.monotonic()
    result = await adapter.get_account()
    _record_adapter_call(
        db,
        item=item,
        operation="GET_ACCOUNT_FOR_WRITE",
        result=result,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    if result.status != AdapterStatus.OK or result.data is None:
        return None
    account = db.scalar(
        select(Account).where(
            Account.platform == "XIANYU",
            Account.external_account_id == result.data.external_account_id,
        )
    )
    if account is None:
        account = Account(
            platform="XIANYU",
            external_account_id=result.data.external_account_id,
            nickname=result.data.nickname,
        )
        db.add(account)
    elif account.owner_user_id not in {None, item.requested_by_user_id}:
        return None
    account.nickname = result.data.nickname
    account.status = result.data.status
    account.is_enabled = True
    account.owner_user_id = account.owner_user_id or item.requested_by_user_id
    account.last_synced_at = datetime.now(UTC)
    db.flush()
    return account


async def _dispatch_action(db: Session, adapter, item: PlatformActionRecord) -> AdapterResult:
    payload = item.request_payload or {}
    if item.action_type == "PUBLISH_XIANYU":
        draft = _latest_draft(
            db,
            item.target_type,
            item.target_id,
            owner_user_id=item.requested_by_user_id,
        )
        return await adapter.publish_product(
            PublishRequest(
                internal_product_id=draft.product_id,
                title=draft.title,
                description=draft.description,
                price=Money(amount=str(draft.price)),
                image_refs=draft.images,
                idempotency_key=item.idempotency_key,
                original_price=(
                    Money(amount=str(payload["original_price"]))
                    if payload.get("original_price")
                    else None
                ),
                delivery=str(payload.get("delivery") or "无需邮寄"),
                post_price=(
                    Money(amount=str(payload["post_price"]))
                    if payload.get("post_price") is not None
                    else None
                ),
                can_self_pickup=bool(payload.get("can_self_pickup", True)),
                platform_payload=payload.get("platform_payload") or {},
            )
        )
    product = _action_product(db, item.target_type, item.target_id)
    if item.action_type in {"UPDATE_PRICE", "REMOVE_LISTING"}:
        link = _xianyu_link(
            db,
            product.id,
            owner_user_id=item.requested_by_user_id,
        )
        if item.action_type == "UPDATE_PRICE":
            return await adapter.update_product(
                UpdateProductRequest(
                    external_product_id=link.external_product_id,
                    fields={"price": str(_decimal(payload.get("price")))},
                    idempotency_key=item.idempotency_key,
                    platform_payload=payload.get("platform_payload") or {},
                )
            )
        return await adapter.delete_product(link.external_product_id)
    if item.action_type == "SEND_REPLY":
        conversation = db.get(Conversation, item.target_id)
        return await adapter.send_message(
            SendMessageRequest(
                external_conversation_id=conversation.external_conversation_id,
                recipient_id=str(payload["recipient_id"]),
                text=str(payload["text"]).strip(),
                idempotency_key=item.idempotency_key,
            )
        )
    order = db.get(Order, item.target_id)
    return await adapter.ship_order(
        ShipOrderRequest(
            external_order_id=order.external_order_id,
            carrier=str(payload["carrier"]),
            tracking_number_ref=str(payload["tracking_number_ref"]),
            logistics_code=payload.get("logistics_code"),
            sender_name=payload.get("sender_name"),
            sender_phone=payload.get("sender_phone"),
            sender_address=payload.get("sender_address"),
            sender_division_id=payload.get("sender_division_id"),
            idempotency_key=item.idempotency_key,
            platform_payload=payload.get("platform_payload") or {},
        )
    )


def _safe_result(result: AdapterResult) -> dict:
    data = result.data
    summary: dict = {"status": result.status.value, "source": result.source}
    for field in ("external_product_id", "external_message_id", "external_order_id", "status"):
        value = getattr(data, field, None) if data is not None else None
        if value is not None:
            summary[field] = str(value)
    return summary


def _apply_success(
    db: Session,
    *,
    item: PlatformActionRecord,
    result: AdapterResult,
    account: Account | None,
    now: datetime,
) -> None:
    if item.action_type == "PUBLISH_XIANYU":
        draft = _latest_draft(
            db,
            item.target_type,
            item.target_id,
            owner_user_id=item.requested_by_user_id,
        )
        product = db.get(Product, draft.product_id)
        link = _xianyu_link(
            db,
            product.id,
            owner_user_id=item.requested_by_user_id,
        )
        if link is None:
            link = XianyuProduct(account_id=account.id, product_id=product.id)
            db.add(link)
        link.external_product_id = result.data.external_product_id
        link.status = result.data.status
        link.published_title = result.data.title
        link.published_price = _decimal(result.data.price.amount)
        link.last_synced_at = now
        draft.status = "PUBLISHED"
        product.xianyu_status = XianyuStatus.ACTIVE
    elif item.action_type == "UPDATE_PRICE":
        product = _action_product(db, item.target_type, item.target_id)
        link = _xianyu_link(
            db,
            product.id,
            owner_user_id=item.requested_by_user_id,
        )
        link.published_price = _decimal(item.request_payload.get("price"))
        link.last_synced_at = now
        for sku in product.skus:
            sku.current_sale_price = link.published_price
    elif item.action_type == "REMOVE_LISTING":
        product = _action_product(db, item.target_type, item.target_id)
        link = _xianyu_link(
            db,
            product.id,
            owner_user_id=item.requested_by_user_id,
        )
        link.status = "REMOVED"
        link.last_synced_at = now
        product.xianyu_status = XianyuStatus.REMOVED
    elif item.action_type == "SEND_REPLY":
        conversation = db.get(Conversation, item.target_id)
        db.add(
            Message(
                conversation_id=conversation.id,
                external_message_id=result.data.external_message_id,
                direction="OUTBOUND",
                role="SELLER",
                content=str(item.request_payload["text"]).strip(),
                content_type="TEXT",
                metadata_json={"platform_action_id": item.id},
                sent_by_ai=item.automatic,
                created_at=now,
            )
        )
        conversation.last_message_at = now
    elif item.action_type == "FILL_TRACKING":
        order = db.get(Order, item.target_id)
        order.status = OrderStatus.XIANYU_SHIPPED
        order.last_synced_at = now


def _update_policy_after_execution(
    controls: tuple[AutomationControl | None, AutomationControl | None],
    *,
    succeeded: bool,
    now: datetime,
    reason: str | None = None,
) -> None:
    for control in (value for value in controls if value is not None):
        control.last_executed_at = now
        if succeeded:
            control.consecutive_failures = 0
            control.stopped_reason = None
        else:
            control.consecutive_failures += 1
            control.stopped_reason = (reason or "平台动作执行失败")[:500]
            if control.consecutive_failures >= control.failure_threshold:
                control.cooldown_until = now + timedelta(minutes=15)


async def execute_action(
    db: Session,
    *,
    action_id: int,
    confirm: bool,
    user_id: int,
    correlation_id: str,
    allow_admin_override: bool = False,
) -> PlatformActionRecord:
    item = db.get(PlatformActionRecord, action_id)
    if item is None:
        raise LookupError("动作记录不存在")
    if not _target_owned_by_user(
        db,
        action_type=item.action_type,
        target_type=item.target_type,
        target_id=item.target_id,
        user_id=item.requested_by_user_id,
    ):
        raise LookupError("动作目标不存在或无权访问")
    admin_override = item.requested_by_user_id != user_id
    if admin_override:
        if not _admin_override_allowed(
            db,
            user_id=user_id,
            explicit=allow_admin_override,
        ):
            raise LookupError("动作记录不存在")
        _audit_admin_override(
            db,
            item=item,
            actor_user_id=user_id,
            correlation_id=correlation_id,
            stage="EXECUTE",
        )
        db.commit()
        item = db.get(PlatformActionRecord, action_id)
    if item.status == "SUCCEEDED":
        return item
    if item.status == "EXECUTING":
        raise ValueError("动作正在执行，请稍后查询结果")
    if item.status != "PREVIEWED" or not item.preview.get("executable"):
        return item
    if item.requires_confirmation and not confirm:
        raise ValueError("必须明确确认动作")
    now = datetime.now(UTC)
    settings = get_settings()
    validation_blockers, _warnings, _expected = _action_validation(
        db,
        action_type=item.action_type,
        target_type=item.target_type,
        target_id=item.target_id,
        payload=item.request_payload or {},
        owner_user_id=item.requested_by_user_id,
    )
    policy_blockers, global_control, scope_control = _policy_blockers(
        db, action_type=item.action_type, now=now
    )
    policy_blockers.extend(validation_blockers)
    if not settings.adapters_enabled or not settings.adapter_write_enabled:
        policy_blockers.append("平台写入总开关当前未启用")
    if policy_blockers:
        item.status = "BLOCKED"
        item.preview = {
            **item.preview,
            "executable": False,
            "blockers": list(dict.fromkeys([*item.preview.get("blockers", []), *policy_blockers])),
        }
        db.commit()
        return item
    acquired = db.execute(
        update(PlatformActionRecord)
        .where(
            PlatformActionRecord.id == item.id,
            PlatformActionRecord.status == "PREVIEWED",
        )
        .values(
            status="EXECUTING",
            attempts=PlatformActionRecord.attempts + 1,
            approved_by_user_id=user_id if confirm else item.approved_by_user_id,
            approved_at=now if confirm else item.approved_at,
            correlation_id=correlation_id,
        )
    )
    if acquired.rowcount != 1:
        db.rollback()
        current = db.get(PlatformActionRecord, action_id)
        if current.status == "SUCCEEDED":
            return current
        raise ValueError("动作已由另一执行器接管")
    db.commit()
    item = db.get(PlatformActionRecord, action_id)
    before = {"status": "PREVIEWED"}
    adapter = get_xianyu_adapter()
    account = None
    try:
        started = time.monotonic()
        health = await adapter.health()
        _record_adapter_call(
            db,
            item=item,
            operation="WRITE_HEALTH_CHECK",
            result=health,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        result = health
        if health.status == AdapterStatus.OK and health.data and health.data.authenticated:
            if item.action_type == "PUBLISH_XIANYU":
                account = await _ensure_account(db, adapter, item)
                if account is None:
                    result = AdapterResult(
                        status=AdapterStatus.AUTH_REQUIRED,
                        source=health.source,
                        source_version=health.source_version,
                        error_code="ACCOUNT_UNAVAILABLE",
                        safe_message="未能取得已授权的闲鱼账号",
                    )
                else:
                    started = time.monotonic()
                    result = await _dispatch_action(db, adapter, item)
                    _record_adapter_call(
                        db,
                        item=item,
                        operation=item.action_type,
                        result=result,
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
            else:
                started = time.monotonic()
                result = await _dispatch_action(db, adapter, item)
                _record_adapter_call(
                    db,
                    item=item,
                    operation=item.action_type,
                    result=result,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
        elif health.status == AdapterStatus.OK:
            result = AdapterResult(
                status=AdapterStatus.AUTH_REQUIRED,
                source=health.source,
                source_version=health.source_version,
                error_code="AUTH_REQUIRED",
                safe_message="闲鱼 Adapter 未通过登录授权检查",
            )
    except Exception:
        result = AdapterResult(
            status=AdapterStatus.TRANSIENT_ERROR,
            source=getattr(adapter, "source", "xianyu-adapter"),
            source_version=getattr(adapter, "source_version", "unknown"),
            retryable=False,
            error_code="UNEXPECTED_ADAPTER_ERROR",
            safe_message="平台动作发生未预期错误，已停止自动重试并转人工检查",
        )

    finished_at = datetime.now(UTC)
    item.provider = result.source
    item.execution_result = _safe_result(result)
    item.error_code = result.error_code
    item.error_message = result.safe_message
    item.executed_at = finished_at
    succeeded = result.status == AdapterStatus.OK
    if succeeded:
        _apply_success(db, item=item, result=result, account=account, now=finished_at)
        item.status = "SUCCEEDED"
    else:
        item.status = (
            "FAILED"
            if result.status in {AdapterStatus.PERMANENT_ERROR, AdapterStatus.NOT_FOUND}
            else "MANUAL_REQUIRED"
        )
        _manual_task(db, item, result.safe_message or "平台未确认动作成功")
    _update_policy_after_execution(
        (global_control, scope_control),
        succeeded=succeeded,
        now=finished_at,
        reason=result.safe_message,
    )
    write_audit(
        db,
        action="PLATFORM_ACTION_EXECUTED" if succeeded else "PLATFORM_ACTION_HANDOFF",
        entity_type="PLATFORM_ACTION_RECORD",
        entity_id=item.id,
        actor_user_id=user_id,
        actor_type="SYSTEM" if item.automatic and not confirm else "USER",
        before_data=before,
        after_data={
            "status": item.status,
            "provider": result.source,
            "error_code": result.error_code,
        },
        result="SUCCESS" if succeeded else item.status,
        correlation_id=correlation_id,
    )
    db.commit()
    return item


def evaluate_product(
    db: Session,
    *,
    product_id: int | None,
    sourcing_candidate_id: int | None,
    stage: str,
    metrics: dict,
) -> ProductEvaluation:
    evidence: dict = {}
    dimensions: dict[str, Decimal] = {}
    if product_id:
        product = db.get(Product, product_id)
        if product is None:
            raise LookupError("商品不存在")
    else:
        candidate = db.get(SourcingCandidate, sourcing_candidate_id)
        if candidate is None:
            raise LookupError("选品候选不存在")

    if stage == "PRE_LAUNCH":
        if product_id:
            latest = db.scalar(
                select(ProductScore)
                .where(ProductScore.product_id == product_id)
                .order_by(ProductScore.created_at.desc())
            )
            if latest is None:
                raise ValueError("商品缺少选品评分证据")
            names = (
                "profit_space",
                "after_sales_safety",
                "fault_safety",
                "compatibility_safety",
                "transport_safety",
                "stock_stability",
                "price_stability",
                "verticality",
            )
            dimensions = {name: _score(getattr(latest, name)) for name in names}
            total = _score(latest.total_score)
            evidence = {"product_score_id": latest.id, "input_hash": latest.input_hash}
        else:
            score_inputs = candidate.normalized_data.get("score_inputs") or {}
            dimensions = {key: _score(value) for key, value in score_inputs.items()}
            if candidate.score is None:
                raise ValueError("候选缺少选品评分证据")
            total = _score(candidate.score)
            evidence = {"sourcing_candidate_id": candidate.id, "adapter": candidate.adapter_name}
    else:
        required = {
            "traffic": Decimal("0.20"),
            "interest": Decimal("0.15"),
            "conversion": Decimal("0.25"),
            "refund_safety": Decimal("0.15"),
            "profit": Decimal("0.15"),
            "supply_stability": Decimal("0.10"),
        }
        missing = sorted(set(required) - set(metrics))
        if missing:
            raise ValueError(f"上架后评估缺少指标: {', '.join(missing)}")
        dimensions = {name: _score(metrics[name]) for name in required}
        total = sum(dimensions[name] * weight for name, weight in required.items())
        evidence = {"provided_metrics": sorted(metrics)}

    payload = {
        "product_id": product_id,
        "sourcing_candidate_id": sourcing_candidate_id,
        "stage": stage,
        "dimensions": dimensions,
        "total": total,
    }
    digest = _hash(payload)
    existing = db.scalar(
        select(ProductEvaluation).where(
            ProductEvaluation.product_id == product_id,
            ProductEvaluation.sourcing_candidate_id == sourcing_candidate_id,
            ProductEvaluation.stage == stage,
            ProductEvaluation.input_hash == digest,
        )
    )
    if existing:
        return existing
    item = ProductEvaluation(
        product_id=product_id,
        sourcing_candidate_id=sourcing_candidate_id,
        stage=stage,
        grade=_grade(total),
        total_score=total,
        dimensions={key: str(value) for key, value in dimensions.items()},
        evidence={
            **evidence,
            "recommended_lifecycle": {"S": "WINNER", "A": "ACTIVE", "B": "TESTING", "C": "PAUSED"}[
                _grade(total)
            ],
        },
        input_hash=digest,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def diagnose_product(
    db: Session,
    *,
    product_id: int | None,
    sourcing_candidate_id: int | None,
    metrics: dict,
) -> ProductDiagnosis:
    target = (
        db.get(Product, product_id)
        if product_id
        else db.get(SourcingCandidate, sourcing_candidate_id)
    )
    if target is None:
        raise LookupError("诊断目标不存在")
    values = {key: _score(value) for key, value in metrics.items()}
    rules = [
        (
            "NO_EXPOSURE",
            "HIGH",
            values.get("traffic", 100) < 15,
            ["检查类目映射与标题关键词", "重新生成草稿并人工复核"],
        ),
        (
            "LOW_CONVERSION",
            "HIGH",
            values.get("traffic", 0) >= 40 and values.get("conversion", 100) < 20,
            ["复核定价与主图信息", "检查规格和兼容性说明"],
        ),
        (
            "INQUIRY_NOT_PAID",
            "MEDIUM",
            values.get("inquiry", 0) >= 40 and values.get("payment", 100) < 20,
            ["检查回复时效与关键信息", "补充运费和发货承诺边界"],
        ),
        (
            "HIGH_REFUND",
            "HIGH",
            values.get("refund_risk", 0) >= 60,
            ["暂停扩量", "复核质量、规格匹配和售后原因"],
        ),
        (
            "LOW_PROFIT",
            "HIGH",
            values.get("profit", 100) < 20,
            ["核对完整成本", "调整价格或更换供应商"],
        ),
        (
            "UNSTABLE_SUPPLY",
            "HIGH",
            values.get("supply_stability", 100) < 30,
            ["启用备选供应商", "暂停新增曝光"],
        ),
    ]
    diagnosis, severity, actions = "HEALTHY", "LOW", ["保持观察并持续记录真实指标"]
    for code, level, matched, recommended in rules:
        if matched:
            diagnosis, severity, actions = code, level, recommended
            break
    item = ProductDiagnosis(
        product_id=product_id,
        sourcing_candidate_id=sourcing_candidate_id,
        diagnosis=diagnosis,
        severity=severity,
        evidence={"provided_metrics": {key: str(value) for key, value in values.items()}},
        recommended_actions=actions,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


FALSE_PROVENANCE = re.compile(r"(?:自用闲置|个人闲置|全新未拆|仅拆封|购于\S+)")


def create_xianyu_draft(
    db: Session,
    *,
    product_id: int,
    target_category: str | None,
    price: Decimal | None,
    user_id: int,
    correlation_id: str,
) -> XianyuDraft:
    product = db.scalar(
        select(Product).where(Product.id == product_id).options(*product_query_options())
    )
    if product is None:
        raise LookupError("商品不存在")
    assert_product_publishable(product)
    sku = product.skus[0] if product.skus else None
    if sku is None:
        raise ValueError("商品缺少 SKU，无法生成可核验草稿")
    draft_price = price or sku.recommended_price
    clean_title = sanitize_publication_source_text(
        " ".join(FALSE_PROVENANCE.sub("", product.title).split())
    )[:30]
    category = target_category or product.category
    attributes = {
        "internal_code": product.internal_code,
        "spec": sku.spec,
        "compatibility": product.compatibility,
    }
    images = [str(item) for item in (product.images or []) if str(item).startswith("https://")]
    description_parts = [clean_title]
    if product.description:
        description_parts.append(sanitize_publication_source_text(product.description))
    if sku.spec:
        description_parts.append(
            "规格：" + "；".join(f"{key}：{value}" for key, value in sku.spec.items())
        )
    description_body = "\n\n".join(part for part in description_parts if part)
    description_body = description_body[: 5000 - len(SELLER_SERVICE_COPY) - 2].rstrip()
    description = "\n\n".join(part for part in (description_body, SELLER_SERVICE_COPY) if part)
    blockers = []
    warnings = []
    if not clean_title:
        blockers.append("标题为空")
    if draft_price < sku.minimum_sale_price:
        blockers.append("价格低于最低安全售价")
    if not category:
        blockers.append("缺少目标类目")
    blockers.extend(publication_copy_blockers(clean_title, description))
    if not images:
        warnings.append("缺少可验证的 HTTPS 商品图片")
    validation = {"passed": not blockers, "blockers": blockers, "warnings": warnings}
    trace = [
        {"step": "SourceNormalizer", "status": "OK"},
        {"step": "CategoryMapper", "status": "OK" if category else "BLOCKED"},
        {"step": "AttributeMapper", "status": "OK"},
        {"step": "ImagePipeline", "status": "OK" if images else "WARNING"},
        {"step": "RuleTitleGenerator", "status": "OK" if clean_title else "BLOCKED"},
        {"step": "DescriptionGenerator", "status": "OK"},
        {"step": "PricingStep", "status": "OK" if not blockers else "REVIEW"},
        {"step": "RiskValidation", "status": "OK" if not blockers else "BLOCKED"},
        {"step": "XianyuDraftBuilder", "status": "OK" if not blockers else "BLOCKED"},
    ]
    payload = {
        "product_id": product_id,
        "title": clean_title,
        "description": description,
        "price": draft_price,
        "category": category,
        "attributes": attributes,
        "images": images,
    }
    digest = _hash(payload)
    existing = db.scalar(
        select(XianyuDraft).where(
            XianyuDraft.product_id == product_id,
            XianyuDraft.input_hash == digest,
        )
    )
    if existing:
        return existing
    latest_version = db.scalar(
        select(func.max(XianyuDraft.version)).where(XianyuDraft.product_id == product_id)
    )
    item = XianyuDraft(
        product_id=product_id,
        version=int(latest_version or 0) + 1,
        title=clean_title,
        description=description,
        price=draft_price,
        category=category,
        attributes=attributes,
        images=images,
        validation=validation,
        pipeline_trace=trace,
        status="REVIEW_READY" if not blockers else "BLOCKED",
        input_hash=digest,
        created_by_user_id=user_id,
    )
    db.add(item)
    db.flush()
    write_audit(
        db,
        action="XIANYU_DRAFT_CREATED",
        entity_type="XIANYU_DRAFT",
        entity_id=item.id,
        actor_user_id=user_id,
        actor_type="USER",
        after_data={"status": item.status, "version": item.version},
        correlation_id=correlation_id,
    )
    db.commit()
    return item


def create_supplier_inquiry(
    db: Session,
    *,
    supplier_candidate_id: int | None,
    sourcing_candidate_id: int | None,
    topic: str,
    questions: list[str],
    idempotency_key: str | None,
    user_id: int,
    correlation_id: str,
) -> SupplierInquiry:
    if supplier_candidate_id and db.get(SupplierCandidate, supplier_candidate_id) is None:
        raise LookupError("供应商候选不存在")
    if sourcing_candidate_id and db.get(SourcingCandidate, sourcing_candidate_id) is None:
        raise LookupError("选品候选不存在")
    key = idempotency_key or _hash(
        {
            "supplier_candidate_id": supplier_candidate_id,
            "sourcing_candidate_id": sourcing_candidate_id,
            "topic": topic,
            "questions": questions,
            "user_id": user_id,
        }
    )
    existing = db.scalar(select(SupplierInquiry).where(SupplierInquiry.idempotency_key == key))
    if existing:
        return existing
    item = SupplierInquiry(
        supplier_candidate_id=supplier_candidate_id,
        sourcing_candidate_id=sourcing_candidate_id,
        topic=topic,
        questions=questions,
        status="MANUAL_REQUIRED",
        result={"reason": "1688 询价写通道未启用", "dispatch": "not_attempted"},
        idempotency_key=key,
        requested_by_user_id=user_id,
        correlation_id=correlation_id,
    )
    db.add(item)
    db.flush()
    db.add(
        SupplierInquiryMessage(
            inquiry_id=item.id,
            direction="OUTBOUND",
            author_type="OPERATOR",
            content="\n".join(
                f"{index}. {question}" for index, question in enumerate(questions, start=1)
            ),
        )
    )
    write_audit(
        db,
        action="SUPPLIER_INQUIRY_QUEUED_MANUAL",
        entity_type="SUPPLIER_INQUIRY",
        entity_id=item.id,
        actor_user_id=user_id,
        actor_type="USER",
        after_data={"status": item.status, "question_count": len(questions)},
        correlation_id=correlation_id,
    )
    db.commit()
    return item


def update_inquiry_result(
    db: Session,
    *,
    inquiry_id: int,
    status: str,
    result: dict,
    message: str | None,
    user_id: int,
    correlation_id: str,
) -> SupplierInquiry:
    item = db.get(SupplierInquiry, inquiry_id)
    if item is None:
        raise LookupError("询价任务不存在")
    before = {"status": item.status}
    item.status = status
    item.result = result
    if message:
        db.add(
            SupplierInquiryMessage(
                inquiry_id=item.id,
                direction="INBOUND" if status == "REPLIED" else "SYSTEM",
                author_type="OPERATOR",
                content=message,
            )
        )
    write_audit(
        db,
        action="SUPPLIER_INQUIRY_RESULT_RECORDED",
        entity_type="SUPPLIER_INQUIRY",
        entity_id=item.id,
        actor_user_id=user_id,
        actor_type="USER",
        before_data=before,
        after_data={"status": status},
        correlation_id=correlation_id,
    )
    db.commit()
    return item


def overview(db: Session) -> dict:
    def count(model, condition=None):
        statement = select(func.count(model.id))
        if condition is not None:
            statement = statement.where(condition)
        return int(db.scalar(statement) or 0)

    settings = get_settings()
    controlled_write = settings.adapters_enabled and settings.adapter_write_enabled
    return {
        "capabilities": [
            {"id": 1, "key": "shopkeeper", "name": "1688 商品与趋势", "status": "ACTIVE"},
            {"id": 2, "key": "product_find", "name": "多入口找货与对比", "status": "PARTIAL"},
            {"id": 3, "key": "supplier_source", "name": "供应商发现", "status": "ACTIVE"},
            {
                "id": 4,
                "key": "action_gateway",
                "name": "动作预检与执行",
                "status": "ACTIVE" if controlled_write else "SAFE_PREVIEW",
            },
            {"id": 5, "key": "item_select", "name": "选品评估", "status": "ACTIVE"},
            {"id": 6, "key": "product_analysis", "name": "商品诊断", "status": "ACTIVE"},
            {"id": 7, "key": "draft_pipeline", "name": "闲鱼草稿流水线", "status": "ACTIVE"},
            {"id": 8, "key": "inquiry", "name": "供应商询价", "status": "MANUAL_REQUIRED"},
        ],
        "counts": {
            "sourcing_candidates": count(SourcingCandidate),
            "supplier_candidates": count(SupplierCandidate),
            "evaluations": count(ProductEvaluation),
            "open_diagnoses": count(ProductDiagnosis, ProductDiagnosis.status == "OPEN"),
            "drafts": count(XianyuDraft),
            "manual_inquiries": count(SupplierInquiry, SupplierInquiry.status == "MANUAL_REQUIRED"),
            "blocked_actions": count(
                PlatformActionRecord, PlatformActionRecord.status == "BLOCKED"
            ),
        },
        "safety": {
            "external_writes_enabled": controlled_write,
            "mode": (
                "CONTROLLED_WRITE_WITH_AUDIT"
                if controlled_write
                else "READ_ONLY_WITH_AUDITED_PREVIEW"
            ),
        },
    }
