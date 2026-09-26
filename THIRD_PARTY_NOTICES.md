# Third-party notices

AutoFish 的原创代码与文档采用根目录的 [MIT License](LICENSE)。第三方代码、
配置和依赖保留各自许可证；根目录 MIT 许可证不替代下述第三方条款。

## Playwright seccomp profile

- 文件：`market-collector/seccomp_profile.json`
- 来源：Microsoft Playwright，版本 `v1.62.1`
- [上游文件](https://github.com/microsoft/playwright/blob/v1.62.1/utils/docker/seccomp_profile.json)
- 许可证：Apache License 2.0。随仓库提供的完整副本：
  [LICENSES/Playwright-Apache-2.0.txt](LICENSES/Playwright-Apache-2.0.txt)
- 上游版权与归属声明：
  [LICENSES/Playwright-NOTICE.txt](LICENSES/Playwright-NOTICE.txt)
- 本地修改：在首个 syscall allow-list 中添加 `chroot`，用于 NAS 容器内
  Chromium 沙箱兼容；在该条目的 `comment` 字段中标记来源和修改。
  该配置文件继续按 Apache-2.0 分发。
- 上游 [LICENSE](https://github.com/microsoft/playwright/blob/v1.62.1/LICENSE)
  与 [NOTICE](https://github.com/microsoft/playwright/blob/v1.62.1/NOTICE)
  已保留，副本仅统一为 LF 换行。

## Dependencies and external adapters

Python、npm、容器镜像与浏览器等外部依赖适用其发布者的许可证；依赖名称与
版本可在各模块的依赖清单、锁文件和 Dockerfile 中核对。构建或重新分发包含
这些依赖的制品时，还需要保留相应依赖的许可证与归属声明。

第三方 Adapter 运行环境、下载缓存和参考仓库未包含在本源码分发中。
`adapter-runtime/1688-product-find-safe/` 中纳入版本控制的包装器与说明
由本项目编写，采用 MIT；它们不会将外部安装的工具或服务改为 MIT 授权。
