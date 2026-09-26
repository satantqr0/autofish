from pydantic import BaseModel, Field, ValidationError

from adapters.base import AdapterResult, AdapterStatus
from adapters.http_gateway import JsonGatewayClient
from adapters.runner import CliFailure


class MarketResponse(BaseModel):
    status: AdapterStatus
    items: list[dict] = Field(default_factory=list, max_length=50)
    source: str
    query: str | None = None
    captured_at: str | None = None
    page_url: str | None = None


class PublicMarketAdapter:
    """Dedicated read-only port; no publishing, chat, payment or account credentials."""

    def __init__(self, url, token):
        self.client = JsonGatewayClient(url, token, timeout_seconds=24)

    async def search_market(self, query):
        try:
            raw = await self.client.request("POST", "/search", json={"query": query})
            result = MarketResponse.model_validate(raw)
            if result.source != "goofish-public-browser" or (
                result.status == AdapterStatus.OK and result.query != query
            ):
                raise ValueError("response does not match requested query")
            return AdapterResult(
                status=result.status,
                data=result.items,
                source=result.source,
                safe_message=None
                if result.status == AdapterStatus.OK
                else "公开搜索页暂不可用，等待登录、验证或网络恢复",
            )
        except (CliFailure, ValidationError, ValueError):
            return AdapterResult(
                status=AdapterStatus.TRANSIENT_ERROR,
                source="goofish-public-browser",
                safe_message="行情采集服务暂不可用",
            )
