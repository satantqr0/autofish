import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_operator
from app.api.routes.xianyu import overview, sync_conversation_messages
from app.models import (
    Account,
    AutonomousLaunch,
    Base,
    Conversation,
    Customer,
    ExternalSnapshot,
    IntegrationSyncRun,
    Lifecycle,
    PlatformActionRecord,
    User,
    XianyuDraft,
    XianyuProduct,
    XianyuStatus,
)
from app.schemas.catalog import ProductCreate
from app.schemas.xianyu import ManualXianyuPublicationRequest, ManualXianyuSnapshotRequest
from app.services.catalog import create_catalog_product
from app.services.xianyu_manual import (
    ingest_manual_xianyu_snapshot,
    reconcile_manual_xianyu_publication,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def make_user(db, username="operator", role="operator"):
    user = User(username=username, password_hash="not-used", role=role, is_active=True)
    db.add(user)
    db.flush()
    return user


def snapshot_payload(**overrides):
    values = {
        "nickname": "测试闲鱼账号",
        "source_url": "https://www.goofish.com/personal",
        "captured_at": datetime.now(UTC),
        "declared_active_count": 2,
        "declared_sold_count": 10,
        "snapshot_complete": True,
        "products": [
            {
                "external_product_id": "1046486693129",
                "title": "桌面电子配件",
                "price": "432.00",
                "status": "ACTIVE",
                "source_url": (
                    "https://www.goofish.com/item?id=1046486693129&categoryId=50023914"
                ),
            },
            {
                "external_product_id": "1014995040059",
                "title": "DDR5 内存条",
                "price": "2200.00",
                "status": "ACTIVE",
                "source_url": (
                    "https://www.goofish.com/item?id=1014995040059&categoryId=50025387"
                ),
            },
        ],
    }
    values.update(overrides)
    return ManualXianyuSnapshotRequest(**values)


def test_manual_browser_snapshot_creates_traceable_read_only_facts(db):
    user = make_user(db)
    result = ingest_manual_xianyu_snapshot(
        db,
        payload=snapshot_payload(),
        user=user,
        correlation_id="manual-import-1",
    )

    assert result["products_seen"] == 2
    assert result["products_created"] == 2
    assert result["products_updated"] == 0
    assert result["snapshots_written"] == 3
    account = db.scalar(select(Account).where(Account.owner_user_id == user.id))
    assert account.status == "MANUAL_READ_ONLY"
    assert account.external_account_id == f"manual-browser:{user.id}"
    assert account.credential_ref is None
    assert db.scalar(select(func.count(XianyuProduct.id))) == 2
    assert db.scalar(select(func.count(ExternalSnapshot.id))) == 3
    run = db.get(IntegrationSyncRun, result["sync_run_id"])
    assert run.operation == "INGEST_MANUAL_BROWSER_SNAPSHOT"
    assert run.status == "SUCCEEDED"


def test_manual_browser_snapshot_replay_is_idempotent(db):
    user = make_user(db)
    payload = snapshot_payload()
    first = ingest_manual_xianyu_snapshot(
        db,
        payload=payload,
        user=user,
        correlation_id="manual-replay-1",
    )
    second = ingest_manual_xianyu_snapshot(
        db,
        payload=payload,
        user=user,
        correlation_id="manual-replay-2",
    )

    assert first["snapshots_written"] == 3
    assert second["products_created"] == 0
    assert second["products_updated"] == 0
    assert second["snapshots_written"] == 0
    assert db.scalar(select(func.count(XianyuProduct.id))) == 2
    assert db.scalar(select(func.count(ExternalSnapshot.id))) == 3
    assert db.scalar(select(func.count(IntegrationSyncRun.id))) == 2


def test_manual_publication_links_live_listing_to_internal_product(db):
    user = make_user(db)
    product = create_catalog_product(
        db,
        ProductCreate(
            supplier_code="1688-HONGCHEN",
            supplier_name="义乌市虹辰塑料制品有限公司",
            external_product_id="852354763336",
            external_sku_id="medium-white-23x15x15.5",
            internal_code="AF-852354763336-MW",
            sku_code="AF-852354763336-MW",
            title="双层桌面收纳架",
            category="收纳置物架",
            supplier_price="2.42",
            shipping_cost="2.50",
            platform_fee="0.32",
            after_sales_reserve="0.50",
            minimum_profit="5",
            target_profit="10",
            negotiation_margin="2",
            stock=99666,
        ),
        actor_user_id=user.id,
        correlation_id="create-real-product",
    )
    db.flush()
    sku = product.skus[0]
    draft = XianyuDraft(
        product_id=product.id,
        version=1,
        title="双层桌面收纳架",
        description="双层桌面收纳架",
        price=Decimal("19.90"),
        category="收纳置物架",
        attributes={},
        images=["https://img.alicdn.com/example.jpg"],
        validation={"passed": True, "blockers": [], "warnings": []},
        pipeline_trace=[],
        status="REVIEW_READY",
        input_hash="manual-publication-draft-hash",
        created_by_user_id=user.id,
    )
    db.add(draft)
    db.flush()
    action = PlatformActionRecord(
        action_type="BROWSER_PUBLISH_PRODUCT",
        target_type="XIANYU_DRAFT",
        target_id=draft.id,
        request_payload={},
        preview={},
        execution_result={},
        status="QUEUED",
        requires_confirmation=False,
        automatic=True,
        provider="seller-browser-bridge",
        idempotency_key="manual-publication-browser-action",
        requested_by_user_id=user.id,
    )
    launch = AutonomousLaunch(
        product_id=product.id,
        draft_id=draft.id,
        status="READY_FOR_BROWSER_HANDOFF",
        current_stage="LOCAL_BROWSER_BRIDGE",
        idempotency_key="manual-publication-launch",
        correlation_id="manual-publication-launch",
        requested_by_user_id=user.id,
    )
    db.add_all([action, launch])
    db.flush()
    payload = ManualXianyuPublicationRequest(
        product_id=product.id,
        product_sku_id=sku.id,
        draft_id=draft.id,
        nickname="火山星登山的灯草",
        captured_at=datetime.now(UTC),
        external_product_id="1076626628320",
        title="双层桌面收纳架 中号白色 23×15×15.5cm",
        price="19.90",
        status="ACTIVE",
        source_url="https://www.goofish.com/item?id=1076626628320",
    )

    first = reconcile_manual_xianyu_publication(
        db, payload=payload, user=user, correlation_id="manual-publish-1"
    )
    second = reconcile_manual_xianyu_publication(
        db, payload=payload, user=user, correlation_id="manual-publish-2"
    )

    listing = db.get(XianyuProduct, first["xianyu_product_id"])
    db.refresh(product)
    db.refresh(sku)
    assert first["created"] is True
    assert second["created"] is False
    assert second["idempotent"] is True
    assert first["draft_id"] == draft.id
    assert first["draft_reconciled"] is True
    assert first["browser_action_id"] == action.id
    assert first["browser_task_reconciled"] is True
    assert first["launch_id"] == launch.id
    assert first["launch_reconciled"] is True
    assert second["draft_reconciled"] is False
    assert second["browser_task_reconciled"] is False
    assert second["launch_reconciled"] is False
    assert listing.product_id == product.id
    assert listing.external_product_id == "1076626628320"
    assert listing.published_price == Decimal("19.90")
    assert product.xianyu_status == XianyuStatus.ACTIVE
    assert product.lifecycle == Lifecycle.ACTIVE
    assert sku.current_sale_price == Decimal("19.90")
    assert draft.status == "PUBLISHED"
    assert action.status == "SUCCEEDED"
    assert action.execution_result["external_product_id"] == "1076626628320"
    assert action.execution_result["phase"] == "SUBMITTED"
    assert launch.status == "PUBLISHED"
    assert launch.current_stage == "XIANYU_PUBLISHED"
    assert db.scalar(select(func.count(XianyuProduct.id))) == 1


def test_manual_publication_rejects_demo_catalog_product(db):
    user = make_user(db)
    product = create_catalog_product(
        db,
        ProductCreate(
            supplier_code="1688-DEMO",
            supplier_name="1688 演示供应商",
            external_product_id="DEMO-P-1",
            supplier_url="https://detail.1688.example/offer/1.html",
            external_sku_id="DEMO-SKU-1",
            internal_code="AF-DEMO-1",
            sku_code="AF-DEMO-SKU-1",
            title="演示商品",
            category="收纳置物架",
            description="演示数据，仅用于功能验证。",
            supplier_price="2.42",
            shipping_cost="2.50",
            minimum_profit="5",
            target_profit="10",
            stock=10,
        ),
        actor_user_id=user.id,
        correlation_id="create-demo-product",
    )
    db.flush()
    payload = ManualXianyuPublicationRequest(
        product_id=product.id,
        product_sku_id=product.skus[0].id,
        nickname="测试账号",
        captured_at=datetime.now(UTC),
        external_product_id="1076626628999",
        title="演示商品",
        price="19.90",
        status="ACTIVE",
        source_url="https://www.goofish.com/item?id=1076626628999",
    )

    with pytest.raises(ValueError, match="商品发布来源校验失败"):
        reconcile_manual_xianyu_publication(
            db,
            payload=payload,
            user=user,
            correlation_id="manual-publish-demo",
        )

    assert db.scalar(select(func.count(XianyuProduct.id))) == 0


def test_complete_snapshot_marks_missing_active_product_removed(db):
    user = make_user(db)
    first_payload = snapshot_payload()
    ingest_manual_xianyu_snapshot(
        db,
        payload=first_payload,
        user=user,
        correlation_id="manual-complete-1",
    )
    second_payload = snapshot_payload(
        captured_at=datetime.now(UTC),
        declared_active_count=1,
        products=[first_payload.products[0].model_dump(mode="json")],
    )

    result = ingest_manual_xianyu_snapshot(
        db,
        payload=second_payload,
        user=user,
        correlation_id="manual-complete-2",
    )

    removed = db.scalar(
        select(XianyuProduct).where(
            XianyuProduct.external_product_id == "1014995040059"
        )
    )
    assert result["products_removed"] == 1
    assert removed.status == "REMOVED"
    assert removed.raw_snapshot["reason"] == "absent-from-complete-snapshot"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "products": [
                    {
                        "external_product_id": "1046486693129",
                        "title": "链接 ID 不一致",
                        "price": "10",
                        "source_url": "https://www.goofish.com/item?id=999999999999",
                    }
                ],
                "declared_active_count": 1,
            },
            "商品链接中的 ID",
        ),
        (
            {"captured_at": datetime.now(UTC) - timedelta(days=2)},
            "超过 24 小时",
        ),
        (
            {
                "products": [
                    {
                        "external_product_id": "1046486693129",
                        "title": "重复商品一",
                        "price": "10",
                        "source_url": "https://www.goofish.com/item?id=1046486693129",
                    },
                    {
                        "external_product_id": "1046486693129",
                        "title": "重复商品二",
                        "price": "20",
                        "source_url": "https://www.goofish.com/item?id=1046486693129",
                    },
                ],
            },
            "重复商品 ID",
        ),
    ],
)
def test_manual_snapshot_rejects_tampered_or_stale_payloads(overrides, message):
    with pytest.raises(ValidationError, match=message):
        snapshot_payload(**overrides)


def test_manual_snapshot_forbids_unknown_sensitive_fields():
    values = snapshot_payload().model_dump(mode="json")
    values["cookie"] = "must-not-be-accepted"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ManualXianyuSnapshotRequest(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "products": [
                    {
                        "external_product_id": "1046486693129",
                        "title": "伪造来源",
                        "price": "10",
                        "source_url": "https://goofish.com.example.com/item?id=1046486693129",
                    }
                ],
                "declared_active_count": 1,
            },
            "闲鱼 HTTPS 页面",
        ),
        (
            {
                "products": [
                    {
                        "external_product_id": "1046486693129",
                        "title": "不安全协议",
                        "price": "10",
                        "source_url": "http://www.goofish.com/item?id=1046486693129",
                    }
                ],
                "declared_active_count": 1,
            },
            "闲鱼 HTTPS 页面",
        ),
        (
            {
                "products": [
                    {
                        "external_product_id": "1046486693129",
                        "title": "异常价格",
                        "price": "100000000.00",
                        "source_url": "https://www.goofish.com/item?id=1046486693129",
                    }
                ],
                "declared_active_count": 1,
            },
            "no more than 8 digits",
        ),
        (
            {"captured_at": datetime.now(UTC) + timedelta(minutes=10)},
            "不能来自未来",
        ),
        (
            {"nickname": "带控制符\n的昵称"},
            "昵称包含无效控制字符",
        ),
        (
            {"declared_active_count": 3},
            "在售商品数与页面声明数量不一致",
        ),
    ],
)
def test_manual_snapshot_rejects_host_protocol_bounds_and_count_tampering(
    overrides, message
):
    with pytest.raises(ValidationError, match=message):
        snapshot_payload(**overrides)


def test_manual_snapshot_requires_operator_role(db):
    viewer = make_user(db, "viewer", role="viewer")

    with pytest.raises(HTTPException) as raised:
        require_operator(viewer)

    assert raised.value.status_code == 403


def test_xianyu_overview_is_scoped_to_current_user(db):
    owner = make_user(db, "owner")
    other = make_user(db, "other")
    ingest_manual_xianyu_snapshot(
        db,
        payload=snapshot_payload(),
        user=owner,
        correlation_id="manual-scope",
    )

    owner_view = overview(user=owner, db=db)
    other_view = overview(user=other, db=db)

    assert len(owner_view["accounts"]) == 1
    assert len(owner_view["products"]) == 2
    assert owner_view["products"][0]["source"] == "manual-browser-snapshot"
    assert other_view == {"accounts": [], "products": [], "conversations": [], "orders": []}


def test_conversation_sync_hides_other_users_conversations(db):
    owner = make_user(db, "owner")
    other = make_user(db, "other")
    account = Account(
        platform="XIANYU",
        external_account_id="owner-account",
        nickname="owner",
        owner_user_id=owner.id,
    )
    customer = Customer(platform="XIANYU", external_customer_id="masked-customer")
    db.add_all([account, customer])
    db.flush()
    conversation = Conversation(
        account_id=account.id,
        customer_id=customer.id,
        external_conversation_id="owner-conversation",
    )
    db.add(conversation)
    db.commit()

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            sync_conversation_messages(
                conversation_id=conversation.id,
                request=None,
                user=other,
                db=db,
            )
        )

    assert raised.value.status_code == 404
