import httpx

from adapters.runner import CliFailure


class JsonGatewayClient:
    def __init__(self, base_url, token=None, *, timeout_seconds=30):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.timeout = timeout_seconds

    @property
    def available(self):
        return bool(self.base_url)

    async def request(self, method, path, *, json=None, params=None, headers=None):
        if not self.base_url:
            raise CliFailure("GATEWAY_NOT_CONFIGURED", "JSON Gateway 地址尚未配置")
        request_headers = {"Accept": "application/json", **(headers or {})}
        if self.token:
            request_headers["Authorization"] = f"Bearer {self.token}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=json,
                    params=params,
                    headers=request_headers,
                )
        except httpx.TimeoutException as exc:
            raise CliFailure("TIMEOUT", "Adapter Gateway 请求超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise CliFailure("NETWORK_ERROR", "Adapter Gateway 暂时不可用", retryable=True) from exc
        if response.status_code in {401, 403}:
            raise CliFailure("AUTH_REQUIRED", "Adapter Gateway 授权无效")
        if response.status_code == 429:
            raise CliFailure("RATE_LIMITED", "Adapter Gateway 已限流", retryable=True)
        if response.status_code >= 500:
            raise CliFailure("UPSTREAM_ERROR", "Adapter Gateway 服务异常", retryable=True)
        if response.status_code >= 400:
            raise CliFailure(
                "REQUEST_REJECTED",
                f"Adapter Gateway 拒绝请求（{response.status_code}）",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CliFailure("INVALID_JSON", "Adapter Gateway 未返回有效 JSON") from exc
        if not isinstance(payload, dict):
            raise CliFailure("INVALID_SHAPE", "Adapter Gateway JSON 顶层必须是对象")
        return payload
