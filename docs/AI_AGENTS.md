# AI Agents 设计

## 1. 原则

AI 负责理解、生成与建议，不拥有资金、状态机或平台写权限。所有输出使用版本化 Pydantic Schema；输入只包含经授权的最小事实。不存在数据时返回“不确定 + 人工任务”，禁止补全想象。

## 2. Agent 分工

```text
Router → Customer / Pricing / Product / Sourcing / Risk / Order / AfterSales
```

- Router：意图、置信度和人工接管判定。
- Customer：依据商品/订单/物流事实生成答复建议。
- Pricing：提出议价候选，PricingEngine 复核最低价。
- Product：生成标题、描述、规格、兼容说明、购买须知和 FAQ。
- Sourcing：根据供应商事实与排除类目生成候选理由。
- Risk：解释规则命中，不直接恢复自动化。
- Order：解释状态与建议下一动作，不跳状态。
- AfterSales：全部默认人工，只整理事实和建议。

## 3. Router Schema

```json
{
  "schema_version": "1.0",
  "intent": "NEGOTIATION",
  "confidence": 0.96,
  "requires_human": false,
  "reason_code": "PRICE_OFFER_DETECTED"
}
```

Intent 至少覆盖：`PRODUCT_QUERY/COMPATIBILITY/PRICE_QUERY/NEGOTIATION/STOCK/SHIPPING/LOGISTICS/ORDER_STATUS/USAGE/AFTERSALES/REFUND/COMPLAINT/UNKNOWN`。

`REFUND/COMPLAINT/AFTERSALES`、低置信度、无法确认兼容性、法律/赔偿、异常订单必须人工。

## 4. 事实约束

回复输入包含字段级来源：

```json
{
  "facts": [
    {"field": "stock", "value": 128, "source": "supplier_skus:42", "checked_at": "..."},
    {"field": "minimum_sale_price", "value": "84.00", "source": "pricing_engine:v1"}
  ]
}
```

模型不得读取 Cookie、供应商账号凭证、买家完整地址/电话或不相关会话。

## 5. 议价门禁

AI 输出：

```json
{"suggested_price":"99.00","reply":"...","confidence":0.91}
```

代码依次验证：金额格式、商品/规则版本、当前库存、价格时效、轮次上限、`suggested_price >= minimum_sale_price`。任何失败都不发送并创建人工任务。浏览器自动回复能力即使已部署，也由独立的 `AUTOFISH_XIANYU_BROWSER_CUSTOMER_SERVICE_ENABLED` 硬开关保持默认关闭；只有 GLOBAL/CUSTOMER_SERVICE 同时启用、CUSTOMER_SERVICE 为 `AUTOMATIC`、最新消息及建议指纹仍一致且建议通过事实门禁时，才允许建立发送任务。

自动客服按“只读采集 → 同源幂等回复任务 → 事实门禁建议 → 浏览器发送任务 → 证据绑定回执”闭环运行。退款、投诉、售后、未知意图、人工接管会话或事实不足只创建人工任务。会话、消息、回复文本或建议指纹任一变化都会阻断发送；进入提交阶段后结果不明确固定转人工，禁止自动重试。

## 6. ai_decisions

每次关键决策记录 `agent`、`schema_version`、`input_context_hash`、结构化 decision、confidence、reason_summary、model/provider、prompt_version、latency、token usage、关联会话/商品/订单和 timestamp。

只保存必要上下文哈希与脱敏摘要，支持离线回放、Prompt 对比和错误分析。

## 7. Prompt 与模型变更

- Prompt 版本化，不允许运行时无审计覆盖。
- 模型切换先跑固定评测集：事实准确、人工分流、最低价、防虚构、敏感信息。
- 温度不能替代业务规则。
- 低置信度阈值按 Agent 配置并存入决策记录。
