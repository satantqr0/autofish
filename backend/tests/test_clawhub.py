import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from adapters.base import AdapterHealth, AdapterResult, AdapterStatus, Money
from adapters.xianyu.port import XianyuAccountSnapshot, XianyuProductSnapshot
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.services.clawhub as clawhub_service
from app.models import (
    AuditLog,
    AutomationControl,
    AutomationScope,
    Base,
    ManualTask,
    PlatformActionRecord,
    ProductEvaluation,
    SourcingCandidate,
    SupplierInquiry,
    SupplierProductMatch,
    User,
    XianyuDraft,
    XianyuProduct,
)
from app.schemas.catalog import ProductCreate
from app.schemas.clawhub import DiscoveryRequest
from app.services.catalog import create_catalog_product
from app.services.clawhub import (
    compare_candidates,
    create_supplier_inquiry,
    create_xianyu_draft,
    diagnose_product,
    discover_suppliers,
    evaluate_product,
    execute_action,
    preview_action,
    update_inquiry_result,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def candidate(db, *, external_id="P-1", supplier_id="S-1", score="81", title="桌面支架"):
    item = SourcingCandidate(
        adapter_name="test-adapter",
        adapter_version="1.0",
        external_product_id=external_id,
        title=title,
        category="电脑配件",
        external_supplier_id=supplier_id,
        supplier_name=f"工厂 {supplier_id}" if supplier_id else None,
        minimum_price=Decimal("18.80"),
        maximum_price=Decimal("29.80"),
        sku_count=2,
        stock=100,
        score=Decimal(score) if score else None,
        normalized_data={
            "stats": {"region": "广东", "factory_tags": ["支持代发"]},
            "score_inputs": {"profit_space": score or "0"},
        },
        last_fetched_at=datetime.now(UTC),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def product(db, title="自用闲置 Mini PC 支架"):
    item = create_catalog_product(
        db,
        ProductCreate(
            supplier_code="SUP-1",
            supplier_name="测试工厂",
            external_product_id="P-100",
            external_sku_id="SKU-100",
            internal_code="AF-100",
            sku_code="AF-SKU-100",
            title=title,
            category="电脑配件",
            spec={"颜色": "黑色"},
            supplier_price=Decimal("20"),
            shipping_cost=Decimal("5"),
            minimum_profit=Decimal("10"),
            target_profit=Decimal("20"),
            stock=50,
        ),
    )
    item.images = ["https://img.example.com/product.jpg", "http://unsafe/image.jpg"]
    db.commit()
    return item


def demo_product(db):
    return create_catalog_product(
        db,
        ProductCreate(
            supplier_code="1688-DEMO",
            supplier_name="1688 演示供应商",
            external_product_id="DEMO-P-100",
            supplier_url="https://detail.1688.example/offer/100.html",
            external_sku_id="DEMO-SKU-100",
            internal_code="AF-DEMO-100",
            sku_code="AF-DEMO-SKU-100",
            title="演示桌面支架",
            category="电脑配件",
            description="演示数据，仅用于功能验证。",
            supplier_price=Decimal("20"),
            shipping_cost=Decimal("5"),
            minimum_profit=Decimal("10"),
            target_profit=Decimal("20"),
            stock=50,
        ),
    )


def enable_automation(db, scope, *, mode="REVIEW", failure_threshold=3):
    for value in (AutomationScope.GLOBAL, scope):
        db.add(
            AutomationControl(
                scope=value,
                enabled=True,
                mode=mode if value == scope else "REVIEW",
                daily_limit=100,
                min_interval_seconds=0,
                failure_threshold=failure_threshold,
            )
        )
    db.commit()


def write_settings(provider="fake"):
    return SimpleNamespace(
        adapters_enabled=True,
        adapter_write_enabled=True,
        xianyu_adapter=provider,
    )


class FakeWriteAdapter:
    source = "fake-xianyu"
    source_version = "1.0"

    def __init__(self, publish_status=AdapterStatus.OK):
        self.publish_status = publish_status
        self.publish_calls = 0

    async def health(self):
        return AdapterResult(
            status=AdapterStatus.OK,
            data=AdapterHealth(available=True, authenticated=True, read_only=False),
            source=self.source,
            source_version=self.source_version,
        )

    async def get_account(self):
        return AdapterResult(
            status=AdapterStatus.OK,
            data=XianyuAccountSnapshot(
                external_account_id="account-1",
                nickname="测试账号",
                status="CONNECTED",
            ),
            source=self.source,
            source_version=self.source_version,
        )

    async def publish_product(self, request):
        self.publish_calls += 1
        if self.publish_status != AdapterStatus.OK:
            return AdapterResult(
                status=self.publish_status,
                source=self.source,
                source_version=self.source_version,
                error_code="INJECTED_FAILURE",
                safe_message="故障注入",
            )
        return AdapterResult(
            status=AdapterStatus.OK,
            data=XianyuProductSnapshot(
                external_product_id="99887766",
                title=request.title,
                price=Money(amount=request.price.amount),
                status="ACTIVE",
            ),
            source=self.source,
            source_version=self.source_version,
        )


def test_discovery_rejects_ssrf_and_credential_urls():
    for value in (
        "https://127.0.0.1/image.jpg",
        "https://192.168.1.10/image.jpg",
        "https://user:pass@example.com/image.jpg",
        "http://example.com/image.jpg",
    ):
        with pytest.raises(ValidationError):
            DiscoveryRequest(mode="IMAGE", source_url=value)
    assert DiscoveryRequest(
        mode="IMAGE", source_url="https://img.example.com/image.jpg"
    ).mode == "IMAGE"


def test_compare_is_evidence_based_and_validates_missing_ids(db):
    first = candidate(db, external_id="P-1", supplier_id="S-1", score="82")
    second = candidate(db, external_id="P-2", supplier_id="S-2", score="60")
    result = compare_candidates(db, [second.id, first.id])
    assert result["recommended_candidate_id"] == first.id
    assert result["items"][0]["score"] == "82.00"
    with pytest.raises(LookupError):
        compare_candidates(db, [first.id, 999])


def test_compare_tolerates_unscored_live_candidates(db):
    unscored = candidate(db, external_id="P-NULL", supplier_id=None, score=None)
    scored = candidate(db, external_id="P-SCORED", supplier_id="S-2", score="60")

    result = compare_candidates(db, [unscored.id, scored.id])

    assert result["recommended_candidate_id"] == scored.id
    assert result["items"][1]["score"] is None


def test_supplier_discovery_is_idempotent_and_keeps_unknown_dimensions_null(db):
    base = candidate(db, external_id="P-1", supplier_id="S-1")
    candidate(db, external_id="P-2", supplier_id="S-2", score="73")
    first = discover_suppliers(
        db,
        sourcing_candidate_id=base.id,
        query=None,
        limit=10,
        user_id=1,
        correlation_id="test-1",
    )
    second = discover_suppliers(
        db,
        sourcing_candidate_id=base.id,
        query=None,
        limit=10,
        user_id=1,
        correlation_id="test-2",
    )
    assert len(first["items"]) == 2
    assert len(second["items"]) == 2
    assert first["items"][0]["response_speed_score"] is None
    assert db.scalar(select(func.count(SupplierProductMatch.id))) == 2


def test_action_gateway_never_executes_external_write(db):
    item = product(db)
    action = preview_action(
        db,
        action_type="PUBLISH_XIANYU",
        target_type="PRODUCT",
        target_id=item.id,
        payload={},
        idempotency_key="publish-product-100",
        user_id=1,
        correlation_id="action-1",
    )
    assert action.status == "BLOCKED"
    assert action.preview["executable"] is False
    executed = asyncio.run(
        execute_action(
            db,
            action_id=action.id,
            confirm=True,
            user_id=1,
            correlation_id="action-2",
        )
    )
    assert executed.status == "BLOCKED"
    assert executed.executed_at is None
    assert db.scalar(select(func.count(PlatformActionRecord.id))) == 1


def test_platform_action_preview_and_execute_are_scoped_to_requesting_user(db):
    owner = User(username="action-owner", password_hash="unused", role="operator", is_active=True)
    intruder = User(
        username="action-intruder",
        password_hash="unused",
        role="operator",
        is_active=True,
    )
    db.add_all([owner, intruder])
    db.commit()
    item = product(db)
    draft = create_xianyu_draft(
        db,
        product_id=item.id,
        target_category=None,
        price=None,
        user_id=owner.id,
        correlation_id="owner-draft",
    )
    action = preview_action(
        db,
        action_type="PUBLISH_XIANYU",
        target_type="XIANYU_DRAFT",
        target_id=draft.id,
        payload={},
        idempotency_key="owner-only-publish-action",
        user_id=owner.id,
        correlation_id="owner-preview",
    )

    with pytest.raises(LookupError, match="动作记录不存在"):
        preview_action(
            db,
            action_type="PUBLISH_XIANYU",
            target_type="XIANYU_DRAFT",
            target_id=draft.id,
            payload={},
            idempotency_key="owner-only-publish-action",
            user_id=intruder.id,
            correlation_id="intruder-idempotent-preview",
        )
    with pytest.raises(LookupError, match="无权访问"):
        preview_action(
            db,
            action_type="PUBLISH_XIANYU",
            target_type="XIANYU_DRAFT",
            target_id=draft.id,
            payload={},
            idempotency_key="intruder-new-publish-action",
            user_id=intruder.id,
            correlation_id="intruder-new-preview",
        )
    with pytest.raises(LookupError, match="动作记录不存在"):
        asyncio.run(
            execute_action(
                db,
                action_id=action.id,
                confirm=True,
                user_id=intruder.id,
                correlation_id="intruder-execute",
            )
        )

    assert db.get(PlatformActionRecord, action.id).requested_by_user_id == owner.id


def test_admin_platform_action_override_must_be_explicit_and_is_audited(db):
    owner = User(username="override-owner", password_hash="unused", role="operator", is_active=True)
    admin = User(username="override-admin", password_hash="unused", role="admin", is_active=True)
    db.add_all([owner, admin])
    db.commit()
    item = product(db)
    draft = create_xianyu_draft(
        db,
        product_id=item.id,
        target_category=None,
        price=None,
        user_id=owner.id,
        correlation_id="override-owner-draft",
    )
    action = preview_action(
        db,
        action_type="PUBLISH_XIANYU",
        target_type="XIANYU_DRAFT",
        target_id=draft.id,
        payload={},
        idempotency_key="admin-override-publish-action",
        user_id=owner.id,
        correlation_id="override-owner-preview",
    )

    with pytest.raises(LookupError, match="动作记录不存在"):
        preview_action(
            db,
            action_type="PUBLISH_XIANYU",
            target_type="XIANYU_DRAFT",
            target_id=draft.id,
            payload={},
            idempotency_key="admin-override-publish-action",
            user_id=admin.id,
            correlation_id="implicit-admin-preview",
        )

    overridden = preview_action(
        db,
        action_type="PUBLISH_XIANYU",
        target_type="XIANYU_DRAFT",
        target_id=draft.id,
        payload={},
        idempotency_key="admin-override-publish-action",
        user_id=admin.id,
        correlation_id="explicit-admin-preview",
        allow_admin_override=True,
    )
    executed = asyncio.run(
        execute_action(
            db,
            action_id=action.id,
            confirm=True,
            user_id=admin.id,
            correlation_id="explicit-admin-execute",
            allow_admin_override=True,
        )
    )

    assert overridden.id == action.id
    assert executed.status == "BLOCKED"
    override_audits = db.scalars(
        select(AuditLog).where(
            AuditLog.action == "PLATFORM_ACTION_ADMIN_OVERRIDE",
            AuditLog.actor_user_id == admin.id,
        )
    ).all()
    assert {entry.after_data["stage"] for entry in override_audits} == {
        "PREVIEW_IDEMPOTENT_READ",
        "EXECUTE",
    }


def test_controlled_publish_executes_once_and_updates_local_state(db, monkeypatch):
    item = product(db)
    draft = create_xianyu_draft(
        db,
        product_id=item.id,
        target_category=None,
        price=None,
        user_id=1,
        correlation_id="draft-publish",
    )
    enable_automation(db, AutomationScope.PUBLISH, mode="AUTOMATIC")
    adapter = FakeWriteAdapter()
    monkeypatch.setattr(clawhub_service, "get_settings", write_settings)
    monkeypatch.setattr(clawhub_service, "get_xianyu_adapter", lambda: adapter)

    action = preview_action(
        db,
        action_type="PUBLISH_XIANYU",
        target_type="XIANYU_DRAFT",
        target_id=draft.id,
        payload={},
        idempotency_key="publish-controlled-100",
        user_id=1,
        correlation_id="publish-preview",
    )
    assert action.status == "PREVIEWED"
    assert action.requires_confirmation is False
    executed = asyncio.run(
        execute_action(
            db,
            action_id=action.id,
            confirm=False,
            user_id=1,
            correlation_id="publish-execute",
        )
    )
    repeated = asyncio.run(
        execute_action(
            db,
            action_id=action.id,
            confirm=False,
            user_id=1,
            correlation_id="publish-repeat",
        )
    )

    assert executed.status == "SUCCEEDED"
    assert repeated.status == "SUCCEEDED"
    assert executed.attempts == 1
    assert adapter.publish_calls == 1
    assert db.scalar(select(XianyuProduct.external_product_id)) == "99887766"
    assert db.get(XianyuDraft, draft.id).status == "PUBLISHED"


def test_price_floor_blocks_controlled_repricing(db, monkeypatch):
    item = product(db)
    enable_automation(db, AutomationScope.REPRICING)
    monkeypatch.setattr(clawhub_service, "get_settings", write_settings)

    action = preview_action(
        db,
        action_type="UPDATE_PRICE",
        target_type="PRODUCT",
        target_id=item.id,
        payload={"price": "1.00"},
        idempotency_key="unsafe-price-100",
        user_id=1,
        correlation_id="unsafe-price",
    )

    assert action.status == "BLOCKED"
    assert any("最低安全售价" in blocker for blocker in action.preview["blockers"])


def test_ambiguous_publish_hands_off_and_opens_circuit(db, monkeypatch):
    item = product(db)
    draft = create_xianyu_draft(
        db,
        product_id=item.id,
        target_category=None,
        price=None,
        user_id=1,
        correlation_id="draft-failure",
    )
    enable_automation(
        db,
        AutomationScope.PUBLISH,
        mode="AUTOMATIC",
        failure_threshold=1,
    )
    adapter = FakeWriteAdapter(AdapterStatus.TRANSIENT_ERROR)
    monkeypatch.setattr(clawhub_service, "get_settings", write_settings)
    monkeypatch.setattr(clawhub_service, "get_xianyu_adapter", lambda: adapter)
    action = preview_action(
        db,
        action_type="PUBLISH_XIANYU",
        target_type="XIANYU_DRAFT",
        target_id=draft.id,
        payload={},
        idempotency_key="publish-failure-100",
        user_id=1,
        correlation_id="publish-failure-preview",
    )

    executed = asyncio.run(
        execute_action(
            db,
            action_id=action.id,
            confirm=False,
            user_id=1,
            correlation_id="publish-failure-execute",
        )
    )
    control = db.scalar(
        select(AutomationControl).where(AutomationControl.scope == AutomationScope.PUBLISH)
    )

    assert executed.status == "MANUAL_REQUIRED"
    assert executed.attempts == 1
    assert control.cooldown_until is not None
    assert db.scalar(select(func.count(ManualTask.id))) == 1


def test_evaluation_grades_and_deduplicates_same_evidence(db):
    item = candidate(db, score="88")
    first = evaluate_product(
        db,
        product_id=None,
        sourcing_candidate_id=item.id,
        stage="PRE_LAUNCH",
        metrics={},
    )
    second = evaluate_product(
        db,
        product_id=None,
        sourcing_candidate_id=item.id,
        stage="PRE_LAUNCH",
        metrics={},
    )
    assert first.grade == "S"
    assert second.id == first.id
    assert db.scalar(select(func.count(ProductEvaluation.id))) == 1


def test_diagnosis_returns_traceable_rule_result(db):
    item = candidate(db)
    result = diagnose_product(
        db,
        product_id=None,
        sourcing_candidate_id=item.id,
        metrics={"traffic": Decimal("70"), "conversion": Decimal("10")},
    )
    assert result.diagnosis == "LOW_CONVERSION"
    assert result.severity == "HIGH"
    assert result.evidence["provided_metrics"]["conversion"] == "10"


def test_draft_pipeline_removes_false_provenance_supplier_copy_and_deduplicates(db):
    item = product(db)
    item.description = (
        "厂家 直发，支持一件代发，代 发 包 邮。商品以供应商实物为准，黑色金属材质。"
    )
    db.commit()
    first = create_xianyu_draft(
        db,
        product_id=item.id,
        target_category=None,
        price=None,
        user_id=1,
        correlation_id="draft-1",
    )
    second = create_xianyu_draft(
        db,
        product_id=item.id,
        target_category=None,
        price=None,
        user_id=1,
        correlation_id="draft-2",
    )
    assert "自用闲置" not in first.title
    assert "厂家直发" not in first.description
    assert "一件代发" not in first.description
    assert "代发" not in first.description
    assert "支持" not in first.description
    assert "供应商" not in first.description
    assert "商品以实物为准" in first.description
    assert "包邮" in first.description
    assert "下单前请先确认库存和规格，商品细节及售后问题可随时沟通" in first.description
    assert "本人" not in first.description
    assert "发货安排" not in first.description
    assert first.status == "REVIEW_READY"
    assert first.images == ["https://img.example.com/product.jpg"]
    assert second.id == first.id
    assert db.scalar(select(func.count(XianyuDraft.id))) == 1


def test_draft_keeps_owner_voice_when_source_description_reaches_length_limit(db):
    item = product(db)
    item.description = "金属桌面支架。" * 800
    db.commit()

    draft = create_xianyu_draft(
        db,
        product_id=item.id,
        target_category=None,
        price=None,
        user_id=1,
        correlation_id="draft-long-description",
    )

    assert len(draft.description) <= 5000
    assert draft.description.endswith(
        "下单前请先确认库存和规格，商品细节及售后问题可随时沟通。"
    )
    assert draft.validation["passed"] is True


def test_legacy_supplier_copy_blocks_publish_preview(db, monkeypatch):
    item = product(db)
    draft = create_xianyu_draft(
        db,
        product_id=item.id,
        target_category=None,
        price=None,
        user_id=1,
        correlation_id="draft-before-copy-tamper",
    )
    draft.description = "支持一键代发，一件发货。"
    db.commit()
    enable_automation(db, AutomationScope.PUBLISH, mode="AUTOMATIC")
    monkeypatch.setattr(clawhub_service, "get_settings", write_settings)

    action = preview_action(
        db,
        action_type="PUBLISH_XIANYU",
        target_type="XIANYU_DRAFT",
        target_id=draft.id,
        payload={},
        idempotency_key="publish-copy-guard",
        user_id=1,
        correlation_id="publish-copy-guard-preview",
    )

    assert action.status == "BLOCKED"
    assert any("代发或供应链话术" in value for value in action.preview["blockers"])
    assert any("中性的库存、规格与售后沟通提示" in value for value in action.preview["blockers"])


def test_demo_product_cannot_create_xianyu_draft(db):
    item = demo_product(db)

    with pytest.raises(ValueError, match="商品发布来源校验失败"):
        create_xianyu_draft(
            db,
            product_id=item.id,
            target_category=None,
            price=None,
            user_id=1,
            correlation_id="demo-draft",
        )

    assert db.scalar(select(func.count(XianyuDraft.id))) == 0


def test_demo_product_blocks_legacy_publish_draft_preview(db, monkeypatch):
    item = demo_product(db)
    draft = XianyuDraft(
        product_id=item.id,
        version=1,
        title=item.title,
        description=item.description,
        price=item.skus[0].recommended_price,
        category=item.category,
        attributes={},
        images=["https://img.alicdn.com/demo.jpg"],
        validation={"passed": True},
        pipeline_trace=[],
        status="REVIEW_READY",
        input_hash="legacy-demo-draft",
        created_by_user_id=1,
    )
    db.add(draft)
    db.commit()
    enable_automation(db, AutomationScope.PUBLISH, mode="AUTOMATIC")
    monkeypatch.setattr(clawhub_service, "get_settings", write_settings)

    action = preview_action(
        db,
        action_type="PUBLISH_XIANYU",
        target_type="XIANYU_DRAFT",
        target_id=draft.id,
        payload={},
        idempotency_key="publish-legacy-demo",
        user_id=1,
        correlation_id="publish-legacy-demo-preview",
    )

    assert action.status == "BLOCKED"
    assert action.preview["executable"] is False
    assert any("演示" in blocker for blocker in action.preview["blockers"])


def test_publish_execution_rechecks_provenance_before_adapter_call(db, monkeypatch):
    item = product(db)
    draft = create_xianyu_draft(
        db,
        product_id=item.id,
        target_category=None,
        price=None,
        user_id=1,
        correlation_id="draft-before-provenance-change",
    )
    enable_automation(db, AutomationScope.PUBLISH, mode="AUTOMATIC")
    adapter = FakeWriteAdapter()
    monkeypatch.setattr(clawhub_service, "get_settings", write_settings)
    monkeypatch.setattr(clawhub_service, "get_xianyu_adapter", lambda: adapter)
    action = preview_action(
        db,
        action_type="PUBLISH_XIANYU",
        target_type="XIANYU_DRAFT",
        target_id=draft.id,
        payload={},
        idempotency_key="publish-provenance-recheck",
        user_id=1,
        correlation_id="publish-provenance-recheck-preview",
    )
    assert action.status == "PREVIEWED"

    item.description = "演示数据，不可发布。"
    db.commit()
    executed = asyncio.run(
        execute_action(
            db,
            action_id=action.id,
            confirm=False,
            user_id=1,
            correlation_id="publish-provenance-recheck-execute",
        )
    )

    assert executed.status == "BLOCKED"
    assert executed.preview["executable"] is False
    assert executed.executed_at is None
    assert adapter.publish_calls == 0


def test_inquiry_is_manual_until_operator_records_real_result(db):
    item = candidate(db)
    inquiry = create_supplier_inquiry(
        db,
        supplier_candidate_id=None,
        sourcing_candidate_id=item.id,
        topic="确认代发条件",
        questions=["当前库存？", "发货时效？"],
        idempotency_key="inquiry-product-1",
        user_id=1,
        correlation_id="inquiry-1",
    )
    assert inquiry.status == "MANUAL_REQUIRED"
    assert inquiry.result["dispatch"] == "not_attempted"
    updated = update_inquiry_result(
        db,
        inquiry_id=inquiry.id,
        status="REPLIED",
        result={"stock": 100},
        message="供应商人工回复：库存 100",
        user_id=1,
        correlation_id="inquiry-2",
    )
    assert updated.status == "REPLIED"
    assert db.scalar(select(func.count(SupplierInquiry.id))) == 1
