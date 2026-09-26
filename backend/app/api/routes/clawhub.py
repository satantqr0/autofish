import time

from adapters.base import AdapterStatus
from adapters.supplier.port import SourcingQuery
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_operator
from app.core.config import get_settings
from app.core.database import get_db
from app.models import (
    AdapterCall,
    PlatformActionRecord,
    ProductDiagnosis,
    ProductEvaluation,
    SupplierCandidate,
    SupplierInquiry,
    User,
    XianyuDraft,
)
from app.schemas.clawhub import (
    ActionExecuteRequest,
    ActionPreviewRequest,
    CompareRequest,
    DiagnosisRequest,
    DiscoveryRequest,
    DraftCreateRequest,
    EvaluationRequest,
    InquiryCreateRequest,
    InquiryResultRequest,
    InsightRequest,
    SupplierDiscoveryRequest,
)
from app.services.adapter_factory import get_supplier_adapter
from app.services.audit import write_audit
from app.services.clawhub import (
    compare_candidates,
    create_supplier_inquiry,
    create_xianyu_draft,
    diagnose_product,
    discover_suppliers,
    evaluate_product,
    execute_action,
    overview,
    preview_action,
    serialize_action,
    serialize_diagnosis,
    serialize_draft,
    serialize_evaluation,
    serialize_inquiry,
    serialize_supplier,
    update_inquiry_result,
)
from app.services.integrations import (
    finish_sync_run,
    save_snapshot,
    serialize_candidate,
    start_sync_run,
    upsert_sourcing_candidate,
)

router = APIRouter(prefix="/clawhub", tags=["clawhub"])


def _service_error(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _adapter_error(result):
    codes = {
        AdapterStatus.AUTH_REQUIRED: 401,
        AdapterStatus.RATE_LIMITED: 429,
        AdapterStatus.UNSUPPORTED: 501,
        AdapterStatus.NOT_FOUND: 404,
    }
    raise HTTPException(
        status_code=codes.get(result.status, 503),
        detail={
            "status": result.status.value,
            "error_code": result.error_code,
            "message": result.safe_message or "Adapter 调用失败",
        },
    )


@router.get("/overview")
def get_overview(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return overview(db)


@router.post("/discover")
async def discover_products(
    payload: DiscoveryRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if not settings.adapters_enabled:
        raise HTTPException(status_code=409, detail="真实 Adapter 总开关未启用")
    adapter = get_supplier_adapter()
    operation = f"{payload.mode}_DISCOVERY"
    run = start_sync_run(
        db,
        platform="1688",
        adapter_name=adapter.source,
        adapter_version=adapter.source_version,
        operation=operation,
        requested_by_user_id=user.id,
        correlation_id=request.state.correlation_id,
    )
    started = time.monotonic()
    if payload.mode == "TEXT":
        result = await adapter.search_products(
            SourcingQuery(
                query=payload.query or "",
                limit=payload.limit,
                filters=payload.filters,
            )
        )
    elif payload.mode == "IMAGE":
        result = await adapter.search_image(payload.source_url or "", payload.limit)
    else:
        result = await adapter.search_link(payload.source_url or "", payload.limit)
    duration_ms = int((time.monotonic() - started) * 1000)
    db.add(
        AdapterCall(
            correlation_id=request.state.correlation_id,
            adapter=result.source,
            adapter_version=result.source_version,
            operation=operation,
            status=result.status.value,
            duration_ms=duration_ms,
            error_code=result.error_code,
            safe_summary={"mode": payload.mode, "result_count": len(result.data or [])},
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
    candidates = []
    written = 0
    for item in result.data or []:
        normalized = item.model_dump(mode="json")
        snapshot, created = save_snapshot(
            db,
            run=run,
            platform="1688",
            object_type=f"{payload.mode}_PRODUCT_CANDIDATE",
            external_id=item.external_product_id,
            adapter_name=result.source,
            adapter_version=result.source_version,
            normalized_data=normalized,
            raw_payload=item.raw_payload or normalized,
        )
        candidates.append(
            upsert_sourcing_candidate(
                db,
                snapshot=snapshot,
                adapter_name=result.source,
                adapter_version=result.source_version,
                data=normalized,
            )
        )
        written += int(created)
    finish_sync_run(
        run,
        status="SUCCEEDED",
        items_seen=len(candidates),
        items_written=written,
    )
    write_audit(
        db,
        action="MULTIMODAL_DISCOVERY_COMPLETED",
        entity_type="INTEGRATION_SYNC_RUN",
        entity_id=run.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={"mode": payload.mode, "items_seen": len(candidates)},
        correlation_id=request.state.correlation_id,
    )
    db.commit()
    return {"sync_run_id": run.id, "items": [serialize_candidate(item) for item in candidates]}


@router.post("/compare")
def compare(payload: CompareRequest, _user: User = Depends(get_current_user), db=Depends(get_db)):
    try:
        return compare_candidates(db, payload.candidate_ids)
    except (LookupError, ValueError) as exc:
        _service_error(exc)


@router.post("/insights")
async def insights(
    payload: InsightRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    if not get_settings().adapters_enabled:
        raise HTTPException(status_code=409, detail="真实 Adapter 总开关未启用")
    adapter = get_supplier_adapter()
    started = time.monotonic()
    if payload.kind == "TREND":
        result = await adapter.get_trends(payload.query or "")
    else:
        result = await adapter.get_opportunities(payload.category)
    db.add(
        AdapterCall(
            correlation_id=request.state.correlation_id,
            adapter=result.source,
            adapter_version=result.source_version,
            operation=payload.kind,
            status=result.status.value,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=result.error_code,
            safe_summary={
                "kind": payload.kind,
                "item_count": len(result.data.items) if result.data else 0,
            },
        )
    )
    if result.status != AdapterStatus.OK:
        db.commit()
        _adapter_error(result)
    data = result.data.model_dump(mode="json")
    snapshot, _created = save_snapshot(
        db,
        run=None,
        platform="1688",
        object_type=payload.kind,
        external_id=f"{payload.kind}:{payload.query or payload.category or 'all'}",
        adapter_name=result.source,
        adapter_version=result.source_version,
        normalized_data=data,
        raw_payload=data.get("raw_payload") or data,
    )
    write_audit(
        db,
        action="SUPPLIER_INSIGHT_FETCHED",
        entity_type="EXTERNAL_SNAPSHOT",
        entity_id=snapshot.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={"kind": payload.kind},
        correlation_id=request.state.correlation_id,
    )
    db.commit()
    return data


@router.post("/suppliers/discover")
def suppliers_discover(
    payload: SupplierDiscoveryRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        return discover_suppliers(
            db,
            sourcing_candidate_id=payload.sourcing_candidate_id,
            query=payload.query,
            limit=payload.limit,
            user_id=user.id,
            correlation_id=request.state.correlation_id,
        )
    except (LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)


@router.get("/suppliers")
def suppliers_list(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(SupplierCandidate).order_by(SupplierCandidate.suitability_score.desc()).limit(100)
    ).all()
    return {"items": [serialize_supplier(item) for item in items], "total": len(items)}


@router.post("/actions/preview")
def actions_preview(
    payload: ActionPreviewRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        item = preview_action(
            db,
            action_type=payload.action_type,
            target_type=payload.target_type,
            target_id=payload.target_id,
            payload=payload.payload,
            idempotency_key=payload.idempotency_key,
            user_id=user.id,
            correlation_id=request.state.correlation_id,
        )
    except (LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)
    return serialize_action(item)


@router.post("/actions/{action_id}/execute")
async def actions_execute(
    action_id: int,
    payload: ActionExecuteRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        item = await execute_action(
            db,
            action_id=action_id,
            confirm=payload.confirm,
            user_id=user.id,
            correlation_id=request.state.correlation_id,
        )
    except (LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)
    return serialize_action(item)


@router.get("/actions")
def actions_list(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(PlatformActionRecord).order_by(PlatformActionRecord.created_at.desc()).limit(100)
    ).all()
    return {"items": [serialize_action(item) for item in items], "total": len(items)}


@router.post("/evaluations")
def evaluations_create(
    payload: EvaluationRequest,
    _user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        item = evaluate_product(db, **payload.model_dump())
    except (LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)
    return serialize_evaluation(item)


@router.get("/evaluations")
def evaluations_list(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(ProductEvaluation).order_by(ProductEvaluation.created_at.desc()).limit(100)
    ).all()
    return {"items": [serialize_evaluation(item) for item in items], "total": len(items)}


@router.post("/diagnoses")
def diagnoses_create(
    payload: DiagnosisRequest,
    _user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        item = diagnose_product(db, **payload.model_dump())
    except (LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)
    return serialize_diagnosis(item)


@router.get("/diagnoses")
def diagnoses_list(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(ProductDiagnosis).order_by(ProductDiagnosis.created_at.desc()).limit(100)
    ).all()
    return {"items": [serialize_diagnosis(item) for item in items], "total": len(items)}


@router.post("/drafts")
def drafts_create(
    payload: DraftCreateRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        item = create_xianyu_draft(
            db,
            product_id=payload.product_id,
            target_category=payload.target_category,
            price=payload.price,
            user_id=user.id,
            correlation_id=request.state.correlation_id,
        )
    except (LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)
    return serialize_draft(item)


@router.get("/drafts")
def drafts_list(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(XianyuDraft).order_by(XianyuDraft.created_at.desc()).limit(100)).all()
    return {"items": [serialize_draft(item) for item in items], "total": len(items)}


@router.post("/inquiries")
def inquiries_create(
    payload: InquiryCreateRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        item = create_supplier_inquiry(
            db,
            **payload.model_dump(),
            user_id=user.id,
            correlation_id=request.state.correlation_id,
        )
    except (LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)
    return serialize_inquiry(item)


@router.get("/inquiries")
def inquiries_list(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(SupplierInquiry).order_by(SupplierInquiry.created_at.desc()).limit(100)
    ).all()
    return {"items": [serialize_inquiry(item) for item in items], "total": len(items)}


@router.patch("/inquiries/{inquiry_id}/result")
def inquiries_result(
    inquiry_id: int,
    payload: InquiryResultRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        item = update_inquiry_result(
            db,
            inquiry_id=inquiry_id,
            **payload.model_dump(),
            user_id=user.id,
            correlation_id=request.state.correlation_id,
        )
    except (LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)
    return serialize_inquiry(item)
