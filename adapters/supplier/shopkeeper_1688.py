from decimal import Decimal, InvalidOperation

from adapters.base import AdapterHealth, AdapterResult, AdapterStatus, Money
from adapters.runner import CliFailure, JsonCliRunner
from adapters.supplier.port import (
    SourcingQuery,
    SupplierInsight,
    SupplierProductCandidate,
    SupplierProductDetail,
)


def _failure_result(source, version, exc):
    status = {
        "AUTH_REQUIRED": AdapterStatus.AUTH_REQUIRED,
        "RATE_LIMITED": AdapterStatus.RATE_LIMITED,
        "TIMEOUT": AdapterStatus.TRANSIENT_ERROR,
        "NETWORK_ERROR": AdapterStatus.TRANSIENT_ERROR,
    }.get(exc.code, AdapterStatus.PERMANENT_ERROR)
    return AdapterResult(
        status=status,
        source=source,
        source_version=version,
        retryable=exc.retryable,
        error_code=exc.code,
        safe_message=exc.safe_message,
    )


def _payload_failure(payload, default_message):
    message = str(payload.get("markdown") or "")
    normalized = message.casefold()
    if "401" in normalized or "签名无效" in message or "ak 无效" in normalized:
        return CliFailure("AUTH_REQUIRED", "1688 AK 无效或已过期")
    if "429" in normalized or "限流" in message:
        return CliFailure("RATE_LIMITED", "1688 接口当前限流", retryable=True)
    if "网络" in message or "timeout" in normalized or "超时" in message:
        return CliFailure("NETWORK_ERROR", "1688 服务暂时不可达", retryable=True)
    return CliFailure("UPSTREAM_REJECTED", default_message)


class Shopkeeper1688Adapter:
    """Read-only process adapter for a separately installed 1688-shopkeeper CLI."""

    source = "1688-shopkeeper-cli"
    source_version = "external"

    def __init__(self, command=None, *, access_key=None, timeout_seconds=60):
        self.runner = JsonCliRunner(
            command,
            timeout_seconds=timeout_seconds,
            environment={"ALI_1688_AK": access_key},
        )

    async def health(self):
        if not self.runner.available:
            return AdapterResult[AdapterHealth](
                status=AdapterStatus.AUTH_REQUIRED,
                data=AdapterHealth(
                    available=False,
                    authenticated=False,
                    read_only=True,
                    details={
                        "capabilities": [
                            "search",
                            "raw_product_detail",
                            "trend",
                            "opportunities",
                        ]
                    },
                ),
                source=self.source,
                source_version=self.source_version,
                error_code="COMMAND_NOT_CONFIGURED",
                safe_message="1688 只读 CLI 或 AK 尚未配置",
            )
        try:
            # `check` only validates AK shape. `shops` performs a signed, read-only
            # upstream request and therefore cannot report a malformed/expired AK as healthy.
            payload = await self.runner.run("shops")
        except CliFailure as exc:
            result = _failure_result(self.source, self.source_version, exc)
            result.data = AdapterHealth(available=True, authenticated=False, read_only=True)
            return result
        if not payload.get("success"):
            result = _failure_result(
                self.source,
                self.source_version,
                _payload_failure(payload, "1688 授权检查未成功"),
            )
            result.data = AdapterHealth(
                available=True,
                authenticated=False,
                read_only=True,
                details={
                    "capabilities": [
                        "search",
                        "raw_product_detail",
                        "trend",
                        "opportunities",
                    ]
                },
            )
            return result
        shop_data = payload.get("data") or {}
        return AdapterResult[AdapterHealth](
            status=AdapterStatus.OK,
            data=AdapterHealth(
                available=True,
                authenticated=True,
                read_only=True,
                details={
                    "capabilities": [
                        "search",
                        "raw_product_detail",
                        "trend",
                        "opportunities",
                    ],
                    "bound_shops": int(shop_data.get("total") or 0),
                    "valid_shops": int(shop_data.get("valid_count") or 0),
                },
            ),
            source=self.source,
            source_version=self.source_version,
        )

    async def search_products(self, query: SourcingQuery):
        arguments = ["search", "--query", query.query]
        channel = query.filters.get("channel")
        if channel:
            arguments.extend(["--channel", str(channel)])
        try:
            payload = await self.runner.run(*arguments)
        except CliFailure as exc:
            return _failure_result(self.source, self.source_version, exc)
        if not payload.get("success"):
            exc = _payload_failure(payload, "1688 搜索未成功")
            return _failure_result(self.source, self.source_version, exc)
        products = (payload.get("data") or {}).get("products") or []
        candidates = []
        for item in products[: query.limit]:
            try:
                price = Money(amount=str(Decimal(str(item.get("price")))))
            except (InvalidOperation, TypeError):
                price = None
            stats = item.get("stats") or {}
            candidates.append(
                SupplierProductCandidate(
                    external_product_id=str(item.get("id") or ""),
                    title=str(item.get("title") or "未知商品"),
                    price=price,
                    url=item.get("url"),
                    image_url=item.get("image"),
                    category=stats.get("categoryListName") or stats.get("categoryName"),
                    stats=stats,
                    raw_payload=item,
                )
            )
        candidates = [item for item in candidates if item.external_product_id]
        return AdapterResult(
            status=AdapterStatus.OK,
            data=candidates,
            source=self.source,
            source_version=self.source_version,
        )

    async def get_product(self, external_product_id):
        try:
            fetched = await self.runner.run("prod_detail", "--item-ids", str(external_product_id))
            if not fetched.get("success"):
                raise _payload_failure(fetched, "1688 商品详情读取失败")
            data_id = (fetched.get("data") or {}).get("data_id")
            if not data_id:
                raise CliFailure("NOT_FOUND", "1688 商品详情为空")
            loaded = await self.runner.run(
                "prod_detail",
                "--data-id",
                str(data_id),
                "--item-ids",
                str(external_product_id),
            )
            if not loaded.get("success"):
                raise _payload_failure(loaded, "1688 商品详情加载失败")
        except CliFailure as exc:
            return _failure_result(self.source, self.source_version, exc)
        raw = ((loaded.get("data") or {}).get("details") or {}).get(str(external_product_id))
        if not isinstance(raw, dict):
            return AdapterResult(
                status=AdapterStatus.NOT_FOUND,
                source=self.source,
                source_version=self.source_version,
                error_code="NOT_FOUND",
                safe_message="1688 商品详情为空",
            )
        return AdapterResult(
            status=AdapterStatus.OK,
            data=SupplierProductDetail(
                external_product_id=str(external_product_id),
                title=str(raw.get("title") or f"1688 商品 {external_product_id}"),
                category=raw.get("category"),
                raw_content=raw.get("all_info"),
                attributes=raw.get("attributes") or {},
                raw_payload=raw,
            ),
            source=self.source,
            source_version=self.source_version,
        )

    async def get_skus(self, _external_product_id):
        return AdapterResult(
            status=AdapterStatus.UNSUPPORTED,
            source=self.source,
            source_version=self.source_version,
            error_code="STRUCTURED_SKU_UNAVAILABLE",
            safe_message="该 CLI 只返回商详原文，不能可靠生成结构化 SKU",
        )

    async def get_price(self, _external_sku_id):
        return await self.get_skus("")

    async def get_stock(self, _external_sku_id):
        return await self.get_skus("")

    async def get_supplier(self, _external_supplier_id):
        return await self.get_skus("")

    async def search_image(self, _source_url, limit=20):
        del limit
        return AdapterResult(
            status=AdapterStatus.UNSUPPORTED,
            source=self.source,
            source_version=self.source_version,
            error_code="CAPABILITY_UNAVAILABLE",
            safe_message="1688 Shopkeeper 不提供图片找同款，请配置 Product Find Adapter",
        )

    async def search_link(self, _source_url, limit=20):
        del limit
        return AdapterResult(
            status=AdapterStatus.UNSUPPORTED,
            source=self.source,
            source_version=self.source_version,
            error_code="CAPABILITY_UNAVAILABLE",
            safe_message="1688 Shopkeeper 不提供链接找同款，请配置 Product Find Adapter",
        )

    async def _insight(self, kind, *arguments, query=None):
        try:
            payload = await self.runner.run(*arguments)
        except CliFailure as exc:
            return _failure_result(self.source, self.source_version, exc)
        if not payload.get("success"):
            return _failure_result(
                self.source,
                self.source_version,
                _payload_failure(payload, f"1688 {kind} 洞察未成功"),
            )
        data = payload.get("data") or {}
        items = []
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict):
            for key in ("items", "products", "trends", "opportunities"):
                if isinstance(data.get(key), list):
                    items = [item for item in data[key] if isinstance(item, dict)]
                    break
        return AdapterResult(
            status=AdapterStatus.OK,
            data=SupplierInsight(
                kind=kind,
                query=query,
                summary=str(payload.get("markdown") or "")[:5000] or None,
                items=items,
                raw_payload=data if isinstance(data, dict) else {"items": data},
            ),
            source=self.source,
            source_version=self.source_version,
        )

    async def get_trends(self, query):
        return await self._insight("TREND", "trend", "--query", query, query=query)

    async def get_opportunities(self, category=None):
        arguments = ["opportunities"]
        if category:
            arguments.extend(["--category", category])
        return await self._insight("OPPORTUNITIES", *arguments, query=category)

    def __getattr__(self, _name):
        raise NotImplementedError("1688 write operations are intentionally unavailable")
