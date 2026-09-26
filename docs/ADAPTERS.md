# Adapter 设计

## 1. 通用原则

Adapter 是防腐层：将外部 CLI/MCP/API 的不稳定结构映射为 AutoFish Schema。核心服务不得 import `goofish-cli`、`1688-shopkeeper` 或任何平台协议代码。

生产优先级：经授权的 `json_gateway` > 隔离的兼容 CLI > Canonical Feed 人工导入。Gateway 契约见 `INTEGRATION_GATEWAY.md`。

每次调用都必须携带 `correlation_id`、超时、来源版本和最小参数；结果分为：

```text
OK
NOT_FOUND
UNSUPPORTED
AUTH_REQUIRED
RATE_LIMITED
MANUAL_REQUIRED
TRANSIENT_ERROR
PERMANENT_ERROR
```

`MANUAL_REQUIRED` 适用于验证码、人脸、二次确认、风险提示和无法解释的平台响应。

## 2. 标准返回

```python
class AdapterResult[T]:
    status: AdapterStatus
    data: T | None
    source: str
    source_version: str
    raw_snapshot_hash: str | None
    retryable: bool
    error_code: str | None
    safe_message: str | None
```

原始输出可加密落到 Adapter 快照存储，但日志只能记录 hash 和脱敏摘要。

## 3. SupplierAdapter

```python
class SupplierAdapter(Protocol):
    async def health(self) -> AdapterResult[AdapterHealth]: ...
    async def search_products(self, query: SourcingQuery) -> AdapterResult[list[SupplierProductCandidate]]: ...
    async def get_product(self, external_product_id: str) -> AdapterResult[SupplierProductDetail]: ...
    async def get_skus(self, external_product_id: str) -> AdapterResult[list[SupplierSkuSnapshot]]: ...
    async def get_price(self, external_sku_id: str) -> AdapterResult[Money]: ...
    async def get_stock(self, external_sku_id: str) -> AdapterResult[StockSnapshot]: ...
    async def get_supplier(self, external_supplier_id: str) -> AdapterResult[SupplierSnapshot]: ...
    async def create_order(self, request: SupplierOrderRequest) -> AdapterResult[SupplierOrderSnapshot]: ...
    async def get_order(self, external_order_id: str) -> AdapterResult[SupplierOrderSnapshot]: ...
    async def get_tracking(self, external_order_id: str) -> AdapterResult[TrackingSnapshot]: ...
    async def cancel_order(self, external_order_id: str) -> AdapterResult[SupplierOrderSnapshot]: ...
```

Phase 3 已提供 `JsonGatewaySupplierAdapter`、`Shopkeeper1688Adapter` 和候选态 `ProductFind1688Adapter`。Gateway 实现健康、搜索、商品、SKU、价格、库存和供应商只读契约；Shopkeeper 实现 `health/search_products/get_product/get_skus`；Product Find 仅消费官方社区技能公开的结构化搜索 JSON，可保存搜索结果中的 SKU、库存与供应商字段，但不会补造缺失 ID。实时价格/库存若无法从稳定契约可靠确认则返回 `UNSUPPORTED`；采购相关不调用铺货命令或网页 RPA。

调用策略：单独虚拟环境或容器，通过 `python cli.py ...` 子进程读取 stdout JSON；固定 30 秒超时；拒绝非 JSON、过大输出和未知字段；AK 由 secret mount 注入。

`product_find_cli` 只作为可选运行时，未完成包体哈希、许可证、依赖、网络目标和使用埋点审计前不得在生产启用。评估记录见 [1688 社区技能评估](./1688_COMMUNITY_SKILLS.md)。

## 4. XianyuAdapter

```python
class XianyuAdapter(Protocol):
    async def health(self) -> AdapterResult[AdapterHealth]: ...
    async def get_account(self) -> AdapterResult[XianyuAccountSnapshot]: ...
    async def get_product(self, external_product_id: str) -> AdapterResult[XianyuProductSnapshot]: ...
    async def list_products(self) -> AdapterResult[list[XianyuProductSnapshot]]: ...
    async def publish_product(self, request: PublishRequest) -> AdapterResult[XianyuProductSnapshot]: ...
    async def update_product(self, request: UpdateProductRequest) -> AdapterResult[XianyuProductSnapshot]: ...
    async def pause_product(self, external_product_id: str) -> AdapterResult[None]: ...
    async def delete_product(self, external_product_id: str) -> AdapterResult[None]: ...
    async def watch_messages(self) -> AsyncIterator[MessageEvent]: ...
    async def list_chats(self) -> AdapterResult[list[ChatSnapshot]]: ...
    async def send_message(self, request: SendMessageRequest) -> AdapterResult[MessageSnapshot]: ...
    async def get_orders(self) -> AdapterResult[list[OrderSnapshot]]: ...
    async def ship_order(self, request: ShipOrderRequest) -> AdapterResult[OrderSnapshot]: ...
```

`0.4.0` 已提供 `JsonGatewayXianyuAdapter`、`GoofishCliAdapter` 与 `TopXianyuAdapter`。Gateway 可实现账号、商品、会话、消息、订单和经授权的写契约；CLI 仅执行其公开且可确认结果的能力；TOP 适配器只调用获批业务的官方方法，通用私信和无授权订单列表明确返回 `UNSUPPORTED`。发布、改价、下架、回复和物流回填均须经过全局/分项开关、`REVIEW/AUTOMATIC` 模式、幂等键、限额、最小间隔、熔断和审计；采购、支付、退款、投诉及平台介入始终转人工。

## 5. 安全执行

- 子进程使用参数数组，不经过 shell 字符串拼接。
- 固定可执行文件路径和允许命令白名单。
- stdout 限制大小，stderr 脱敏；超时后终止整个进程组。
- Cookie/AK 只在 secret 文件或专用凭证目录，核心 DB 保存引用。
- Adapter 运行用户不拥有 PostgreSQL 凭证。
- 写命令前后保存业务截图/结果（工具能够提供时）和 AuditLog。

## 6. Mock 与契约测试

Phase 0–2 只使用 `MockSupplierAdapter` 与 `ManualXianyuAdapter`。每个真实 Adapter 必须通过相同 contract test：正常返回、超时、鉴权失败、限流、未知结构、风险提示、幂等重试和敏感数据日志扫描。
