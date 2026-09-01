# AutoDy 项目交接

## 当前检查点

- `v1.5.2` release source 是 `940bac8e8299d5a40338b32a58fae16fd1af3410`，已推送到 `origin/main`；annotated tag `v1.5.2` 的对象为 `bcb598c80edbfacc8bcda032230d6a4bd64dfdf8`，指向该 source。不要移动 tag。
- 正式 Release workflow `33543897478` 已成功：clean-source build/accept、canonical guarded publish 与 public asset reverify 都通过；公开 MSI、Portable、两份 SHA-256 和 `release-manifest.json` 已发布。
- 公共资产随后在本机重新下载并经 `scripts/verify-release-artifacts.ps1` 通过。该 verifier 已收敛 initial config seed 为 UTF-8 内容比较并规范化换行，避免工作树 CRLF/LF 策略造成误报；它不放宽路径、隐私或 payload 检查。
- 仍未完成的唯一 release acceptance 是本机已安装升级：`D:\AutoDy` 仍是 1.4.4，尝试从公开 v1.5.2 MSI 升级因未提升上下文在 `RemoveExistingProducts` 得到 Error 1730/1603 回滚。DataRoot 的 `config.yaml` SHA-256 未变。必须在管理员上下文重跑同一公开 MSI 的升级，再做 identity-aware endpoint/Dashboard 验收。
- 8765 当前是源码模式的 AutoDy（解释器与 package 在 canonical repo），不是 `D:\AutoDy`；不得为安装验收停止它。未发生真实抖音访问、输入或发送。

## 已执行的 release 证据

- Phase A focused Python integration：`337 passed`；覆盖 runner/chat、friend discovery、P0 evidence、P1 audit/classification、CLI/API、runtime/message packs、安装脚本。RuntimeContext/message-pack 的旧失败均为过时测试，当前默认 pack 是 `daily-greeting`。
- exact source pre-tag gate：clean-source preflight、`614 passed, 2 deselected` 的非 `release_build` Python suite、Vitest `66 passed`、frontend build、PowerShell 解析和 `git diff --check` 全部通过；`release_source_sha == validated_source_sha`。
- BrowserRoot 已有 Chromium，headless `doctor_playwright` 启动检查通过；没有访问抖音、没有输入或发送。

## 继续本机验收的顺序

1. 以管理员上下文运行公开 `AutoDy-1.5.2-x64.msi` 的升级，并保留 verbose MSI log；不能以复制文件代替 MSI。
2. 先确认 8765 的 listener 与 `/api/service-identity` 是不是 `D:\AutoDy`；源码实例不是 installed owner，不能终止。仅在 identity 完全匹配后控制 installed instance。
3. 验证 installed version/source identity、service health、canonical endpoint、Dashboard/status 与 DataRoot 保全；不发送真实抖音消息。

## 稳定指针

- 项目边界、运行时根、module owners 与副作用限制：`AGENTS.md`。
- 发布/验证 owner：`.github/workflows/release.yml`、`scripts/build-release-from-clean-source.ps1`、`scripts/verify-release-artifacts.ps1`、`scripts/verify-msi-lifecycle.ps1`。
- Release/version truth：`pyproject.toml`、`frontend/package.json`、`.github/workflows/release.yml`、`CHANGELOG.md`、`docs/RELEASE_NOTES.md`。
