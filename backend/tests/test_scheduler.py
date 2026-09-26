from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from workers import tasks

from app.models import (
    AutomationControl,
    AutomationJob,
    AutomationScope,
    Base,
    Product,
    User,
    XianyuDraft,
)


def test_scheduler_uses_stable_publish_idempotency_key(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with Session(engine) as db:
        user = User(
            username="admin",
            password_hash="test",
            role="admin",
            is_active=True,
        )
        product = Product(
            internal_code="AF-SCHEDULER",
            title="调度测试商品",
            category="电脑配件",
        )
        db.add_all([user, product])
        db.flush()
        db.add_all(
            [
                AutomationControl(
                    scope=AutomationScope.GLOBAL,
                    enabled=True,
                    mode="REVIEW",
                ),
                AutomationControl(
                    scope=AutomationScope.PUBLISH,
                    enabled=True,
                    mode="AUTOMATIC",
                ),
                XianyuDraft(
                    product_id=product.id,
                    version=1,
                    title=product.title,
                    description="测试描述",
                    price=Decimal("99"),
                    category=product.category,
                    validation={"passed": True, "blockers": [], "warnings": []},
                    status="REVIEW_READY",
                    input_hash="scheduler-draft-hash",
                    created_by_user_id=user.id,
                ),
            ]
        )
        db.commit()

    sent = []
    settings = SimpleNamespace(
        automation_scheduler_enabled=True,
        adapters_enabled=True,
        automation_scheduler_batch_size=20,
    )
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda name, args: sent.append((name, args)))

    first = tasks.schedule_automation.run()
    second = tasks.schedule_automation.run()

    with Session(engine) as db:
        assert db.scalar(select(func.count(AutomationJob.id))) == 1
        job = db.scalar(select(AutomationJob))
        assert job.idempotency_key.startswith("auto:publish:")
    assert first["scheduled"] == 1
    assert second["scheduled"] == 0
    assert len(sent) == 1
