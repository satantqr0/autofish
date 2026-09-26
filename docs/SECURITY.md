# 安全设计

## 1. 凭证

- `.env`、Cookie、AK/SK、API Key、Token 和账号密码不进入 Git、业务表或日志。
- NAS 部署为 PostgreSQL、JWT、管理员分别生成独立强随机值。
- 外部 Adapter 使用只读 secret mount；核心数据库仅保存 `credential_ref`。
- 密钥轮换会撤销旧会话，并写入 AuditLog。

## 2. 认证与授权

- 管理员密码使用带随机盐的 scrypt；每次哈希使用独立随机盐。
- 短期 JWT 放在 `HttpOnly`、`SameSite=Strict` Cookie；HTTPS 后启用 `Secure`。
- API 按 `admin/operator/viewer` 最小权限授权；平台写入、Kill Switch 和凭证变更只允许 admin/operator。
- 登录、失败、登出、密码变更、开关变化全部审计。

## 3. 网络

- PostgreSQL、Redis、worker 不暴露宿主端口。
- CORS 只允许 NAS 管理后台来源，禁止 `*` 与凭证组合。
- Adapter 子进程不持有核心 DB 密码；可通过独立网络/容器进一步隔离。
- 反向代理启用 HTTPS、访问控制、请求大小限制和安全响应头。

## 4. 隐私

- 买家姓名、电话、地址按最小必要原则收集并应用层加密。
- 列表默认脱敏；查看完整信息需要额外权限并审计。
- AI 上下文移除联系方式、地址和不相关历史。
- 日志过滤 Authorization、Cookie、AK/SK、手机号、地址和平台签名。

## 5. 自动化安全

- 全局与功能 Kill Switch 在任务领取和写动作前双检。
- 不可逆动作要求 idempotency key、最新价格/库存校验和 AuditLog。
- 闲鱼发布还必须通过商品来源门禁；演示说明、演示供应商代码/名称、`DEMO` 来源类型、`.example` 供应商链接或断裂的供应商 SKU 链路一律拒绝。
- 来源门禁同时覆盖发布队列、草稿生成、动作预检、执行前复核、人工发布回填，以及商家工作台预填任务创建与代理领取，防止旧记录或竞态绕过。
- 风控、验证码、人脸、二次确认、未知响应直接 `MANUAL_REQUIRED`。
- 失败最多三次；禁止无限循环或无限重连。
- 新环境默认平台总写动作每日上限 20、发布每日上限 10；正式首轮金丝雀应把单项上限降为 1，稳定后再逐步提高。

## 6. 供应链安全

- 外部 JSON 经过 Schema、长度、类型、URL 和枚举白名单验证。
- subprocess 使用 argv 数组、固定 binary、最小环境变量和超时，不使用 `shell=True`。
- 供应商图片下载限制域名、大小、MIME，并重新编码后使用。
- 原始快照用 SHA-256 关联，便于复核但不信任其内容。

## 7. 备份与恢复

- 每日 `pg_dump` 加密备份到另一个存储池；定期做恢复演练。
- 更新前自动备份，migration 失败不启动新版本。
- 手工备份脚本必须先定位可执行 Docker CLI，并使用临时文件生成；只有 SQL 非空、gzip 校验通过后才原子改名为正式恢复点。
- AuditLog、订单和资金记录采用不可变更策略；只归档不删除。
