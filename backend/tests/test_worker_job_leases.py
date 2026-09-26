import time as wall_time
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from workers import tasks

from app.models import (
    AutomationControl,
    AutomationJob,
    AutomationScope,
    Base,
    JobStatus,
    JobType,
    ManualTask,
)
from app.worker import celery_app


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _add_job(
    db,
    *,
    key: str,
    job_type: JobType = JobType.ANALYZE_SKU,
    status: JobStatus = JobStatus.PENDING,
    attempts: int = 0,
    max_attempts: int = 3,
    timeout_seconds: int = 60,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
):
    job = AutomationJob(
        type=job_type,
        status=status,
        idempotency_key=key,
        correlation_id=f"test:{key}",
        payload={},
        attempts=attempts,
        max_attempts=max_attempts,
        timeout_seconds=timeout_seconds,
        started_at=datetime.now(UTC) if status == JobStatus.RUNNING else None,
        heartbeat_at=datetime.now(UTC) if status == JobStatus.RUNNING else None,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
    )
    db.add(job)
    db.commit()
    return job


def _enable(db, *scopes: AutomationScope):
    db.add(
        AutomationControl(
            scope=AutomationScope.GLOBAL,
            enabled=True,
            mode="REVIEW",
        )
    )
    for scope in scopes:
        db.add(AutomationControl(scope=scope, enabled=True, mode="AUTOMATIC"))
    db.commit()


def test_atomic_claim_only_allows_one_pending_delivery():
    factory = _session_factory()
    with factory() as db:
        job = _add_job(db, key="lease-atomic-claim")
        job_id = job.id

    with factory() as first, factory() as second:
        claimed = tasks._claim_automation_job(first, job_id, lease_owner="worker-a")
        duplicate = tasks._claim_automation_job(second, job_id, lease_owner="worker-b")

    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING
    assert claimed.attempts == 1
    assert claimed.lease_owner == "worker-a"
    assert claimed.heartbeat_at is not None
    assert claimed.lease_expires_at is not None
    assert duplicate is None

    with factory() as db:
        stored = db.get(AutomationJob, job_id)
        assert stored.status == JobStatus.RUNNING
        assert stored.attempts == 1
        assert stored.lease_owner == "worker-a"


def test_lease_heartbeat_requires_owner_and_renews_expiry():
    factory = _session_factory()
    with factory() as db:
        job = _add_job(db, key="lease-heartbeat")
        claimed = tasks._claim_automation_job(db, job.id, lease_owner="worker-a")
        original_expiry = claimed.lease_expires_at

    with factory() as db:
        assert not tasks._renew_automation_job_lease(
            db,
            job_id=job.id,
            lease_owner="worker-b",
            timeout_seconds=60,
        )
        wall_time.sleep(0.01)
        assert tasks._renew_automation_job_lease(
            db,
            job_id=job.id,
            lease_owner="worker-a",
            timeout_seconds=60,
        )

    with factory() as db:
        stored = db.get(AutomationJob, job.id)
        assert stored.heartbeat_at is not None
        stored_expiry = stored.lease_expires_at.replace(tzinfo=UTC)
        original_expiry = original_expiry.replace(tzinfo=UTC)
        assert stored_expiry >= original_expiry


def test_expired_read_job_retries_but_write_job_requires_reconciliation():
    factory = _session_factory()
    now = datetime.now(UTC)
    with factory() as db:
        read_job = _add_job(
            db,
            key="expired-read-job",
            status=JobStatus.RUNNING,
            attempts=1,
            lease_owner="dead-worker-read",
            lease_expires_at=now - timedelta(seconds=1),
        )
        write_job = _add_job(
            db,
            key="expired-write-job",
            job_type=JobType.PUBLISH_PRODUCT,
            status=JobStatus.RUNNING,
            attempts=1,
            lease_owner="dead-worker-write",
            lease_expires_at=now - timedelta(seconds=1),
        )
        active_job = _add_job(
            db,
            key="active-job",
            status=JobStatus.RUNNING,
            attempts=1,
            lease_owner="live-worker",
            lease_expires_at=now + timedelta(minutes=1),
        )

        recovered = tasks._recover_expired_jobs(db, now=now)

        db.expire_all()
        read_job = db.get(AutomationJob, read_job.id)
        write_job = db.get(AutomationJob, write_job.id)
        active_job = db.get(AutomationJob, active_job.id)
        manual = db.scalar(
            select(ManualTask).where(ManualTask.automation_job_id == write_job.id)
        )

    assert recovered["retry_job_ids"] == [read_job.id]
    assert recovered["manual_job_ids"] == [write_job.id]
    assert read_job.status == JobStatus.RETRY
    assert read_job.error_code == "LEASE_EXPIRED"
    assert read_job.lease_owner is None
    assert write_job.status == JobStatus.MANUAL_REQUIRED
    assert write_job.error_code == "WRITE_RESULT_UNKNOWN"
    assert write_job.lease_owner is None
    assert manual is not None
    assert manual.type == "WRITE_RESULT_UNKNOWN"
    assert active_job.status == JobStatus.RUNNING
    assert active_job.lease_owner == "live-worker"


def test_recovery_sweep_requeues_only_safe_expired_job(monkeypatch):
    factory = _session_factory()
    now = datetime.now(UTC)
    with factory() as db:
        read_job = _add_job(
            db,
            key="requeue-expired-read",
            status=JobStatus.RUNNING,
            attempts=1,
            lease_owner="dead-read-worker",
            lease_expires_at=now - timedelta(seconds=1),
        )
        write_job = _add_job(
            db,
            key="do-not-requeue-expired-write",
            job_type=JobType.REPLY_MESSAGE,
            status=JobStatus.RUNNING,
            attempts=1,
            lease_owner="dead-write-worker",
            lease_expires_at=now - timedelta(seconds=1),
        )
        read_job_id = read_job.id
        write_job_id = write_job.id

    sent = []
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(
        tasks.celery_app,
        "send_task",
        lambda name, args: sent.append((name, args)),
    )

    result = tasks.recover_expired_automation_jobs.run()

    assert result == {"status": "OK", "requeued": 1, "manual_required": 1}
    assert sent == [("workers.execute_automation_job", [read_job_id])]
    with factory() as db:
        assert db.get(AutomationJob, read_job_id).status == JobStatus.RETRY
        assert db.get(AutomationJob, write_job_id).status == JobStatus.MANUAL_REQUIRED


def test_execute_job_is_idempotent_after_atomic_success(monkeypatch):
    factory = _session_factory()
    with factory() as db:
        _enable(db)
        job = _add_job(db, key="execute-once")
        job_id = job.id

    monkeypatch.setattr(tasks, "SessionLocal", factory)

    first = tasks.execute_automation_job.run(job_id)
    second = tasks.execute_automation_job.run(job_id)

    assert first["status"] == "SUCCEEDED"
    assert second == {"status": "SUCCEEDED", "idempotent": True}
    with factory() as db:
        stored = db.get(AutomationJob, job_id)
        assert stored.status == JobStatus.SUCCEEDED
        assert stored.attempts == 1
        assert stored.lease_owner is None
        assert stored.lease_expires_at is None


def test_per_job_timeout_becomes_manual_after_attempt_limit(monkeypatch):
    factory = _session_factory()
    with factory() as db:
        _enable(db)
        job = _add_job(
            db,
            key="job-timeout",
            timeout_seconds=1,
            max_attempts=1,
        )
        job_id = job.id

    def run_too_long(_db, _job):
        wall_time.sleep(2)

    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "_execute_claimed_job", run_too_long)

    result = tasks.execute_automation_job.run(job_id)

    assert result == {"status": "MANUAL_REQUIRED", "reason": "JOB_TIMEOUT"}
    with factory() as db:
        stored = db.get(AutomationJob, job_id)
        assert stored.status == JobStatus.MANUAL_REQUIRED
        assert stored.error_code == "JOB_TIMEOUT"
        assert stored.attempts == 1
        assert stored.lease_owner is None


def test_unexpected_write_interruption_never_retries(monkeypatch):
    factory = _session_factory()
    with factory() as db:
        _enable(db, AutomationScope.PUBLISH)
        job = _add_job(
            db,
            key="ambiguous-write",
            job_type=JobType.PUBLISH_PRODUCT,
        )
        job_id = job.id

    def interrupt_after_dispatch(_db, _job):
        raise RuntimeError("connection lost after dispatch")

    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "_execute_claimed_job", interrupt_after_dispatch)

    result = tasks.execute_automation_job.run(job_id)

    assert result == {
        "status": "MANUAL_REQUIRED",
        "reason": "WRITE_RESULT_UNKNOWN",
    }
    with factory() as db:
        stored = db.get(AutomationJob, job_id)
        manual = db.scalar(
            select(ManualTask).where(ManualTask.automation_job_id == job_id)
        )
        assert stored.status == JobStatus.MANUAL_REQUIRED
        assert stored.error_code == "WRITE_RESULT_UNKNOWN"
        assert stored.attempts == 1
        assert stored.lease_owner is None
        assert manual.type == "WRITE_RESULT_UNKNOWN"


def test_worker_loss_configuration_is_fail_safe():
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_cancel_long_running_tasks_on_connection_loss is True
    assert celery_app.conf.broker_transport_options["visibility_timeout"] == 1200
