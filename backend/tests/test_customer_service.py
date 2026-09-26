from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.services.customer_service as customer_service
from app.models import (
    Account,
    AIDecision,
    Base,
    Conversation,
    Customer,
    ManualTask,
    Message,
    Product,
)
from app.schemas.catalog import ProductCreate
from app.services.catalog import create_catalog_product
from app.services.customer_service import _price_from_message, generate_reply_suggestion


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def conversation(db):
    product = create_catalog_product(
        db,
        ProductCreate(
            supplier_code="SUP-CS",
            supplier_name="客服测试工厂",
            external_product_id="P-CS",
            external_sku_id="SKU-CS",
            internal_code="AF-CS",
            sku_code="AF-SKU-CS",
            title="铝合金桌面支架",
            category="电脑配件",
            spec={"材质": "铝合金", "颜色": "黑色"},
            supplier_price=Decimal("20"),
            shipping_cost=Decimal("5"),
            minimum_profit=Decimal("10"),
            target_profit=Decimal("20"),
            stock=50,
        ),
    )
    account = Account(
        platform="XIANYU",
        external_account_id="account-cs",
        nickname="客服账号",
        status="CONNECTED",
        is_enabled=True,
    )
    customer = Customer(
        platform="XIANYU",
        external_customer_id="buyer-cs",
        display_name_masked="买家***",
    )
    db.add_all([account, customer])
    db.flush()
    item = Conversation(
        account_id=account.id,
        customer_id=customer.id,
        product_id=product.id,
        external_conversation_id="conversation-cs",
    )
    db.add(item)
    db.commit()
    return item


def inbound(db, conversation_id, content):
    item = Message(
        conversation_id=conversation_id,
        external_message_id=f"message-{datetime.now(UTC).timestamp()}",
        direction="INBOUND",
        role="BUYER",
        content=content,
        created_at=datetime.now(UTC),
    )
    db.add(item)
    db.commit()
    return item


def test_stock_question_generates_fact_based_idempotent_suggestion(db):
    item = conversation(db)
    inbound(db, item.id, "这个还有现货吗？")

    first = generate_reply_suggestion(db, item.id)
    second = generate_reply_suggestion(db, item.id)

    assert first.id == second.id
    assert first.decision["intent"] == "STOCK"
    assert first.decision["auto_eligible"] is True
    assert "50 件" in first.decision["reply"]
    assert db.scalar(select(func.count(AIDecision.id))) == 1


def test_low_risk_reply_uses_guarded_model_when_available(db, monkeypatch):
    item = conversation(db)
    inbound(db, item.id, "这个还有现货吗？")

    monkeypatch.setattr(
        customer_service,
        "chat_json",
        lambda *args, **kwargs: SimpleNamespace(
            data=SimpleNamespace(
                reply="您好，这款系统库存为 50 件。下单前我再帮您核对实时库存和规格。",
                reason="保留库存事实并缩短表述",
            ),
            provider="qwen",
            model="qwen-test",
            usage={"total_tokens": 42},
        ),
    )

    decision = generate_reply_suggestion(db, item.id)

    assert decision.provider == "qwen"
    assert decision.model == "qwen-test"
    assert decision.decision["generation_mode"] == "MODEL_GUARDED"
    assert decision.decision["model_usage"] == {"total_tokens": 42}
    assert "50 件" in decision.decision["reply"]


def test_guarded_model_allows_material_already_present_in_verified_spec(db, monkeypatch):
    item = conversation(db)
    inbound(db, item.id, "这个是什么材质和颜色？")
    monkeypatch.setattr(
        customer_service,
        "chat_json",
        lambda *args, **kwargs: SimpleNamespace(
            data=SimpleNamespace(
                reply="您好，已核验规格为铝合金材质、黑色，请以您要购买的具体规格为准。",
                reason="只重排已核验规格",
            ),
            provider="qwen",
            model="qwen-test",
            usage={},
        ),
    )

    decision = generate_reply_suggestion(db, item.id)

    assert decision.provider == "qwen"
    assert decision.decision["generation_mode"] == "MODEL_GUARDED"
    assert "铝合金材质" in decision.decision["reply"]


@pytest.mark.parametrize(
    ("buyer_message", "drafted_reply", "blocked_phrase"),
    [
        ("这个还有现货吗？", "您好，这款当前售价为 50 元。", "售价"),
        (
            "20 元可以吗？",
            "您好，目前库存有 20 件，之后会补到 45 件。",
            "库存有 20 件",
        ),
        ("这个还有现货吗？", "您好，这款系统库存为 50 件，保证正品。", "正品"),
        ("这个还有现货吗？", "您好，这款系统库存为 50 件，支持质保。", "质保"),
        ("这个还有现货吗？", "您好，这款系统库存为 50 件，属于德国进口。", "进口"),
        ("这个还有现货吗？", "您好，这款系统库存为 50 件，采用钛合金材质。", "钛合金"),
        ("这个还有现货吗？", "您好，这款系统库存为 50 件，承重 50kg。", "承重"),
    ],
)
def test_guarded_model_rejects_semantic_swaps_and_unverified_claims(
    db,
    monkeypatch,
    buyer_message,
    drafted_reply,
    blocked_phrase,
):
    item = conversation(db)
    inbound(db, item.id, buyer_message)
    monkeypatch.setattr(
        customer_service,
        "chat_json",
        lambda *args, **kwargs: SimpleNamespace(
            data=SimpleNamespace(reply=drafted_reply, reason="尝试新增未经核验的说法"),
            provider="qwen",
            model="qwen-test",
            usage={"total_tokens": 42},
        ),
    )

    decision = generate_reply_suggestion(db, item.id)

    assert decision.provider == "local"
    assert decision.model == "deterministic-policy-engine"
    assert decision.decision["generation_mode"] == "POLICY_FALLBACK"
    assert blocked_phrase not in decision.decision["reply"]
    assert "大模型不可用，已使用本地安全模板" in decision.reason_summary


def test_refund_question_forces_manual_takeover(db):
    item = conversation(db)
    inbound(db, item.id, "我要求退款并投诉")

    decision = generate_reply_suggestion(db, item.id)

    assert decision.decision["requires_human"] is True
    assert decision.decision["reply"] is None
    assert db.get(Conversation, item.id).manual_mode is True
    assert db.scalar(select(func.count(ManualTask.id))) == 1


def test_negotiation_reply_never_goes_below_price_floor(db):
    item = conversation(db)
    inbound(db, item.id, "20 元可以吗？")

    decision = generate_reply_suggestion(db, item.id)

    assert decision.decision["intent"] == "NEGOTIATION"
    assert decision.decision["auto_eligible"] is True
    assert "¥20.00 暂时无法成交" in decision.decision["reply"]
    assert "当前可确认到 ¥45.00" in decision.decision["reply"]
    assert "35.00" not in decision.decision["reply"]


def test_negotiation_question_quotes_public_price_without_exposing_floor(db):
    item = conversation(db)
    product = db.get(Product, item.product_id)
    product.skus[0].current_sale_price = Decimal("49.90")
    db.commit()
    inbound(db, item.id, "这款最低多少钱？")

    decision = generate_reply_suggestion(db, item.id)

    assert decision.decision["intent"] == "NEGOTIATION"
    assert "当前标价是 ¥49.90" in decision.decision["reply"]
    assert "35.00" not in decision.decision["reply"]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("18元买2个可以吗", Decimal("18")),
        ("2个，¥18.80可以吗", Decimal("18.80")),
        ("23×15.5cm这个多少钱", None),
        ("40可以吗", Decimal("40")),
    ],
)
def test_price_parser_distinguishes_offer_from_quantity_and_dimensions(message, expected):
    assert _price_from_message(message) == expected


def test_outbound_latest_message_has_no_pending_reply(db):
    item = conversation(db)
    inbound(db, item.id, "你好")
    db.add(
        Message(
            conversation_id=item.id,
            external_message_id="seller-reply",
            direction="OUTBOUND",
            role="SELLER",
            content="您好",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    with pytest.raises(ValueError, match="没有待处理"):
        generate_reply_suggestion(db, item.id)
