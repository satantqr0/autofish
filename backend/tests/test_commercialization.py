from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, CommercialEvidencePeriod, User
from app.schemas.commercialization import (
    CommercialAcceptanceRequest,
    CommercialEvidenceUpsert,
)
from app.services.commercialization import (
    accept_commercial_boundaries,
    build_commercial_overview,
    ensure_commercial_deployment,
    upsert_commercial_evidence,
)


def make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_user(db):
    user = User(username="owner", password_hash="unused", role="admin")
    db.add(user)
    db.flush()
    return user


def evidence_payload(**overrides):
    values = {
        "period_start": date.today().replace(day=1),
        "qualified_leads": 8,
        "product_demos": 5,
        "paid_new_customers": 3,
        "active_customers": 3,
        "retained_30d_customers": 3,
        "refunded_customers": 0,
        "revenue_cny": Decimal("5940.00"),
        "delivery_hours": Decimal("12.00"),
        "support_hours": Decimal("1.50"),
        "notes": "首批付费试点",
    }
    values.update(overrides)
    return CommercialEvidenceUpsert(**values)


def test_new_installation_is_internal_and_does_not_claim_market_readiness():
    with make_db() as db:
        overview = build_commercial_overview(db)

        assert overview["status"] == "INTERNAL"
        assert overview["readiness_score"] == 20
        assert overview["growth_level"] == 1
        assert overview["evidence_summary"]["paid_customers"] == 0
        assert overview["release_ready"] is False


def test_acceptance_only_unlocks_paid_pilot_not_general_release():
    with make_db() as db:
        user = make_user(db)
        deployment = ensure_commercial_deployment(db)
        accept_commercial_boundaries(deployment, user_id=user.id)
        db.flush()

        overview = build_commercial_overview(db)

        assert overview["status"] == "PAID_PILOT"
        assert overview["readiness_score"] == 45
        assert overview["release_ready"] is False
        assert overview["deployment"]["acceptance"]["accepted_at"] is not None


def test_false_commercial_boundary_cannot_be_submitted():
    with pytest.raises(ValidationError):
        CommercialAcceptanceRequest(
            account_owned_by_customer=True,
            data_stays_customer_controlled=True,
            no_credential_custody=False,
            no_revenue_guarantee_acknowledged=True,
            prohibited_automation_acknowledged=True,
            regulatory_obligations_acknowledged=True,
        )


def test_three_retained_customers_and_bounded_delivery_unlock_private_release():
    with make_db() as db:
        user = make_user(db)
        deployment = ensure_commercial_deployment(db)
        accept_commercial_boundaries(deployment, user_id=user.id)
        upsert_commercial_evidence(db, evidence_payload(), user_id=user.id)
        db.flush()

        overview = build_commercial_overview(db)

        assert overview["status"] == "PRIVATE_RELEASE_READY"
        assert overview["readiness_score"] == 100
        assert overview["growth_level"] == 3
        assert overview["release_ready"] is True
        assert overview["evidence_summary"]["delivery_hours_per_paid_customer"] == 4.0
        assert overview["evidence_summary"]["support_hours_per_active_customer"] == 0.5


def test_evidence_is_monthly_upsert_and_does_not_duplicate_customers():
    with make_db() as db:
        user = make_user(db)
        first, _, created = upsert_commercial_evidence(db, evidence_payload(), user_id=user.id)
        second, before, created_again = upsert_commercial_evidence(
            db,
            evidence_payload(revenue_cny=Decimal("6000.00")),
            user_id=user.id,
        )
        db.flush()

        assert created is True
        assert created_again is False
        assert first.id == second.id
        assert before["revenue_cny"] == Decimal("5940.00")
        assert db.scalar(select(func.count()).select_from(CommercialEvidencePeriod)) == 1


def test_retention_cannot_exceed_active_customers():
    with pytest.raises(ValidationError, match="30 天留存客户不能超过当月活跃客户"):
        evidence_payload(active_customers=1, retained_30d_customers=2)
