# 1688 ClawHub 八大能力整合说明

## 交付结论

AutoFish `0.4.0` 已将八项能力收敛到 `/clawhub` 工作台和 `/api/v1/clawhub/*` API。整合采用“核心业务模型 + 授权 Adapter + 事实快照 + 受控动作 + 审计”的方式，外部技能不直接访问数据库，也不复制私有协议。

| # | 能力 | API / 数据落点 | 当前运行状态 |
|---|---|---|---|
| 1 | 1688 Shopkeeper | `/discover`、`/insights`、`external_snapshots` | 关键词、趋势、商机可经授权 CLI 读取 |
| 2 | Product Find | `/discover`、`/compare`、`sourcing_candidates` | 本地事实对比可用；图片/链接依赖外部 Product Find CLI |
| 3 | Source Suppliers | `/suppliers/discover`、供应商候选/匹配/可靠性表 | 使用已保存的真实供应商标识生成主供与备选池 |
| 4 | Item One Click 架构 | `/actions/preview`、`/actions/{id}/execute` | 预检、确认/自动模式和审计可用；生产写开关关闭 |
| 5 | Item Select | `/evaluations`、`product_evaluations` | 上架前后评分、S/A/B/C 分层可用 |
| 6 | Product Analysis | `/diagnoses`、`product_diagnoses` | 规则诊断、证据与动作建议可用 |
| 7 | Distribution Pipeline | `/drafts`、`xianyu_drafts` | 1688 商品事实到闲鱼待审草稿可用 |
| 8 | Inquiry 1688 | `/inquiries`、询价任务/消息表 | 任务与人工回填闭环可用；自动发送未启用 |

## 安全与真实性边界

- 图片和链接输入只接受不含认证信息的 HTTPS 公网地址，并拒绝 localhost、内网、回环和保留 IP。
- 供应商池只读取已入库候选中的外部供应商 ID、名称、商品与实际返回字段。响应速度、交付能力等未知指标保持 `null`。
- 动作网关保存目标、请求、阻断原因、幂等键、执行结果和操作员审计。经授权的 TOP/Gateway 可执行发布、改价、下架、回复与物流回填；生产默认关闭。采购、退款和支付不进入通用写执行器。
- 草稿流水线只使用商品、SKU、定价、规格和图片事实；会移除“自用闲置”“全新未拆”等无法证明的来源表述。
- 询价上游不可用时状态为 `MANUAL_REQUIRED`，结果标记 `not_attempted`，只有操作员取得真实回复后才能回填 `REPLIED`。
- 评分和诊断不会自动改变商品生命周期，只提供可追溯建议。

## 数据库迁移

Alembic head：`a4b7c9d2e615`

新增表：

- `supplier_discovery_jobs`
- `supplier_candidates`
- `supplier_product_matches`
- `supplier_reliability_scores`
- `platform_action_records`
- `product_evaluations`
- `product_diagnoses`
- `xianyu_drafts`
- `supplier_inquiries`
- `supplier_inquiry_messages`

## 使用顺序

1. 在“1688 能力台”执行关键词找货，或从“选品中心”读取已有候选。
2. 对至少两个候选执行事实对比。
3. 从选定候选建立主供/备选供应商池。
4. 运行上架前评估；低等级商品先处理问题。
5. 将已导入商品转换为闲鱼待审草稿。
6. 首次只做预检；取得授权后以 `REVIEW + 每日上限 1` 完成人工确认金丝雀。
7. 创建供应商询价任务，取得真实答复后回填。
8. 上架后录入真实指标，执行评分和诊断。

## 验证命令

```bash
./.venv/bin/ruff check backend adapters
cd backend && ../.venv/bin/pytest -q
cd ../frontend && npm run typecheck && npm run build
cd ../backend && AUTOFISH_DATABASE_URL='postgresql+psycopg://u:p@localhost/db' \
  ../.venv/bin/alembic upgrade head --sql
```

运行时验收还应检查：`/health` 版本、`/ready`、Alembic revision、登录、能力台八张卡片、关键词找货、供应商发现、评估、诊断、草稿、询价和动作阻断。
