# AutoDy

AutoDy 是面向 Windows 的本机 Douyin 私信工作流管理台，用于管理续火目标、文案、计划、运行状态、历史、日志与备份。核心原则是：**不能证明安全就停止**。发送前必须验证稳定好友身份、账号范围、当前会话定位、重复状态和页面条件，并通过全局浏览器锁避免并发冲突。

当前维护环境采用**源码仓库作为本机 canonical runtime**：影响实际运行行为的源码修改完成后，直接重启当前源码实例并做最小运行验收，不需要先构建 MSI/Portable 才能让本机修改生效。

## 当前版本状态

- 当前源码版本线：**v1.5.2**。
- 当前源码 Master 已完成 daily denominator、好友扫描性能、reconciliation、发送确认 provenance、`one_for_all` 每日轮换和源码直运行收敛。
- `v1.5.0` tag 的正式 workflow 在 MSI lifecycle acceptance 阶段失败且没有 GitHub Release；该标签保留为不可变历史。
- `v1.5.1` lifecycle 失败候选保留为不可变历史。
- 当前没有正式发布的 v1.5.2 GitHub Release；在新的正式发布明确执行并完成资产验证前，最后已确认公开稳定基线仍是 **v1.4.4**。

因此需要公开稳定安装包的普通用户仍应从正式 Releases 获取已发布资产；当前项目维护和本机实际使用则以源码直运行为主。

## 主要能力

- 本地 Dashboard：总览、好友、文案、文案包、定时任务、日志、备份和设置。
- 好友 durable identity、账号范围、discovery candidate 与 conversation locator 分层；失效绑定通过显式重新关联恢复，不按昵称/头像猜测。
- 好友 discovery 使用单一串行 DOM lane 获取真实列表，可见行批量 snapshot；targeted scan 找齐配置目标即可结束，新鲜完整 snapshot 命中时无需重复扫描。
- 每日任务分母以全部 enabled 目标为准；目标暂时不可安全执行只影响本次发送资格，不会把任务提前算成完成。
- 发送成功必须来自本次真实发送边界之后的新 outgoing observation；历史同文案气泡、旧 `confirmed` 字符串、旧 `succeeded/consumed` 都不能单独证明今天已经发送。
- 当历史证据不足时 Dashboard 显示“待核实”，运行进入 `uncertain` 并安全停止；`human_verified_today` 仅保留为旧版本污染数据的一次性迁移 evidence，不伪造成自动发送确认，也不是日常流程。
- reconciliation 会以只读 live chat audit 将当日事实区分为 `confirmed_sent`、`confirmed_missing`、`unknown`；只对可证明缺失且绑定有效的目标复用正常发送/确认链，`unknown` 不自动补发，但可由“一键诊断与修复”重新 audit，reconciliation 本身不重复扫描好友。
- `one_for_all` 文案包每天持久化选择一条 canonical 文案，当天所有目标及 retry/reconciliation 复用同一条；当天完整完成后才推进既有 `MessageRotation`。
- 只读发送前自检（Preflight）：可打开会话和检查 DOM，但不输入、不准备、不发送消息。
- 多账号本地隔离：账号级目标、计划、受管 browser profile 和 runtime snapshot。
- Windows Task Scheduler 作为自动运行权威；Dashboard 非提升，Scheduler 写操作使用一次性受约束 UAC。
- 本机托盘单实例、service identity 与动态端口；8765 被无关程序占用时安全回退至 8766–8799，不结束对方进程。
- Watchdog 只恢复经验证的 AutoDy 服务：health 迟滞、graceful first、exact PID 再验证、3 次/10 分钟熔断、手动完整退出 suppression。
- standalone MSI/Portable 分发能力仍保留；正式发布时可包含固定 Python、依赖、Chromium、生产前端和内置资源。
- Release pipeline 保留可重复构建、privacy/package、MSI lifecycle、manifest、公开资产重新下载与 hash 再验证能力；只有明确进行正式发布时才使用。

## 截图

现有公开截图来自已安装 AutoDy v1.4.4 的只读导航，账号与好友信息已模糊处理。

### 运行总览

![AutoDy 运行总览](docs/assets/screenshots/dashboard-overview.png)

### 好友管理

![AutoDy 好友管理](docs/assets/screenshots/friends-management.png)

### 定时任务

![AutoDy 定时任务](docs/assets/screenshots/scheduler-status.png)

## 源码直运行（当前维护方式）

当前开发/维护环境以仓库本身作为 ProgramRoot：

```text
ProgramRoot = <repo>
Python      = <repo>\.venv\Scripts\python.exe
DataRoot    = %LOCALAPPDATA%\AutoDy
```

源码模式下现有 `AutoDy.cmd` / `scripts\start-dashboard.vbs` 使用 canonical DataRoot；本机 Scheduler 也应指向同一仓库脚本和工作目录，避免安装版与源码版同时成为 active runtime。

一次影响运行行为的修改完成后，正常收口是：

```text
修改源码 → focused validation → 重启源码实例 → 核验 service identity / 127.0.0.1:8765 → 最小实际运行验收
```

文档、测试等不影响 runtime 的修改不需要无意义重启服务。MSI/Portable 只在明确需要正式分发时构建。

## 下载与安装

正式下载入口：<https://github.com/ACCXhub/XXhub/releases>

下载后先核对同一 Release 提供的 SHA-256 sidecar。例如：

```powershell
Get-FileHash -Algorithm SHA256 '.\AutoDy-<version>-x64.msi'
```

已确认 v1.4.4 MSI SHA-256：

```text
bea7a7e7495c0137d33463f504d2999dcef250e7df7766c90eb0dbcb4a1daa10
```

只有未来正式 Release 页面真正发布新的 MSI/Portable/checksum/manifest 并完成发布验证后，才把对应资产视为正式升级版本。

既有 MSI 新安装默认优先 `D:\AutoDy`；D: 不可用时回退到原交互用户 `%LocalAppData%\Programs\AutoDy`。用户数据位于原交互用户 `%LocalAppData%\AutoDy`，普通卸载删除程序、快捷方式和三项 Windows Tasks，但默认保留 DataRoot。

详细步骤见 [安装部署与用户手册](docs/软件工程/10-安装部署与用户手册.md)。

## 首次启动与登录

源码维护环境从仓库现有启动入口启动 AutoDy；正式安装版则从桌面/开始菜单启动。托盘会发现或启动经验证的本机服务，并打开 `127.0.0.1:<selected-port>` Dashboard。

首次登录只在 AutoDy 受管 Chromium 中完成，不复制日常浏览器 Cookie/profile。登录过期时在受管窗口重新登录；退出/切换账号后旧好友绑定需要重新通过账号范围与权威 identity 证明。

## 日常使用

1. 在“好友管理”扫描并加入/重新关联续火目标。
2. 在“文案库/文案包”维护本地内容和默认/目标级 pack。
3. 在“定时任务”设置每日健康检查、每日续火和每周健康检查。
4. 用“总览/运行日志”查看今日结果、历史和需处理事项。
5. “待核实”表示当前证据不足以证明今天已发送或未发送；此时不会自动补发。
6. `uncertain` 时使用“一键诊断与修复”重新执行只读 live audit；确认未发送的目标才会恢复到原有安全发送链，确认已发送或无法确认的目标不会盲目重发。人工核对仅用于处理旧版本污染数据的一次性迁移，不是日常流程。

关闭浏览器窗口不等于停止 Scheduler。显式“完整退出”才会抑制 watchdog 同日自动恢复服务。

## 本地数据与隐私

AutoDy 不要求把账号、目标、消息、Cookie、browser profile、日志或备份上传到 AutoDy 自有服务器。普通诊断/发布包应排除这些运行资料。

不要向支持人员发送完整 DataRoot、Cookie、browser profile、真实消息或未经审查的日志/截图。备份和日志中心提供受控导出边界。

## 故障排查

- Dashboard 未打开：从当前 canonical 启动入口重新打开，先确认 service identity，不按进程名批量结束 Python/Chromium。
- 8765 被占：允许 AutoDy 使用 8766–8799 的已验证回退端口。
- 好友要求重新关联：完整 discovery 后在 Friends 使用显式 relink，不按昵称直接覆盖绑定。
- `uncertain` / “待核实”：使用“一键诊断与修复”重新进行只读 live audit；无法自动确认时保留现场，不用旧 bare confirmation 猜测成功，也不重复发送覆盖结果。
- Scheduler 失败：确认 UAC、snapshot read error/drift、原用户 Principal、ProgramRoot 与 DataRoot。
- 源码维护环境出现“改了代码但界面没变化”：先确认当前服务确实来自仓库 `.venv` 和 `src\autody`，再重启 canonical runtime。
- MSI/Repair 失败：仅在使用正式分发版时校验来源/hash、保存 verbose log，不删除 DataRoot。

完整排障见 [运维维护与故障排查](docs/软件工程/11-运维维护与故障排查.md)。

## 软件工程文档

完整文档链入口：**[AutoDy 软件工程文档总览](docs/软件工程/00-文档总览.md)**。

- [01 可行性分析与项目概述](docs/软件工程/01-可行性分析与项目概述.md)
- [02 软件开发计划](docs/软件工程/02-软件开发计划.md)
- [03 软件需求规格说明书](docs/软件工程/03-软件需求规格说明书.md)
- [04 系统概要设计说明书](docs/软件工程/04-系统概要设计说明书.md)
- [05 详细设计说明书](docs/软件工程/05-详细设计说明书.md)
- [06 接口设计说明书](docs/软件工程/06-接口设计说明书.md)
- [07 数据字典与数据存储设计](docs/软件工程/07-数据字典与数据存储设计.md)
- [08 测试计划与测试用例说明](docs/软件工程/08-测试计划与测试用例说明.md)
- [09 测试与验收报告](docs/软件工程/09-测试与验收报告.md)
- [10 安装部署与用户手册](docs/软件工程/10-安装部署与用户手册.md)
- [11 运维维护与故障排查](docs/软件工程/11-运维维护与故障排查.md)
- [12 隐私与安全设计](docs/软件工程/12-隐私与安全设计.md)
- [13 项目开发总结报告](docs/软件工程/13-项目开发总结报告.md)

版本差异见 [CHANGELOG](CHANGELOG.md)，候选发布正文见 [RELEASE_NOTES](docs/RELEASE_NOTES.md)，安全说明见 [SECURITY](SECURITY.md)。

## 维护状态

AutoDy 当前以源码直运行作为本机 canonical runtime，优先维护发送真值、安全恢复、好友发现性能、Scheduler 和 DataRoot 连续性。影响实际行为的修改在源码实例上完成最小运行验收后即可使用；只有明确需要公开分发时才进入 MSI/Portable 与正式 Release 流程。

许可证见 [LICENSE](LICENSE)，第三方说明见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。
