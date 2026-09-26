from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, ExternalSnapshot, Message, Order, OrderStatus
from app.schemas.xianyu import XianyuWebhookEvent
from app.services.xianyu_events import ingest_xianyu_event


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def event(event_id, event_type, data):
    return XianyuWebhookEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        account_external_id="account-1",
        data=data,
    )


def test_gateway_events_are_idempotent_and_materialize_messages(db):
    account_event = event(
        "event-account-1",
        "ACCOUNT_UPSERT",
        {"nickname": "授权账号", "status": "CONNECTED"},
    )
    message_event = event(
        "event-message-1",
        "MESSAGE_UPSERT",
        {
            "external_conversation_id": "conversation-1",
            "external_message_id": "message-1",
            "customer_id_masked": "buyer-***",
            "direction": "INBOUND",
            "role": "BUYER",
            "content": "请问什么时候发货？",
        },
    )

    ingest_xianyu_event(db, event=account_event, correlation_id="webhook-1")
    first = ingest_xianyu_event(db, event=message_event, correlation_id="webhook-2")
    duplicate = ingest_xianyu_event(db, event=message_event, correlation_id="webhook-3")

    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True
    assert db.scalar(select(func.count(Message.id))) == 1
    assert db.scalar(
        select(func.count(ExternalSnapshot.id)).where(
            ExternalSnapshot.object_type == "GATEWAY_EVENT"
        )
    ) == 2


def test_order_event_unknown_status_fails_closed(db):
    order_event = event(
        "event-order-1",
        "ORDER_UPSERT",
        {"external_order_id": "order-1", "status": "unexpected", "amount": "99.80"},
    )

    ingest_xianyu_event(db, event=order_event, correlation_id="webhook-order")
    order = db.scalar(select(Order).where(Order.external_order_id == "order-1"))

    assert order.status == OrderStatus.MANUAL_REQUIRED
    assert str(order.revenue) == "99.80"


def test_gateway_schema_rejects_stale_or_malformed_events():
    with pytest.raises(ValidationError):
        XianyuWebhookEvent(
            event_id="event-stale-1",
            event_type="ORDER_UPSERT",
            occurred_at=datetime.now(UTC) - timedelta(days=31),
            account_external_id="account-1",
            data={"external_order_id": "order-1", "status": "PAID"},
        )
    with pytest.raises(ValidationError):
        event(
            "event-message-bad",
            "MESSAGE_UPSERT",
            {
                "external_conversation_id": "conversation-1",
                "external_message_id": "message-1",
                "direction": "SIDEWAYS",
                "role": "BUYER",
                "content": "bad",
            },
        )
