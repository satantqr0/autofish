# AutoFish 0.4.0 验收记录

验收日期：2026-08-16  
目标环境：TerraMaster NAS，`/Volume2/docker/autofish`

## 自动化与部署检查

- 0.4.0 后端：Ruff 全量通过；Pytest `67 passed`，NAS backend 容器内复跑一致。
- 0.4.0 前端：TypeScript 类型检查和 Next.js 生产构建通过，共 22 条路由。
- 数据库：Alembic `a4b7c9d2e615 (head)`；升级前 custom-format 备份 `pre-controlled-automation-a4b7c9d2-20260816.dump` 已通过 `pg_restore --list`。
- 运行时：frontend、backend、worker、scheduler、PostgreSQL、Redis、backup 共 7 个服务启动；backend/frontend/postgres/redis 健康。
- 生产冒烟：`/health`、`/ready`、登录、用户、经营总览、集成状态、策略控制、ClawHub 总览和闲鱼中心均通过；无凭证 401、恶意跨域写入 403。
- 生产配置：`READ_ONLY_LIVE`；演示数据关闭、1688 `shopkeeper_cli` 只读、闲鱼 Adapter 关闭、平台写总开关关闭、自动调度关闭。
- 日志：backend、worker、scheduler、frontend、postgres、redis 最近部署窗口的严重错误匹配均为 0。
- 受控动作：发布、改价、下架、客服发送和物流回填具备预检、确认/自动模式、幂等、限额、最小间隔、熔断、审计和人工接管；采购/支付及高风险售后保持人工。

以下条目保留 0.3.0 及更早版本的回归证据：

- 后端：Ruff（含 Adapter）全量通过；Pytest 52 项全部通过（NAS 容器内复跑一致）。
- 前端：TypeScript 类型检查通过；Next.js 生产构建通过，共生成 22 条路由，包含 `/clawhub`。
- 数据库：Alembic `d9a42f6e871b (head)`；PostgreSQL public schema 42 张表。
- 运行时：frontend、backend、PostgreSQL、Redis 健康；worker 正常；新增 backup 服务正常运行。
- 生产冒烟：`/health` 返回版本 `0.3.0`，`/ready` 返回 database/redis 均为 ok。
- 鉴权冒烟：登录、集成状态、选品池、闲鱼事实库、商品列表接口均返回 HTTP 200。
- 实况状态：NAS 当前为 `READ_ONLY_LIVE`；1688 为 `OK/authenticated/read_only`；闲鱼官方 Adapter 为 `AUTH_REQUIRED`，人工商品快照可用；平台写操作为 `false`。
- 备份：升级前压缩 SQL 通过 gzip 完整性校验；自动备份生成 106 KiB custom-format `.dump` 并通过 `pg_restore --list`。
- 1688 冒烟：真实 `shops` 授权成功且当前无绑定店铺；应用内搜索 HTTP 200、返回并落库 20 条候选；商品详情抽样可读取标题与原文，但无可靠结构化类目/SKU。
- 社区兼容层：`1688-product-find` 的公开商品/SKU/库存/供应商字段映射通过单元测试；缺失 ID、价格区间和非数值库存不会被猜测。外部技能未安装、未运行，NAS 继续使用原 `shopkeeper_cli`。
- 安全：`.env`、初始管理员密码和 1688 Secret 权限均为 `0600`；production CSP 不含 `unsafe-eval`；凭证不在容器顶层环境或日志中，外部凭证目录只读挂载。
- 0.2.4 部署后复核：`/health`、`/ready` 和 `scripts/healthcheck.sh` 通过；frontend/backend/postgres/redis 健康，worker/backup 运行；供应商状态仍为 `OK/authenticated/read_only`、绑定店铺 0；生产配置仍为 `shopkeeper_cli` 且写开关为 `false`。
- 升级备份：`backups/autofish-20260816-002936.sql.gz` 已生成并通过 gzip 完整性校验。
- 闲鱼真实页面：从本人已登录个人页规范化导入 1 个账号、10 条在售商品；伪造域名和无效 JSON 在生产界面均被拒绝。
- 对抗性测试：伪造来源、协议降级、ID/数量/时间/价格篡改、敏感字段注入、角色与对象越权、幂等重放、完整快照移除等矩阵全部通过，详见 `docs/ADVERSARIAL_TESTING.md`。
- ClawHub API 冒烟：25 项通过；覆盖八能力总览、真实关键词找货、候选对比、上架前/后评估、诊断、草稿、动作预检/再次确认仍阻断、人工询价、趋势契约和诚实空供应商结果。
- ClawHub 安全对抗：无凭证 401、恶意 Origin 403、内网图片 URL 422、未知字段 422、未配置图片 Adapter 501；平台写开关仍为 `false`。
- 缺陷闭环：首轮真实 Shopkeeper 候选对比发现空评分触发 Decimal `InvalidOperation`；修复归一化后增加空评分回归测试，NAS 重建并复测通过。
- 升级备份：`pre-clawhub-20260815T170715Z.dump` 为 custom-format 非空备份，`pg_restore --list` 通过。

## 浏览器交互验收

验收使用应用内浏览器的语义定位与原生截图；桌面视口 1440×900，移动视口 390×844，验收结束后已复位临时视口。

核心路径：登录 → 选品中心 → 导入 Canonical Feed → 校验 85.50 八维评分 → 导入商品中心 → 闲鱼只读中心 → 订单/会话重定向 → 设置 → 修改密码 → 退出 → 使用新密码重新登录。

结果：原有闭环所有动作成功；本轮新增验证 `READ_ONLY_LIVE`、`1/2 Adapter 已授权`、1688“只读连接可用”和 20 条真实候选。驾驶舱在闲鱼订单/经营数据未授权时统一显示 0 与“等待授权”，不再展示 DEMO GMV。修复状态请求去重/有限重试后，页面读取到 0 条应用自身 warning/error；Chrome 扩展自身告警不归属于 AutoFish。

0.2.4 新增闭环：已登录闲鱼个人页 → 读取页面可见在售事实 → 无效 JSON 中文拒绝 → 伪造闲鱼域名由服务端拒绝 → 真实完整快照导入 → 账号与 10 条商品展示 → 会话/订单保持关闭。干净新标签页复测无 React 错误浮层，应用自身 warning/error 为 0；测试中发现的日期水合不一致已改为客户端挂载后渲染。

0.3.0 新增界面：左侧加入“1688 能力台”，桌面为八能力卡片 + 操作台 + 真实结果区，移动端 390×844 自动折叠为单列。浏览器确认路由、标题、菜单、保护状态、表单和加载态可见，应用控制台 warning/error 为 0。验收时原 AutoFish 浏览器会话已过期，因此登录后逐按钮视觉回归由内部 API 全功能冒烟替代；未在浏览器重新传输管理员密码。

## 视觉对照

确认概念稿：

- `docs/design/dashboard-concept.png`
- `docs/design/product-center-concept.png`

实现对照要点：

1. 保留海军蓝固定侧栏、青绿主色和白色工作区。
2. 保留顶部上下文栏、日期、账号和状态徽标的信息层级。
3. 保留方形边界、紧凑表格和低阴影的运营后台质感。
4. 选品中心沿用“筛选栏 + 事实表 + 右侧检查器”三栏工作台。
5. 桌面表格与移动端标签均使用显式状态色，并允许局部横向滚动，不造成文档级横向溢出。
6. Kill Switch 始终使用红色；保护/待授权状态使用琥珀色；可用只读能力使用绿色。

首屏文案有意差异：概念稿的“自动化运行中”改成 `READ_ONLY_LIVE / 部分只读就绪 / 1/2 Adapter 已授权`；新增 `PHASE 3/4 · READ ONLY`、待授权原因和“写操作关闭”，确保视觉不会暗示尚未取得的权限。

其他有意差异：未生成真实平台商品图时使用中性图标；移动端优先保留事实状态和核心动作，侧栏改为抽屉；闲鱼订单不支持时展示能力缺口而非空成功。

验收截图保存在项目之外，避免把临时测试证据混入发布包：

- `/Users/satantqr/.codex/visualizations/2026/08/15/01a0053f-db51-7750-817f-3585e7240543/autofish-sourcing-desktop.png`
- `/Users/satantqr/.codex/visualizations/2026/08/15/01a0053f-db51-7750-817f-3585e7240543/autofish-xianyu-mobile.png`
- `/Users/satantqr/.codex/visualizations/2026/08/15/01a0053f-db51-7750-817f-3585e7240543/autofish-readonly-live-desktop.png`
- `/Users/satantqr/.codex/visualizations/2026/08/15/01a0053f-db51-7750-817f-3585e7240543/autofish-readonly-live-mobile.png`
- `/Users/satantqr/.codex/visualizations/2026/08/15/01a0053f-db51-7750-817f-3585e7240543/autofish-1688-sourcing-live.png`
- `/Users/satantqr/.codex/visualizations/2026/08/15/01a0053f-db51-7750-817f-3585e7240543/autofish-xianyu-0.2.4-final.jpg`
- `/Users/satantqr/.codex/visualizations/2026/08/15/01a0053f-db51-7750-817f-3585e7240543/autofish-clawhub-0.3.0-mobile.png`（会话过期时的移动布局与安全重定向证据）

## 剩余风险

- 1688 当前未绑定下游店铺，且兼容 CLI 不提供可靠结构化 SKU/库存/供应商字段；真实候选只能进入“待补字段/已排除”，不能直接导入商品中心。
- `1688-product-find` 页面披露调用埋点；在包体、依赖、网络目标、许可证和埋点内容完成审计前，不得切换生产 Adapter。
- 闲鱼商品已完成人工只读快照验证；会话、消息和订单仍未提供开放平台或授权 Gateway 凭证，无法自动同步。
- Phase 5–10 的 AI 发送、发布、订单写入、采购、物流写回和无人值守尚未开放。
- 当前 NAS 入口是局域网 HTTP；若要公网使用，必须先加 HTTPS 反向代理与访问控制。
- `1688-product-find` 未安装，因此图片/链接找同款在生产会明确返回 501；自动询价发送仍为 `MANUAL_REQUIRED`。
- 0.3.0 登录后逐按钮浏览器回归需在操作员重新登录后补做；API、移动布局、生产构建和服务端闭环已验收。
