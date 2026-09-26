from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_operator
from app.core.database import get_db
from app.models import AutomationControl, AutomationScope, User
from app.schemas.automation import AutomationControlUpdate
from app.services.audit import write_audit

router = APIRouter(prefix="/automation", tags=["automation"])


def serialize_control(control):
    return {
        "scope": control.scope.value,
        "enabled": control.enabled,
        "mode": control.mode,
        "daily_limit": control.daily_limit,
        "min_interval_seconds": control.min_interval_seconds,
        "failure_threshold": control.failure_threshold,
        "consecutive_failures": control.consecutive_failures,
        "cooldown_until": control.cooldown_until,
        "last_executed_at": control.last_executed_at,
        "stopped_reason": control.stopped_reason,
        "updated_at": control.updated_at,
    }


@router.get("/controls")
def list_controls(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    controls = db.scalars(select(AutomationControl).order_by(AutomationControl.id)).all()
    return [serialize_control(control) for control in controls]


@router.patch("/controls/{scope}")
def update_control(
    scope: str,
    payload: AutomationControlUpdate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        scope_enum = AutomationScope(scope.upper())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="unknown automation scope") from exc
    control = db.scalar(select(AutomationControl).where(AutomationControl.scope == scope_enum))
    if control is None:
        raise HTTPException(status_code=404, detail="automation control not found")
    before = serialize_control(control)
    control.enabled = payload.enabled
    for field in ("mode", "daily_limit", "min_interval_seconds", "failure_threshold"):
        value = getattr(payload, field)
        if value is not None:
            setattr(control, field, value)
    control.stopped_reason = None if payload.enabled else payload.reason
    control.updated_by_user_id = user.id
    write_audit(
        db,
        action="AUTOMATION_CONTROL_CHANGED",
        entity_type="AUTOMATION_CONTROL",
        entity_id=scope_enum.value,
        actor_user_id=user.id,
        actor_type="USER",
        before_data={"enabled": before["enabled"], "reason": before["stopped_reason"]},
        after_data={
            "enabled": payload.enabled,
            "reason": control.stopped_reason,
            "mode": control.mode,
            "daily_limit": control.daily_limit,
            "min_interval_seconds": control.min_interval_seconds,
            "failure_threshold": control.failure_threshold,
        },
        correlation_id=request.state.correlation_id,
    )
    db.commit()
    db.refresh(control)
    return serialize_control(control)


@router.post("/stop-all")
def stop_all(
    payload: AutomationControlUpdate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    if payload.enabled:
        raise HTTPException(status_code=422, detail="stop-all only accepts enabled=false")
    controls = db.scalars(select(AutomationControl)).all()
    before = {control.scope.value: control.enabled for control in controls}
    for control in controls:
        control.enabled = False
        control.stopped_reason = payload.reason
        control.updated_by_user_id = user.id
    write_audit(
        db,
        action="AUTOMATION_STOP_ALL",
        entity_type="AUTOMATION_CONTROL",
        entity_id="ALL",
        actor_user_id=user.id,
        actor_type="USER",
        before_data=before,
        after_data={scope.value: False for scope in AutomationScope},
        correlation_id=request.state.correlation_id,
    )
    db.commit()
    return [serialize_control(control) for control in controls]
