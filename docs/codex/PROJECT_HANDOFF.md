# AutoDy 项目交接

## 当前 Master

- Canonical repo：`ACCXhub/XXhub`，canonical branch：`main`。
- 当前可用运行时代码检查点：`2ee9c22e2059f8a78570e7e7c5925ab5949706d5`；1.5.4 在其上收敛安装与发布层。
- 当前源码/发布版本线：`1.5.4`；本机维护继续以源码直运行为主。
- 上一公开稳定升级基线：`v1.4.4`。

## 当前已收敛

- 发送与发送验证：可靠 post-send confirmation、当天投递证据与重复保护保持 fail-closed。
- 源码安装：locked frontend dependencies 在 build 前安装；不完整 HKCU 注册残留可安全忽略；`install.cmd` 正确传播 PowerShell 失败退出码。
- 托盘启动：模块状态快照暂缺 `required_autody_version` 等字段时返回未就绪，不再产生伪启动失败弹窗。
- 1.5.4 Windows 分发：普通用户主入口为 `AutoDy-Setup-1.5.4.exe`，底层 canonical MSI 负责升级、Repair、rollback 与卸载；安装目录及开始菜单提供 `Uninstall AutoDy.exe`，普通卸载默认保留 `%LOCALAPPDATA%\AutoDy`。

## Release 状态

- v1.5.0/v1.5.1 失败候选以及 v1.5.2/v1.5.3 退役发布身份均已移除。
- v1.4.4 保留为上一稳定升级基线。
- v1.5.4 正式资产集合：Setup EXE、MSI、Portable、三份 SHA-256 sidecar 与 `release-manifest.json`。
- `.github/workflows/release.yml` 对 `v1.5.4` 执行 clean-source build、隐私/package 验证、发布以及公开资产回下载复核。

## 稳定指针

- 项目边界与运行时根：`AGENTS.md`。
- 安装 owner：`install.cmd`、`scripts/install.ps1`。
- 托盘启动 owner：`scripts/autody-tray.ps1`。
- Windows 分发 owner：`packaging/wix/`、`packaging/uninstall/`、`scripts/build-msi.ps1`、`scripts/build-setup-exe.ps1`。
- 发布/验证 owner：`.github/workflows/release.yml`、`scripts/build-release-from-clean-source.ps1`、`scripts/verify-release-artifacts.ps1`、`scripts/verify-msi-lifecycle.ps1`。
