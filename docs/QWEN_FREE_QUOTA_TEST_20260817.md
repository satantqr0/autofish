# 阿里云百炼免费额度模型识别与实测

- 测试日期：2026-08-17
- 接口地域：华北 2（北京）
- 兼容接口：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- 安全说明：API Key 已在 AutoFish 服务端加密保存，本报告不记录密钥明文。

## 结论

阿里云百炼没有适合生产系统的“永久免费模型”。下表中的免费指新人免费额度；官方当前规则为仅华北 2（北京）提供，通常每个模型独立赠送 100 万 Token，有效期 90 天。账户已启用“仅免费额度”，额度耗尽时接口会停止响应而不是继续扣费。

## 实测结果

| 模型 | 用途 | 结果 | 消耗/输出 | 延迟 |
| --- | --- | --- | --- | --- |
| `qwen-flash` | 默认文本、客服、批量任务 | HTTP 200 | 13 Token，返回 `OK` | 253 ms |
| `qwen3.7-plus` | 高质量商品文案、复杂推理 | HTTP 200 | 17 Token，返回 `OK` | 654 ms |
| `qwen3-vl-flash` | 图片事实质检 | HTTP 200 | 14 Token，返回 `OK` | 277 ms |
| `qwen-image-2.0` | 精品图片生成 | HTTP 200 | 成功生成 1 张 512×512 图片 | 1,066 ms |
| `qwen3.7-flash` | 新版低价文本 | HTTP 403 | `AllocationQuota.FreeTierOnly`，免费额度已耗尽 | 252 ms |

模型列表接口返回 238 个当前密钥可见模型。模型可见不代表仍有免费额度；只有实际调用或百炼控制台免费额度页面能确认剩余量。

## AutoFish 当前配置

- 文本模型：`qwen-flash`
- 视觉模型：`qwen3-vl-flash`
- 生图模型：`qwen-image-2.0`
- 主服务商：阿里云百炼
- 全部任务路由：阿里云百炼
- 模型总开关：关闭（仅完成配置与测试，不自动启动生产调用）
- 连接测试：通过

## 费用边界

- 免费额度只适用于实时推理，不适用于 Batch、模型调优、模型部署或自定义模型。
- 各模型额度相互独立，不会在某一模型额度耗尽后自动切换。
- `qwen-image-2.0` 官方新人免费额度为 100 张；本次测试使用 1 张。
- `qwen3.7-flash` 当前账户免费额度已经耗尽，不应设为默认模型。
- 当前账户的 `FreeTierOnly` 保护已经验证有效，继续保持开启可避免自动产生付费调用。

## 官方依据

- [新人免费额度](https://help.aliyun.com/zh/model-studio/new-free-quota/)
- [模型调用价格](https://help.aliyun.com/zh/model-studio/model-pricing)
- [Qwen-Image 文生图 API](https://help.aliyun.com/zh/model-studio/qwen-image-api)

## 本次发现并修复的问题

在服务商完成连接测试后再次修改模型名称时，审计数据中的时间类型不能直接写入 JSON，导致保存接口返回 500。现已统一将审计数据转换为 JSON 安全格式，并增加回归测试；后端全量测试 120 项通过。
