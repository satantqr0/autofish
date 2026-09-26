import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from adapters.base import AdapterResult, AdapterStatus
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.models.market import MarketPriceRun
from app.services.market_prices import clean_samples, collect, compare_categories, overview, slot_at


def samples(price="100", offset=0, count=10):
    return [{"id": str(i + offset), "title": "商品", "price": price} for i in range(count)]


def category(name, price="100", **kwargs):
    return {"category": name, "status": "OK", "samples": samples(price, **kwargs)}


def test_matching_avoids_composition_bias_and_requires_ten():
    old = [category("手机"), category("电脑"), category("耳机")]
    new = [
        category("手机", "120"),
        category("电脑", "500", offset=100),
        category("耳机", "200", count=9),
    ]
    up, down, eligible = compare_categories(new, old)
    assert eligible == 1 and len(up) == 1 and not down
    assert up[0]["change_pct"] == "20.00"


def test_signs_top_ten_median_and_unchanged():
    old = [category(str(i)) for i in range(25)]
    new = [category(str(i), str(40 + i * 5)) for i in range(25)]
    up, down, eligible = compare_categories(new, old)
    assert eligible == 25 and len(up) == len(down) == 10
    assert up[0]["change_pct"] == "60.00"
    assert down[0]["change_pct"] == "-60.00"
    assert all(x["change_pct"] != "0.00" for x in up + down)


def test_clean_rejects_fake_ranges_deposits_and_duplicate_ids():
    items = [{"item_id": "10", "title": "手机", "price": "¥100"}] * 2
    items += [
        {"item_id": str(i + 20), "title": "手机", "price": p}
        for i, p in enumerate(["NaN", "1", "0", "-30", "100-200", "100起"])
    ]
    items.append({"item_id": "55", "title": "手机定金", "price": "100"})
    assert len(clean_samples(items)) == 1


def test_slot_is_shanghai_and_crosses_midnight():
    assert slot_at(datetime(2026, 9, 21, 0, tzinfo=UTC)) == "2026-09-20T21"
    assert slot_at(datetime(2026, 9, 21, 1, tzinfo=UTC)) == "2026-09-21T09"
    assert slot_at(datetime(2026, 9, 21, 13, tzinfo=UTC)) == "2026-09-21T21"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_auth_failure_is_audited_and_same_slot_not_retried(db):
    class Adapter:
        calls = 0

        async def search_market(self, query):
            self.calls += 1
            return AdapterResult(status=AdapterStatus.AUTH_REQUIRED, source="test")

    adapter = Adapter()
    assert asyncio.run(collect(db, adapter))["status"] == "BLOCKED"
    assert asyncio.run(collect(db, adapter))["status"] == "ALREADY_SCHEDULED"
    assert adapter.calls == 1
    assert overview(db)["rising"] == []


def test_requires_exact_24_hour_baseline_and_reports_stale(db):
    now = datetime(2026, 9, 21, 13, tzinfo=UTC)
    db.add_all(
        [
            MarketPriceRun(
                slot="2026-09-20T09",
                started_at=now - timedelta(hours=36),
                status="COMPLETED",
                results=[category("手机")],
            ),
            MarketPriceRun(
                slot="2026-09-21T21",
                started_at=now,
                status="COMPLETED",
                results=[category("手机", "150")],
            ),
        ]
    )
    db.commit()
    report = overview(db, now + timedelta(hours=14))
    assert report["baseline_slot"] is None and report["rising"] == [] and report["stale"]


def test_manual_retries_are_bounded_and_keep_audit(db):
    class Adapter:
        async def search_market(self, query):
            return AdapterResult(status=AdapterStatus.AUTH_REQUIRED, source="test")

    adapter = Adapter()
    for _ in range(3):
        assert asyncio.run(collect(db, adapter, retry=True))["status"] == "BLOCKED"
    assert asyncio.run(collect(db, adapter, retry=True))["status"] == "RETRY_NOT_ALLOWED"
