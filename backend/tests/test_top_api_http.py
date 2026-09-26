import asyncio

import adapters.xianyu.top_api as top_api
import httpx
import pytest
from adapters.runner import CliFailure


def _run_status(monkeypatch, status_code: int, content: bytes = b"not-json"):
    async def handler(_request):
        return httpx.Response(status_code, content=content)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        top_api.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    client = top_api.TopApiClient("app", "secret", "session")
    return asyncio.run(client.call("alibaba.idle.test"))


@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (401, "AUTH_REQUIRED", False),
        (429, "RATE_LIMITED", True),
        (503, "UPSTREAM_HTTP_ERROR", True),
        (400, "REQUEST_REJECTED", False),
    ],
)
def test_top_api_classifies_http_status_before_json(
    monkeypatch, status_code, expected_code, retryable
):
    with pytest.raises(CliFailure) as raised:
        _run_status(monkeypatch, status_code)

    assert raised.value.code == expected_code
    assert raised.value.retryable is retryable
