import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.services.autonomous_launch as launch_service
from app.models import AIDecision, AutonomousLaunch, Base, SourcingCandidate, User, XianyuDraft
from app.schemas.autonomous import (
    AutonomousLaunchCreate,
    CopyQAOutput,
    ListingCopyOutput,
    VisionQAOutput,
)
from app.services.ai_inference import AIInferenceError, InferenceResult
from app.services.autonomous_launch import execute_autonomous_launch, score_candidate


def verified_candidate(db: Session) -> SourcingCandidate:
    item = SourcingCandidate(
        adapter_name="1688-product-find-cli",
        adapter_version="1.0",
        external_product_id="972222937029",
        title="米白色可拆分隔桌面收纳盒 29*11.8*7.3cm 新人专享包邮",
        url="https://detail.1688.com/offer/972222937029.html",
        image_url="https://cbu01.alicdn.com/img/ibank/source.jpg",
        category="收纳清洁用具/收纳防尘/收纳盒",
        supplier_name="义乌市测试贸易有限公司",
        minimum_price=Decimal("7.01"),
        maximum_price=Decimal("7.01"),
        sku_count=1,
        stock=29997,
        status="DISCOVERED",
        normalized_data={
            "skus": [
                {
                    "external_sku_id": "5921969636489",
                    "price": {"amount": "7.01", "currency": "CNY"},
                    "shipping": {"amount": "0", "currency": "CNY"},
                    "stock": 29997,
                    "spec": {"颜色": "米白色", "尺寸": "29*11.8*7.3cm"},
                }
            ],
            "stats": {
                "detail_verified": True,
                "requires_preorder_recheck": True,
                "shipping_condition": "NEW_BUYER_ONLY",
                "price_policy": "MAX_OF_SEARCH_SKU_AND_DETAIL_BASE",
            },
            "adapter_verification": {"method": "AUTHORIZED_1688_PRODUCT_FIND_PLUS_SHOPKEEPER"},
        },
        last_fetched_at=datetime.now(UTC),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_candidate_score_requires_verified_complete_evidence():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        item = verified_candidate(db)
        result = score_candidate(item)

        assert result["eligible"] is True
        assert Decimal(result["total_score"]) >= 70
        assert result["selected_sku"]["external_sku_id"] == "5921969636489"
        assert result["warnings"]

        item.normalized_data = {**item.normalized_data, "adapter_verification": {}}
        result = score_candidate(item)
        assert result["eligible"] is False
        assert "缺少授权 Adapter 的商品详情核验" in result["blockers"]


def test_download_image_treats_1688_http_420_as_retryable(monkeypatch, tmp_path):
    class RateLimitedResponse:
        status_code = 420

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(launch_service, "_assert_public_https", lambda _url: None)
    monkeypatch.setattr(
        launch_service.httpx,
        "stream",
        lambda *_args, **_kwargs: RateLimitedResponse(),
    )

    with pytest.raises(AIInferenceError) as error:
        launch_service.download_image(
            "https://cbu01.alicdn.com/img/ibank/source.jpg",
            tmp_path / "source.jpg",
        )

    assert error.value.code == "IMAGE_DOWNLOAD_RATE_LIMITED"
    assert error.value.retryable is True


def test_copy_falls_back_to_verified_template_when_ai_is_unavailable(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(username="admin", password_hash="hash", role="admin", is_active=True)
        db.add(user)
        db.flush()
        candidate = verified_candidate(db)
        launch = AutonomousLaunch(
            status="RUNNING",
            current_stage="AI_COPY",
            idempotency_key="test-offline-copy-001",
            correlation_id="test-offline-copy-correlation",
            request_payload={"image_count": 1},
            requested_by_user_id=user.id,
            sourcing_candidate_id=candidate.id,
            stages=[],
        )
        db.add(launch)
        db.commit()

        monkeypatch.setattr(
            launch_service,
            "chat_json",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AIInferenceError("AI_AUTH_OR_QUOTA", "模型额度不可用")
            ),
        )
        result = launch_service._generate_copy(
            db,
            launch,
            {
                "source_title": candidate.title,
                "category": candidate.category,
                "supplier_name": candidate.supplier_name,
                "sku": {
                    "spec": {"颜色": "米白色", "尺寸": "29*11.8*7.3cm"},
                },
            },
        )

        assert result["copy_qa"]["mode"] == "VERIFIED_FACT_TEMPLATE"
        assert "本人发货" not in result["description"]
        assert "代发" not in result["description"]
        assert launch_service.SELLER_SERVICE_COPY in result["description"]


def test_source_preserving_premium_image_is_square_and_audited(tmp_path):
    source = tmp_path / "source.jpg"
    target = tmp_path / "premium.png"
    Image.new("RGB", (720, 540), (80, 120, 160)).save(source, format="JPEG")

    metadata = launch_service._create_source_preserving_premium_image(source, target)

    assert target.is_file()
    assert metadata["width"] == 1200
    assert metadata["height"] == 1200
    assert metadata["source_preserving"] is True
    with Image.open(target) as image:
        assert image.size == (1200, 1200)


def test_offline_source_card_never_satisfies_premium_image_gate():
    candidate = SimpleNamespace(normalized_data={})
    error = AIInferenceError("AI_AUTH_OR_QUOTA", "模型额度不可用")
    source = {"sha256": "source-hash"}
    facts = {"sku": {"external_sku_id": "sku-1"}}

    blocked = launch_service._local_image_qa(
        error,
        candidate=candidate,
        source=source,
        facts=facts,
    )
    assert blocked["passed"] is False
    assert blocked["specification_consistent"] is False

    candidate.normalized_data = {
        "image_sku_verification": {
            "method": "OPERATOR_VISUAL_REVIEW",
            "source_sha256": "source-hash",
            "external_sku_id": "sku-1",
        }
    }
    verified_card = launch_service._local_image_qa(
        error,
        candidate=candidate,
        source=source,
        facts=facts,
    )
    assert verified_card["passed"] is False
    assert verified_card["specification_consistent"] is True
    assert verified_card["premium_quality"] is False
    assert verified_card["no_misleading_text"] is False
    assert "不属于精品商品图" in verified_card["blockers"][0]


def test_prepare_candidate_pool_automatically_verifies_authorized_candidates(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(username="admin", password_hash="hash", role="admin", is_active=True)
        db.add(user)
        db.flush()
        candidate = verified_candidate(db)
        data = dict(candidate.normalized_data)
        data["stats"] = {}
        data["adapter_verification"] = {}
        data["skus"] = [{**data["skus"][0], "shipping": None}]
        candidate.normalized_data = data
        db.commit()
        launch = AutonomousLaunch(
            status="PENDING",
            current_stage="QUEUED",
            idempotency_key="test-auto-evidence-001",
            correlation_id="test-auto-evidence-correlation",
            request_payload={"candidate_limit": 50},
            requested_by_user_id=user.id,
            stages=[],
        )
        db.add(launch)
        db.commit()

        observed = {}

        async def fake_verification(_db, *, candidate, actor_type, **_kwargs):
            observed["actor_type"] = actor_type
            normalized = dict(candidate.normalized_data)
            normalized["stats"] = {"detail_verified": True}
            normalized["adapter_verification"] = {"method": "AUTHORIZED_TEST"}
            normalized["skus"] = [
                {
                    **normalized["skus"][0],
                    "shipping": {"amount": "0", "currency": "CNY"},
                }
            ]
            candidate.normalized_data = normalized
            return {"candidate": {"id": candidate.id}}

        monkeypatch.setattr(
            launch_service,
            "verify_candidate_from_authorized_adapter",
            fake_verification,
        )

        result = asyncio.run(launch_service.prepare_candidate_pool(db, launch))

        assert result["attempted"] == 1
        assert result["verified_candidate_ids"] == [candidate.id]
        assert result["failures"] == []
        assert observed["actor_type"] == "SYSTEM"
        assert score_candidate(candidate)["eligible"] is True


def test_draft_asset_snapshot_restores_final_launch_manifest():
    launch = SimpleNamespace(
        id=41,
        request_payload={"image_count": 1},
        asset_manifest=[{"filename": "00-source.jpg", "kind": "source"}],
    )
    complete_manifest = [
        {"filename": "00-source.jpg", "kind": "source"},
        {
            "filename": "01-premium.png",
            "kind": "generated",
            "qa": {"passed": True},
        },
    ]
    draft = SimpleNamespace(
        attributes={
            "autonomous_launch_id": 41,
            "asset_manifest": complete_manifest,
        }
    )

    launch_service._persist_draft_asset_manifest(launch, draft)

    assert launch.asset_manifest == complete_manifest
    assert launch.asset_manifest is not complete_manifest


def test_new_launch_requires_clean_and_dimension_image_by_default():
    payload = AutonomousLaunchCreate(idempotency_key="dimension-default-001")

    assert payload.dimension_image_required is True
    assert payload.image_count == 2

    clean = {
        "kind": "generated",
        "role": "clean",
        "qa": {"passed": True, "generated_visible_text": []},
    }
    dimensions = {
        "kind": "generated",
        "role": "dimensions",
        "qa": {"passed": True, "generated_visible_text": ["29 cm"]},
    }
    assert launch_service._image_pair_ready([clean], True) is False
    assert launch_service._image_pair_ready([clean, dimensions], True) is True


def test_draft_manifest_fails_closed_when_dimension_image_is_missing():
    launch = SimpleNamespace(
        id=42,
        request_payload={"image_count": 2, "dimension_image_required": True},
        asset_manifest=[],
    )
    clean_only = [
        {
            "filename": "01-premium.png",
            "kind": "generated",
            "role": "clean",
            "qa": {"passed": True},
        },
        {
            "filename": "02-detail.png",
            "kind": "generated",
            "role": "clean",
            "qa": {"passed": True},
        },
    ]
    draft = SimpleNamespace(
        attributes={"autonomous_launch_id": 42, "asset_manifest": clean_only}
    )

    with pytest.raises(launch_service.LaunchManualRequired) as error:
        launch_service._persist_draft_asset_manifest(launch, draft)

    assert error.value.code == "DRAFT_DIMENSION_IMAGE_INCOMPLETE"


def test_pipeline_uses_ai_and_stops_at_browser_handoff(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(username="admin", password_hash="hash", role="admin", is_active=True)
        db.add(user)
        db.flush()
        candidate = verified_candidate(db)
        launch = AutonomousLaunch(
            status="PENDING",
            current_stage="QUEUED",
            idempotency_key="test-launch-001",
            correlation_id="test-correlation",
            request_payload={
                "candidate_id": candidate.id,
                "candidate_limit": 50,
                "image_count": 2,
                "platform_fee": "2.00",
                "after_sales_reserve": "3.00",
                "minimum_profit": "8.00",
                "target_profit": "14.00",
                "negotiation_margin": "2.00",
            },
            requested_by_user_id=user.id,
            stages=[],
        )
        db.add(launch)
        db.commit()
        db.refresh(launch)

        monkeypatch.setattr(
            launch_service,
            "get_settings",
            lambda: SimpleNamespace(asset_root=str(tmp_path)),
        )

        copy_attempts = 0
        vision_attempts = 0

        def fake_chat_json(_db, *, schema, **_kwargs):
            nonlocal copy_attempts, vision_attempts
            if schema is ListingCopyOutput:
                copy_attempts += 1
                data = ListingCopyOutput(
                    title="米白色可拆分隔桌面收纳盒",
                    description=(
                        "新人专享包邮，规格为29*11.8*7.3cm。"
                        if copy_attempts == 1
                        else "桌面小物可分类摆放，可拆分隔设计，规格为29*11.8*7.3cm。"
                    ),
                    image_prompts=["暖白背景主图", "俯拍展示分隔结构"],
                    factual_basis=[
                        "米白色",
                        "可拆分隔",
                        "sku.spec.尺寸: 29*11.8*7.3cm",
                    ],
                )
                model = "qwen-flash"
            elif schema is CopyQAOutput:
                data = CopyQAOutput(
                    factual_consistency=True,
                    owner_voice=True,
                    no_supplier_context=True,
                    buyer_relevance=True,
                    confidence=Decimal("0.98"),
                    unsupported_claims=[],
                    blockers=[],
                    summary="文案仅包含核验事实",
                )
                model = "qwen3-vl-flash"
            else:
                vision_attempts += 1
                generated_text = (
                    ["1cm"]
                    if vision_attempts == 2
                    else ["29cm", "11.8cm", "7.3cm"]
                )
                data = VisionQAOutput(
                    structure_match=True,
                    color_match=True,
                    specification_consistent=True,
                    no_added_accessories=True,
                    no_misleading_text=True,
                    premium_quality=True,
                    source_visible_text=["29cm", "11.8cm", "7.3cm"],
                    generated_visible_text=generated_text,
                    unreadable_text=False,
                    confidence=Decimal("0.96"),
                    blockers=[],
                    summary="商品事实一致，画面质量通过",
                )
                model = "qwen3-vl-flash"
            return InferenceResult(
                data=data,
                provider="qwen",
                model=model,
                request_id="request-test",
                usage={"total_tokens": 10},
            )

        def fake_image(_db, **_kwargs):
            return InferenceResult(
                data=["https://dashscope-result.aliyuncs.com/result.png"],
                provider="qwen",
                model="qwen-image-2.0",
                request_id="image-test",
                usage={"image_count": 1},
            )

        def fake_download(_url, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (512, 512), (238, 232, 218)).save(destination, format="PNG")
            content = destination.read_bytes()
            import hashlib

            return {
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "mime_type": "image/png",
                "format": "PNG",
                "width": 512,
                "height": 512,
            }

        monkeypatch.setattr(launch_service, "chat_json", fake_chat_json)
        monkeypatch.setattr(launch_service, "generate_reference_image", fake_image)
        monkeypatch.setattr(launch_service, "download_image", fake_download)

        result = execute_autonomous_launch(db, launch.id)

        assert result["status"] == "READY_FOR_BROWSER_HANDOFF"
        assert result["handoff"]["external_submission_performed"] is False
        assert result["product_id"] is not None
        assert result["draft_id"] is not None
        assert len([item for item in result["asset_manifest"] if item["kind"] == "generated"]) == 2
        rejected = [item for item in result["asset_manifest"] if item["kind"] == "rejected"]
        assert rejected[0]["qa"]["unexpected_measurements"] == ["1cm"]
        draft = db.get(XianyuDraft, result["draft_id"])
        assert draft.status == "REVIEW_READY"
        assert draft.validation["passed"] is True
        assert "一件代发" not in draft.description
        assert copy_attempts == 2
        assert db.scalar(select(func.count(AIDecision.id))) == 7
        db.expire_all()
        persisted = db.get(AutonomousLaunch, launch.id)
        assert len([item for item in persisted.asset_manifest if item["kind"] == "generated"]) == 2

        old_draft = XianyuDraft(
            product_id=result["product_id"],
            version=0,
            title="旧草稿",
            description="旧草稿内容",
            price=Decimal("20.00"),
            category="收纳盒",
            status="REVIEW_READY",
            input_hash="old-draft-hash",
            created_by_user_id=user.id,
        )
        db.add(old_draft)
        db.flush()
        old_launch = AutonomousLaunch(
            status="READY_FOR_BROWSER_HANDOFF",
            current_stage="BROWSER_HANDOFF",
            idempotency_key="old-launch-001",
            correlation_id="old-correlation",
            request_payload={},
            requested_by_user_id=user.id,
            sourcing_candidate_id=candidate.id,
            product_id=result["product_id"],
            draft_id=old_draft.id,
            stages=[],
        )
        db.add(old_launch)
        db.commit()

        launch_service._supersede_previous_outputs(db, persisted)
        db.commit()

        assert db.get(XianyuDraft, old_draft.id).status == "SUPERSEDED"
        assert db.get(AutonomousLaunch, old_launch.id).status == "SUPERSEDED"
