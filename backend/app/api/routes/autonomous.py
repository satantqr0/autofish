from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_operator
from app.core.config import get_settings
from app.core.database import get_db
from app.models import AutomationJob, AutonomousLaunch, JobStatus, JobType, User
from app.schemas.autonomous import AutonomousLaunchCreate
from app.services.audit import write_audit
from app.services.autonomous_launch import serialize_launch

router = APIRouter(prefix="/autonomous-launches", tags=["autonomous-launches"])


@router.get("")
def list_launches(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(AutonomousLaunch).order_by(AutonomousLaunch.created_at.desc()).limit(100)
    ).all()
    return {"items": [serialize_launch(item) for item in items], "total": len(items)}


@router.get("/{launch_id}")
def get_launch(
    launch_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = db.get(AutonomousLaunch, launch_id)
    if item is None:
        raise HTTPException(status_code=404, detail="自主上架任务不存在")
    return serialize_launch(item)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_launch(
    payload: AutonomousLaunchCreate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    existing = db.scalar(
        select(AutonomousLaunch).where(AutonomousLaunch.idempotency_key == payload.idempotency_key)
    )
    if existing:
        return {**serialize_launch(existing), "idempotent": True}
    request_payload = payload.model_dump(mode="json")
    launch = AutonomousLaunch(
        status="PENDING",
        current_stage="QUEUED",
        idempotency_key=payload.idempotency_key,
        correlation_id=request.state.correlation_id,
        request_payload=request_payload,
        requested_by_user_id=user.id,
        stages=[{"name": "QUEUED", "status": "OK", "detail": {}}],
    )
    db.add(launch)
    db.flush()
    job = AutomationJob(
        type=JobType.GENERATE_PRODUCT,
        status=JobStatus.PENDING,
        idempotency_key=f"autonomous-launch:{launch.id}",
        correlation_id=request.state.correlation_id,
        payload={"launch_id": launch.id, "_requested_by_user_id": user.id},
        max_attempts=3,
        timeout_seconds=840,
    )
    db.add(job)
    db.flush()
    launch.automation_job_id = job.id
    write_audit(
        db,
        action="AUTONOMOUS_LAUNCH_QUEUED",
        entity_type="AUTONOMOUS_LAUNCH",
        entity_id=launch.id,
        actor_type="USER",
        actor_user_id=user.id,
        after_data={
            "candidate_id": payload.candidate_id,
            "candidate_limit": payload.candidate_limit,
            "image_count": payload.image_count,
            "external_submission": False,
        },
        correlation_id=request.state.correlation_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    from app.worker import celery_app

    celery_app.send_task("workers.execute_automation_job", args=[job.id])
    return {**serialize_launch(launch), "idempotent": False}


@router.post("/{launch_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_launch(
    launch_id: int,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    launch = db.get(AutonomousLaunch, launch_id)
    if launch is None:
        raise HTTPException(status_code=404, detail="自主上架任务不存在")
    if launch.status in {"PENDING", "RUNNING", "READY_FOR_BROWSER_HANDOFF"}:
        raise HTTPException(status_code=409, detail="当前任务状态不允许重试")
    job = db.get(AutomationJob, launch.automation_job_id)
    if job is None:
        raise HTTPException(status_code=409, detail="任务执行记录不存在")
    job.status = JobStatus.RETRY
    job.error_code = None
    job.error_message = None
    launch.status = "RETRY"
    launch.error_code = None
    launch.error_message = None
    launch.blockers = []
    write_audit(
        db,
        action="AUTONOMOUS_LAUNCH_RETRIED",
        entity_type="AUTONOMOUS_LAUNCH",
        entity_id=launch.id,
        actor_type="USER",
        actor_user_id=user.id,
        after_data={"automation_job_id": job.id},
        correlation_id=request.state.correlation_id,
    )
    db.commit()
    from app.worker import celery_app

    celery_app.send_task("workers.execute_automation_job", args=[job.id])
    return serialize_launch(launch)


@router.get("/{launch_id}/assets/{filename}")
def get_launch_asset(
    launch_id: int,
    filename: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    launch = db.get(AutonomousLaunch, launch_id)
    if launch is None:
        raise HTTPException(status_code=404, detail="自主上架任务不存在")
    entry = next(
        (item for item in launch.asset_manifest or [] if item.get("filename") == filename),
        None,
    )
    if entry is None or entry.get("kind") not in {"source", "generated"}:
        raise HTTPException(status_code=404, detail="图片资产不存在")
    root = Path(get_settings().asset_root).expanduser().resolve()
    path = (root / str(entry["relative_path"])).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="图片资产文件不存在")
    return FileResponse(
        path,
        media_type=entry.get("mime_type") or "application/octet-stream",
        filename=filename,
        headers={"Cache-Control": "private, max-age=300"},
    )
