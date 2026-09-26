# AutoFish 开发约定

## 项目目标

构建私人自用、可审计、可人工接管的闲鱼无货源运营中台。核心业务独立于闲鱼和供应商的具体实现，任何平台动作只能通过 Adapter。

## 目录地图

- `backend/`：FastAPI、SQLAlchemy、Alembic、业务服务。
- `frontend/`：Next.js 管理后台。
- `adapters/`：平台接口契约和外部进程实现；不得包含业务规则。
- `agents/`：结构化 AI Agent 契约；资金和风控决策由代码复核。
- `workers/`：幂等后台任务入口。
- `docs/`：架构、数据库、Adapter、AI、安全、合规与路线图。
- `tests/`：跨模块验收；模块单测可放在对应子项目内。

## 架构原则

1. 核心层只依赖 `SupplierAdapter`、`XianyuAdapter` 抽象。
2. 外部仓库作为独立 CLI/MCP/进程调用，禁止复制其协议实现。
3. 金额使用 `Decimal`；最低售价与议价底线必须由 `PricingEngine` 校验。
4. 自动任务必须幂等、超时、最多重试三次并完整审计。
5. 数据库结构只通过 Alembic migration 修改。
6. AI 输出必须通过 Pydantic Schema，关键决策写入 `ai_decisions`。

## 禁止事项

不得实现验证码/滑块绕过、设备指纹伪造、反检测、批量养号、封号规避、刷量、虚假交易/物流/评价、虚构商品来源。遇到验证或风险提示立即 `MANUAL_REQUIRED`。

## 命令

```bash
docker compose up --build -d
docker compose run --rm backend pytest -q
docker compose run --rm backend ruff check .
docker compose run --rm frontend npm run lint
docker compose run --rm frontend npm run build
```

## 流程

每个 Phase 按“分析 → 设计 → 编码 → 测试 → 审查 → 文档”完成；一个 Phase 稳定后再启用下一个。不得修改 `../references/`。

## 文档索引

[架构](docs/ARCHITECTURE.md) · [数据库](docs/DATABASE.md) · [Adapter](docs/ADAPTERS.md) · [AI Agents](docs/AI_AGENTS.md) · [安全](docs/SECURITY.md) · [合规](docs/COMPLIANCE.md) · [参考仓库分析](docs/REFERENCE_ANALYSIS.md) · [路线图](docs/ROADMAP.md)

