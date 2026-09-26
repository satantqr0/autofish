import asyncio

from adapters.base import AdapterStatus
from adapters.xianyu.market_gateway import PublicMarketAdapter


def test_gateway_binds_query_and_source():
    adapter = PublicMarketAdapter("http://unused", "test")

    async def wrong_query(*args, **kwargs):
        return {"status": "OK", "items": [], "source": "goofish-public-browser", "query": "其他"}

    adapter.client.request = wrong_query
    assert asyncio.run(adapter.search_market("手机")).status == AdapterStatus.TRANSIENT_ERROR


def test_gateway_preserves_verification_stop():
    adapter = PublicMarketAdapter("http://unused", "test")

    async def challenge(*args, **kwargs):
        return {"status": "MANUAL_REQUIRED", "items": [], "source": "goofish-public-browser"}

    adapter.client.request = challenge
    assert asyncio.run(adapter.search_market("手机")).status == AdapterStatus.MANUAL_REQUIRED
