# AutoDy v1.5.4

AutoDy v1.5.4 将当前已确认可用的发送与发送验证修复、源码安装修复和托盘启动修复收敛为新的公开发布身份，并新增面向普通用户的 EXE 安装入口。

## 安装

普通用户优先下载 `AutoDy-Setup-1.5.4.exe`。该 Setup EXE 使用 AutoDy 的 canonical MSI 作为安装 payload，因此既有升级、Repair、rollback、Scheduler 和 DataRoot 生命周期继续由 Windows Installer 管理。

安装后 `D:\AutoDy`（或安装时选定的程序目录）包含 `Uninstall AutoDy.exe`，开始菜单也提供“卸载 AutoDy”。卸载默认保留 `%LOCALAPPDATA%\AutoDy` 用户数据；只有用户明确选择时才删除本地数据。

## 发布资产

- `AutoDy-Setup-1.5.4.exe`
- `AutoDy-Setup-1.5.4.exe.sha256`
- `AutoDy-1.5.4-x64.msi`
- `AutoDy-1.5.4-x64.msi.sha256`
- `AutoDy-Windows-Portable-1.5.4.zip`
- `AutoDy-Windows-Portable-1.5.4.zip.sha256`
- `release-manifest.json`

## 修复

- 保留当前可靠 post-send confirmation、当天投递证据和重复保护边界。
- 修复模块元数据暂未就绪时的伪“AutoDy 启动失败”提示。
- 源码安装按 `package-lock.json` 安装前端依赖后构建，可安全处理不完整 HKCU 注册残留，并正确传播安装失败退出码。
