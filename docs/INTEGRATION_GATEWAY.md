# 授权 JSON Gateway 契约

AutoFish 推荐把官方平台 SDK/API、企业内部集成服务或合规数据服务放在独立 Gateway 中。核心应用只通过 HTTPS + Bearer Token 读取标准 JSON；Gateway 不持有 AutoFish 数据库凭证，AutoFish 也不保存平台 Cookie。

## 通用约定

- Base URL 分别由 `AUTOFISH_SUPPLIER_GATEWAY_URL`、`AUTOFISH_XIANYU_GATEWAY_URL` 配置。
- 请求头：`Authorization: Bearer <token>`、`Accept: application/json`。
- 超时默认 30 秒；响应体上限 2 MiB；非 JSON、重定向到未知主机、超时和非 2xx 都会被归类为安全错误。
- 所有金额均为字符串十进制，币种默认 `CNY`；时间为带时区 ISO 8601。
- Gateway 必须返回稳定的外部 ID，不能用列表序号或标题充当 ID。
- V1 读取契约与 V2 受控写入契约分开授权；未声明写能力、健康检查仍为 `read_only=true` 或 AutoFish 写总开关关闭时，任何写路径都不可执行。

## 健康检查

供应商与闲鱼 Gateway 均实现：

```http
GET /health
```

```json
{
  "version": "vendor-gateway-1.2.0",
  "data": {
    "available": true,
    "authenticated": true,
    "read_only": true,
    "details": {
      "products": true,
      "conversations": true,
      "orders": false
    }
  }
}
```

`authenticated=false` 表示凭证缺失/过期；能力不支持时在 `details` 中明确设为 `false`，不得返回伪造空结果冒充“支持但无数据”。

## 供应商 Gateway

### 搜索

```http
POST /products/search
Content-Type: application/json

{"query":"桌面收纳","limit":20,"filters":{}}
```

```json
{
  "version": "vendor-gateway-1.2.0",
  "items": [{
    "external_product_id": "offer-123",
    "title": "铝合金桌面支架",
    "price": {"amount": "29.90", "currency": "CNY"},
    "url": "https://authorized.example/products/offer-123",
    "image_url": "https://authorized.example/images/offer-123.jpg",
    "category": "被动配件",
    "external_supplier_id": "factory-9",
    "supplier_name": "示例工厂",
    "stats": {"recent_sales": 120},
    "raw_payload": {}
  }]
}
```

### 详情、SKU、价格、库存、供应商

```text
GET /products/{external_product_id}
GET /products/{external_product_id}/skus
GET /skus/{external_sku_id}/price
GET /skus/{external_sku_id}/stock
GET /suppliers/{external_supplier_id}
```

SKU 示例：

```json
{
  "items": [{
    "external_sku_id": "sku-123-silver",
    "spec": {"颜色": "银色"},
    "price": {"amount": "29.90", "currency": "CNY"},
    "shipping": {"amount": "6.00", "currency": "CNY"},
    "stock": 100,
    "raw_payload": {}
  }]
}
```

若官方能力不能可靠提供结构化 SKU、实时价格或库存，Gateway 应返回 HTTP 501，或健康能力设为 `false`；AutoFish 不从 Markdown 猜字段。

## 闲鱼 Gateway

```text
GET /account
GET /products
GET /products/{external_product_id}
GET /conversations
GET /conversations/{external_conversation_id}/messages
GET /orders
```

经平台授权且完成小流量验收的 Gateway 可额外实现：

```text
POST /products
PATCH /products/{external_product_id}
DELETE /products/{external_product_id}
POST /conversations/{external_conversation_id}/messages
POST /orders/{external_order_id}/ship
```

每次写入必须接收 `Idempotency-Key`，返回稳定的外部对象 ID 和明确状态。超时、空响应或无法确认结果时必须返回“结果不确定”，AutoFish 会停止自动重试并创建人工任务。物流单号使用受信 Gateway 内的引用解析，核心日志不得保存明文单号、电话或地址。

账号、商品、会话、消息和订单的最小示例：

```json
{
  "external_account_id": "account-1",
  "nickname": "店铺昵称",
  "status": "ACTIVE",
  "raw_payload": {}
}
```

```json
{
  "items": [{
    "external_product_id": "item-1",
    "title": "闲置桌面支架",
    "price": {"amount": "89.00", "currency": "CNY"},
    "status": "ON_SALE",
    "images": [],
    "raw_payload": {}
  }]
}
```

```json
{
  "items": [{
    "external_conversation_id": "chat-1",
    "external_customer_id": "buyer-1",
    "customer_name": "买家",
    "status": "OPEN",
    "last_message_at": "2026-08-15T12:00:00+08:00",
    "manual_mode": false,
    "raw_payload": {}
  }]
}
```

```json
{
  "items": [{
    "external_message_id": "msg-1",
    "sender_type": "CUSTOMER",
    "content_type": "TEXT",
    "content": "还有货吗？",
    "sent_at": "2026-08-15T12:00:00+08:00",
    "raw_payload": {}
  }]
}
```

```json
{
  "items": [{
    "external_order_id": "order-1",
    "status": "paid",
    "revenue": {"amount": "89.00", "currency": "CNY"},
    "expected_profit": {"amount": "30.00", "currency": "CNY"},
    "actual_profit": null,
    "paid_at": "2026-08-15T12:10:00+08:00",
    "raw_payload": {}
  }]
}
```

## Canonical Feed 备用入口

当官方 Gateway 尚未完成时，操作员可在“选品中心 → 标准 JSON”导入经授权取得的数据。入口执行 Pydantic 字段校验、快照脱敏、哈希去重、排除类目和可选八维评分；它不是绕过平台授权的数据采集器。

## 上线前契约验收

1. 逐字段对照至少 20 个供应商品、一个闲鱼账号、10 个闲鱼商品和 10 个会话。
2. 验证空列表、401、403、429、500、超时、非 JSON、未知字段与重复响应。
3. 扫描 API/容器日志，确保 Bearer Token、Cookie、电话和地址未出现。
4. 重放同一响应，确认 `external_snapshots` 不重复且业务表幂等更新。
5. 首轮保持 `AUTOFISH_ADAPTER_WRITE_ENABLED=false`；分别验证写总开关、分项开关、`REVIEW` 确认、限额、最小间隔、熔断和幂等重放后，才允许单动作小流量启用。
