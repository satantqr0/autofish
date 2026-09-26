import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_operator
from app.core.database import get_db
from app.models import AutomationJob, JobStatus, JobType, ManualTask, User
from app.schemas.automation import AutomationJobCreate
from app.services.audit import write_audit

router = APIRouter(prefix="/tasks", tags=["tasks"])


def serialize_job(job):
    return {
        "id": job.id,
        "type": job.type.value,
        "status": job.status.value,
        "idempotency_key": job.idempotency_key,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "error_code": job.error_code,
        "scheduled_at": job.scheduled_at,
        "created_at": job.created_at,
    }


@router.get("")
def list_tasks(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    jobs = db.scalars(
        select(AutomationJob).order_by(AutomationJob.created_at.desc()).limit(100)
    ).all()
    manual = db.scalars(
        select(ManualTask)
        .where(ManualTask.status == "OPEN")
        .order_by(ManualTask.priority.desc())
        .limit(100)
    ).all()
    return {
        "jobs": [serialize_job(job) for job in jobs],
        "manual_tasks": [
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
            for item in manual
        ],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(
    payload: AutomationJobCreate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    try:
        job_type = JobType(payload.type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="unknown job type") from exc
    existing = db.scalar(
        select(AutomationJob).where(AutomationJob.idempotency_key == payload.idempotency_key)
    )
    if existing:
        return {**serialize_job(existing), "idempotent": True}
    job = AutomationJob(
        type=job_type,
        status=JobStatus.PENDING,
        idempotency_key=payload.idempotency_key,
        correlation_id=request.state.correlation_id or str(uuid.uuid4()),
        payload={**payload.payload, "_requested_by_user_id": user.id},
        timeout_seconds=payload.timeout_seconds,
        max_attempts=3,
    )
    db.add(job)
    db.flush()
    write_audit(
        db,
        action="AUTOMATION_JOB_CREATED",
        entity_type="AUTOMATION_JOB",
        entity_id=job.id,
        actor_user_id=user.id,
        actor_type="USER",
        after_data={"type": job.type.value, "idempotency_key": job.idempotency_key},
        correlation_id=request.state.correlation_id,
    )
    db.commit()
    from app.worker import celery_app

    celery_app.send_task("workers.execute_automation_job", args=[job.id])
    return {**serialize_job(job), "idempotent": False}
