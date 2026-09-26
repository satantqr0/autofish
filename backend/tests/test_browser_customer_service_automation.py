from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from workers import tasks

import app.services.xianyu_browser_bridge as bridge_service
from app.models import (
    Account,
    AutomationControl,
    AutomationJob,
    AutomationScope,
    Base,
    BrowserBridgeAgent,
    Conversation,
    Customer,
    JobStatus,
    JobType,
    ManualTask,
    Message,
    PlatformActionRecord,
    User,
)
from app.schemas.xianyu import BrowserBridgeAgentState
from app.services.customer_service import generate_reply_suggestion


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _settings(*, customer_service_enabled: bool):
    return SimpleNamespace(
        adapters_enabled=False,
        automation_scheduler_enabled=True,
        automation_message_interval_seconds=120,
        automation_scheduler_batch_size=20,
        xianyu_browser_bridge_token="test-browser-customer-service-token-32-chars",
        xianyu_browser_bridge_token_file=None,
        xianyu_browser_publish_enabled=False,
        xianyu_browser_customer_service_enabled=customer_service_enabled,
    )


def _configure(monkeypatch, factory, *, customer_service_enabled: bool):
    settings = _settings(customer_service_enabled=customer_service_enabled)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(bridge_service, "get_settings", lambda: settings)
    return settings


def _seed_conversation(db, *, content: str = "您好，在吗？"):
    user = User(
        username="admin",
        password_hash="unused",
        role="admin",
        is_active=True,
    )
    account = Account(
        platform="XIANYU",
        external_account_id="browser-customer-service-account",
        nickname="浏览器客服账号",
        status="BROWSER_CONNECTED",
        is_enabled=True,
        owner_user_id=None,
    )
    customer = Customer(
        platform="XIANYU",
        external_customer_id="buyer-" + "a" * 24,
        display_name_masked="买***家",
    )
    db.add_all([user, account, customer])
    db.flush()
    account.owner_user_id = user.id
    conversation = Conversation(
        account_id=account.id,
        customer_id=customer.id,
        external_conversation_id="browser-conv-" + "b" * 64,
        status="OPEN",
        manual_mode=False,
    )
    db.add(conversation)
    db.flush()
    message = Message(
        conversation_id=conversation.id,
        external_message_id="browser-msg-" + "c" * 64,
        direction="INBOUND",
        role="BUYER",
        content=content,
        created_at=datetime.now(UTC),
    )
    db.add(message)
    db.add_all(
        [
            AutomationControl(
                scope=AutomationScope.GLOBAL,
                enabled=True,
                mode="REVIEW",
                daily_limit=100,
                min_interval_seconds=1,
            ),
            AutomationControl(
                scope=AutomationScope.CUSTOMER_SERVICE,
                enabled=True,
                mode="AUTOMATIC",
                daily_limit=100,
                min_interval_seconds=1,
            ),
        ]
    )
    db.commit()
    return user, conversation, message


def _reply_job(db, *, user, conversation, message, key: str):
    job = AutomationJob(
        type=JobType.REPLY_MESSAGE,
        status=JobStatus.PENDING,
        idempotency_key=key,
        correlation_id=f"test:{key}",
        payload={
            "execution_path": "LOCAL_BROWSER_BRIDGE",
            "conversation_id": conversation.id,
            "source_message_id": message.id,
            "external_message_id": message.external_message_id,
            "_requested_by_user_id": user.id,
        },
        max_attempts=3,
        timeout_seconds=60,
    )
    db.add(job)
    db.commit()
    return job


def _agent_state():
    return BrowserBridgeAgentState(
        bridge_id="chrome-customer-service-test",
        name="测试 Chrome",
        version="0.5.1",
        capabilities=["SEND_REPLY"],
        current_url="https://seller.goofish.com/?site=COMMONPRO#/im",
    )


def test_closed_switch_never_queues_capture_or_reply(monkeypatch):
    factory = _session_factory()
    with factory() as db:
        user, conversation, message = _seed_conversation(db)
        job = _reply_job(
            db,
            user=user,
            conversation=conversation,
            message=message,
            key="closed-browser-customer-service",
        )
        job_id = job.id
    _configure(monkeypatch, factory, customer_service_enabled=False)
    sent = []
    monkeypatch.setattr(
        tasks.celery_app,
        "send_task",
        lambda name, args: sent.append((name, args)),
    )

    schedule_result = tasks.schedule_automation.run()
    execute_result = tasks.execute_automation_job.run(job_id)

    assert schedule_result["browser_conversation_tasks"] == []
    assert execute_result == {
        "status": "MANUAL_REQUIRED",
        "reason": "AUTOMATIC_CUSTOMER_SERVICE_POLICY_BLOCKED",
    }
    assert sent == []
    with factory() as db:
        stored = db.get(AutomationJob, job_id)
        assert stored.status == JobStatus.MANUAL_REQUIRED
        assert db.scalar(
            select(func.count(PlatformActionRecord.id)).where(
                PlatformActionRecord.action_type.in_([
                    "BROWSER_CAPTURE_CONVERSATIONS",
                    "BROWSER_SEND_REPLY",
                ])
            )
        ) == 0


def test_closed_switch_rejects_automatic_reply_at_creation(monkeypatch):
    factory = _session_factory()
    _configure(monkeypatch, factory, customer_service_enabled=False)
    with factory() as db:
        user, conversation, _message = _seed_conversation(db)
        suggestion = generate_reply_suggestion(db, conversation.id)

        with pytest.raises(
            ValueError,
            match="AUTOFISH_XIANYU_BROWSER_CUSTOMER_SERVICE_ENABLED=false",
        ):
            bridge_service.create_automatic_reply_task(
                db,
                conversation_id=conversation.id,
                suggestion_id=suggestion.id,
                user=user,
                idempotency_key="closed-automatic-browser-reply",
                correlation_id="test:closed-create",
            )

        assert db.scalar(select(func.count(PlatformActionRecord.id))) == 0


def test_scheduler_queues_capture_and_one_reply_job_per_source(monkeypatch):
    factory = _session_factory()
    with factory() as db:
        _seed_conversation(db)
    _configure(monkeypatch, factory, customer_service_enabled=True)
    sent = []
    monkeypatch.setattr(
        tasks.celery_app,
        "send_task",
        lambda name, args: sent.append((name, args)),
    )

    first = tasks.schedule_automation.run()
    second = tasks.schedule_automation.run()

    assert len(first["browser_conversation_tasks"]) == 1
    assert second["browser_conversation_tasks"] == []
    with factory() as db:
        assert db.scalar(
            select(func.count(PlatformActionRecord.id)).where(
                PlatformActionRecord.action_type == "BROWSER_CAPTURE_CONVERSATIONS"
            )
        ) == 1
        jobs = db.scalars(
            select(AutomationJob).where(AutomationJob.type == JobType.REPLY_MESSAGE)
        ).all()
        assert len(jobs) == 1
        assert jobs[0].payload["execution_path"] == "LOCAL_BROWSER_BRIDGE"
        assert jobs[0].payload["source_message_id"] > 0
    assert sent == [("workers.execute_automation_job", [jobs[0].id])]


def test_low_risk_reply_is_guarded_then_queued_without_platform_call(monkeypatch):
    factory = _session_factory()
    with factory() as db:
        user, conversation, message = _seed_conversation(db, content="您好，在吗？")
        job = _reply_job(
            db,
            user=user,
            conversation=conversation,
            message=message,
            key="low-risk-browser-reply",
        )
        job_id = job.id
    _configure(monkeypatch, factory, customer_service_enabled=True)

    result = tasks.execute_automation_job.run(job_id)

    assert result["status"] == "SUCCEEDED"
    assert result["execution_path"] == "LOCAL_BROWSER_BRIDGE"
    with factory() as db:
        action = db.scalar(
            select(PlatformActionRecord).where(
                PlatformActionRecord.action_type == "BROWSER_SEND_REPLY"
            )
        )
        assert action is not None
        assert action.status == "QUEUED"
        assert action.automatic is True
        assert action.requires_confirmation is False
        assert action.attempts == 0
        assert action.request_payload["source_message_id"] == message.external_message_id


def test_message_change_blocks_automatic_reply_at_browser_claim(monkeypatch):
    factory = _session_factory()
    with factory() as db:
        user, conversation, message = _seed_conversation(db, content="您好，在吗？")
        job = _reply_job(
            db,
            user=user,
            conversation=conversation,
            message=message,
            key="stale-browser-reply",
        )
        job_id = job.id
    _configure(monkeypatch, factory, customer_service_enabled=True)
    assert tasks.execute_automation_job.run(job_id)["status"] == "SUCCEEDED"

    with factory() as db:
        action = db.scalar(
            select(PlatformActionRecord).where(
                PlatformActionRecord.action_type == "BROWSER_SEND_REPLY"
            )
        )
        db.add(
            Message(
                conversation_id=conversation.id,
                external_message_id="browser-msg-" + "d" * 64,
                direction="INBOUND",
                role="BUYER",
                content="补充问一下，能退款吗？",
                created_at=datetime.now(UTC) + timedelta(seconds=1),
            )
        )
        db.commit()

        claimed = bridge_service.claim_bridge_task(db, _agent_state())
        db.refresh(action)

        assert claimed is None
        assert action.status == "BLOCKED"
        assert action.error_code == "AUTOMATIC_REPLY_REVALIDATION_BLOCKED"
        assert action.attempts == 0
        assert db.scalar(select(func.count(BrowserBridgeAgent.id))) == 1


def test_suggestion_fingerprint_change_blocks_browser_claim(monkeypatch):
    factory = _session_factory()
    with factory() as db:
        user, conversation, message = _seed_conversation(db, content="您好，在吗？")
        job = _reply_job(
            db,
            user=user,
            conversation=conversation,
            message=message,
            key="fingerprint-browser-reply",
        )
        job_id = job.id
    _configure(monkeypatch, factory, customer_service_enabled=True)
    assert tasks.execute_automation_job.run(job_id)["status"] == "SUCCEEDED"

    with factory() as db:
        action = db.scalar(
            select(PlatformActionRecord).where(
                PlatformActionRecord.action_type == "BROWSER_SEND_REPLY"
            )
        )
        stored_message = db.get(Message, message.id)
        stored_message.content = "原消息内容已被重新同步修正"
        db.commit()

        claimed = bridge_service.claim_bridge_task(db, _agent_state())
        db.refresh(action)

        assert claimed is None
        assert action.status == "BLOCKED"
        assert action.error_code == "AUTOMATIC_REPLY_REVALIDATION_BLOCKED"
        assert "指纹" in action.error_message
        assert action.attempts == 0


def test_high_risk_reply_creates_manual_work_only(monkeypatch):
    factory = _session_factory()
    with factory() as db:
        user, conversation, message = _seed_conversation(
            db,
            content="我要退款并投诉，要求平台介入",
        )
        job = _reply_job(
            db,
            user=user,
            conversation=conversation,
            message=message,
            key="high-risk-browser-reply",
        )
        job_id = job.id
    _configure(monkeypatch, factory, customer_service_enabled=True)

    result = tasks.execute_automation_job.run(job_id)

    assert result == {
        "status": "MANUAL_REQUIRED",
        "reason": "CUSTOMER_SERVICE_REVIEW_REQUIRED",
    }
    with factory() as db:
        conversation = db.get(Conversation, conversation.id)
        assert conversation.manual_mode is True
        assert db.scalar(
            select(func.count(PlatformActionRecord.id)).where(
                PlatformActionRecord.action_type == "BROWSER_SEND_REPLY"
            )
        ) == 0
        assert db.scalar(select(func.count(ManualTask.id))) >= 1
