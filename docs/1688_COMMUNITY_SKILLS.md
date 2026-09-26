# 1688 社区技能评估与接入策略

评估日期：2026-08-16

## 结论

1688 社区技能可以作为 AutoFish 的“外部能力实现”和契约参考，但不能直接并入核心代码或默认启用。当前最有价值的是官方发布者 `1688AiInfra` 的 `1688-product-find`：其公开输出字段覆盖商品 ID、标题、图片、详情链接、价格、SKU、供应商、库存、销量、起批量、服务保障和卖点，能补足现有 `1688-shopkeeper` 只有搜索和商详原文、缺少结构化 SKU/库存/供应商的问题。

AutoFish 已增加 `ProductFind1688Adapter` 兼容层，仅解析公开声明的 JSON stdout 字段；社区技能本体仍需独立安装、单独审查、只读挂载和最小权限运行。生产环境继续使用现有 `shopkeeper_cli`，新适配器未安装、未启用，也未执行任何社区代码。

## 候选能力

| 社区技能 | 公开能力 | 对 AutoFish 的价值 | 当前决策 |
|---|---|---|---|
| [`1688-product-find`](https://clawhub.ai/1688aiinfra/skills/1688-product-find) | 文本/图片/链接找货、比价；公开字段含 SKU、库存、供应商、起批量和服务信息 | 最高，直接补结构化选品字段 | 已实现隔离适配器；待包体审计和真实字段验证后启用 |
| [`1688-product-search`](https://clawhub.ai/1688aiinfra/skills/1688-product-search) | 1688 开放平台商品搜索、类目、图片搜索、商品详情、相关商品和货盘 | 可补类目与商品详情 | 仅纳入后续评估，未安装 |
| [`1688-distribution`](https://clawhub.ai/1688aiinfra/skills/1688-distribution) | 选品、分销参谋、店铺绑定、铺货和订单 | 品牌授权字段与订单状态机可作门禁参考 | 不启用写能力；铺货必须另行人工确认和小流量验收 |
| [`1688-item-select`](https://clawhub.ai/1688aiinfra/skills/1688-item-select) | 五维评分与 S/A/B/C 商品分层 | 可校准 AutoFish 选品评分 | 只借鉴指标口径，不替代本地可解释评分引擎 |
| [`1688-shopkeeper`](https://github.com/next-1688/1688-shopkeeper) | 搜索、商详原文、店铺、趋势、日报与铺货 | 已完成真实只读验证 | 保持当前生产只读 Adapter，不开放写操作 |

## 已落地的兼容层

- 新增 `adapters/supplier/product_find_1688.py`，适配公开命令 `text_search --query` 和 `{"success", "markdown", "data"}` JSON 输出。
- `SupplierProductCandidate` 增加结构化 `skus`，候选同步可保存外部 SKU ID、规格、价格和库存。
- 支持公开字段的兼容命名：`product_id/offerId`、`detail_url`、`sku_id`、`sku_title`、`supplier_id`、`supplier`、`stock_amount` 等。
- 价格区间或非数值库存不做猜测；没有真实供应商 ID 或 SKU ID 时保持为空，候选继续显示但不能导入。
- `product_find_cli` 作为可选配置加入 Adapter Factory；默认配置和 NAS 当前生产选择均未改变。
- 商品详情、独立实时价格、独立实时库存、采购和铺货在未确认稳定契约前统一返回 `UNSUPPORTED`。

## 安全与许可边界

`1688-product-find` 页面标示版本 `v1.7.0`、许可证 `MIT-0`，同时披露 CLI 每次调用会向技能网关发送一次使用埋点。正式安装前仍必须检查下载包内实际许可证、文件清单、版本签名/哈希、依赖、网络目标和埋点内容；网页标签不能替代包体审计。

接入门禁：

1. 只从官方发布者或官方源码仓库获取固定版本，不执行社区评论中的安装脚本。
2. 下载包先离线解压，静态扫描 Python/Node 依赖、子进程、文件写入、凭证读取和外发请求。
3. 记录包体 SHA-256、来源 URL、版本、许可证、审计日期和允许的网络域名。
4. 以非 root UID 独立运行；代码只读挂载；临时状态目录独立；不提供 PostgreSQL、Redis、闲鱼 Cookie 或 NAS 管理权限。
5. 1688 凭证仅通过只读 Secret 文件注入，不写入项目、镜像、日志或快照。
6. 首轮只执行授权状态和少量搜索；对照官方页面验证至少 20 个商品的字段完整性。
7. 埋点必须在隐私审查后明确接受；若无法确认上报内容，新技能不得进入生产。
8. 所有铺货、下单、催发、发送旺旺消息等动作保持关闭，另走写操作审批和幂等门禁。

## 真实验收标准

只有同时具备下列真实字段，候选才允许进入商品中心：类目、真实供应商 ID/名称、外部 SKU ID、可解析采购价、库存和来源链接。服务保障、起批量、销量、严选指数和品牌授权状态作为风险/评分输入保存，但不能替代必填字段。

若 `isBrandOffer=true` 且 `isBrandAuth=false`，后续任何铺货方案必须硬性拒绝；当前 AutoFish 没有开放铺货写接口，因此只保存风险事实，不执行动作。

## 后续顺序

1. 获取并审计官方 `1688-product-find` 固定版本包体。
2. 在隔离环境完成字段样本对照、限流、401、超时和异常结构测试。
3. 验证其埋点内容与数据合规边界。
4. 通过门禁后在 NAS 增加独立 runtime，切换前保留 `shopkeeper_cli` 回滚路径。
5. 再评估 `1688-product-search` 的类目/商详能力，避免同时引入两个重叠运行时。
