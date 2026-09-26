from adapters.base import AdapterHealth, AdapterResult, AdapterStatus
from adapters.supplier.product_find_1688 import ProductFind1688Adapter
from adapters.supplier.shopkeeper_1688 import Shopkeeper1688Adapter


class Hybrid1688Adapter:
    """Compose the two audited, read-only 1688 runtimes.

    Product Find supplies structured SKU/stock search results. Shopkeeper supplies
    signed account health, product-detail evidence, trends and opportunities. The
    write surface remains intentionally unavailable.
    """

    source = "1688-hybrid-cli"
    source_version = "1.0"

    def __init__(
        self,
        shopkeeper_command=None,
        product_find_command=None,
        *,
        access_key=None,
        timeout_seconds=60,
    ):
        self.shopkeeper = Shopkeeper1688Adapter(
            shopkeeper_command,
            access_key=access_key,
            timeout_seconds=timeout_seconds,
        )
        self.product_find = ProductFind1688Adapter(
            product_find_command,
            access_key=access_key,
            timeout_seconds=timeout_seconds,
        )

    async def health(self):
        # Shopkeeper performs a signed read. Both runtimes use the same mounted AK,
        # so Product Find only needs an executable command here; its own failures
        # remain visible on the actual search call without making every UI health
        # refresh execute a slow product search.
        signed = await self.shopkeeper.health()
        product_find_available = self.product_find.runner.available
        available = bool(signed.data and signed.data.available and product_find_available)
        authenticated = bool(signed.data and signed.data.authenticated)
        status = signed.status if not authenticated else (
            AdapterStatus.OK if product_find_available else AdapterStatus.AUTH_REQUIRED
        )
        return AdapterResult[AdapterHealth](
            status=status,
            data=AdapterHealth(
                available=available,
                authenticated=authenticated and product_find_available,
                read_only=True,
                details={
                    "capabilities": [
                        "structured_search_results",
                        "raw_product_detail",
                        "trend",
                        "opportunities",
                    ],
                    "shopkeeper_status": signed.status.value,
                    "product_find_configured": product_find_available,
                },
            ),
            source=self.source,
            source_version=self.source_version,
            retryable=signed.retryable,
            error_code=(
                signed.error_code
                if not authenticated
                else (None if product_find_available else "PRODUCT_FIND_NOT_CONFIGURED")
            ),
            safe_message=(
                signed.safe_message
                if not authenticated
                else (None if product_find_available else "1688 Product Find 只读 CLI 尚未配置")
            ),
        )

    async def search_products(self, query):
        return await self.product_find.search_products(query)

    async def search_image(self, source_url, limit=20):
        return await self.product_find.search_image(source_url, limit)

    async def search_link(self, source_url, limit=20):
        return await self.product_find.search_link(source_url, limit)

    async def get_product(self, external_product_id):
        return await self.shopkeeper.get_product(external_product_id)

    async def get_skus(self, external_product_id):
        return await self.product_find.get_skus(external_product_id)

    async def get_price(self, external_sku_id):
        return await self.product_find.get_price(external_sku_id)

    async def get_stock(self, external_sku_id):
        return await self.product_find.get_stock(external_sku_id)

    async def get_supplier(self, external_supplier_id):
        return await self.product_find.get_supplier(external_supplier_id)

    async def get_trends(self, query):
        return await self.shopkeeper.get_trends(query)

    async def get_opportunities(self, category=None):
        return await self.shopkeeper.get_opportunities(category)

    def __getattr__(self, _name):
        raise NotImplementedError("1688 hybrid adapter write operations are unavailable")
