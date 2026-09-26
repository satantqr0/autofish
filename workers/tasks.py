import asyncio
import hashlib
import signal
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import (
    Account,
    AutomationControl,
    AutomationJob,
    AutomationScope,
    AutonomousLaunch,
    Conversation,
    JobStatus,
    JobType,
    ManualTask,
    Message,
    Order,
    OrderStatus,
    PlatformActionRecord,
    ProductSKU,
    User,
    XianyuDraft,
)
from app.services.ai_inference import AIInferenceError
from app.services.audit import write_audit
from app.services.automation_runtime import (
    RuntimeAdapterError,
    execute_platform_job,
    sync_supplier_job,
    sync_xianyu_job,
)
from app.services.autonomous_launch import (
    LaunchManualRequired,
    execute_autonomous_launch,
)
from app.services.customer_service import generate_reply_suggestion
from app.services.xianyu_browser_bridge import (
    automatic_customer_service_blockers,
    configured_bridge_token,
    create_automatic_conversation_capture_task,
    create_automatic_metrics_task,
    create_automatic_publish_task,
    create_automatic_reply_task,
)
from app.worker import celery_app

MAX_JOB_TIMEOUT_SECONDS = 900
LEASE_GRACE_SECONDS = 30
MAX_LEASE_WINDOW_SECONDS = 120
LEGACY_RUNNING_EXPIRY_SECONDS = MAX_JOB_TIMEOUT_SECONDS + LEASE_GRACE_SECONDS

EXTERNAL_WRITE_JOB_TYPES = frozenset(
    {
        JobType.PUBLISH_PRODUCT,
        JobType.REPLY_MESSAGE,
        JobType.CREATE_PURCHASE,
        JobType.SYNC_LOGISTICS,
    }
)

SCOPE_BY_JOB = {
    JobType.GENERATE_PRODUCT: AutomationScope.PUBLISH,
    JobType.PUBLISH_PRODUCT: AutomationScope.PUBLISH,
    JobType.REPLY_MESSAGE: AutomationScope.CUSTOMER_SERVICE,
    JobType.CREATE_PURCHASE: AutomationScope.PURCHASE,
    JobType.SYNC_SUPPLIER_PRICE: AutomationScope.REPRICING,
    JobType.SYNC_SUPPLIER_STOCK: AutomationScope.REPRICING,
    JobType.SYNC_MESSAGES: AutomationScope.CUSTOMER_SERVICE,
    JobType.SYNC_ORDERS: AutomationScope.LOGISTICS,
    JobType.SYNC_LOGISTICS: AutomationScope.LOGISTICS,
}


class JobExecutionTimeout(TimeoutError):
    """A per-job deadline expired before the AutomationJob reached a terminal state."""


def _bounded_timeout(value: int) -> int:
    return max(1, min(MAX_JOB_TIMEOUT_SECONDS, int(value or 1)))


def _lease_window_seconds(timeout_seconds: int) -> int:
    timeout = _bounded_timeout(timeout_seconds)
    return max(
        LEASE_GRACE_SECONDS,
        min(timeout + LEASE_GRACE_SECONDS, MAX_LEASE_WINDOW_SECONDS),
    )


def _heartbeat_interval_seconds(timeout_seconds: int) -> int:
    return max(5, min(30, _bounded_timeout(timeout_seconds) // 3 or 1))


def _release_lease(job: AutomationJob) -> None:
    job.lease_owner = None
    job.lease_expires_at = None


def _claim_automation_job(db, job_id: int, *, lease_owner: str) -> AutomationJob | None:
    """Atomically move one due PENDING/RETRY job to RUNNING.

    Reading the timeout first is safe: the conditional UPDATE is the claim.  A
    concurrent delivery can never cross the status/attempt predicate and only
    the winner receives a row from RETURNING.
    """

    timeout_seconds = db.scalar(
        select(AutomationJob.timeout_seconds).where(AutomationJob.id == job_id)
    )
    if timeout_seconds is None:
        return None
    now = datetime.now(UTC)
    claimed_id = db.scalar(
        update(AutomationJob)
        .where(
            AutomationJob.id == job_id,
            AutomationJob.status.in_([JobStatus.PENDING, JobStatus.RETRY]),
            AutomationJob.attempts < AutomationJob.max_attempts,
        )
        .values(
            status=JobStatus.RUNNING,
            attempts=AutomationJob.attempts + 1,
            started_at=case(
                (AutomationJob.started_at.is_(None), now),
                else_=AutomationJob.started_at,
            ),
            finished_at=None,
            heartbeat_at=now,
            lease_owner=lease_owner,
            lease_expires_at=now
            + timedelta(seconds=_lease_window_seconds(timeout_seconds)),
            error_code=None,
            error_message=None,
            updated_at=now,
        )
        .returning(AutomationJob.id)
    )
    if claimed_id is None:
        db.rollback()
        return None
    db.commit()
    return db.get(AutomationJob, claimed_id)


def _renew_automation_job_lease(
    db,
    *,
    job_id: int,
    lease_owner: str,
    timeout_seconds: int,
) -> bool:
    now = datetime.now(UTC)
    result = db.execute(
        update(AutomationJob)
        .where(
            AutomationJob.id == job_id,
            AutomationJob.status == JobStatus.RUNNING,
            AutomationJob.lease_owner == lease_owner,
        )
        .values(
            heartbeat_at=now,
            lease_expires_at=now
            + timedelta(seconds=_lease_window_seconds(timeout_seconds)),
            updated_at=now,
        )
    )
    db.commit()
    return result.rowcount == 1


@contextmanager
def _lease_heartbeat(job: AutomationJob):
    stop = threading.Event()
    job_id = job.id
    lease_owner = job.lease_owner
    timeout_seconds = job.timeout_seconds
    interval = _heartbeat_interval_seconds(timeout_seconds)

    def heartbeat_loop():
        while not stop.wait(interval):
            heartbeat_db = SessionLocal()
            try:
                renewed = _renew_automation_job_lease(
                    heartbeat_db,
                    job_id=job_id,
                    lease_owner=lease_owner,
                    timeout_seconds=timeout_seconds,
                )
                if not renewed:
                    return
            except SQLAlchemyError:
                heartbeat_db.rollback()
                # A transient heartbeat failure must not hide the original job
                # result.  If it persists, lease expiry safely hands the job to
                # the recovery sweep.
            finally:
                heartbeat_db.close()

    heartbeat = threading.Thread(
        target=heartbeat_loop,
        name=f"autofish-job-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat.start()
    try:
        yield
    finally:
        stop.set()
        heartbeat.join(timeout=1)


@contextmanager
def _job_execution_deadline(timeout_seconds: int):
    timeout = _bounded_timeout(timeout_seconds)
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "setitimer")
    ):
        # Production Celery prefork tasks execute on the process main thread.
        # The global Celery soft/hard limits remain the fallback on platforms
        # without interval timers.
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)

    def deadline_reached(_signum, _frame):
        raise JobExecutionTimeout(f"AutomationJob exceeded {timeout} seconds")

    signal.signal(signal.SIGALRM, deadline_reached)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _active_control(controls, scope, *, automatic=False):
    global_control = controls.get(AutomationScope.GLOBAL)
    control = controls.get(scope)
    if (
        not global_control
        or not global_control.enabled
        or not control
        or not control.enabled
    ):
        return False
    if automatic and control.mode != "AUTOMATIC":
        return False
    now = datetime.now(UTC)
    for item in (global_control, control):
        cooldown = item.cooldown_until
        if cooldown and cooldown.tzinfo is None:
            cooldown = cooldown.replace(tzinfo=UTC)
        if cooldown and cooldown > now:
            return False
    return True


def _bucket(interval_seconds):
    interval = max(60, int(interval_seconds))
    return int(datetime.now(UTC).timestamp()) // interval


def _global_control_active(controls):
    control = controls.get(AutomationScope.GLOBAL)
    if control is None or not control.enabled:
        return False
    cooldown = control.cooldown_until
    if cooldown and cooldown.tzinfo is None:
        cooldown = cooldown.replace(tzinfo=UTC)
    return not cooldown or cooldown <= datetime.now(UTC)


def _scheduled_job(db, *, job_type, key, payload, user_id):
    existing = db.scalar(
        select(AutomationJob).where(AutomationJob.idempotency_key == key)
    )
    if existing:
        return None
    job = AutomationJob(
        type=job_type,
        status=JobStatus.PENDING,
        idempotency_key=key,
        correlation_id=f"scheduler:{key}",
        payload={**payload, "_requested_by_user_id": user_id},
        max_attempts=3,
        timeout_seconds=300,
    )
    db.add(job)
    db.flush()
    write_audit(
        db,
        action="AUTOMATION_JOB_SCHEDULED",
        entity_type="AUTOMATION_JOB",
        entity_id=job.id,
        actor_type="SYSTEM",
        after_data={"type": job_type.value, "idempotency_key": key},
        correlation_id=job.correlation_id,
    )
    return job.id


def _browser_reply_sources(db, *, owner_user_id: int, limit: int):
    """Return browser conversations whose latest message still needs one reply job."""

    latest_message = aliased(Message)
    latest_message_id = (
        select(latest_message.id)
        .where(latest_message.conversation_id == Conversation.id)
        .order_by(latest_message.created_at.desc(), latest_message.id.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )
    candidates = db.execute(
        select(
            Conversation.id,
            Message.id,
            Message.external_message_id,
        )
        .join(Account, Account.id == Conversation.account_id)
        .join(Message, Message.id == latest_message_id)
        .where(
            Account.owner_user_id == owner_user_id,
            Conversation.status == "OPEN",
            Conversation.manual_mode.is_(False),
            Conversation.external_conversation_id.like("browser-conv-%"),
            Message.direction == "INBOUND",
            Message.external_message_id.like("browser-msg-%"),
        )
        .order_by(Message.created_at, Message.id)
        .limit(limit)
    ).all()
    if not candidates:
        return []

    conversation_ids = [row[0] for row in candidates]
    conversation_field = AutomationJob.payload["conversation_id"].as_integer()
    existing_payloads = db.scalars(
        select(AutomationJob.payload).where(
            AutomationJob.type == JobType.REPLY_MESSAGE,
            conversation_field.in_(conversation_ids),
        )
    ).all()
    existing_sources = {
        (
            int(payload.get("conversation_id") or 0),
            int(payload.get("source_message_id") or 0),
        )
        for payload in existing_payloads
        if isinstance(payload, dict)
    }
    return [
        row
        for row in candidates
        if (int(row[0]), int(row[1])) not in existing_sources
    ]


@celery_app.task(name="workers.schedule_automation")
def schedule_automation():
    settings = get_settings()
    browser_publish_enabled = bool(
        getattr(settings, "xianyu_browser_publish_enabled", False)
    )
    browser_bridge_configured = configured_bridge_token() is not None
    browser_metrics_enabled = browser_bridge_configured
    browser_customer_service_enabled = bool(
        getattr(settings, "xianyu_browser_customer_service_enabled", False)
        and browser_bridge_configured
    )
    if not settings.automation_scheduler_enabled or not (
        settings.adapters_enabled or browser_publish_enabled or browser_metrics_enabled
    ):
        return {"status": "DISABLED", "scheduled": 0}
    db = SessionLocal()
    try:
        controls = {
            item.scope: item for item in db.scalars(select(AutomationControl)).all()
        }
        owner = db.scalar(
            select(User)
            .where(User.role == "admin", User.is_active.is_(True))
            .order_by(User.id)
        )
        if owner is None:
            return {"status": "NO_OPERATOR", "scheduled": 0}
        job_ids = []
        bridge_task_ids = []
        metric_task_ids = []
        conversation_task_ids = []
        batch_size = max(1, min(200, settings.automation_scheduler_batch_size))
        local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
        if (
            browser_metrics_enabled
            and _global_control_active(controls)
            and local_now.time() >= time(10, 0)
        ):
            metric_date = local_now.date() - timedelta(days=1)
            metric_key = f"browser:auto:metrics:{metric_date.isoformat()}"
            exists = db.scalar(
                select(PlatformActionRecord.id).where(
                    PlatformActionRecord.idempotency_key == metric_key
                )
            )
            if exists is None:
                task = create_automatic_metrics_task(
                    db,
                    user=owner,
                    idempotency_key=metric_key,
                    correlation_id=f"scheduler:browser:metrics:{metric_date.isoformat()}",
                )
                bridge_task_ids.append(task.id)
                metric_task_ids.append(task.id)
        if browser_customer_service_enabled and _active_control(
            controls,
            AutomationScope.CUSTOMER_SERVICE,
            automatic=True,
        ):
            message_bucket = _bucket(settings.automation_message_interval_seconds)
            capture_key = f"browser:auto:conversations:{message_bucket}"
            capture_exists = db.scalar(
                select(PlatformActionRecord.id).where(
                    PlatformActionRecord.idempotency_key == capture_key
                )
            )
            if capture_exists is None:
                capture_task = create_automatic_conversation_capture_task(
                    db,
                    user=owner,
                    idempotency_key=capture_key,
                    correlation_id=f"scheduler:browser:conversations:{message_bucket}",
                )
                bridge_task_ids.append(capture_task.id)
                conversation_task_ids.append(capture_task.id)

            for conversation_id, source_message_id, external_message_id in (
                _browser_reply_sources(
                    db,
                    owner_user_id=owner.id,
                    limit=batch_size,
                )
            ):
                source_digest = hashlib.sha256(
                    f"{conversation_id}:{source_message_id}:{external_message_id}".encode()
                ).hexdigest()[:32]
                job_id = _scheduled_job(
                    db,
                    job_type=JobType.REPLY_MESSAGE,
                    key=f"browser:auto:reply:{source_digest}",
                    payload={
                        "execution_path": "LOCAL_BROWSER_BRIDGE",
                        "conversation_id": conversation_id,
                        "source_message_id": source_message_id,
                        "external_message_id": external_message_id,
                    },
                    user_id=owner.id,
                )
                if job_id:
                    job_ids.append(job_id)
        if settings.adapters_enabled and _active_control(
            controls, AutomationScope.CUSTOMER_SERVICE
        ):
            bucket = _bucket(settings.automation_message_interval_seconds)
            job_id = _scheduled_job(
                db,
                job_type=JobType.SYNC_MESSAGES,
                key=f"auto:messages:{bucket}",
                payload={},
                user_id=owner.id,
            )
            if job_id:
                job_ids.append(job_id)
        if settings.adapters_enabled and _active_control(controls, AutomationScope.LOGISTICS):
            bucket = _bucket(settings.automation_order_interval_seconds)
            job_id = _scheduled_job(
                db,
                job_type=JobType.SYNC_ORDERS,
                key=f"auto:orders:{bucket}",
                payload={},
                user_id=owner.id,
            )
            if job_id:
                job_ids.append(job_id)
        if settings.adapters_enabled and _active_control(controls, AutomationScope.PURCHASE):
            orders = db.scalars(
                select(Order)
                .where(
                    Order.status.in_([OrderStatus.PAID, OrderStatus.PURCHASE_PENDING])
                )
                .order_by(Order.paid_at, Order.id)
                .limit(max(1, min(200, settings.automation_scheduler_batch_size)))
            ).all()
            for order in orders:
                job_id = _scheduled_job(
                    db,
                    job_type=JobType.CREATE_PURCHASE,
                    key=f"auto:purchase:{order.id}",
                    payload={"order_id": order.id},
                    user_id=owner.id,
                )
                if job_id:
                    job_ids.append(job_id)
        if settings.adapters_enabled and _active_control(controls, AutomationScope.REPRICING):
            bucket = _bucket(settings.automation_supplier_interval_seconds)
            sku_ids = db.scalars(
                select(ProductSKU.id).order_by(ProductSKU.id).limit(batch_size)
            ).all()
            for sku_id in sku_ids:
                for job_type, label in (
                    (JobType.SYNC_SUPPLIER_PRICE, "price"),
                    (JobType.SYNC_SUPPLIER_STOCK, "stock"),
                ):
                    job_id = _scheduled_job(
                        db,
                        job_type=job_type,
                        key=f"auto:{label}:{sku_id}:{bucket}",
                        payload={"product_sku_id": sku_id},
                        user_id=owner.id,
                    )
                    if job_id:
                        job_ids.append(job_id)
        if (settings.adapters_enabled or browser_publish_enabled) and _active_control(
            controls, AutomationScope.PUBLISH, automatic=True
        ):
            drafts = db.scalars(
                select(XianyuDraft)
                .where(XianyuDraft.status == "REVIEW_READY")
                .order_by(XianyuDraft.created_at, XianyuDraft.id)
                .limit(batch_size)
            ).all()
            for draft in drafts:
                job_id = _scheduled_job(
                    db,
                    job_type=JobType.PUBLISH_PRODUCT,
                    key=f"auto:publish:{draft.id}",
                    payload={"target_type": "XIANYU_DRAFT", "target_id": draft.id},
                    user_id=owner.id,
                )
                if job_id:
                    job_ids.append(job_id)
        db.commit()
        for job_id in job_ids:
            celery_app.send_task("workers.execute_automation_job", args=[job_id])
        return {
            "status": "OK",
            "scheduled": len(job_ids) + len(bridge_task_ids),
            "browser_metric_tasks": metric_task_ids,
            "browser_conversation_tasks": conversation_task_ids,
        }
    finally:
        db.close()


def _enabled(db, scope):
    global_control = db.scalar(
        select(AutomationControl).where(
            AutomationControl.scope == AutomationScope.GLOBAL
        )
    )
    if global_control is None or not global_control.enabled:
        return False, "全局自动化已停止"
    if scope is None:
        return True, None
    control = db.scalar(
        select(AutomationControl).where(AutomationControl.scope == scope)
    )
    if control is None or not control.enabled:
        return False, f"{scope.value} 自动化已停止"
    return True, None


def _ensure_manual_task(db, job, code, reason):
    exists = db.scalar(
        select(ManualTask).where(
            ManualTask.automation_job_id == job.id,
            ManualTask.status == "OPEN",
        )
    )
    if exists is None:
        db.add(
            ManualTask(
                automation_job_id=job.id,
                type=code,
                title=f"任务 {job.type.value} 需要人工处理",
                reason=reason,
                priority=80,
                entity_type="AUTOMATION_JOB",
                entity_id=str(job.id),
            )
        )


def _manual(db, job, code, reason):
    job.status = JobStatus.MANUAL_REQUIRED
    job.error_code = code
    job.error_message = reason
    job.finished_at = datetime.now(UTC)
    _release_lease(job)
    _ensure_manual_task(db, job, code, reason)


def _retry(job, *, code, reason):
    job.status = JobStatus.RETRY
    job.error_code = code
    job.error_message = reason
    _release_lease(job)


def _succeeded(db, job, summary):
    job.result_summary = summary
    job.status = JobStatus.SUCCEEDED
    job.error_code = None
    job.error_message = None
    job.finished_at = datetime.now(UTC)
    _release_lease(job)
    db.commit()
    return {"status": "SUCCEEDED", **summary}


def _expired_running_clause(now: datetime):
    legacy_cutoff = now - timedelta(seconds=LEGACY_RUNNING_EXPIRY_SECONDS)
    return and_(
        AutomationJob.status == JobStatus.RUNNING,
        or_(
            AutomationJob.lease_expires_at <= now,
            and_(
                AutomationJob.lease_expires_at.is_(None),
                func.coalesce(
                    AutomationJob.heartbeat_at,
                    AutomationJob.started_at,
                    AutomationJob.updated_at,
                    AutomationJob.created_at,
                )
                <= legacy_cutoff,
            ),
        ),
    )


def _recover_expired_jobs(db, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    snapshots = db.scalars(
        select(AutomationJob).where(_expired_running_clause(now)).order_by(AutomationJob.id)
    ).all()
    retry_job_ids: list[int] = []
    manual_job_ids: list[int] = []
    for snapshot in snapshots:
        external_write = snapshot.type in EXTERNAL_WRITE_JOB_TYPES
        exhausted = snapshot.attempts >= snapshot.max_attempts
        if external_write:
            status = JobStatus.MANUAL_REQUIRED
            code = "WRITE_RESULT_UNKNOWN"
            reason = (
                "外部写动作执行器失联且租约已过期，平台结果可能不明确；"
                "已禁止自动重试，请人工对账。"
            )
        elif exhausted:
            status = JobStatus.MANUAL_REQUIRED
            code = "LEASE_EXPIRED"
            reason = "任务执行器失联且已达到最大尝试次数，请人工检查。"
        else:
            status = JobStatus.RETRY
            code = "LEASE_EXPIRED"
            reason = "任务执行器失联且租约已过期，已安全回收等待重试。"

        recovered_id = db.scalar(
            update(AutomationJob)
            .where(
                AutomationJob.id == snapshot.id,
                _expired_running_clause(now),
            )
            .values(
                status=status,
                error_code=code,
                error_message=reason,
                finished_at=now if status == JobStatus.MANUAL_REQUIRED else None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .returning(AutomationJob.id)
        )
        if recovered_id is None:
            continue
        if status == JobStatus.RETRY:
            retry_job_ids.append(recovered_id)
        else:
            manual_job_ids.append(recovered_id)
            job = db.get(AutomationJob, recovered_id)
            _ensure_manual_task(db, job, code, reason)
    db.commit()
    return {
        "retry_job_ids": retry_job_ids,
        "manual_job_ids": manual_job_ids,
    }


@celery_app.task(name="workers.recover_expired_automation_jobs")
def recover_expired_automation_jobs():
    db = SessionLocal()
    try:
        recovered = _recover_expired_jobs(db)
        retry_job_ids = db.scalars(
            select(AutomationJob.id).where(
                AutomationJob.status == JobStatus.RETRY,
                AutomationJob.error_code == "LEASE_EXPIRED",
            )
        ).all()
        for retry_job_id in retry_job_ids:
            celery_app.send_task(
                "workers.execute_automation_job",
                args=[retry_job_id],
            )
        return {
            "status": "OK",
            "requeued": len(retry_job_ids),
            "manual_required": len(recovered["manual_job_ids"]),
        }
    finally:
        db.close()


def _browser_reply_job_source(db, job: AutomationJob, owner: User):
    conversation_id = int(job.payload.get("conversation_id") or 0)
    source_message_id = int(job.payload.get("source_message_id") or 0)
    external_message_id = str(job.payload.get("external_message_id") or "")
    if not conversation_id or not source_message_id or not external_message_id:
        raise ValueError("浏览器自动回复任务缺少会话或消息来源")
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise ValueError("浏览器自动回复会话不存在")
    account = db.get(Account, conversation.account_id)
    if account is None or account.owner_user_id != owner.id:
        raise ValueError("浏览器自动回复会话不属于执行账号")
    if conversation.manual_mode:
        raise ValueError("客服会话已切换人工接管模式")
    latest = db.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    if (
        latest is None
        or latest.direction != "INBOUND"
        or latest.id != source_message_id
        or latest.external_message_id != external_message_id
    ):
        raise ValueError("会话最新消息已变化，自动回复任务已失效")
    return conversation, latest


def _execute_claimed_job(db, job: AutomationJob):
    if job.type == JobType.ANALYZE_SKU:
        return _succeeded(
            db,
            job,
            {"validated": True, "mode": "LOCAL_RULE_ENGINE"},
        )

    if job.type == JobType.GENERATE_PRODUCT:
        launch_id = int(job.payload.get("launch_id") or 0)
        if not launch_id:
            raise ValueError("GENERATE_PRODUCT 缺少 launch_id")
        result = execute_autonomous_launch(db, launch_id)
        return _succeeded(
            db,
            job,
            {
                "autonomous_launch_id": launch_id,
                "launch_status": result["status"],
                "candidate_id": result["sourcing_candidate_id"],
                "product_id": result["product_id"],
                "draft_id": result["draft_id"],
            },
        )

    settings = get_settings()
    if job.type == JobType.PUBLISH_PRODUCT and bool(
        getattr(settings, "xianyu_browser_publish_enabled", False)
    ):
        draft_id = int(job.payload.get("target_id") or job.payload.get("draft_id") or 0)
        draft = db.get(XianyuDraft, draft_id)
        if draft is None:
            raise ValueError("PUBLISH_PRODUCT 缺少有效 draft_id")
        owner = db.get(User, int(job.payload.get("_requested_by_user_id") or 0))
        if owner is None or not owner.is_active:
            raise ValueError("PUBLISH_PRODUCT 缺少有效执行账号")
        task = create_automatic_publish_task(
            db,
            draft_id=draft.id,
            user=owner,
            idempotency_key=f"browser:auto:publish:{draft.id}:{draft.input_hash}",
            correlation_id=job.correlation_id,
        )
        return _succeeded(
            db,
            job,
            {
                "browser_task_id": task.id,
                "browser_task_status": task.status,
                "execution_path": "LOCAL_BROWSER_BRIDGE",
            },
        )

    if (
        job.type == JobType.REPLY_MESSAGE
        and job.payload.get("execution_path") == "LOCAL_BROWSER_BRIDGE"
    ):
        owner = db.get(User, int(job.payload.get("_requested_by_user_id") or 0))
        if owner is None or not owner.is_active:
            _manual(
                db,
                job,
                "BROWSER_REPLY_OPERATOR_INVALID",
                "浏览器自动回复任务缺少有效执行账号",
            )
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": "BROWSER_REPLY_OPERATOR_INVALID"}
        blockers = automatic_customer_service_blockers(
            db,
            enforce_action_limits=False,
        )
        if blockers:
            _manual(
                db,
                job,
                "AUTOMATIC_CUSTOMER_SERVICE_POLICY_BLOCKED",
                "；".join(blockers),
            )
            db.commit()
            return {
                "status": "MANUAL_REQUIRED",
                "reason": "AUTOMATIC_CUSTOMER_SERVICE_POLICY_BLOCKED",
            }
        try:
            conversation, latest = _browser_reply_job_source(db, job, owner)
        except ValueError as exc:
            _manual(db, job, "STALE_REPLY_SOURCE", str(exc))
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": "STALE_REPLY_SOURCE"}

        suggestion = generate_reply_suggestion(db, conversation.id)
        decision = suggestion.decision or {}
        if (
            decision.get("source_message_id") != latest.id
            or decision.get("external_message_id") != latest.external_message_id
        ):
            _manual(
                db,
                job,
                "STALE_REPLY_SUGGESTION",
                "回复建议没有绑定当前最新买家消息",
            )
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": "STALE_REPLY_SUGGESTION"}
        if (
            not decision.get("auto_eligible")
            or decision.get("requires_human")
            or not decision.get("reply")
        ):
            _manual(
                db,
                job,
                "CUSTOMER_SERVICE_REVIEW_REQUIRED",
                suggestion.reason_summary or "客服建议需要人工复核",
            )
            db.commit()
            return {
                "status": "MANUAL_REQUIRED",
                "reason": "CUSTOMER_SERVICE_REVIEW_REQUIRED",
            }
        try:
            task = create_automatic_reply_task(
                db,
                conversation_id=conversation.id,
                suggestion_id=suggestion.id,
                user=owner,
                idempotency_key=f"browser:auto:reply:{suggestion.input_context_hash}",
                correlation_id=job.correlation_id,
            )
        except (LookupError, ValueError) as exc:
            _manual(db, job, "AUTOMATIC_REPLY_REVALIDATION_BLOCKED", str(exc))
            db.commit()
            return {
                "status": "MANUAL_REQUIRED",
                "reason": "AUTOMATIC_REPLY_REVALIDATION_BLOCKED",
            }
        return _succeeded(
            db,
            job,
            {
                "browser_task_id": task.id,
                "browser_task_status": task.status,
                "suggestion_id": suggestion.id,
                "execution_path": "LOCAL_BROWSER_BRIDGE",
            },
        )

    if not settings.adapters_enabled:
        _manual(db, job, "ADAPTERS_DISABLED", "真实平台 Adapter 总开关未启用")
        db.commit()
        return {"status": "MANUAL_REQUIRED", "reason": "ADAPTERS_DISABLED"}

    if job.type in {JobType.SYNC_MESSAGES, JobType.SYNC_ORDERS}:
        return _succeeded(db, job, asyncio.run(sync_xianyu_job(db, job)))
    if job.type in {JobType.SYNC_SUPPLIER_PRICE, JobType.SYNC_SUPPLIER_STOCK}:
        return _succeeded(db, job, asyncio.run(sync_supplier_job(db, job)))
    if job.type in EXTERNAL_WRITE_JOB_TYPES:
        action = asyncio.run(execute_platform_job(db, job))
        summary = {
            "platform_action_id": action.id,
            "platform_action_status": action.status,
        }
        if action.status == "SUCCEEDED":
            return _succeeded(db, job, summary)
        reason = action.error_message or "; ".join(action.preview.get("blockers", []))
        if action.status == "PREVIEWED":
            reason = "动作已通过校验，等待操作员确认"
        _manual(db, job, "PLATFORM_ACTION_REVIEW", reason or "平台动作需要人工复核")
        job.result_summary = summary
        db.commit()
        return {"status": "MANUAL_REQUIRED", **summary}

    _manual(db, job, "NO_SAFE_AUTOMATION", "该任务尚无可安全执行的自动化路径")
    db.commit()
    return {"status": "MANUAL_REQUIRED", "reason": "NO_SAFE_AUTOMATION"}


@celery_app.task(bind=True, name="workers.execute_automation_job", max_retries=2)
def execute_automation_job(self, job_id):
    db = SessionLocal()
    try:
        lease_owner = str(
            getattr(getattr(self, "request", None), "id", None)
            or f"local-{uuid.uuid4()}"
        )
        job = _claim_automation_job(db, job_id, lease_owner=lease_owner)
        if job is None:
            current = db.get(AutomationJob, job_id)
            if current is None:
                return {"status": "NOT_FOUND"}
            if current.status == JobStatus.SUCCEEDED:
                return {"status": "SUCCEEDED", "idempotent": True}
            if (
                current.status in {JobStatus.PENDING, JobStatus.RETRY}
                and current.attempts >= current.max_attempts
            ):
                _manual(
                    db,
                    current,
                    "RETRY_EXHAUSTED",
                    "任务达到最大尝试次数，未再次执行。",
                )
                db.commit()
                return {"status": "MANUAL_REQUIRED", "reason": "RETRY_EXHAUSTED"}
            return {"status": current.status.value, "claimed": False}

        enabled, reason = _enabled(db, SCOPE_BY_JOB.get(job.type))
        if not enabled:
            if job.type == JobType.GENERATE_PRODUCT:
                launch = db.get(
                    AutonomousLaunch, int(job.payload.get("launch_id") or 0)
                )
                if launch:
                    launch.status = "MANUAL_REQUIRED"
                    launch.error_code = "AUTOMATION_DISABLED"
                    launch.error_message = reason
                    launch.blockers = [reason]
                    launch.finished_at = datetime.now(UTC)
            _manual(db, job, "AUTOMATION_DISABLED", reason)
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": reason}

        with _lease_heartbeat(job), _job_execution_deadline(job.timeout_seconds):
            return _execute_claimed_job(db, job)
    except JobExecutionTimeout as exc:
        db.rollback()
        job = db.get(AutomationJob, job_id)
        if job.type in EXTERNAL_WRITE_JOB_TYPES:
            _manual(
                db,
                job,
                "WRITE_RESULT_UNKNOWN",
                "外部写动作执行超时，平台结果可能不明确；已禁止自动重试，请人工对账。",
            )
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": "WRITE_RESULT_UNKNOWN"}
        if job.attempts >= job.max_attempts:
            _manual(
                db,
                job,
                "JOB_TIMEOUT",
                f"任务超过 {job.timeout_seconds} 秒且已达到最大尝试次数。",
            )
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": "JOB_TIMEOUT"}
        _retry(
            job,
            code="JOB_TIMEOUT",
            reason=f"任务超过 {job.timeout_seconds} 秒，等待安全重试。",
        )
        db.commit()
        try:
            raise self.retry(exc=exc, countdown=60 if job.attempts <= 1 else 300)
        except MaxRetriesExceededError:
            _manual(db, job, "JOB_TIMEOUT", "任务超时且 Celery 重试次数已耗尽。")
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": "JOB_TIMEOUT"}
    except LaunchManualRequired as exc:
        db.rollback()
        job = db.get(AutomationJob, job_id)
        launch = db.get(AutonomousLaunch, int(job.payload.get("launch_id") or 0))
        _manual(db, job, exc.code, exc.safe_message)
        job.result_summary = {
            "autonomous_launch_id": launch.id if launch else None,
            "launch_status": launch.status if launch else "MANUAL_REQUIRED",
        }
        db.commit()
        return {"status": "MANUAL_REQUIRED", "reason": exc.code}
    except AIInferenceError as exc:
        db.rollback()
        job = db.get(AutomationJob, job_id)
        launch = db.get(AutonomousLaunch, int(job.payload.get("launch_id") or 0))
        if not exc.retryable or job.attempts >= job.max_attempts:
            if launch:
                launch.status = "MANUAL_REQUIRED"
                launch.error_code = exc.code
                launch.error_message = exc.safe_message
                launch.blockers = [exc.safe_message]
                launch.finished_at = datetime.now(UTC)
            _manual(db, job, exc.code, exc.safe_message)
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": exc.code}
        _retry(job, code=exc.code, reason=exc.safe_message)
        if launch:
            launch.status = "RETRY"
            launch.error_code = exc.code
            launch.error_message = exc.safe_message
        db.commit()
        raise self.retry(exc=exc, countdown=60 if job.attempts <= 1 else 300) from exc
    except RuntimeAdapterError as exc:
        db.rollback()
        job = db.get(AutomationJob, job_id)
        result = exc.result
        if job.type in EXTERNAL_WRITE_JOB_TYPES:
            _manual(
                db,
                job,
                "WRITE_RESULT_UNKNOWN",
                result.safe_message
                or "外部写动作返回不确定结果，已禁止自动重试，请人工对账。",
            )
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": "WRITE_RESULT_UNKNOWN"}
        if not result.retryable or job.attempts >= job.max_attempts:
            _manual(
                db,
                job,
                result.error_code or result.status.value,
                result.safe_message or "Adapter 调用失败",
            )
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": result.error_code}
        _retry(
            job,
            code=result.error_code or "TRANSIENT_ERROR",
            reason=result.safe_message or "Adapter 暂时不可用",
        )
        db.commit()
        raise self.retry(exc=exc, countdown=60 if job.attempts <= 1 else 300) from exc
    except ValueError as exc:
        db.rollback()
        job = db.get(AutomationJob, job_id)
        _manual(db, job, "INVALID_JOB_PAYLOAD", str(exc))
        db.commit()
        return {"status": "MANUAL_REQUIRED", "reason": "INVALID_JOB_PAYLOAD"}
    except Exception as exc:
        db.rollback()
        job = db.get(AutomationJob, job_id)
        if job is None:
            raise
        if job.type in EXTERNAL_WRITE_JOB_TYPES:
            _manual(
                db,
                job,
                "WRITE_RESULT_UNKNOWN",
                "外部写动作发生未预期中断，平台结果可能不明确；"
                "已禁止自动重试，请人工对账。",
            )
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": "WRITE_RESULT_UNKNOWN"}
        if job.attempts >= job.max_attempts:
            _manual(db, job, "RETRY_EXHAUSTED", type(exc).__name__)
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": "RETRY_EXHAUSTED"}
        _retry(job, code="TRANSIENT_ERROR", reason=type(exc).__name__)
        db.commit()
        countdown = 60 if job.attempts <= 1 else 300
        try:
            raise self.retry(exc=exc, countdown=countdown)
        except MaxRetriesExceededError:
            _manual(db, job, "RETRY_EXHAUSTED", type(exc).__name__)
            db.commit()
            return {"status": "MANUAL_REQUIRED", "reason": "RETRY_EXHAUSTED"}
    finally:
        db.close()
