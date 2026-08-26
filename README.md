# AutoDy

AutoDy 是面向 Windows 的本机 Douyin 私信工作流管理台，用于管理续火目标、文案、计划、运行状态和历史。它以“不能证明安全就停止”为原则：发送前验证稳定会话身份、重复状态和页面条件，并通过全局浏览器锁避免并发冲突。

当前稳定版本为 **v1.5.0**。这是当前已验收产品状态的正式维护版本；后续通用浏览器工作流研究将在独立项目中继续，不属于 AutoDy 现有功能。

## 主要能力

- 本地 Dashboard、好友目标、文案库、文案包、计划任务、运行日志、备份迁移和设置。
- 历史成功率按历史日期自己的目标与结果事实计算，当前配置不会追溯改写过去。
- Windows 计划任务作为自动运行权威，包含每日登录健康检查、每日续火和每周健康检查。
- Dashboard 保持普通用户权限；安装、修复、移除或应用计划任务时仅为该次操作请求 UAC。
- 稳定会话身份、重复保护、确认与全局浏览器锁；身份不一致、草稿、附件或结果不确定时停止。
- MSI 与 Portable 均为 standalone 分发，内含固定 Python、依赖、Chromium、生产前端和内置资源，不要求系统 Python、Node/npm 或手动安装 Playwright Chromium。
- 本地托盘单实例与服务身份验证；8765 被无关程序占用时安全回退至 8766–8799，不结束对方进程。

## 截图

截图来自真实安装的 AutoDy v1.4.4，只读导航拍摄；账号和好友姓名均已模糊处理。

### 运行总览

![AutoDy 运行总览](docs/assets/screenshots/dashboard-overview.png)

### 好友管理

![AutoDy 好友管理](docs/assets/screenshots/friends-management.png)

### 定时任务

![AutoDy 定时任务](docs/assets/screenshots/scheduler-status.png)

## 安装

普通用户请从 [GitHub Releases](https://github.com/ACCXhub/hlhub/releases) 下载：

- `AutoDy-1.5.0-x64.msi`
- `AutoDy-1.5.0-x64.msi.sha256`

下载后先校验：

```powershell
Get-FileHash -Algorithm SHA256 '.\AutoDy-1.5.0-x64.msi'
```

结果必须与 Release 中同名 `.sha256` 文件记录的值一致。

新安装默认优先 `D:\AutoDy`；D: 不可用时回退到原交互用户的 `%LocalAppData%\Programs\AutoDy`。程序数据始终位于原交互用户的 `%LocalAppData%\AutoDy`，普通卸载会移除程序、快捷方式和三项 Windows 任务，但默认保留 DataRoot。

1.4.0–1.4.2 为 per-user 安装，1.4.3 及后续版本为 per-machine 安装。升级旧 per-user 版本时，先从 Windows 设置正常卸载旧程序，再安装 v1.5.0；不要删除 `%LocalAppData%\AutoDy`。

v1.5.0 Release 同时提供 canonical MSI 与 Portable。Portable 完整解压后从顶层 `AutoDy.cmd` 启动，程序与数据都保存在解包目录。

## 首次启动与登录

安装完成后，从桌面或开始菜单打开 AutoDy。托盘会启动并验证本机服务，然后打开 `127.0.0.1:<selected-port>` 的管理台。

首次使用时，在 AutoDy 启动的受管 Chromium 中完成登录。不要复制日常浏览器的 Cookie 或 profile。登录过期时仍应在同一受管窗口重新登录；出现身份不一致、已有草稿、附件或不确定页面状态时停止操作。

## 日常使用

1. 在“好友管理”扫描并选择需要续火的目标。
2. 在“文案库”或“文案包”准备本地内容。
3. 在“定时任务”设置每日健康检查、每日续火和每周健康检查时间。
4. 通过“总览”和“运行日志”查看当日结果、历史状态与需处理事项。

`retry_pending` 只表示系统已证明失败发生在可能发送之前；`uncertain` 表示结果无法确认，绝不会自动重试。不要通过反复点击、刷新页面或手动运行任务来覆盖不确定状态。

## 计划任务

Scheduler 是自动运行的权威。三项任务均使用原交互用户 Principal 和 `RunLevel Limited`；ProgramRoot 与 DataRoot 由安装时捕获的原用户身份显式传递，不从提升进程的环境重新推断。

Dashboard 正常以普通权限运行。只有安装、修复、移除或应用 Windows 任务时才出现一次 UAC。取消 UAC 不会改变现有任务或计划设置。

## 本地数据与隐私

AutoDy 的应用服务只监听回环地址。账号、目标、消息、计划、历史、浏览器 profile、Cookie 和备份不应进入 Git、发布包或普通诊断材料。MSI 卸载默认保留 DataRoot；Portable 数据随解包目录保存，复制该目录也会复制本地运行数据。

不要向支持人员提供完整 DataRoot、浏览器 profile、Cookie、真实消息或未经检查的日志与截图。

## 故障排查

- 管理台未打开：从托盘重新选择“打开 AutoDy 管理台”，不要猜测端口或批量结束 Python/Chromium 进程。
- 8765 被占用：允许 AutoDy 使用 8766–8799 的已验证回退端口。
- Scheduler 操作失败：确认 UAC 是否被取消，保留错误摘要；不要永久以管理员权限运行 Dashboard。
- MSI 安装/Repair 失败：保留 verbose log，核对 MSI SHA-256、磁盘空间和安装目录；不要先删除 DataRoot。
- 登录过期：只在 AutoDy 受管 Chromium 内重新登录。

完整步骤见[安装部署与用户手册](docs/软件工程/03-安装部署与用户手册.md)和[运维维护与故障排查](docs/软件工程/05-运维维护与故障排查.md)。

## 文档

- [文档总览](docs/文档总览.md)
- [需求规格](docs/软件工程/01-软件需求规格说明书.md)
- [系统设计](docs/软件工程/02-系统设计说明书.md)
- [安装与用户手册](docs/软件工程/03-安装部署与用户手册.md)
- [测试与验收](docs/软件工程/04-测试与验收报告.md)
- [运维与故障排查](docs/软件工程/05-运维维护与故障排查.md)
- [隐私与安全](docs/软件工程/06-隐私与安全设计.md)
- [v1.5.0 Release Notes](docs/RELEASE_NOTES.md)
- [变更记录](CHANGELOG.md)

## 维护状态

AutoDy v1.5.0 已进入维护状态。维护范围聚焦于阻断性可靠性、安全和安装问题，不在本仓库继续扩展 AutoDy 2.0 或 BrowserWeave 功能。

许可证见 [LICENSE](LICENSE)，安全说明见 [SECURITY.md](SECURITY.md)。
