import hashlib
import time
import uuid
from datetime import UTC, datetime

from adapters.base import AdapterStatus
from adapters.supplier.port import SourcingQuery
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_operator
from app.core.config import get_settings
from app.core.database import get_db
from app.models import AdapterCall, SourcingCandidate, User
from app.schemas.catalog import ProductCreate
from app.schemas.integrations import (
    CandidateImportRequest,
    CandidateManualVerificationRequest,
    SourcingSearchRequest,
    SupplierFeedRequest,
)
from app.services.adapter_factory import get_supplier_adapter
from app.services.audit import write_audit
from app.services.catalog import DuplicateCatalogItem, create_catalog_product
from app.services.integrations import (
    finish_sync_run,
    save_snapshot,
    serialize_candidate,
    start_sync_run,
    upsert_sourcing_candidate,
    verify_sourcing_candidate_manually,
)
from app.services.sourcing_adapter_verification import (
    CandidateAdapterVerificationError,
    verify_candidate_from_authorized_adapter,
)

router = APIRouter(prefix="/sourcing", tags=["sourcing"])


def _money_amount(value):
    if isinstance(value, dict):
        return value.get("amount")
    return value


def _supplier_code(candidate, data):
    external_supplier_id = data.get("external_supplier_id")
    if external_supplier_id:
        return f"{candidate.adapter_name}:{external_supplier_id}"[:80]
    identity = f"{candidate.adapter_name}\0{data['supplier_name'].strip().casefold()}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"{candidate.adapter_name}:name:{digest}"[:80]


def _adapter_error(result):
    code = 401 if result.status == AdapterStatus.AUTH_REQUIRED else 503
    if result.status == AdapterStatus.RATE_LIMITED:
        code = 429
    raise HTTPException(status_code=code, detail=result.safe_message or "Adapter 调用失败")


@router.get("")
def list_candidates(
    q: str | None = Query(default=None, max_length=200),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(SourcingCandidate).order_by(
        SourcingCandidate.last_fetched_at.desc(), SourcingCandidate.id.desc()
    )
    if q:
        statement = statement.where(SourcingCandidate.title.ilike(f"%{q}%"))
    items = db.scalars(statement.limit(200)).all()
    return {"items": [serialize_candidate(item) for item in items], "total": len(items)}


@router.post("/search")
async def search_supplier_products(
    payload: SourcingSearchRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if not settings.adapters_enabled:
        raise HTTPException(status_code=409, detail="真实 Adapter 总开关未启用")
    adapter = get_supplier_adapter()
    started = time.monotonic()
    run = start_sync_run(
        db,
        platform="1688",
        adapter_name=adapter.source,
        adapter_version=adapter.source_version,
        operation="SEARCH_PRODUCTS",
        requested_by_user_id=user.id,
        correlation_id=request.state.correlation_id,
    )
    result = await adapter.search_products(
        SourcingQuery(query=payload.query, limit=payload.limit, filters=payload.filters)
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    db.add(
        AdapterCall(
            correlation_id=request.state.correlation_id,
            adapter=result.source,
            adapter_version=result.source_version,
            operation="SEARCH_PRODUCTS",
            status=result.status.value,
            duration_ms=duration_ms,
            error_code=result.error_code,
            safe_summary={
                "query_length": len(payload.query),
                "result_count": len(result.data or []),
            },
        )
    )
    if result.status != AdapterStatus.OK:
        finish_sync_run(
            run,
            status="FAILED",
            error_code=result.error_code,
            message=result.safe_message,
        )
        db.commit()
        _adapter_error(result)
    written = 0
    candidates = []
    for item in result.data or []:
        normalized = item.model_dump(mode="json")
        raw = item.raw_payload or normalized
        snapshot, created = save_snapshot(
            db,
            run=run,
            platform="1688",
            object_type="SUPPLIER_PRODUCT_CANDIDATE",
            external_id=item.external_product_id,
            adapter_name=result.source,
            adapter_version=result.source_version,
            normalized_data=normalized,
            raw_payload=raw,
        )
        candidate = upsert_sourcing_candidate(
            db,
            snapshot=snapshot,
            adapter_name=result.source,
            adapter_version=result.source_version,
            data=normalized,
        )
        candidates.append(candidate)
        written += int(created)
    finish_sync_run(
        run,
        status="SUCCEEDED",
        items_seen=len(result.data or []),
        items_written=written,
    )
    write_audit(
        db,
        action="SUPPLIER_SEARCH_COMPLETED",
        entity_type="INTEGRATION_SYNC_RUN",
        entity_id=run.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={"items_seen": len(candidates), "adapter": result.source},
        correlation_id=request.state.correlation_id,
    )
    db.commit()
    return {"sync_run_id": run.id, "items": [serialize_candidate(item) for item in candidates]}


@router.post("/ingest", status_code=status.HTTP_201_CREATED)
def ingest_supplier_feed(
    payload: SupplierFeedRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    run = start_sync_run(
        db,
        platform="1688",
        adapter_name=payload.adapter_name,
        adapter_version=payload.adapter_version,
        operation="INGEST_CANONICAL_FEED",
        requested_by_user_id=user.id,
        correlation_id=request.state.correlation_id,
    )
    candidates = []
    written = 0
    for item in payload.items:
        normalized = item.model_dump(mode="json")
        raw = item.raw_payload or normalized
        snapshot, created = save_snapshot(
            db,
            run=run,
            platform="1688",
            object_type="SUPPLIER_PRODUCT",
            external_id=item.external_product_id,
            adapter_name=payload.adapter_name,
            adapter_version=payload.adapter_version,
            normalized_data=normalized,
            raw_payload=raw,
        )
        candidate = upsert_sourcing_candidate(
            db,
            snapshot=snapshot,
            adapter_name=payload.adapter_name,
            adapter_version=payload.adapter_version,
            data=normalized,
        )
        candidates.append(candidate)
        written += int(created)
    finish_sync_run(
        run,
        status="SUCCEEDED",
        items_seen=len(payload.items),
        items_written=written,
    )
    write_audit(
        db,
        action="SUPPLIER_FEED_INGESTED",
        entity_type="INTEGRATION_SYNC_RUN",
        entity_id=run.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={"items_seen": len(payload.items), "adapter": payload.adapter_name},
        correlation_id=request.state.correlation_id,
    )
    db.commit()
    return {"sync_run_id": run.id, "items": [serialize_candidate(item) for item in candidates]}


@router.post("/{candidate_id}/manual-verification")
def manually_verify_candidate(
    candidate_id: int,
    payload: CandidateManualVerificationRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    candidate = db.get(SourcingCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="选品候选不存在")
    if candidate.status == "IMPORTED":
        raise HTTPException(status_code=409, detail="候选已经导入商品中心，不能覆盖供应证据")
    if candidate.status == "EXCLUDED":
        raise HTTPException(status_code=422, detail="已排除候选不能补全供应证据")
    try:
        result = verify_sourcing_candidate_manually(
            db,
            candidate=candidate,
            payload=payload,
            actor_user_id=user.id,
            correlation_id=request.state.correlation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return result


@router.post("/{candidate_id}/adapter-verification")
async def verify_candidate_from_authorized_adapters(
    candidate_id: int,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    candidate = db.get(SourcingCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="选品候选不存在")
    if candidate.status == "IMPORTED":
        raise HTTPException(status_code=409, detail="候选已经导入商品中心")
    if candidate.adapter_name != "1688-product-find-cli":
        raise HTTPException(status_code=422, detail="仅支持核验 Product Find 候选")

    try:
        result = await verify_candidate_from_authorized_adapter(
            db,
            candidate=candidate,
            actor_user_id=user.id,
            actor_type="USER",
            correlation_id=request.state.correlation_id,
        )
    except CandidateAdapterVerificationError as exc:
        db.commit()
        code = 401 if exc.status == AdapterStatus.AUTH_REQUIRED else 503
        if exc.status == AdapterStatus.RATE_LIMITED:
            code = 429
        raise HTTPException(status_code=code, detail=exc.safe_message) from exc
    except ValueError as exc:
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return result


@router.post("/{candidate_id}/import", status_code=status.HTTP_201_CREATED)
def import_candidate(
    candidate_id: int,
    payload: CandidateImportRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    candidate = db.get(SourcingCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="选品候选不存在")
    if candidate.status == "IMPORTED":
        raise HTTPException(status_code=409, detail="候选已经导入商品中心")
    data = candidate.normalized_data
    if candidate.status == "EXCLUDED":
        raise HTTPException(
            status_code=422,
            detail=data.get("excluded_reason") or "候选命中排除类目，不能导入",
        )
    skus = data.get("skus") or []
    if not skus:
        raise HTTPException(status_code=422, detail="候选缺少结构化 SKU，不能安全导入")
    selected = next(
        (item for item in skus if item.get("external_sku_id") == payload.external_sku_id),
        skus[0] if payload.external_sku_id is None else None,
    )
    if selected is None:
        raise HTTPException(status_code=422, detail="指定 SKU 不存在")
    required = [data.get("category"), data.get("supplier_name")]
    if not all(required):
        raise HTTPException(status_code=422, detail="候选缺少类目或供应商名称")
    shipping = _money_amount(selected.get("shipping"))
    if shipping is None:
        raise HTTPException(status_code=422, detail="候选缺少可核验运费，不能安全导入")
    score_inputs = data.get("score_inputs") or {}
    try:
        product = create_catalog_product(
            db,
            ProductCreate(
                supplier_code=_supplier_code(candidate, data),
                supplier_name=data["supplier_name"],
                supplier_source_type=candidate.adapter_name,
                external_product_id=candidate.external_product_id,
                external_supplier_id=data.get("external_supplier_id"),
                supplier_url=candidate.url,
                external_sku_id=selected["external_sku_id"],
                internal_code=payload.internal_code,
                sku_code=payload.sku_code,
                title=payload.listing_title or candidate.title,
                category=data["category"],
                description=payload.description,
                images=[candidate.image_url] if candidate.image_url else [],
                spec=selected.get("spec") or {},
                supplier_price=_money_amount(selected["price"]),
                shipping_cost=shipping,
                platform_fee=payload.platform_fee,
                after_sales_reserve=payload.after_sales_reserve,
                minimum_profit=payload.minimum_profit,
                target_profit=payload.target_profit,
                negotiation_margin=payload.negotiation_margin,
                stock=selected.get("stock") or 0,
                after_sales_safety=score_inputs.get("after_sales_safety", 85),
                fault_safety=score_inputs.get("fault_safety", 85),
                compatibility_safety=score_inputs.get("compatibility_safety", 80),
                transport_safety=score_inputs.get("transport_safety", 85),
                stock_stability=score_inputs.get("stock_stability", 80),
                price_stability=score_inputs.get("price_stability", 80),
                verticality=score_inputs.get("verticality", 90),
            ),
            actor_user_id=user.id,
            correlation_id=request.state.correlation_id,
        )
    except DuplicateCatalogItem as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    candidate.status = "IMPORTED"
    candidate.last_fetched_at = candidate.last_fetched_at or datetime.now(UTC)
    write_audit(
        db,
        action="SOURCING_CANDIDATE_IMPORTED",
        entity_type="SOURCING_CANDIDATE",
        entity_id=candidate.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={"product_id": product.id, "external_sku_id": selected["external_sku_id"]},
        correlation_id=request.state.correlation_id or str(uuid.uuid4()),
    )
    db.commit()
    return {"candidate_id": candidate.id, "product_id": product.id, "status": candidate.status}
