from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import (
    AfterSale,
    AIDecision,
    AuditLog,
    AutomationControl,
    AutomationJob,
    JobStatus,
    ManualTask,
    Shipment,
    Supplier,
    SupplierOrder,
    User,
)
from app.services.operations_catalog import get_operations_guide

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/guide")
def operations_guide(_user: User = Depends(get_current_user)):
    """Return the versioned, read-only AutoFish operating guide."""

    return get_operations_guide()


@router.get("/facts")
def operations_facts(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return read-only facts for the agent, risk, purchase and logistics consoles."""

    decision_counts = dict(
        db.execute(
            select(AIDecision.agent, func.count(AIDecision.id)).group_by(AIDecision.agent)
        ).all()
    )
    decisions = db.scalars(
        select(AIDecision).order_by(AIDecision.created_at.desc()).limit(30)
    ).all()
    controls = db.scalars(
        select(AutomationControl).order_by(AutomationControl.scope)
    ).all()
    manual_tasks = db.scalars(
        select(ManualTask)
        .where(ManualTask.status == "OPEN")
        .order_by(ManualTask.priority.desc(), ManualTask.created_at.desc())
        .limit(100)
    ).all()
    failed_jobs = db.scalars(
        select(AutomationJob)
        .where(AutomationJob.status.in_([JobStatus.FAILED, JobStatus.MANUAL_REQUIRED]))
        .order_by(AutomationJob.created_at.desc())
        .limit(50)
    ).all()
    after_sales = db.scalars(
        select(AfterSale).order_by(AfterSale.created_at.desc()).limit(50)
    ).all()
    audit_events = db.scalars(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(50)
    ).all()

    supplier_orders = db.scalars(
        select(SupplierOrder).order_by(SupplierOrder.created_at.desc()).limit(100)
    ).all()
    suppliers = {
        item.id: item
        for item in db.scalars(
            select(Supplier).where(
                Supplier.id.in_({item.supplier_id for item in supplier_orders})
            )
        ).all()
    } if supplier_orders else {}
    shipments = db.scalars(
        select(Shipment).order_by(Shipment.created_at.desc()).limit(100)
    ).all()

    return {
        "agents": {
            "total_decisions": sum(decision_counts.values()),
            "by_agent": [
                {"agent": agent, "count": count}
                for agent, count in sorted(decision_counts.items())
            ],
            "recent": [
                {
                    "id": item.id,
                    "agent": item.agent,
                    "confidence": str(item.confidence),
                    "reason_summary": item.reason_summary,
                    "provider": item.provider,
                    "model": item.model,
                    "created_at": item.created_at,
                }
                for item in decisions
            ],
        },
        "risk": {
            "open_manual_tasks": [
                {
                    "id": item.id,
                    "type": item.type,
                    "title": item.title,
                    "reason": item.reason,
                    "priority": item.priority,
                    "entity_type": item.entity_type,
                    "entity_id": item.entity_id,
                    "created_at": item.created_at,
                }
                for item in manual_tasks
            ],
            "failed_jobs": [
                {
                    "id": item.id,
                    "type": item.type.value,
                    "status": item.status.value,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                    "attempts": item.attempts,
                    "created_at": item.created_at,
                }
                for item in failed_jobs
            ],
            "controls": [
                {
                    "scope": item.scope.value,
                    "enabled": item.enabled,
                    "mode": item.mode,
                    "consecutive_failures": item.consecutive_failures,
                    "cooldown_until": item.cooldown_until,
                    "stopped_reason": item.stopped_reason,
                }
                for item in controls
            ],
            "after_sales": [
                {
                    "id": item.id,
                    "order_id": item.order_id,
                    "type": item.type,
                    "status": item.status,
                    "amount": str(item.amount) if item.amount is not None else None,
                    "reason": item.reason,
                    "created_at": item.created_at,
                }
                for item in after_sales
            ],
            "recent_audit": [
                {
                    "id": item.id,
                    "action": item.action,
                    "entity_type": item.entity_type,
                    "entity_id": item.entity_id,
                    "result": item.result,
                    "actor_type": item.actor_type,
                    "created_at": item.created_at,
                }
                for item in audit_events
            ],
        },
        "purchases": {
            "items": [
                {
                    "id": item.id,
                    "order_id": item.order_id,
                    "supplier": suppliers.get(item.supplier_id).name
                    if suppliers.get(item.supplier_id)
                    else f"供应商 #{item.supplier_id}",
                    "status": item.status,
                    "cost": str(item.cost),
                    "shipping_cost": str(item.shipping_cost),
                    "external_order_recorded": bool(item.external_order_id),
                    "created_at": item.created_at,
                }
                for item in supplier_orders
            ]
        },
        "logistics": {
            "items": [
                {
                    "id": item.id,
                    "supplier_order_id": item.supplier_order_id,
                    "carrier": item.carrier,
                    "tracking_recorded": bool(item.tracking_number_encrypted),
                    "status": item.status,
                    "shipped_at": item.shipped_at,
                    "delivered_at": item.delivered_at,
                    "last_event_at": item.last_event_at,
                    "created_at": item.created_at,
                }
                for item in shipments
            ]
        },
    }
