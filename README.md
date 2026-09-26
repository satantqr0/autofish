# AutoFish

> 代码与实际运行能力请分开评估：真实行情已完成辅助采样入库；Chrome 桥接 0.6.0 的无人值守采集、两次真实定时运行及长期稳定性仍待验收。详见 [行情采集记录](docs/MARKET_PRICE_MONITOR_20260921.md)。源码仓库不包含生产凭据、数据库、运行日志、商品图片或第三方 Adapter 运行环境。

`docs/design` 与 `docs/ui-concepts` 中的图片仅为设计概念图，不代表真实经营数据或收益。文档中的历史内网地址及本机路径需按自己的环境替换；`.env.example` 仅提供占位配置。

AutoFish 是客户自有设备、自有账号、自有数据的单租户闲鱼运营中台。当前 `0.9.0` 已完成商品、定价、评分、1688 选品事实库、八大 ClawHub 能力台、闲鱼事实库、大模型加密设置入口、自主上架流水线、模型故障回退、运行就绪检查、无需 Codex 参与正常链路的本地浏览器发布执行器，以及商业交付准备度与付费试点证据控制台。生产默认保持硬开关保护；只有来源、文案、精品图、策略、限额与本机执行器全部通过门禁，商品才会进入外部发布。

## 已实现

- FastAPI + PostgreSQL + Redis + Next.js 的 Docker Compose 环境
- 管理员登录、登录限流、短期 JWT、修改密码后全设备旧会话失效
- Supplier、SupplierProduct、SupplierSKU、Product、ProductSKU、来源映射、价格/库存历史数据库模型与 Alembic migration
- 商品新增、批量导入、搜索、筛选、排序和详情查看
- Decimal 精度的成本、最低售价、建议售价与利润率计算
- 八维 ProductScore 评分与强制排除品类规则
- 外部同步批次、脱敏原始快照、来源版本、幂等去重和 Adapter 调用记录
- 1688 授权 Gateway / 外部 CLI 只读 Adapter、Canonical Feed 导入、候选评分和安全导入
- 1688 候选人工核验补全：来源/时效/图片域名/SKU/成本/运费/库存门禁、幂等证据快照与审计
- 关键词/图片/链接发现契约、候选对比、1688 趋势与即时商机读取
- 供应商发现与主供/备选匹配、S/A/B/C 评估、商品诊断、闲鱼待审草稿流水线
- 供应商询价任务与人工结果回填、统一动作预检与不可绕过的写执行阻断
- 闲鱼授权 Gateway / 外部 CLI 只读 Adapter、账号、商品、会话、消息和订单能力探测
- 已登录闲鱼个人页的规范化人工快照导入：只接收商品事实，不接收 Cookie、Token、消息或买家隐私
- 闲鱼商家工作台本地 Chrome 桥接：模块跳转、工作台检测、草稿预填、精品图哈希下载与上传、字段回读、最终发布、成功证据回写、租约和完整审计
- 自动化任务、人工审核队列、审计日志、全局与分项 Kill Switch
- 发布、改价、下架、回复与物流回填的统一预检/执行状态机，具备幂等键、每日限额、最小间隔、熔断与人工接管
- 官方 TOP 与授权 JSON Gateway 写入适配层；平台响应不明确时停止重试，避免重复写入
- 闲鱼规范化事件 webhook、消息/订单轮询、供应商价格/库存同步及受控 Celery Beat 调度器
- 基于可核验事实的客服建议：退款、投诉、售后、法律、赔偿和未知意图强制转人工
- 经营总览、商品中心、选品中心、闲鱼只读中心、设置控制面和移动端适配
- 系统内运营方法中心：14 项端到端 SOP、停手门禁、模型能力/价格快照和月度成本测算
- 商业化控制台：客户自有化验收、版本化条款确认、付费/留存/交付工时证据和正式销售门禁
- 大模型设置控制面：OpenAI、DeepSeek、阿里云百炼密钥加密保存、连接测试、任务路由、预算与启停门禁
- 自主上架流水线：候选安全评分、Qwen 文案与独立事实复核、Qwen 参考图编辑、视觉/OCR 双重质检、商品更新、草稿建档与浏览器交接
- 每日 PostgreSQL custom-format 自动备份与 14 天保留（可配置）
- 演示数据初始化、核心业务单元测试与 NAS 冒烟脚本

未配置经授权的凭证时，系统以保护模式运行并显示明确的“待授权”状态。当前版本不包含验证码破解、设备伪造、反检测、风控绕过、自动支付、虚假交易、虚假物流或评价操纵。采购/支付、退款/投诉和平台介入始终保留人工门禁。商业交付固定采用客户自有单租户模式，不托管第三方登录态、不做多账号群控，也不承诺经营收益。

## Docker 启动

```bash
cp .env.example .env
# 修改 .env 中的全部密码与 JWT_SECRET
docker compose up --build -d
```

访问：

- 管理后台：`http://localhost:18180`
- 健康检查：`http://localhost:18181/health`

Compose 默认使用生产环境，因此 OpenAPI 文档端点关闭；只在显式的开发环境中使用 `/docs`。

停止：

```bash
docker compose down
```

更新数据库结构：

```bash
docker compose exec backend alembic upgrade head
```

运行测试：

生产镜像只包含哈希锁定的运行依赖，并在构建后移除 `pip`；测试不要在生产容器内临时安装依赖。开发机使用独立环境：

```bash
uv venv .venv --python 3.12
uv pip sync --python .venv/bin/python --require-hashes backend/requirements-dev.lock
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
.venv/bin/ruff check backend workers adapters agents
(cd frontend && npm ci && npm test && npm run typecheck && npm run build)
```

## 真实数据接入

推荐使用企业自有、经平台授权的 JSON Gateway 或获批的官方 TOP 应用。首次接入必须保持 `AUTOFISH_ADAPTER_WRITE_ENABLED=false`；兼容 CLI 仅用于隔离验证，不能依靠页面私有协议、Cookie 抽取或验证码绕过上线。

接入前先阅读 [产品化运行手册](./docs/PRODUCTIZATION.md)、[Gateway 契约](./docs/INTEGRATION_GATEWAY.md)、[真实数据验证矩阵](./docs/REAL_DATA_VALIDATION.md) 和 [对抗性测试](./docs/ADVERSARIAL_TESTING.md)。未取得平台准入、AppKey/AK 或必要权限时，不得把 `AUTOFISH_ADAPTERS_ENABLED` 改为 `true`。

## 目录

```text
autofish/
├── backend/              FastAPI、业务服务、数据库模型与迁移
├── frontend/             Next.js 管理后台
├── browser-bridge/       本地 Chrome 执行器，不导出登录态；最终提交受独立硬开关与策略双重控制
├── adapters/             平台抽象与隔离实现
├── agents/               结构化 AI Agent 契约
├── workers/              后台任务入口
├── data/                 PostgreSQL / Redis 持久化目录（不入 Git）
├── docs/                 架构、安全、数据库、路线图和设计规格
├── logs/                 运行日志（不入 Git）
├── docker-compose.yml
└── .env.example
```

## 数据与安全

生产凭证只放在部署主机的 `.env`，不写入代码或 Git。页面默认只展示脱敏的供应商标识。所有影响商品、资金边界、自动化状态和发布队列的动作都会写入 `audit_logs`。

## 开源许可证

AutoFish 原创代码与文档采用 [MIT License](LICENSE)。第三方代码、配置和依赖保留各自许可证，详见 [第三方声明](THIRD_PARTY_NOTICES.md)；其中 Playwright seccomp 配置按 Apache-2.0 分发。

开源许可不授予闲鱼、1688 或大模型服务的账号、API、数据和商标使用权限。部署者仍需自行取得必要的平台授权，并遵守相应服务条款。公开源码不表示无人值守运行已通过完整验收，当前能力与待验证项见本文顶部说明。

详细说明见 [商业化可行性诊断](./docs/AutoFish_商业化可行性诊断与升级方案_20260819.md)、[商业交付标准](./docs/COMMERCIAL_DELIVERY.md)、[真实端到端提交闲鱼验收](./docs/REAL_END_TO_END_PUBLICATION_ACCEPTANCE_20260818.md)、[自主上架产品化验收](./docs/AUTONOMOUS_LAUNCH_ACCEPTANCE_20260817.md)、[大模型设置](./docs/AI_MODEL_SETTINGS.md)、[运营方法与大模型路由评估](./docs/OPERATIONS_METHODS_AND_LLM_ROUTING_20260817.md)、[全面自动化交付总结](./docs/AUTOMATION_PRODUCTIZATION_SUMMARY.md)、[架构](./docs/ARCHITECTURE.md)、[数据库](./docs/DATABASE.md)、[Adapter](./docs/ADAPTERS.md)、[八大能力整合](./docs/CLAWHUB_EIGHT_CAPABILITIES.md)、[1688 社区技能评估](./docs/1688_COMMUNITY_SKILLS.md)、[1688 候选人工核验](./docs/SUPPLIER_MANUAL_VERIFICATION.md)、[产品化运行](./docs/PRODUCTIZATION.md)、[Gateway 契约](./docs/INTEGRATION_GATEWAY.md)、[真实数据验证](./docs/REAL_DATA_VALIDATION.md)、[安全](./docs/SECURITY.md)、[合规](./docs/COMPLIANCE.md)、[NAS 部署](./docs/DEPLOYMENT.md)、[验收记录](./docs/QA_ACCEPTANCE.md)、[参考仓库分析](./docs/REFERENCE_ANALYSIS.md) 与 [路线图](./docs/ROADMAP.md)。
