# 参考仓库分析

分析日期：2026-08-15。四个仓库位于正式项目之外的 `../references/`，仅做只读研究；AutoFish 不直接 import、复制或修改其中代码。

## 结论摘要

| 仓库 | 当前审计提交 | 许可证 | 在 AutoFish 中的定位 | 结论 |
|---|---|---|---|---|
| `fancyboi999/goofish-cli` | `f586ee2` | Apache-2.0 | 独立 Xianyu CLI/MCP Adapter 候选 | 可通过子进程/MCP 调用；默认关闭写操作 |
| `next-1688/1688-shopkeeper` | `99537bc` | 未提供许可证 | 独立 Supplier/Sourcing Adapter 候选 | 仅调用，不复制；启用前需确认授权与 API 条款 |
| `zhinianboke/xianyu-auto-reply` | `c5d969f` | AGPL-3.0，README 另称禁止商业用途 | 仅研究多服务架构 | 不链接、不复制、不部署；验证码绕过相关设计全部排除 |
| `shaxiu/XianyuAutoAgent` | `540bbc2` | GPL-3.0 | 仅研究 Agent 分工和上下文 | 不链接、不复制；自行实现结构化 Agent 与硬性定价校验 |

许可证判断只用于工程隔离，不构成法律意见。

## 1. goofish-cli

### 架构

Python 3.11+ 包，采用 Single Registry：命令通过 `@command` 注册，同一注册表生成 Typer CLI 与 MCP 工具。核心包含 Cookie/二维码登录、MTOP 请求、WebSocket IM、浏览器只读查询、输出渲染、令牌桶和风险熔断。

已观察到的命令面包括：

- 认证：`auth login/status/reset-guard`；
- 商品：`item get/list/view/publish/delete`；
- 图片、类目和地址：`media upload`、`category recommend`、`location default`；
- 消息：`message list-chats/history/watch/send`；
- 搜索：`search items`。

### 可复用思想

- 命令注册信息同时生成 CLI/MCP 的单一契约；
- JSON/JSONL 作为进程间结构化边界；
- 所有写操作在底层统一限流与熔断，Agent 无权绕过；
- IM 长连与普通命令分离，事件区分 message/read/new_msg；
- 登录态健康检查、脱敏输出和明确错误分类。

### 可直接作为外部 Adapter 调用的能力

Phase 4 可优先接：账号状态、商品查询/列表、会话列表、历史消息、消息监听和消息发送。发布与删除虽然存在，但应延后到人工审核稳定后，并在 AutoFish 侧再加全局/分项 Kill Switch、每日上限和幂等键。

### 不应复制

不复制 MTOP 签名、Cookie 刷新、WebSocket 编解码、浏览器 Cookie、二维码登录或任何静态协议文件。AutoFish 只解析公开 CLI/MCP 输出。

### 风险

- 项目状态为 Alpha，平台私有接口和输出结构可能随时变化；
- README 声称真实账号验证并不等于官方授权；
- `item publish/delete` 与 `message send` 是不可逆写操作；
- 项目合规文档限定自有账号并反对黑灰产批量铺货、商业 SaaS 和数据贩卖；
- 缺少任务书要求的订单与发货命令，相关 Adapter 方法必须 `NotImplementedError`。

## 2. 1688-shopkeeper

### 架构

轻量 Python CLI，启动时扫描 `scripts/capabilities/*/cmd.py` 注册命令。使用 AK/SK 对 `https://ainext.1688.com` 请求签名，统一返回：

```json
{"success": true, "markdown": "...", "data": {}}
```

能力包括 `search`、`prod_detail`、`shops`、`publish`、`opportunities`、`trend`、`shop_daily`、`configure`、`check`。网络层具有三次指数退避和 400/401/429/500 错误分类。

### 可复用思想

- 搜索结果与详情快照使用 `data_id`，支持先保存再按需读取；
- Agent 只读取 `data` 做判断，`markdown` 只作为展示；
- 能力目录自动发现；
- 详情逐商品展开，避免一次将大段商详放入模型上下文；
- 数据契约明确传递 `product_id`、`data_id`、`shop_code`。

### 可直接作为外部 Adapter 调用的能力

Phase 3 仅接 `check`、`search`、`prod_detail`。搜索能提供价格、近 30 天销量/代发销量、好评率、复购率、揽收率、下游铺货数和类目；详情是 Markdown 型 `all_info`，需在 Adapter 内解析并保留原始快照哈希。

`publish` 面向抖店/拼多多/小红书/淘宝，不用于闲鱼。仓库未提供稳定的实时库存、采购下单、供应商订单、物流或取消订单接口；这些方法必须返回 `UNSUPPORTED` 或创建 `PurchaseTask`。

### 不应复制

仓库没有 LICENSE/COPYING/NOTICE，默认不存在复制、修改或再分发授权。不得复制 AK 签名、HTTP 客户端、CLI 代码或文档内容到 AutoFish。

### 风险

- 无许可证是最高级别整合风险；
- 服务依赖 `ainext.1688.com`、AK 权限和未承诺稳定的返回结构；
- 详情关键字段封装在 Markdown，解析必须容错并保留原文；
- 搜索指标适合选品参考，不代表实时库存或可履约承诺；
- 启用前需用户自行确认 1688 服务/API 使用条款和账号权限。

## 3. xianyu-auto-reply

### 架构

大型多服务系统：React/Vite 前端、FastAPI `backend-web`、独立 `websocket`、独立 `scheduler`、MySQL、Redis 与共享 `common`。三个后端服务均有健康检查；Scheduler 拆分订单拉取、Cookie 更新、发货、评价、列表监控等任务；WebSocket 拆分连接、资源、消息、自动回复和配送规则。

### 可复用思想

- API、实时连接、计划任务分进程，故障域清晰；
- 数据库/Redis 就绪后再启动上层服务；
- 长连接的 connection/resource/task manager 分工；
- 定时任务拥有独立日志和状态；
- 前端通过专门的系统控制、风险、订单和发布页面操作。

### 不应复制

项目为 AGPL-3.0，README 另有“禁止商业用途”声明。AutoFish 不复制模型、服务、路由、前端组件、协议、发布或消息实现，也不链接/嵌入该项目。

尤其禁止借鉴或运行其 `captcha`、`slider_stealth`、轨迹、远程 solver、真实鼠标、反检测/验证码处理代码；这些与 AutoFish 的强制合规边界冲突。

### 技术风险

- Compose 示例存在默认数据库密码、`CORS_ORIGINS=*` 和默认管理员；
- 运行时自动建表/补字段而不是完整 migration；
- 数据关系主要靠代码维护且不使用外键；
- 业务错误统一 HTTP 200 会削弱监控与重试判断；
- 大量远程内容、广告、激活、返佣和验证码服务不属于 AutoFish；
- 服务面过大，不适合 V1 直接照搬。

AutoFish 只采用“API/worker/实时 Adapter 分离、健康检查、任务日志”的架构思想，并以 PostgreSQL 外键、Alembic、标准 HTTP 状态码和最小服务面重新实现。

## 4. XianyuAutoAgent

### 架构

单进程 Python WebSocket 机器人：`XianyuLive` 处理连接/心跳/Token，`ChatContextManager` 用 SQLite 保存消息和议价次数，`XianyuReplyBot` 将消息路由到 price/tech/default/classify Agent，提示词从文件加载。

### 可复用思想

- 意图路由与领域 Agent 分开；
- 会话历史按 chat_id 限长；
- 每个会话可切换人工模式；
- 议价轮次是独立状态；
- Prompt 可版本化并热加载。

### 不应复制

项目为 GPL-3.0，不复制代码、Prompt、WebSocket/API、Cookie、签名、设备 ID 或解密实现。其直接使用私有协议、在环境变量放 Cookie、自动发消息的做法不进入核心。

### 技术风险

- 人工接管状态仅在内存，重启即丢失；
- SQLite 与单进程设计难以支持审计和并发；
- 议价策略主要依靠 Prompt/temperature，没有代码级最低价门禁；
- 规则路由只覆盖少数 intent，缺少置信度和 `requires_human` Schema；
- LLM 调用、上下文、发送动作耦合，无法独立回放；
- Cookie、Token、平台协议变化会直接导致主进程失败。

AutoFish 将自行实现持久化会话、结构化 Router、AI decision 审计、PricingEngine 硬门禁与默认人工发送。

## 5. AutoFish 自行实现的功能

- 统一领域模型：SupplierProduct 与内部 Product 分离；
- PostgreSQL 资金/订单/审计模型与 Alembic migration；
- ProductScore、成本、最低价、利润和价格历史；
- Adapter 输入输出 Schema、超时、错误映射、速率限制和健康状态；
- AutomationJob 幂等、有限重试、MANUAL_REQUIRED；
- AI Router/Customer/Pricing/Product/Risk/Order/AfterSales 结构化决策；
- 全局与发布/客服/采购/调价分项 Kill Switch；
- 人工任务、审计、风险与经营后台；
- 外部输出的原始快照哈希、来源版本和调用追踪。

## 6. 推荐整合架构

```text
Next.js UI
   │
FastAPI Core ── PostgreSQL
   │             │
   ├── Redis ── Celery Worker
   │
   ├── SupplierAdapter ── subprocess ── 1688-shopkeeper (Phase 3)
   │
   └── XianyuAdapter ── subprocess/MCP ── goofish-cli (Phase 4)
                       read first; writes require manual approval
```

外部仓库不与 AutoFish 共用 Python 进程、依赖环境或数据库。Adapter 只接受最小参数，通过 JSON 返回标准 Schema；stderr、退出码、超时和来源版本写入调用日志。

## 7. Phase 0–10 实施计划

| Phase | 交付物 | 启用真实平台 |
|---|---|---|
| 0 | Compose、PostgreSQL、Redis、FastAPI、Next.js、worker、migration、登录、日志 | 否 |
| 1 | Supplier/SupplierProduct/SKU/Product、历史记录、人工导入 | 否 |
| 2 | PricingEngine、ProductScore、Dashboard、审计、Kill Switch | 否 |
| 3 | Shopkeeper1688Adapter 的 check/search/detail，只读快照 | 1688 只读 |
| 4 | GoofishCliAdapter 的账号/商品/会话/消息能力，默认只读 | 闲鱼只读优先 |
| 5 | Router/Customer/Pricing 建议回复，人工点击发送 | 人工发送 |
| 6 | ProductAgent、发布队列、人工审核、幂等发布 | 有限写入 |
| 7 | 订单状态机和成本归集 | 有限写入 |
| 8 | PurchaseTask 与半自动采购 | 人工确认 |
| 9 | 物流同步与超时人工任务 | 有限写入 |
| 10 | 14 天验收后逐项开启无人值守 | 分项启用 |

## 8. 采用/拒绝清单

采用：进程隔离、统一 JSON 契约、命令注册、写操作底层护栏、数据快照、渐进读取、连接/任务分进程、会话限长、领域 Agent 分工。

拒绝：复制协议实现、运行验证码/滑块 solver、默认密码、通配 CORS、无 migration 补表、HTTP 200 包裹全部错误、Prompt 控制资金底线、内存人工接管、外部仓库直接访问核心数据库。

