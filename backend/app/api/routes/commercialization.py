from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_operator
from app.core.database import get_db
from app.models import User
from app.schemas.commercialization import (
    CommercialAcceptanceRequest,
    CommercialEvidenceUpsert,
    CommercialProfileUpdate,
)
from app.services.audit import write_audit
from app.services.commercialization import (
    accept_commercial_boundaries,
    build_commercial_overview,
    ensure_commercial_deployment,
    serialize_deployment,
    serialize_evidence,
    update_commercial_profile,
    upsert_commercial_evidence,
)

router = APIRouter(prefix="/commercialization", tags=["commercialization"])


@router.get("/overview")
def commercial_overview(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = build_commercial_overview(db)
    db.commit()
    return result


@router.patch("/profile")
def update_profile(
    payload: CommercialProfileUpdate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    item = ensure_commercial_deployment(db)
    before = update_commercial_profile(item, payload)
    db.flush()
    write_audit(
        db,
        action="COMMERCIAL_PROFILE_UPDATED",
        entity_type="COMMERCIAL_DEPLOYMENT",
        entity_id=item.id,
        actor_user_id=user.id,
        actor_type="USER",
        before_data=before,
        after_data=serialize_deployment(item),
        correlation_id=request.state.correlation_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return build_commercial_overview(db)


@router.post("/acceptance")
def accept_boundaries(
    _payload: CommercialAcceptanceRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    item = ensure_commercial_deployment(db)
    before = accept_commercial_boundaries(item, user_id=user.id)
    db.flush()
    write_audit(
        db,
        action="COMMERCIAL_BOUNDARIES_ACCEPTED",
        entity_type="COMMERCIAL_DEPLOYMENT",
        entity_id=item.id,
        actor_user_id=user.id,
        actor_type="USER",
        before_data=before,
        after_data=serialize_deployment(item),
        correlation_id=request.state.correlation_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return build_commercial_overview(db)


@router.put("/evidence")
def record_evidence(
    payload: CommercialEvidenceUpsert,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        item, before, created = upsert_commercial_evidence(db, payload, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    write_audit(
        db,
        action="COMMERCIAL_EVIDENCE_RECORDED",
        entity_type="COMMERCIAL_EVIDENCE_PERIOD",
        entity_id=item.id,
        actor_user_id=user.id,
        actor_type="USER",
        before_data=before,
        after_data={**serialize_evidence(item), "created": created},
        correlation_id=request.state.correlation_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return build_commercial_overview(db)
