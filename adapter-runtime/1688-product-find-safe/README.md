# 1688 Product Find 安全运行时

AutoFish 通过本目录的 `cli.py` 调用 1688 官方社区技能
`1688-product-find`。上游代码保持为独立运行时，不进入 AutoFish 核心层。

## 固定版本与来源

- 上游：`1688aiinfra/1688-product-find`
- 版本：`1.7.0`
- 下载包 SHA-256：`1472768279b340657f8c3c220350a89b54ec4c482d4b1f654c8a646d1af1a4d5`
- 安装目录：`upstream/1688-product-find-1.7.0/`

运行 `scripts/install_1688_product_find_runtime.sh` 可按固定版本下载、校验并安装。

## 安全边界

- 只开放结构化 `text_search`，不开放下单、改价或其他写操作。
- 不调用上游顶层 CLI，因其包含自动安装、凭证持久化和使用遥测。
- AK 只从进程环境读取，不写入项目或运行时目录。
- 每次调用使用临时工作目录并在结束时删除。
- 健康检查执行一条最小真实签名搜索，不把“格式像 AK”误报为已授权。
- 未开放上游 `link_search`：其页面提取实现会关闭 TLS 证书验证。

## AutoFish 配置

容器内命令：

```text
python /opt/autofish-adapters/1688-product-find-safe/cli.py
```

生产环境仍由 `/run/autofish-credentials/1688-api-key` 只读挂载 AK；禁止把 AK
写进 `.env`、日志、文档或命令行。
