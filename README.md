# AutoDy

AutoDy 是面向 Windows 的本机 Douyin 私信工作流管理台，用于管理续火目标、文案、计划、运行状态、历史、日志与备份。发送链保持 fail-closed：只有好友身份、账号范围、会话定位和本次发送证据能够被可靠确认时，系统才继续自动操作。

## 当前版本

- 当前源码/发布版本线：**1.5.4**。
- Canonical branch：`main`。
- 当前运行时修复基线包含发送确认、源码安装与托盘启动元数据容错；1.5.4 在此基础上收敛 Windows 分发与卸载体验。
- **v1.4.4** 保留为上一稳定升级基线；v1.5.0/v1.5.1 失败候选以及 v1.5.2/v1.5.3 退役发布身份均已移除。

## 下载与安装

正式下载入口：<https://github.com/ACCXhub/XXhub/releases>

普通用户优先下载：

```text
AutoDy-Setup-1.5.4.exe
```

Setup EXE 使用同版本 canonical MSI 作为安装 payload，因此升级、Repair、rollback 和卸载生命周期仍由 Windows Installer 管理。Release 同时提供：

```text
AutoDy-Setup-1.5.4.exe
AutoDy-Setup-1.5.4.exe.sha256
AutoDy-1.5.4-x64.msi
AutoDy-1.5.4-x64.msi.sha256
AutoDy-Windows-Portable-1.5.4.zip
AutoDy-Windows-Portable-1.5.4.zip.sha256
release-manifest.json
```

下载后可用 PowerShell 校验 SHA-256：

```powershell
Get-FileHash -Algorithm SHA256 '.\AutoDy-Setup-1.5.4.exe'
```

结果应与同一 Release 中对应 `.sha256` 文件一致。

### 安装位置

新安装默认优先：

```text
D:\AutoDy
```

D: 不可用时回退到：

```text
%LOCALAPPDATA%\Programs\AutoDy
```

用户数据独立保存在：

```text
%LOCALAPPDATA%\AutoDy
```

## 卸载

1.5.4 安装后提供三个等价入口：

- 安装目录中的 `Uninstall AutoDy.exe`；
- 开始菜单“卸载 AutoDy”；
- Windows“设置 → 应用”中的 AutoDy 卸载项。

普通卸载会移除程序文件、快捷方式、安装注册和 AutoDy Windows Tasks，**默认保留 `%LOCALAPPDATA%\AutoDy`**。`Uninstall AutoDy.exe` 会明确询问是否同时删除用户数据；只有用户选择删除时才清理 DataRoot。

## 主要能力

- 本地 Dashboard：总览、好友、文案、文案包、定时任务、日志、备份和设置。
- 好友 durable identity、账号范围、discovery candidate 与 conversation locator 分层；失效绑定通过显式重新关联恢复。
- 好友 discovery 使用单一串行 DOM lane 获取真实列表；targeted scan 找齐配置目标即可结束。
- 每日任务分母使用全部 enabled 目标；暂时不可安全执行只影响本次发送资格，不会提前把当天任务算作完成。
- 所有发送入口共用 today-delivery pipeline：可靠本地 `SENT` 直接跳过；其余目标只读 audit，`MISSING` 才进入既有安全发送/确认链，`UNKNOWN` 停止发送。
- `one_for_all` 文案包当天持久化同一 canonical 文案，retry/reconciliation 复用同一选择。
- 多账号本地隔离：账号级目标、计划、受管 browser profile 与 runtime snapshot。
- Windows Task Scheduler 作为自动运行权威；Dashboard 常态非提升，Scheduler 写操作使用一次性受约束 UAC。
- 托盘单实例、service identity 与动态端口；8765 被无关程序占用时安全回退到 8766–8799。
- Watchdog 只恢复经过 AutoDy service identity 验证的服务，带 health 迟滞与恢复熔断。

## 源码维护

当前开发/维护环境仍以源码仓库作为 canonical runtime：

```text
ProgramRoot = <repo>
Python      = <repo>\.venv\Scripts\python.exe
DataRoot    = %LOCALAPPDATA%\AutoDy
```

影响运行行为的修改完成后，按以下路径收口：

```text
修改源码 → focused validation → 重启源码实例 → 核验 service identity → 最小实际运行验收
```

MSI/Setup EXE/Portable 只在明确进行 Windows 分发或正式 Release 时构建。

## 日常使用

1. 在“好友管理”扫描并加入或重新关联续火目标。
2. 在“文案库 / 文案包”维护本地文案和默认/目标级 pack。
3. 在“定时任务”配置每日续火、健康检查等任务。
4. 在“总览 / 运行日志”查看当天结果和需处理事项。
5. 出现“待核实”或 `uncertain` 时，使用现有只读诊断链重新核实；没有可靠证据时系统不会盲目补发。

首次 Douyin 登录只在 AutoDy 受管 Chromium 中完成，不复制日常浏览器 Cookie/profile。

## 文档

完整软件工程文档入口：**[AutoDy 软件工程文档总览](docs/软件工程/00-文档总览.md)**。

常用文档：

- [安装部署与用户手册](docs/软件工程/10-安装部署与用户手册.md)
- [运维维护与故障排查](docs/软件工程/11-运维维护与故障排查.md)
- [隐私与安全设计](docs/软件工程/12-隐私与安全设计.md)
- [CHANGELOG](CHANGELOG.md)
- [Release Notes](docs/RELEASE_NOTES.md)
- [SECURITY](SECURITY.md)
