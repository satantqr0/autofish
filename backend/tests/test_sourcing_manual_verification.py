from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.routes.sourcing import manually_verify_candidate
from app.models import (
    AuditLog,
    Base,
    ExternalSnapshot,
    IntegrationSyncRun,
    SourcingCandidate,
    User,
)
from app.schemas.integrations import CandidateManualVerificationRequest
from app.services.integrations import (
    save_snapshot,
    serialize_candidate,
    start_sync_run,
    upsert_sourcing_candidate,
    verify_sourcing_candidate_manually,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def make_candidate(db):
    user = User(
        username="sourcing-operator",
        password_hash="not-used",
        role="operator",
        is_active=True,
    )
    db.add(user)
    db.flush()
    initial = {
        "external_product_id": "1010671800807",
        "title": "桌面书桌收纳置物架",
        "url": "https://detail.1688.com/offer/1010671800807.html",
        "category": "办公/收纳",
        "price": "9.23",
        "skus": [],
    }
    run = start_sync_run(
        db,
        platform="1688",
        adapter_name="shopkeeper-1688",
        adapter_version="1.0",
        operation="TEST_DISCOVERY",
        requested_by_user_id=user.id,
        correlation_id="manual-verification-fixture",
    )
    snapshot, _ = save_snapshot(
        db,
        run=run,
        platform="1688",
        object_type="SUPPLIER_PRODUCT_CANDIDATE",
        external_id=initial["external_product_id"],
        adapter_name="shopkeeper-1688",
        adapter_version="1.0",
        normalized_data=initial,
        raw_payload=initial,
    )
    candidate = upsert_sourcing_candidate(
        db,
        snapshot=snapshot,
        adapter_name="shopkeeper-1688",
        adapter_version="1.0",
        data=initial,
    )
    db.commit()
    return user, candidate


def verification_payload(**overrides):
    values = {
        "source_url": "https://detail.1688.com/offer/1010671800807.html",
        "captured_at": datetime.now(UTC),
        "category": "办公/收纳",
        "external_supplier_id": "SUP-1010671800807",
        "supplier_name": "义乌市启尚日用百货有限公司",
        "image_url": "https://cbu01.alicdn.com/img/ibank/test-product.jpg",
        "skus": [
            {
                "external_sku_id": "SKU-2-LAYER",
                "spec": {"层数": "2层", "材质": "PET"},
                "price": "9.23",
                "shipping": "3.00",
                "stock": 120,
            }
        ],
        "evidence_note": "人工核对 1688 详情页、SKU 面板和供应商信息",
    }
    values.update(overrides)
    return CandidateManualVerificationRequest(**values)


def test_manual_verification_completes_candidate_with_traceable_evidence(db):
    user, candidate = make_candidate(db)

    result = verify_sourcing_candidate_manually(
        db,
        candidate=candidate,
        payload=verification_payload(),
        actor_user_id=user.id,
        correlation_id="manual-verification-1",
    )
    db.commit()

    loaded = db.get(SourcingCandidate, candidate.id)
    serialized = serialize_candidate(loaded)
    assert result["snapshot_created"] is True
    assert serialized["import_ready"] is True
    assert loaded.external_supplier_id == "SUP-1010671800807"
    assert loaded.supplier_name == "义乌市启尚日用百货有限公司"
    assert loaded.sku_count == 1
    assert loaded.stock == 120
    assert str(loaded.minimum_price) == "9.23"
    assert loaded.image_url.endswith("test-product.jpg")
    assert loaded.normalized_data["manual_verification"]["method"] == (
        "OPERATOR_1688_DETAIL_PAGE"
    )
    snapshot = db.get(ExternalSnapshot, result["snapshot_id"])
    assert snapshot.object_type == "SUPPLIER_PRODUCT_VERIFICATION"
    assert snapshot.adapter_name == "manual-operator-verification"
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "SOURCING_CANDIDATE_MANUALLY_VERIFIED"
        )
    )
    assert audit.after_data["snapshot_id"] == snapshot.id


def test_authorized_api_evidence_can_trace_supplier_by_name_when_id_is_omitted(db):
    user, candidate = make_candidate(db)
    candidate.adapter_name = "1688-product-find-cli"
    payload = verification_payload(
        external_supplier_id=None,
        evidence_method="AUTHORIZED_1688_API",
        evidence_note="1688 官方结构化检索与官方商详接口交叉核验",
    )

    result = verify_sourcing_candidate_manually(
        db,
        candidate=candidate,
        payload=payload,
        actor_user_id=user.id,
        correlation_id="authorized-api-evidence",
    )
    db.commit()

    loaded = db.get(SourcingCandidate, candidate.id)
    assert loaded.external_supplier_id is None
    assert loaded.normalized_data["manual_verification"]["method"] == (
        "AUTHORIZED_1688_API"
    )
    assert result["candidate"]["import_ready"] is True


def test_manual_verification_replay_reuses_identical_snapshot(db):
    user, candidate = make_candidate(db)
    payload = verification_payload()
    first = verify_sourcing_candidate_manually(
        db,
        candidate=candidate,
        payload=payload,
        actor_user_id=user.id,
        correlation_id="manual-replay-1",
    )
    db.commit()
    second = verify_sourcing_candidate_manually(
        db,
        candidate=candidate,
        payload=payload,
        actor_user_id=user.id,
        correlation_id="manual-replay-2",
    )
    db.commit()

    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["snapshot_created"] is True
    assert second["snapshot_created"] is False
    assert db.scalar(
        select(func.count(ExternalSnapshot.id)).where(
            ExternalSnapshot.object_type == "SUPPLIER_PRODUCT_VERIFICATION"
        )
    ) == 1
    assert db.scalar(
        select(func.count(IntegrationSyncRun.id)).where(
            IntegrationSyncRun.operation == "VERIFY_SUPPLIER_CANDIDATE"
        )
    ) == 2


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"source_url": "http://detail.1688.com/offer/1010671800807.html"},
            "1688 商品详情 HTTPS",
        ),
        (
            {"image_url": "https://example.com/untrusted.jpg"},
            "阿里 CDN",
        ),
        (
            {"captured_at": datetime.now(UTC) - timedelta(hours=25)},
            "超过 24 小时",
        ),
        (
            {
                "skus": [
                    {
                        "external_sku_id": "DUPLICATE",
                        "price": "9.23",
                        "shipping": "0",
                        "stock": 1,
                    },
                    {
                        "external_sku_id": "DUPLICATE",
                        "price": "10.23",
                        "shipping": "0",
                        "stock": 1,
                    },
                ]
            },
            "重复 SKU ID",
        ),
    ],
)
def test_manual_verification_rejects_untrusted_or_stale_evidence(overrides, message):
    with pytest.raises(ValidationError, match=message):
        verification_payload(**overrides)


def test_manual_verification_forbids_unknown_sensitive_fields():
    values = verification_payload().model_dump(mode="json")
    values["cookie"] = "must-not-be-accepted"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CandidateManualVerificationRequest(**values)


def test_manual_verification_rejects_mismatched_product_link_without_writes(db):
    user, candidate = make_candidate(db)
    payload = verification_payload(
        source_url="https://detail.1688.com/offer/999999999999.html"
    )

    with pytest.raises(ValueError, match="商品 ID 与候选不一致"):
        verify_sourcing_candidate_manually(
            db,
            candidate=candidate,
            payload=payload,
            actor_user_id=user.id,
            correlation_id="manual-mismatch",
        )

    assert db.scalar(
        select(func.count(IntegrationSyncRun.id)).where(
            IntegrationSyncRun.operation == "VERIFY_SUPPLIER_CANDIDATE"
        )
    ) == 0


@pytest.mark.parametrize("blocked_status", ["EXCLUDED", "IMPORTED"])
def test_manual_verification_does_not_overwrite_terminal_candidate(db, blocked_status):
    user, candidate = make_candidate(db)
    candidate.status = blocked_status
    db.commit()

    with pytest.raises(ValueError):
        verify_sourcing_candidate_manually(
            db,
            candidate=candidate,
            payload=verification_payload(),
            actor_user_id=user.id,
            correlation_id=f"manual-blocked-{blocked_status}",
        )


def test_manual_verification_route_commits_and_returns_serialized_candidate(db):
    user, candidate = make_candidate(db)
    request = SimpleNamespace(
        state=SimpleNamespace(correlation_id="manual-route-success")
    )

    result = manually_verify_candidate(
        candidate_id=candidate.id,
        payload=verification_payload(),
        request=request,
        user=user,
        db=db,
    )

    assert result["candidate"]["import_ready"] is True
    assert result["candidate"]["sku_count"] == 1
    db.expire_all()
    assert db.get(SourcingCandidate, candidate.id).external_supplier_id == (
        "SUP-1010671800807"
    )


@pytest.mark.parametrize(
    ("candidate_id", "terminal_status", "expected_status"),
    [(999_999, None, 404), (None, "IMPORTED", 409), (None, "EXCLUDED", 422)],
)
def test_manual_verification_route_rejects_missing_or_terminal_candidate(
    db, candidate_id, terminal_status, expected_status
):
    user, candidate = make_candidate(db)
    if terminal_status:
        candidate.status = terminal_status
        db.commit()
    request = SimpleNamespace(state=SimpleNamespace(correlation_id="manual-route-blocked"))

    with pytest.raises(HTTPException) as raised:
        manually_verify_candidate(
            candidate_id=candidate_id or candidate.id,
            payload=verification_payload(),
            request=request,
            user=user,
            db=db,
        )

    assert raised.value.status_code == expected_status


def test_manual_verification_route_maps_source_mismatch_to_unprocessable(db):
    user, candidate = make_candidate(db)
    request = SimpleNamespace(state=SimpleNamespace(correlation_id="manual-route-mismatch"))

    with pytest.raises(HTTPException) as raised:
        manually_verify_candidate(
            candidate_id=candidate.id,
            payload=verification_payload(
                source_url="https://detail.1688.com/offer/999999999999.html"
            ),
            request=request,
            user=user,
            db=db,
        )

    assert raised.value.status_code == 422
    assert "商品 ID 与候选不一致" in raised.value.detail
