# TerraMaster NAS 部署与运维

## 部署口径

- 项目目录：`/Volume2/docker/autofish`
- 管理后台：`http://192.168.199.180:18180`
- 后端健康检查：`http://192.168.199.180:18181/health`
- PostgreSQL、Redis、worker 不暴露宿主端口。
- `.env` 权限必须为 `0600`，随机值不得复用 NAS 登录密码。`POSTGRES_PASSWORD` 同时进入 PostgreSQL 与 SQLAlchemy URL，使用 `openssl rand -hex 32` 生成 URL 安全值，避免未编码的 `@` 破坏连接地址。
- 首次生成的管理员密码仅保存在 `.initial-admin-password`，权限必须为 `0600`。
- 未完成真实数据验收前，`AUTOFISH_ADAPTERS_ENABLED` 必须保持 `false`；`AUTOFISH_ADAPTER_WRITE_ENABLED` 始终保持 `false`。
- `credentials/` 与 `adapter-runtime/` 只读挂载；目录可为空，但不得提交真实凭证。
- 1688 生产凭证写入 `credentials/1688-api-key` 并设置 `0600`；不得直接放进 `.env`，以免出现在容器环境检查结果中。
- 浏览器桥接使用独立随机令牌 `credentials/xianyu-browser-bridge-token`，不得复用 NAS、后台、1688 或闲鱼密码。
- backend/worker 以 UID/GID `10001` 运行；`credentials/` 与 `adapter-runtime/` 写入实际内容后需归属 `10001:10001` 且目录权限为 `0700`，否则容器无法读取只读挂载。可写的 `assets/` 与 `logs/` 也必须归属 `10001:10001`，否则图片流水线会在创建文件时失败。
- `backup` 服务每日生成 PostgreSQL custom-format 备份，默认保留 14 天。

从 macOS 打包上传时应关闭 AppleDouble 元数据，避免生成 `._*` 文件：

```bash
COPYFILE_DISABLE=1 tar -czf autofish.tar.gz autofish
```

根目录与 `frontend/` 的 `.dockerignore` 必须随项目上传；两者会排除 `.env`、依赖缓存、
构建产物和 macOS 元数据，防止密钥进入 Docker 构建上下文。

## 首次启动

```bash
cd /Volume2/docker/autofish
export PATH=/Volume2/@apps/DockerEngine/dockerd/bin:$PATH
docker compose config --quiet
mkdir -p credentials adapter-runtime backups assets logs
docker run --rm -v "$PWD/assets:/assets" -v "$PWD/logs:/logs" postgres:16-alpine \
  sh -c 'chown -R 10001:10001 /assets /logs && chmod 700 /assets /logs'
docker compose up --build -d
docker compose ps
./scripts/healthcheck.sh
```

启用独立安装的 1688 只读 CLI 时，保持外部仓库与 AutoFish 源码隔离，并配置：

```bash
./scripts/install_1688_shopkeeper_runtime.sh
chmod 0600 credentials/1688-api-key
AUTOFISH_ADAPTERS_ENABLED=true
AUTOFISH_ADAPTER_WRITE_ENABLED=false
AUTOFISH_SUPPLIER_ADAPTER=hybrid_cli
AUTOFISH_SUPPLIER_ACCESS_KEY_FILE=/run/autofish-credentials/1688-api-key
AUTOFISH_SUPPLIER_SHOPKEEPER_CLI_COMMAND=python /opt/autofish-adapters/1688-shopkeeper/cli.py
AUTOFISH_SUPPLIER_PRODUCT_FIND_CLI_COMMAND=python /opt/autofish-adapters/1688-product-find-safe/cli.py
```

TerraMaster 上可用一次性容器修正挂载目录的所有权；命令只处理下列明确目录：

```bash
docker run --rm -v "$PWD/credentials:/credentials" postgres:16-alpine \
  sh -c 'chown 10001:10001 /credentials /credentials/1688-api-key && chmod 700 /credentials && chmod 600 /credentials/1688-api-key'
docker run --rm -v "$PWD/adapter-runtime:/runtime" postgres:16-alpine \
  sh -c 'chown 10001:10001 /runtime && chmod 700 /runtime'
docker run --rm -v "$PWD/assets:/assets" -v "$PWD/logs:/logs" postgres:16-alpine \
  sh -c 'chown -R 10001:10001 /assets /logs && chmod 700 /assets /logs'
```

安装脚本固定到官方仓库 `next-1688/1688-shopkeeper` 的 `1.0.1`
（commit `99537bce780dd2bb926891f11223aeaa30842eff`），并校验下载包
SHA-256 `96bfd38a6f64b3cc5c65fa74ed1e4f7d96edff84fc7b43737cde84fbd861e5a3`。
`adapter-runtime/1688-shopkeeper` 保持为只读挂载；由于该外部仓库未声明许可证，
不得复制进 AutoFish 镜像或随项目再分发，只能由部署者在目标设备独立下载。

代码同时支持可选的 `product_find_cli`，但该社区技能会进行使用埋点上报。只有完成固定版本包体、SHA-256、实际许可证、依赖、网络目标和上报内容审计后，才可在隔离环境配置：

```bash
AUTOFISH_SUPPLIER_ADAPTER=product_find_cli
AUTOFISH_SUPPLIER_CLI_COMMAND=python /opt/autofish-adapters/1688-product-find/cli.py
```

切换时写开关仍必须为 `false`，并保留 `shopkeeper_cli` 配置作为回滚路径。未经审计不得在 NAS 执行社区安装命令。

读取首次管理员密码：

```bash
cat /Volume2/docker/autofish/.initial-admin-password
```

首次构建会拉取 Node、Python、PostgreSQL 和 Redis 基础镜像。后端先执行生产配置安全预检，
通过后才执行 `alembic upgrade head`；迁移成功后才启动 API，worker 只在 API 健康后启动。

## 本地 Chrome 浏览器桥接

在 NAS 项目目录生成独立令牌并只让容器运行用户读取：

```bash
openssl rand -hex 32 | docker run --rm -i -v "$PWD/credentials:/credentials" \
  postgres:16-alpine sh -ec \
  'umask 077; cat > /credentials/xianyu-browser-bridge-token; chown 10001:10001 /credentials /credentials/xianyu-browser-bridge-token; chmod 700 /credentials; chmod 600 /credentials/xianyu-browser-bridge-token'
```

若宿主机创建的文件不是 UID/GID `10001`，使用部署章节中的一次性容器方式修正所有权。随后重新构建 backend 和 worker。

Mac Chrome 安装步骤：

1. 打开 `chrome://extensions` 并启用开发者模式；
2. 加载项目中的 `browser-bridge/` 目录；
3. 扩展设置填写 `http://192.168.199.180:18181/api/v1`；
4. 填写 NAS 上 `xianyu-browser-bridge-token` 的内容并保存测试；
5. 在 AutoFish 的“闲鱼工作台”页面先执行“检测工作台”，确认心跳、扩展版本和模块列表后再预填一个测试草稿；
6. 单款预填验收通过后，启用 GLOBAL/PUBLISH，把 PUBLISH 设为 `AUTOMATIC`，并在 `.env` 设置 `AUTOFISH_XIANYU_BROWSER_PUBLISH_ENABLED=true` 后重建 backend、worker 与 scheduler。

浏览器 AI 客服使用另一套独立硬开关，发布开关不会隐式开启客服。默认保持
`AUTOFISH_XIANYU_BROWSER_CUSTOMER_SERVICE_ENABLED=false`。完成只读会话采集、低风险回复、
高风险转人工、消息变化阻断和不明确结果对账的金丝雀验收后，才可同时启用
GLOBAL/CUSTOMER_SERVICE、将 CUSTOMER_SERVICE 设置为 `AUTOMATIC`，再显式开启该硬开关。
调度器按消息间隔创建幂等采集任务；只有最新消息仍为买家消息、会话未人工接管、
同源回复任务不存在且事实门禁建议为 `auto_eligible` 时才会排队发送。验证码、登录失效、
风控、会话或建议指纹变化以及提交结果不明确都会停止自动执行并转人工。

0.5.1 扩展可以执行固定的商品最终发布、会话采集和受控回复，但只有对应硬开关、自动化策略和能力门禁同时放行时才可领取。当卖家工作台的“商品发布”菜单被浮层遮挡时，它只会使用预置的同源商品发布 hash 路由，不接受任意 URL。最终提交使用短时 `chrome.debugger` 会话，仅向指纹未变化、文本为“发布”且坐标位于当前视口的按钮发送一次鼠标事件，之后立即脱离；不会用该权限执行脚本、读取 Cookie、抓取网络响应或点击其他动作。它不会执行发货、退款、取消、删除、支付或充值。提交阶段断线或结果不明确时固定转人工且禁止自动重试。公共网络使用前必须为 NAS 配置 HTTPS；局域网 HTTP 仅用于当前受信网络。

## 更新

```bash
cd /Volume2/docker/autofish
export PATH=/Volume2/@apps/DockerEngine/dockerd/bin:$PATH
./scripts/backup.sh
docker compose up --build -d
./scripts/healthcheck.sh
```

更新前保留数据库备份。不要删除 `data/postgres`、`data/redis`、`backups` 或 `.env`。

## 备份与恢复

自动备份由 `backup` 容器执行。确认最近文件非空：

```bash
docker compose ps backup
ls -lh backups/autofish-*.dump | tail
docker compose exec backup sh -ec 'cd /backups; latest="$(ls -1t autofish-*.dump | head -1)"; pg_restore --list "$latest" >/dev/null; sha256sum -c "$latest.sha256"'
```

升级前手工备份：

```bash
./scripts/backup.sh
```

`scripts/backup.sh` 生成压缩 SQL 和同名 SHA-256 校验文件。恢复前先停止所有可能访问数据库或生成备份的服务，并把备份文件名替换为实际值：

```bash
(cd backups && sha256sum -c autofish-YYYYMMDD-HHMMSS.sql.gz.sha256)
gzip -t backups/autofish-YYYYMMDD-HHMMSS.sql.gz
docker compose stop backend worker scheduler backup
gzip -dc backups/autofish-YYYYMMDD-HHMMSS.sql.gz \
  | docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose up -d backend worker scheduler backup
./scripts/healthcheck.sh
```

自动 `.dump` 恢复使用：

```bash
docker compose exec backup sh -ec \
  'cd /backups; sha256sum -c autofish-YYYYMMDDTHHMMSSZ.dump.sha256; pg_restore --list autofish-YYYYMMDDTHHMMSSZ.dump >/dev/null'
docker compose stop backend worker scheduler backup
docker compose run --rm backup sh -ec \
  'pg_restore --list /backups/autofish-YYYYMMDDTHHMMSSZ.dump >/dev/null && pg_restore --clean --if-exists --no-owner --no-acl --dbname="$PGDATABASE" /backups/autofish-YYYYMMDDTHHMMSSZ.dump'
docker compose up -d backend worker scheduler backup
./scripts/healthcheck.sh
```

恢复会替换业务数据库，只能在确认备份文件、维护窗口和目标库后执行。

## 排障

```bash
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=200 worker
docker compose logs --tail=200 frontend
docker compose logs --tail=100 backup
docker compose exec backend alembic current
```

若出现验证码、风控、人脸或二次确认，不得在系统中尝试绕过；任务必须进入
`MANUAL_REQUIRED`，由操作员在官方界面处理。
