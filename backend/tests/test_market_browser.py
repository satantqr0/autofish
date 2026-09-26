import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.models.market import MarketPriceRun
from app.schemas.market import MarketCapture
from app.services.market_browser import claim, complete
from app.services.market_prices import clean_samples, collect, overview


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def capture(job, now, status="OK"):
    return MarketCapture(
        **{k: v for k, v in job.items() if k != "expires_at"},
        bridge_id="chrome-test",
        status=status,
        captured_at=now,
        page_url="https://www.goofish.com/search?q=" + quote(job["query"]),
        items=[
            {
                "item_id": "123456",
                "title": "手机无维修 配件齐全",
                "price": "100",
                "url": "https://www.goofish.com/item?id=123456",
            }
        ]
        if status == "OK"
        else [],
    )


def test_queue_real_browser_default_and_dedup(db):
    assert asyncio.run(collect(db))["status"] == "WAITING_BROWSER"
    assert asyncio.run(collect(db))["status"] == "ALREADY_SCHEDULED"
    now = datetime.now(UTC)
    job = claim(db, "chrome-test", now)
    assert claim(db, "other", now) is None
    payload = capture(job, now)
    assert complete(db, payload, now=now)["categories_received"] == 1
    assert complete(db, payload, now=now)["duplicate"]
    report = overview(db)
    assert report["latest_samples"][0]["count"] == 1
    assert report["latest_samples"][0]["source"] == "goofish-chrome-bridge"


@pytest.mark.parametrize(
    "field,value", [("query", "错误"), ("bridge_id", "wrong"), ("lease_token", "wrong" * 9)]
)
def test_wrong_identity_or_query_cannot_complete(db, field, value):
    asyncio.run(collect(db))
    now = datetime.now(UTC)
    payload = capture(claim(db, "chrome-test", now), now).model_copy(update={field: value})
    with pytest.raises(HTTPException) as exc:
        complete(db, payload, now=now)
    assert exc.value.status_code == 409
    assert db.query(MarketPriceRun).one().results == []


def test_stale_and_forged_urls_rejected(db):
    asyncio.run(collect(db))
    now = datetime.now(UTC)
    payload = capture(claim(db, "chrome-test", now), now)
    for update in [
        {"captured_at": now - timedelta(minutes=3)},
        {"page_url": "https://www.goofish.com.evil/search?q=手机"},
    ]:
        with pytest.raises(HTTPException) as exc:
            complete(db, payload.model_copy(update=update), now=now)
        assert exc.value.status_code == 422


def test_risk_stops_all_remaining_queries(db):
    asyncio.run(collect(db))
    now = datetime.now(UTC)
    payload = capture(claim(db, "chrome-test", now), now, "MANUAL_REQUIRED")
    assert complete(db, payload, now=now)["status"] == "BLOCKED"
    assert claim(db, "chrome-test", now + timedelta(minutes=2)) is None


def test_three_expired_leases_end_run(db):
    now = datetime(2026, 9, 21, 2, tzinfo=UTC)
    asyncio.run(collect(db, now=now))
    for i in range(3):
        assert claim(db, "chrome-test", now + timedelta(minutes=2 * i)) is not None
    assert claim(db, "chrome-test", now + timedelta(minutes=6)) is None
    assert db.query(MarketPriceRun).one().status == "FAILED"


def test_accessory_text_not_mistaken_for_accessory_listing():
    def item(title):
        return {"item_id": "123456", "title": title, "price": "100"}

    assert clean_samples([item("手机无维修 配件齐全")])
    assert clean_samples([item("电脑无任何故障")])
    assert not clean_samples([item("手机配件机维修故障")])
    for title in ["电脑电池坏了", "电脑屏幕碎了一条线", "电脑触屏功能失效"]:
        assert not clean_samples([item(title)])


def test_old_offline_browser_slot_expires(db):
    now = datetime(2026, 9, 21, 2, tzinfo=UTC)
    asyncio.run(collect(db, now=now))
    asyncio.run(collect(db, now=now + timedelta(hours=12)))
    oldest = db.query(MarketPriceRun).order_by(MarketPriceRun.id).first()
    assert oldest.status == "MISSED"
