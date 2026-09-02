# AutoDy 项目交接

## 当前检查点

- `v1.5.3` release source 是 `39e413b64d8174a7e1e71107b0f5b154f7a69779`，已推送到 `origin/main`；annotated tag `v1.5.3` 的对象为 `fa41d5a69a37ac4c959bd3ec8e2a0e85a1aadebd`，指向该 source。不要移动 tag 或替换公开资产。
- 正式 Release workflow `33629891971` 已成功：`build-accept-publish` 与 `verify-public-assets` 均通过。公开 MSI、Portable、两份 SHA-256 和 `release-manifest.json` 已发布，且已在本机重新下载并经 `scripts/verify-release-artifacts.ps1` 验证。
- 已由公开 `AutoDy-1.5.3-x64.msi` 完成本机管理员升级；已安装服务的 Python、package 与静态资源均归属 `D:\AutoDy`，版本为 `1.5.3`。三个 Scheduler task 均启用并指向该 ProgramRoot。
- 持久 DataRoot 的 `state.json`、两份投递历史、`config.yaml` 和 `messages.txt` 在升级前后及最终验收中保持哈希一致。当天 9 个启用目标均为 `pending`，成功数为 0、未完成，且没有投递 runner；本次验收未访问抖音、未输入、未发送。
- 下一个交接记录提交只更新本文档，位于 release tag 之后；它不属于不可变的 v1.5.3 release source，因此 `main` 将领先 `v1.5.3`。

## 已执行的 release 证据

- 针对修复的 focused Python suite 为 `5 passed`；完整非 `release_build` Python suite 为 `616 passed, 2 deselected`。Vitest `66 passed`、frontend build、PowerShell parser checks 与 `git diff --check` 均通过。
- Portable 与 MSI 的 release 可复现性测试均在独占 staging 条件下通过。此前共享 `output\\work` 的并发和 Chromium 文件锁只造成一次非产品脚本失败，独占重跑已完成；无需改动产品代码。
- BrowserRoot 的 headless `doctor_playwright` 启动检查通过；发布与安装验收期间均没有访问抖音、没有输入或发送。

## 稳定指针

- 项目边界、运行时根、module owners 与副作用限制：`AGENTS.md`。
- 发布/验证 owner：`.github/workflows/release.yml`、`scripts/build-release-from-clean-source.ps1`、`scripts/verify-release-artifacts.ps1`、`scripts/verify-msi-lifecycle.ps1`。
- Release/version truth：`pyproject.toml`、`frontend/package.json`、`.github/workflows/release.yml`、`CHANGELOG.md`、`docs/RELEASE_NOTES.md`。
