from decimal import Decimal, InvalidOperation

from adapters.base import AdapterHealth, AdapterResult, AdapterStatus, Money
from adapters.runner import CliFailure, JsonCliRunner
from adapters.supplier.port import (
    SourcingQuery,
    SupplierProductCandidate,
    SupplierSkuSnapshot,
)
from adapters.supplier.shopkeeper_1688 import _failure_result, _payload_failure


def _first_present(item, *keys):
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _money(value):
    if isinstance(value, dict):
        value = _first_present(value, "amount", "value", "price")
    if isinstance(value, str):
        value = value.strip().removeprefix("¥").removeprefix("￥").strip()
    try:
        return Money(amount=str(Decimal(str(value))))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _stock(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _product_rows(payload):
    data = payload.get("data") or {}
    for value in (
        data.get("similar_products"),
        data.get("products"),
        data.get("items"),
        payload.get("items"),
    ):
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


class ProductFind1688Adapter:
    """Read-only adapter for a separately installed official 1688-product-find CLI.

    AutoFish consumes only the documented JSON stdout contract. The external skill
    remains outside the application image and does not receive database access.
    """

    source = "1688-product-find-cli"
    source_version = "external"

    def __init__(self, command=None, *, access_key=None, timeout_seconds=60):
        self.runner = JsonCliRunner(
            command,
            timeout_seconds=timeout_seconds,
            environment={"ALI_1688_AK": access_key},
        )

    async def health(self):
        capabilities = [
            "text_search",
            "structured_search_results",
        ]
        if not self.runner.available:
            return AdapterResult[AdapterHealth](
                status=AdapterStatus.AUTH_REQUIRED,
                data=AdapterHealth(
                    available=False,
                    authenticated=False,
                    read_only=True,
                    details={"capabilities": capabilities},
                ),
                source=self.source,
                source_version=self.source_version,
                error_code="COMMAND_NOT_CONFIGURED",
                safe_message="1688 Product Find 只读 CLI 尚未配置",
            )
        try:
            payload = await self.runner.run("configure", "--status")
        except CliFailure as exc:
            result = _failure_result(self.source, self.source_version, exc)
            result.data = AdapterHealth(available=True, authenticated=False, read_only=True)
            return result
        if not payload.get("success"):
            result = _failure_result(
                self.source,
                self.source_version,
                _payload_failure(payload, "1688 Product Find 授权检查未成功"),
            )
            result.data = AdapterHealth(
                available=True,
                authenticated=False,
                read_only=True,
                details={"capabilities": capabilities},
            )
            return result
        return AdapterResult[AdapterHealth](
            status=AdapterStatus.OK,
            data=AdapterHealth(
                available=True,
                authenticated=True,
                read_only=True,
                details={"capabilities": capabilities},
            ),
            source=self.source,
            source_version=self.source_version,
        )

    def _candidates(self, payload, limit):
        candidates = []
        for item in _product_rows(payload)[:limit]:
            product_id = _first_present(
                item,
                "product_id",
                "productId",
                "offer_id",
                "offerId",
                "id",
            )
            if product_id is None:
                continue
            price = _money(_first_present(item, "price", "unit_price", "unitPrice"))
            shipping = _money(
                _first_present(item, "shipping", "shipping_cost", "shippingCost")
            )
            sku_id = _first_present(item, "sku_id", "skuId")
            sku_title = _first_present(item, "sku_title", "skuTitle", "spec")
            stock = _stock(_first_present(item, "stock_amount", "stockAmount", "stock"))
            skus = []
            if sku_id is not None:
                spec = sku_title if isinstance(sku_title, dict) else {}
                if sku_title and not isinstance(sku_title, dict):
                    spec = {"title": str(sku_title)}
                skus.append(
                    SupplierSkuSnapshot(
                        external_sku_id=str(sku_id),
                        spec=spec,
                        price=price,
                        shipping=shipping,
                        stock=stock,
                        raw_payload=item,
                    )
                )
            stats = {
                key: item[key]
                for key in (
                    "yx_index",
                    "quantity_begin",
                    "unit",
                    "sold_count",
                    "stock_amount",
                    "promotion_tags",
                    "service_infos",
                    "selling_points",
                    "isBrandOffer",
                    "isBrandAuth",
                )
                if item.get(key) not in (None, "", [], {})
            }
            candidates.append(
                SupplierProductCandidate(
                    external_product_id=str(product_id),
                    title=str(_first_present(item, "title", "subject") or "未知商品"),
                    price=price,
                    url=_first_present(item, "detail_url", "detailUrl", "url", "promotionURL"),
                    image_url=_first_present(item, "image_url", "imageUrl", "image"),
                    category=_first_present(
                        item,
                        "category",
                        "category_name",
                        "categoryName",
                        "category_list_name",
                    ),
                    external_supplier_id=(
                        str(value)
                        if (
                            value := _first_present(
                                item,
                                "supplier_id",
                                "supplierId",
                                "seller_id",
                                "sellerId",
                                "member_id",
                                "memberId",
                            )
                        )
                        else None
                    ),
                    supplier_name=_first_present(
                        item,
                        "supplier",
                        "supplier_name",
                        "supplierName",
                        "seller_name",
                    ),
                    skus=skus,
                    stats=stats,
                    raw_payload=item,
                )
            )
        return candidates

    async def _search(self, arguments, limit, default_message):
        try:
            payload = await self.runner.run(*arguments)
        except CliFailure as exc:
            return _failure_result(self.source, self.source_version, exc)
        if not payload.get("success"):
            return _failure_result(
                self.source,
                self.source_version,
                _payload_failure(payload, default_message),
            )
        return AdapterResult(
            status=AdapterStatus.OK,
            data=self._candidates(payload, limit),
            source=self.source,
            source_version=self.source_version,
        )

    async def search_products(self, query: SourcingQuery):
        return await self._search(
            ["text_search", "--query", query.query, "--limit", str(query.limit)],
            query.limit,
            "1688 Product Find 搜索未成功",
        )

    async def search_image(self, source_url, limit=20):
        return self._unsupported("安全运行时未开放图片找同款")

    async def search_link(self, source_url, limit=20):
        return self._unsupported("安全运行时未开放链接找同款")

    async def get_trends(self, _query):
        return self._unsupported("趋势洞察由 1688 Shopkeeper Adapter 提供")

    async def get_opportunities(self, _category=None):
        return self._unsupported("即时商机由 1688 Shopkeeper Adapter 提供")

    def _unsupported(self, message):
        return AdapterResult(
            status=AdapterStatus.UNSUPPORTED,
            source=self.source,
            source_version=self.source_version,
            error_code="CAPABILITY_UNAVAILABLE",
            safe_message=message,
        )

    async def get_product(self, _external_product_id):
        return self._unsupported("该 CLI 不提供按商品 ID 读取商详的稳定契约")

    async def get_skus(self, _external_product_id):
        return self._unsupported("SKU 仅在搜索结果实际返回时保存，不额外推断")

    async def get_price(self, _external_sku_id):
        return self._unsupported("该 CLI 不提供独立实时价格查询")

    async def get_stock(self, _external_sku_id):
        return self._unsupported("该 CLI 不提供独立实时库存查询")

    async def get_supplier(self, _external_supplier_id):
        return self._unsupported("该 CLI 不提供独立供应商详情查询")

    def __getattr__(self, _name):
        raise NotImplementedError(
            "1688 Product Find write operations are intentionally unavailable"
        )
