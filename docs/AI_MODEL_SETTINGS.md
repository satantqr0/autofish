# 大模型设置

系统入口：`/model-settings`  
API：`/api/v1/ai-settings`  
引入版本：`0.6.0`

## 支持的服务商

- OpenAI：文本、视觉、工具调用和图像生成；
- DeepSeek：文本、结构化输出和工具调用；
- 阿里云百炼：文本、视觉、工具调用和图像生成。

模型名称和官方兼容 API 地址可以在页面中维护。为防止 API Key 被发送到未知主机，Base URL 必须使用 HTTPS，并且域名必须匹配对应服务商的官方域名。

## 页面功能

1. 保存服务商的 Base URL、文本模型、视觉模型和图像生成模型；
2. 输入或更换 API Key；
3. 使用已保存密钥读取服务商模型列表，验证密钥、网络和权限；
4. 设置主服务商、故障降级服务商、月度人民币预算、Temperature、最大输出 token 和超时；
5. 分别配置客服回复、商品文案、图片质检、图片生成、批量文本和风险摘要的任务路由；
6. 控制模型调用总开关。

模型总开关与闲鱼平台写入开关彼此独立。启用模型不会自动启用发布、回复、采购、物流或其他外部写动作。

## 密钥保护

- API Key 在后端使用 Fernet 对称加密后写入 PostgreSQL；
- 加密密钥优先读取 `AUTOFISH_CREDENTIAL_ENCRYPTION_SECRET`，未单独设置时使用部署环境中的 `JWT_SECRET` 派生；
- 查询接口只返回“是否已配置”和脱敏提示，不返回密钥或密文；
- 服务商配置变更后自动取消此前的测试通过状态；
- 清除密钥后会同时关闭该服务商的已配置状态；
- 保存、清除、连接测试和运行设置变更均进入审计日志。

生产环境建议在 `.env` 中配置独立、随机且长度至少 32 字节的 `AUTOFISH_CREDENTIAL_ENCRYPTION_SECRET`。更换该值前必须先重新录入所有模型 API Key，否则旧密文无法解密。

## 启用门禁

只有主服务商满足以下条件时，后端才允许保存 `enabled=true`：

1. API Key 已加密保存；
2. 最近一次连接测试为成功；
3. 服务商配置在测试后没有再次修改。

连接测试只访问兼容 API 的 `/models` 接口，不执行文本或图像生成，因此不会主动产生生成 token 费用。服务商仍可能对接口请求实施额度或频率限制。

## 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/v1/ai-settings` | 读取脱敏配置、运行设置和安全状态 |
| PUT | `/api/v1/ai-settings/providers/{provider}` | 保存服务商与加密密钥 |
| POST | `/api/v1/ai-settings/providers/{provider}/test` | 测试已保存配置 |
| DELETE | `/api/v1/ai-settings/providers/{provider}/api-key` | 清除服务商密钥 |
| PATCH | `/api/v1/ai-settings/runtime` | 保存任务路由、预算与总开关 |

所有写接口都要求管理员或操作员权限，并遵循现有 CSRF 来源校验和审计机制。
