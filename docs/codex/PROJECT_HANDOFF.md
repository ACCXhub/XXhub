# AutoDy 项目交接

## 当前 Master

- Canonical repo：`ACCXhub/XXhub`，canonical branch：`main`。
- 当前可用运行时代码检查点：`2ee9c22e2059f8a78570e7e7c5925ab5949706d5`。
- 当前源码版本线：`1.5.3`；本机维护继续以源码直运行为主。
- 当前公开推荐安装基线：`v1.4.4`。

## 当前已收敛

- 发送与发送验证：可靠 post-send confirmation、当天投递证据与重复保护已恢复。
- 源码安装：locked frontend dependencies 在 build 前安装；不完整 HKCU 注册残留可安全忽略；`install.cmd` 正确传播 PowerShell 失败退出码。
- 托盘启动：模块状态快照暂时缺少 `required_autody_version` 等字段时返回未就绪，不再因 PowerShell StrictMode 产生伪启动失败弹窗。
- 旧修复分支已收敛；保留 `fix/required-autody-version-startup` 作为本次小修分支，并与 `main` 对齐。

## Release 状态

- v1.5.0/v1.5.1 失败候选标签已退役。
- v1.5.2/v1.5.3 的公开 Release 与标签已退役。
- v1.4.0、v1.4.1、v1.4.3、v1.4.4 为历史公开发布；没有证据表明它们属于不可运行资产，因此不按坏版本删除。当前推荐公开安装基线为 v1.4.4。
- `.github/workflows/release.yml` 仅保留手动诊断入口，避免重新发布已经退役的 v1.5.3。下一次正式 Release 先更新到新的版本身份。

## 稳定指针

- 项目边界与运行时根：`AGENTS.md`。
- 安装 owner：`install.cmd`、`scripts/install.ps1`。
- 托盘启动 owner：`scripts/autody-tray.ps1`。
- 发布/验证 owner：`.github/workflows/release.yml`、`scripts/build-release-from-clean-source.ps1`、`scripts/verify-release-artifacts.ps1`、`scripts/verify-msi-lifecycle.ps1`。
