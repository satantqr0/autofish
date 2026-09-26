from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes import dashboard as dashboard_route
from app.api.routes.operations import operations_facts
from app.models import Account, AIDecision, Base, User, XianyuProductDailyMetric


def sqlite_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_operations_facts_has_meaningful_empty_sections():
    with sqlite_session() as db:
        result = operations_facts(_user=object(), db=db)

    assert result["agents"] == {
        "total_decisions": 0,
        "by_agent": [],
        "recent": [],
    }
    assert result["risk"]["open_manual_tasks"] == []
    assert result["risk"]["failed_jobs"] == []
    assert result["purchases"]["items"] == []
    assert result["logistics"]["items"] == []


def test_operations_facts_returns_real_ai_decisions():
    with sqlite_session() as db:
        db.add(
            AIDecision(
                agent="PRODUCT_AGENT",
                schema_version="1",
                input_context_hash="a" * 64,
                decision={"title": "真实标题"},
                confidence=Decimal("0.8750"),
                reason_summary="基于已核验商品事实",
                model="qwen-plus",
                provider="qwen",
                prompt_version="1",
            )
        )
        db.commit()

        result = operations_facts(_user=object(), db=db)

    assert result["agents"]["total_decisions"] == 1
    assert result["agents"]["by_agent"] == [{"agent": "PRODUCT_AGENT", "count": 1}]
    assert result["agents"]["recent"][0]["reason_summary"] == "基于已核验商品事实"


def test_dashboard_live_traffic_uses_imported_xianyu_metrics(monkeypatch):
    monkeypatch.setattr(
        dashboard_route,
        "get_settings",
        lambda: SimpleNamespace(seed_demo=False, data_mode="LIVE_READY"),
    )
    with sqlite_session() as db:
        user = User(username="admin", password_hash="test")
        db.add(user)
        db.flush()
        account = Account(
            platform="XIANYU",
            external_account_id="account-1",
            nickname="验收账号",
            owner_user_id=user.id,
        )
        db.add(account)
        db.flush()
        db.add(
            XianyuProductDailyMetric(
                account_id=account.id,
                external_product_id="1070000000001",
                metric_date=date.today(),
                listing_title="真实商品",
                listing_price=Decimal("29.90"),
                exposures=120,
                exposed_users=100,
                views=18,
                viewers=15,
                inquiries=3,
                paid_users=0,
                paid_orders=0,
                paid_amount=Decimal("0"),
                refund_requested_users=0,
                refund_requested_orders=0,
                refund_requested_amount=Decimal("0"),
                refund_success_users=0,
                refund_success_orders=0,
                refund_success_amount=Decimal("0"),
                source_hash="b" * 64,
            )
        )
        db.commit()

        result = dashboard_route.dashboard(_user=user, db=db)

    assert result["metrics_mode"] == "LIVE_FACTS"
    assert result["traffic"]["exposures"] == 120
    assert result["traffic"]["views"] == 18
    assert result["traffic"]["consultations"] == 3
