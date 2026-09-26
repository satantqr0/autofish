import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.services.xianyu_browser_bridge as bridge_service
from app.models import (
    Account,
    AIDecision,
    AutomationControl,
    AutomationScope,
    AutonomousLaunch,
    Base,
    BrowserBridgeAgent,
    Conversation,
    Customer,
    ManualTask,
    Message,
    PlatformActionRecord,
    Product,
    User,
    XianyuDraft,
    XianyuProduct,
    XianyuProductDailyMetric,
)
from app.schemas.catalog import ProductCreate
from app.schemas.xianyu import (
    BrowserBridgeAgentState,
    BrowserBridgeTaskCreate,
    BrowserBridgeTaskProgress,
    BrowserBridgeTaskResult,
)
from app.services.catalog import create_catalog_product
from app.services.xianyu_browser_bridge import (
    bridge_overview,
    bridge_token_matches,
    claim_bridge_task,
    create_bridge_task,
    finish_bridge_task,
    heartbeat_agent,
    record_bridge_progress,
    resolve_bridge_task_asset,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def bridge_settings(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        xianyu_browser_bridge_token="test-bridge-token-with-at-least-32-characters",
        xianyu_browser_bridge_token_file=None,
        xianyu_browser_publish_enabled=True,
        asset_root=str(tmp_path),
    )
    monkeypatch.setattr(bridge_service, "get_settings", lambda: settings)


def make_user(db, username="operator"):
    user = User(username=username, password_hash="unused", role="operator", is_active=True)
    db.add(user)
    db.flush()
    return user


def make_draft(db, user):
    product = create_catalog_product(
        db,
        ProductCreate(
            supplier_code="SUP-BRIDGE-1",
            supplier_name="浏览器桥接测试工厂",
            external_product_id="BRIDGE-P-1",
            external_sku_id="BRIDGE-SKU-1",
            internal_code="AF-BRIDGE-1",
            sku_code="AF-BRIDGE-SKU-1",
            title="桌面收纳支架",
            category="电脑配件",
            description="真实商品描述",
            supplier_price="20",
            shipping_cost="5",
            minimum_profit="10",
            target_profit="20",
            stock=50,
        ),
        actor_user_id=user.id,
        correlation_id="create-bridge-product",
    )
    draft = XianyuDraft(
        product_id=product.id,
        version=1,
        title="桌面收纳支架",
        description=(
            "真实商品描述。\n\n下单前请先确认库存和规格，商品细节及售后问题可随时沟通。"
        ),
        price=Decimal("59.90"),
        category="电脑配件",
        images=["https://img.example.com/product.jpg", "http://unsafe/image.jpg"],
        validation={"passed": True, "blockers": [], "warnings": []},
        pipeline_trace=[],
        status="DRAFT",
        input_hash="a" * 64,
        created_by_user_id=user.id,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def agent_state(bridge_id="chrome-test"):
    return BrowserBridgeAgentState(
        bridge_id=bridge_id,
        name="测试 Chrome",
        version="0.5.0",
        capabilities=[
            "OPEN_MODULE",
            "CAPTURE_OVERVIEW",
            "CAPTURE_PRODUCT_METRICS",
            "CAPTURE_CONVERSATIONS",
            "PREFILL_PRODUCT",
            "PUBLISH_PRODUCT",
            "SEND_REPLY",
        ],
        current_url="https://seller.goofish.com/?site=COMMONPRO#/seller-base/home",
    )


def make_reply_task(db, *, idempotency_key="browser-send-reply-confirmed-001"):
    user = make_user(db)
    account = Account(
        platform="XIANYU",
        external_account_id="manual-browser:reply-owner",
        nickname="回复账号",
        owner_user_id=user.id,
        status="BROWSER_CONNECTED",
    )
    customer = Customer(
        platform="XIANYU",
        external_customer_id="buyer-" + "e" * 24,
        display_name_masked="买***家",
    )
    db.add_all([account, customer])
    db.flush()
    conversation = Conversation(
        account_id=account.id,
        customer_id=customer.id,
        external_conversation_id="browser-conv-" + "f" * 64,
        status="OPEN",
    )
    db.add(conversation)
    db.flush()
    inbound = Message(
        conversation_id=conversation.id,
        external_message_id="browser-msg-" + "1" * 64,
        direction="INBOUND",
        role="BUYER",
        content="在吗",
        created_at=datetime.now(UTC),
    )
    db.add(inbound)
    db.flush()
    suggestion_input_hash = hashlib.sha256(
        json.dumps(
            {
                "conversation_id": conversation.id,
                "message_id": inbound.id,
                "external_message_id": inbound.external_message_id,
                "content": inbound.content,
                "product_id": conversation.product_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    suggestion = AIDecision(
        agent="CUSTOMER_ROUTER",
        schema_version="1.0",
        input_context_hash=suggestion_input_hash,
        decision={
            "intent": "GREETING",
            "requires_human": False,
            "auto_eligible": True,
            "reply": "您好，我在。请告诉我想确认的商品规格、库存或发货问题。",
            "source_message_id": inbound.id,
            "external_message_id": inbound.external_message_id,
        },
        confidence=Decimal("0.95"),
        reason_summary="低风险问候",
        model="qwen-test",
        provider="qwen",
        prompt_version="customer-policy-1.1",
        conversation_id=conversation.id,
    )
    db.add(suggestion)
    db.commit()
    task = create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="SEND_REPLY",
            conversation_id=conversation.id,
            suggestion_id=suggestion.id,
            idempotency_key=idempotency_key,
            confirm=True,
        ),
        user=user,
        correlation_id="reply-create",
    )
    claimed = claim_bridge_task(db, agent_state())
    return conversation, inbound, task, claimed


def test_bridge_token_requires_exact_match():
    assert bridge_token_matches("test-bridge-token-with-at-least-32-characters")
    assert not bridge_token_matches("wrong")
    assert not bridge_token_matches(None)


def test_browser_bridge_heartbeat_claim_and_finish(db):
    user = make_user(db)
    task = create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="OPEN_MODULE",
            module="商品管理",
            idempotency_key="browser-open-products-001",
        ),
        user=user,
        correlation_id="bridge-create",
    )
    heartbeat_agent(db, agent_state())
    claimed = claim_bridge_task(db, agent_state())

    assert claimed.id == task.id
    assert claimed.status == "RUNNING"
    assert claimed.lease_owner == "chrome-test"
    assert claimed.request_payload == {"operation": "OPEN_MODULE", "module": "商品管理"}

    finished = finish_bridge_task(
        db,
        task_id=task.id,
        payload=BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="SUCCEEDED",
            current_url="https://seller.goofish.com/?site=COMMONPRO#/seller-base/item",
            page_title="商品管理",
            module="商品管理",
        ),
        correlation_id="bridge-result",
    )

    assert finished.status == "SUCCEEDED"
    assert finished.executed_at is not None
    assert finished.lease_owner is None
    assert db.scalar(select(func.count(BrowserBridgeAgent.id))) == 1


def test_prefill_task_is_server_built_and_stops_for_manual_confirmation(db):
    user = make_user(db)
    draft = make_draft(db, user)
    task = create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="PREFILL_PRODUCT",
            draft_id=draft.id,
            idempotency_key="browser-prefill-draft-001",
            confirm=True,
        ),
        user=user,
        correlation_id="prefill-create",
    )

    assert task.request_payload["title"] == draft.title
    assert task.request_payload["price"] == "59.90"
    assert task.request_payload["images"] == ["https://img.example.com/product.jpg"]
    assert "selector" not in task.request_payload
    assert "script" not in task.request_payload
    claimed = claim_bridge_task(db, agent_state())
    finished = finish_bridge_task(
        db,
        task_id=claimed.id,
        payload=BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="MANUAL_REQUIRED",
            current_url="https://seller.goofish.com/?site=COMMONPRO#/seller-base/publish",
            page_title="商品发布",
            module="商品发布",
            filled_fields=["title", "description", "price"],
            missing_fields=["category", "images"],
            manual_reason="草稿已预填，请人工核对后发布",
        ),
        correlation_id="prefill-result",
    )

    assert finished.status == "MANUAL_REQUIRED"
    assert finished.executed_at is None
    manual = db.scalar(
        select(ManualTask).where(
            ManualTask.entity_type == "PLATFORM_ACTION",
            ManualTask.entity_id == str(task.id),
        )
    )
    assert manual.type == "BROWSER_CONFIRMATION"


def test_prefill_requires_operator_confirmation_and_owned_draft(db):
    owner = make_user(db, "owner")
    other = make_user(db, "other")
    draft = make_draft(db, owner)

    with pytest.raises(ValidationError, match="明确确认"):
        BrowserBridgeTaskCreate(
            operation="PREFILL_PRODUCT",
            draft_id=draft.id,
            idempotency_key="browser-prefill-no-confirm",
        )

    with pytest.raises(LookupError, match="草稿不存在"):
        create_bridge_task(
            db,
            payload=BrowserBridgeTaskCreate(
                operation="PREFILL_PRODUCT",
                draft_id=draft.id,
                idempotency_key="browser-prefill-other-user",
                confirm=True,
            ),
            user=other,
            correlation_id="other-user",
        )


def test_prefill_rejects_demo_provenance_at_task_creation(db):
    user = make_user(db)
    draft = make_draft(db, user)
    product = db.get(Product, draft.product_id)
    product.description = "演示数据，仅用于功能验证。"
    db.commit()

    with pytest.raises(ValueError, match="商品发布来源校验失败"):
        create_bridge_task(
            db,
            payload=BrowserBridgeTaskCreate(
                operation="PREFILL_PRODUCT",
                draft_id=draft.id,
                idempotency_key="browser-prefill-demo-create",
                confirm=True,
            ),
            user=user,
            correlation_id="prefill-demo-create",
        )


def test_prefill_rejects_supplier_facing_copy_at_task_creation(db):
    user = make_user(db)
    draft = make_draft(db, user)
    draft.description = "一键代发，支持代发货，一件发货。"
    db.commit()

    with pytest.raises(ValueError, match="闲鱼发布文案校验失败"):
        create_bridge_task(
            db,
            payload=BrowserBridgeTaskCreate(
                operation="PREFILL_PRODUCT",
                draft_id=draft.id,
                idempotency_key="browser-prefill-supplier-copy",
                confirm=True,
            ),
            user=user,
            correlation_id="prefill-supplier-copy",
        )


def test_prefill_claim_rechecks_provenance_before_browser_receives_payload(db):
    user = make_user(db)
    draft = make_draft(db, user)
    task = create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="PREFILL_PRODUCT",
            draft_id=draft.id,
            idempotency_key="browser-prefill-provenance-recheck",
            confirm=True,
        ),
        user=user,
        correlation_id="prefill-provenance-recheck-create",
    )
    product = db.get(Product, draft.product_id)
    product.description = "演示数据，不可进入发布页面。"
    db.commit()

    claimed = claim_bridge_task(db, agent_state())
    db.refresh(task)

    assert claimed is None
    assert task.status == "BLOCKED"
    assert task.error_code == "PUBLICATION_PROVENANCE_BLOCKED"
    assert task.attempts == 0
    assert task.preview["executable"] is False


def test_bridge_result_forbids_sensitive_or_arbitrary_payloads():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="SUCCEEDED",
            current_url="https://seller.goofish.com/",
            cookie="forbidden",
        )
    with pytest.raises(ValidationError, match="商家工作台地址"):
        BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="SUCCEEDED",
            current_url="https://example.com/steal",
        )
    with pytest.raises(ValidationError, match="必须转人工"):
        BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="SUCCEEDED",
            current_url="https://seller.goofish.com/",
            risk_detected=True,
        )


def test_bridge_overview_is_scoped_to_current_user(db):
    owner = make_user(db, "owner")
    other = make_user(db, "other")
    create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="CAPTURE_OVERVIEW",
            idempotency_key="browser-capture-owner-001",
        ),
        user=owner,
        correlation_id="owner-task",
    )
    heartbeat_agent(db, agent_state())

    assert len(bridge_overview(db, user=owner)["tasks"]) == 1
    assert bridge_overview(db, user=other)["tasks"] == []
    assert bridge_overview(db, user=owner)["agents"][0]["status"] == "ONLINE"


@pytest.mark.parametrize(
    ("publish_enabled", "customer_service_enabled"),
    [
        (False, True),
        (True, False),
    ],
)
def test_bridge_overview_submission_hard_gates_are_independent(
    db,
    monkeypatch,
    publish_enabled,
    customer_service_enabled,
):
    settings = SimpleNamespace(
        xianyu_browser_bridge_token="test-bridge-token-with-at-least-32-characters",
        xianyu_browser_bridge_token_file=None,
        xianyu_browser_publish_enabled=publish_enabled,
        xianyu_browser_customer_service_enabled=customer_service_enabled,
    )
    monkeypatch.setattr(bridge_service, "get_settings", lambda: settings)
    user = make_user(db)

    safety = bridge_overview(db, user=user)["safety"]

    assert safety["publish_submission_enabled"] is publish_enabled
    assert safety["customer_service_submission_enabled"] is customer_service_enabled
    assert safety["external_submission"] is (
        publish_enabled or customer_service_enabled
    )


def test_product_metrics_capture_is_read_only_and_materializes_daily_facts(db):
    user = make_user(db)
    account = Account(
        platform="XIANYU",
        external_account_id="manual-browser:metrics-bridge",
        nickname="指标采集账号",
        owner_user_id=user.id,
        status="MANUAL_READ_ONLY",
    )
    db.add(account)
    db.commit()
    task = create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="CAPTURE_PRODUCT_METRICS",
            idempotency_key="browser-capture-product-metrics-001",
        ),
        user=user,
        correlation_id="metrics-capture-create",
    )
    metric_date = task.request_payload["metric_date"]
    heartbeat_agent(db, agent_state())
    claimed = claim_bridge_task(db, agent_state())

    finished = finish_bridge_task(
        db,
        task_id=claimed.id,
        payload=BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="SUCCEEDED",
            current_url="https://seller.goofish.com/?site=COMMONPRO#/seller-data/commodity",
            module="商品数据",
            metric_date=metric_date,
            metrics_duplicate_rows_skipped=9,
            metrics_pages=2,
            metrics=[
                {
                    "external_product_id": "1075744605568",
                    "listing_title": "透明旋转收纳架",
                    "listing_price": "39.90",
                    "exposures": 53,
                    "exposed_users": 53,
                    "views": 0,
                    "viewers": 0,
                    "inquiries": 0,
                    "paid_users": 0,
                    "paid_orders": 0,
                    "paid_amount": "0",
                    "browse_pay_conversion": None,
                    "refund_requested_users": 0,
                    "refund_requested_orders": 0,
                    "refund_requested_amount": "0",
                    "refund_success_users": 0,
                    "refund_success_orders": 0,
                    "refund_success_amount": "0",
                }
            ],
        ),
        correlation_id="metrics-capture-result",
    )

    assert finished.status == "SUCCEEDED"
    assert finished.requires_confirmation is False
    assert finished.execution_result["metrics_import"]["rows"] == 1
    assert finished.execution_result["metrics_duplicate_rows_skipped"] == 9
    assert finished.execution_result["metrics_pages"] == 2
    assert "metrics" not in finished.execution_result
    assert db.scalar(select(func.count(XianyuProductDailyMetric.id))) == 1


def test_conversation_capture_materializes_real_messages_without_raw_identity(db):
    user = make_user(db)
    account = Account(
        platform="XIANYU",
        external_account_id="manual-browser:chat-owner",
        nickname="客服采集账号",
        owner_user_id=user.id,
        status="MANUAL_READ_ONLY",
    )
    db.add(account)
    db.flush()
    db.add(
        XianyuProduct(
            account_id=account.id,
            external_product_id="1075744605568",
            status="ON_SALE",
            published_title="透明旋转收纳架",
        )
    )
    db.commit()
    create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="CAPTURE_CONVERSATIONS",
            idempotency_key="browser-capture-conversations-001",
        ),
        user=user,
        correlation_id="chat-capture-create",
    )
    claimed = claim_bridge_task(db, agent_state())
    captured_at = datetime.now(UTC)
    conversation_id = "browser-conv-" + "a" * 64
    buyer_message_id = "browser-msg-" + "b" * 64
    seller_message_id = "browser-msg-" + "c" * 64

    finished = finish_bridge_task(
        db,
        task_id=claimed.id,
        payload=BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="SUCCEEDED",
            current_url="https://seller.goofish.com/?site=COMMONPRO#/im",
            module="消息",
            captured_at=captured_at,
            conversations=[
                {
                    "external_conversation_id": conversation_id,
                    "customer_id_masked": "buyer-" + "d" * 24,
                    "customer_name_masked": "落***r",
                    "external_product_id": "1075744605568",
                    "last_message_at": captured_at,
                    "messages": [
                        {
                            "external_message_id": seller_message_id,
                            "direction": "OUTBOUND",
                            "role": "SELLER",
                            "content": "您好，请问想了解哪个规格？",
                            "sent_at": captured_at - timedelta(seconds=1),
                            "timestamp_source": "PLATFORM_VISIBLE",
                        },
                        {
                            "external_message_id": buyer_message_id,
                            "direction": "INBOUND",
                            "role": "BUYER",
                            "content": "这个还有现货吗？",
                            "sent_at": captured_at,
                            "timestamp_source": "PLATFORM_VISIBLE",
                        },
                    ],
                }
            ],
        ),
        correlation_id="chat-capture-result",
    )

    assert finished.status == "SUCCEEDED"
    assert finished.execution_result["conversation_import"]["conversations_seen"] == 1
    assert finished.execution_result["conversation_import"]["conversations_verified"] == 1
    assert finished.execution_result["conversation_import"]["conversations_unverified"] == 0
    assert finished.execution_result["conversation_import"]["messages_created"] == 2
    assert "conversations" not in finished.execution_result
    conversation = db.scalar(
        select(Conversation).where(Conversation.external_conversation_id == conversation_id)
    )
    assert conversation is not None
    assert db.scalar(select(func.count(Message.id))) == 2


def test_conversation_capture_ignores_unverified_purchase_or_supplier_chat(db):
    user = make_user(db)
    account = Account(
        platform="XIANYU",
        external_account_id="manual-browser:chat-filter-owner",
        nickname="客服过滤账号",
        owner_user_id=user.id,
        status="MANUAL_READ_ONLY",
    )
    db.add(account)
    db.commit()
    create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="CAPTURE_CONVERSATIONS",
            idempotency_key="browser-filter-unverified-chat-001",
        ),
        user=user,
        correlation_id="chat-filter-create",
    )
    claimed = claim_bridge_task(db, agent_state())
    captured_at = datetime.now(UTC)
    finished = finish_bridge_task(
        db,
        task_id=claimed.id,
        payload=BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="SUCCEEDED",
            current_url="https://seller.goofish.com/?site=COMMONPRO#/im",
            module="消息",
            captured_at=captured_at,
            conversations=[
                {
                    "external_conversation_id": "browser-conv-" + "7" * 64,
                    "customer_id_masked": "buyer-" + "8" * 24,
                    "customer_name_masked": "供***商",
                    "external_product_id": "1067582425147",
                    "last_message_at": captured_at,
                    "messages": [
                        {
                            "external_message_id": "browser-msg-" + "9" * 64,
                            "direction": "INBOUND",
                            "role": "BUYER",
                            "content": "你好，散片",
                            "sent_at": captured_at,
                            "timestamp_source": "PLATFORM_VISIBLE",
                        }
                    ],
                }
            ],
        ),
        correlation_id="chat-filter-result",
    )

    imported = finished.execution_result["conversation_import"]
    assert imported["conversations_verified"] == 0
    assert imported["conversations_unverified"] == 1
    assert imported["messages_created"] == 0
    assert db.scalar(select(func.count(Conversation.id))) == 0


def test_confirmed_browser_reply_is_stale_checked_and_recorded_once(db):
    conversation, inbound, task, claimed = make_reply_task(db)
    sent_message_id = "browser-msg-" + "3" * 64
    finished = finish_bridge_task(
        db,
        task_id=claimed.id,
        payload=BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="SUCCEEDED",
            current_url="https://seller.goofish.com/?site=COMMONPRO#/im",
            module="消息",
            submission_attempted=True,
            reply_sent=True,
            external_conversation_id=conversation.external_conversation_id,
            source_message_id=inbound.external_message_id,
            sent_message_id=sent_message_id,
            success_evidence="MESSAGE_APPEARED",
            form_fingerprint=bridge_service._reply_form_fingerprint(task.request_payload),
        ),
        correlation_id="reply-result",
    )

    assert task.id == finished.id
    assert finished.status == "SUCCEEDED"
    outbound = db.scalar(select(Message).where(Message.external_message_id == sent_message_id))
    assert outbound is not None
    assert outbound.sent_by_ai is True
    assert outbound.direction == "OUTBOUND"
    assert finished.execution_result["reply_evidence_verified"] is True


@pytest.mark.parametrize(
    ("evidence_change", "expected_error"),
    [
        ("conversation", "REPLY_CONVERSATION_EVIDENCE_MISMATCH"),
        ("source_message", "REPLY_SOURCE_MESSAGE_EVIDENCE_MISMATCH"),
        ("reply_text", "REPLY_TEXT_EVIDENCE_MISMATCH"),
    ],
)
def test_browser_reply_evidence_mismatch_never_records_success(
    db,
    evidence_change,
    expected_error,
):
    conversation, inbound, task, claimed = make_reply_task(
        db,
        idempotency_key=f"browser-reply-evidence-{evidence_change}",
    )
    evidence = {
        "external_conversation_id": conversation.external_conversation_id,
        "source_message_id": inbound.external_message_id,
        "reply": task.request_payload["reply"],
    }
    if evidence_change == "conversation":
        evidence["external_conversation_id"] = "browser-conv-" + "a" * 64
    elif evidence_change == "source_message":
        evidence["source_message_id"] = "browser-msg-" + "b" * 64
    else:
        evidence["reply"] = "这是一条未经确认的错误回复"
    sent_message_id = "browser-msg-" + "9" * 64

    finished = finish_bridge_task(
        db,
        task_id=claimed.id,
        payload=BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="SUCCEEDED",
            current_url="https://seller.goofish.com/?site=COMMONPRO#/im",
            module="消息",
            submission_attempted=True,
            reply_sent=True,
            external_conversation_id=evidence["external_conversation_id"],
            source_message_id=evidence["source_message_id"],
            sent_message_id=sent_message_id,
            success_evidence="MESSAGE_APPEARED",
            form_fingerprint=bridge_service._reply_form_fingerprint(evidence),
        ),
        correlation_id=f"reply-evidence-{evidence_change}",
    )

    assert finished.status == "MANUAL_REQUIRED"
    assert finished.error_code == expected_error
    assert finished.execution_result["reply_evidence_verified"] is False
    assert db.scalar(select(Message).where(Message.external_message_id == sent_message_id)) is None
    assert db.scalar(
        select(func.count(ManualTask.id)).where(
            ManualTask.entity_type == "PLATFORM_ACTION",
            ManualTask.entity_id == str(task.id),
        )
    ) == 1


def test_result_rejects_wrong_lease_owner(db):
    user = make_user(db)
    task = create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="CAPTURE_OVERVIEW",
            idempotency_key="browser-capture-lease-001",
        ),
        user=user,
        correlation_id="lease-create",
    )
    claim_bridge_task(db, agent_state("chrome-owner"))

    with pytest.raises(ValueError, match="租约不属于"):
        finish_bridge_task(
            db,
            task_id=task.id,
            payload=BrowserBridgeTaskResult(
                bridge_id="chrome-other",
                status="SUCCEEDED",
                current_url="https://seller.goofish.com/",
            ),
            correlation_id="lease-result",
        )

    assert db.get(PlatformActionRecord, task.id).status == "RUNNING"


def make_publishable_launch(db, user, draft):
    draft.status = "REVIEW_READY"
    root = Path(bridge_service.get_settings().asset_root)
    relative = Path("autonomous") / "bridge-test" / "01-premium.png"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"verified-premium-image"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    launch = AutonomousLaunch(
        draft_id=draft.id,
        product_id=draft.product_id,
        status="READY_FOR_BROWSER_HANDOFF",
        current_stage="READY_FOR_BROWSER_HANDOFF",
        idempotency_key=f"autonomous-bridge-{draft.id}",
        correlation_id=f"autonomous-bridge-{draft.id}",
        request_payload={},
        selection={},
        ai_copy={},
        asset_manifest=[
            {
                "filename": path.name,
                "relative_path": str(relative),
                "kind": "generated",
                "mime_type": "image/png",
                "sha256": digest,
                "bytes": len(data),
                "qa": {"passed": True},
            }
        ],
        vision_qa={"passed": True},
        stages=[],
        blockers=[],
        requested_by_user_id=user.id,
    )
    db.add_all(
        [
            launch,
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
        ]
    )
    db.commit()
    return launch, path, digest


def test_publish_task_uses_verified_assets_and_reconciles_success(db):
    user = make_user(db)
    draft = make_draft(db, user)
    launch, _path, digest = make_publishable_launch(db, user, draft)
    task = create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="PUBLISH_PRODUCT",
            draft_id=draft.id,
            idempotency_key="browser-publish-verified-001",
            confirm=True,
        ),
        user=user,
        correlation_id="publish-create",
    )

    assert task.request_payload["submit"] is True
    assert task.request_payload["images"] == [
        {
            "ordinal": 0,
            "filename": "01-premium.png",
            "mime_type": "image/png",
            "sha256": digest,
            "bytes": len(b"verified-premium-image"),
        }
    ]
    assert "relative_path" not in task.request_payload["images"][0]
    claimed = claim_bridge_task(db, agent_state())
    record_bridge_progress(
        db,
        task_id=claimed.id,
        payload=BrowserBridgeTaskProgress(
            bridge_id="chrome-test",
            phase="SUBMITTING",
            current_url="https://seller.goofish.com/?site=COMMONPRO#/seller-item/publish",
            form_fingerprint="b" * 64,
        ),
        correlation_id="publish-progress",
    )
    finished = finish_bridge_task(
        db,
        task_id=task.id,
        payload=BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="SUCCEEDED",
            current_url="https://seller.goofish.com/?site=COMMONPRO#/seller-item/publish",
            module="商品发布",
            filled_fields=["title", "description", "price", "images"],
            submission_attempted=True,
            uploaded_images=1,
            form_fingerprint="b" * 64,
            external_product_id="123456789012",
            success_evidence="ITEM_URL",
        ),
        correlation_id="publish-result",
    )

    assert finished.status == "SUCCEEDED"
    assert db.get(XianyuDraft, draft.id).status == "PUBLISHED"
    assert db.get(AutonomousLaunch, launch.id).status == "PUBLISHED"
    assert finished.execution_result["phase"] == "SUBMITTED"


def test_publish_task_recovers_final_manifest_from_audited_draft_snapshot(db):
    user = make_user(db)
    draft = make_draft(db, user)
    launch, _path, digest = make_publishable_launch(db, user, draft)
    final_manifest = list(launch.asset_manifest)
    draft.attributes = {
        "autonomous_launch_id": launch.id,
        "asset_manifest": final_manifest,
    }
    launch.asset_manifest = [
        {
            "filename": "00-source.jpg",
            "relative_path": "autonomous/bridge-test/00-source.jpg",
            "kind": "source",
        }
    ]
    db.commit()

    task = create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="PUBLISH_PRODUCT",
            draft_id=draft.id,
            idempotency_key="browser-publish-draft-manifest-fallback",
            confirm=True,
        ),
        user=user,
        correlation_id="publish-draft-manifest-fallback",
    )

    assert task.request_payload["images"][0]["sha256"] == digest
    claim_bridge_task(db, agent_state("chrome-owner"))
    path, descriptor = resolve_bridge_task_asset(
        db,
        task_id=task.id,
        bridge_id="chrome-owner",
        ordinal=0,
    )
    assert path.name == "01-premium.png"
    assert descriptor["sha256"] == digest


def test_publish_asset_requires_active_lease_and_detects_tampering(db):
    user = make_user(db)
    draft = make_draft(db, user)
    _launch, path, _digest = make_publishable_launch(db, user, draft)
    task = create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="PUBLISH_PRODUCT",
            draft_id=draft.id,
            idempotency_key="browser-publish-asset-001",
            confirm=True,
        ),
        user=user,
        correlation_id="asset-create",
    )
    claim_bridge_task(db, agent_state("chrome-owner"))

    with pytest.raises(ValueError, match="租约无效"):
        resolve_bridge_task_asset(db, task_id=task.id, bridge_id="chrome-other", ordinal=0)
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="完整性校验失败"):
        resolve_bridge_task_asset(db, task_id=task.id, bridge_id="chrome-owner", ordinal=0)


def test_submitting_lease_expiry_never_retries_publish(db):
    user = make_user(db)
    draft = make_draft(db, user)
    make_publishable_launch(db, user, draft)
    task = create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="PUBLISH_PRODUCT",
            draft_id=draft.id,
            idempotency_key="browser-publish-lease-001",
            confirm=True,
        ),
        user=user,
        correlation_id="lease-create",
    )
    claim_bridge_task(db, agent_state("chrome-owner"))
    record_bridge_progress(
        db,
        task_id=task.id,
        payload=BrowserBridgeTaskProgress(
            bridge_id="chrome-owner",
            phase="SUBMITTING",
            current_url="https://seller.goofish.com/?site=COMMONPRO#/seller-item/publish",
            form_fingerprint="c" * 64,
        ),
        correlation_id="lease-progress",
    )
    task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    assert claim_bridge_task(db, agent_state("chrome-other")) is None
    db.refresh(task)
    assert task.status == "MANUAL_REQUIRED"
    assert task.error_code == "PUBLISH_RESULT_AMBIGUOUS"
    assert task.attempts == 1


def test_failed_result_after_click_is_forced_to_manual_review(db):
    user = make_user(db)
    draft = make_draft(db, user)
    make_publishable_launch(db, user, draft)
    task = create_bridge_task(
        db,
        payload=BrowserBridgeTaskCreate(
            operation="PUBLISH_PRODUCT",
            draft_id=draft.id,
            idempotency_key="browser-publish-ambiguous-001",
            confirm=True,
        ),
        user=user,
        correlation_id="ambiguous-create",
    )
    claim_bridge_task(db, agent_state())
    finished = finish_bridge_task(
        db,
        task_id=task.id,
        payload=BrowserBridgeTaskResult(
            bridge_id="chrome-test",
            status="FAILED",
            current_url="https://seller.goofish.com/?site=COMMONPRO#/seller-item/publish",
            submission_attempted=True,
            error_code="CONTENT_SCRIPT_ERROR",
            manual_reason="页面跳转导致连接中断",
        ),
        correlation_id="ambiguous-result",
    )

    assert finished.status == "MANUAL_REQUIRED"
    assert db.get(XianyuDraft, draft.id).status == "REVIEW_READY"
