# AutoDy v1.5.1

AutoDy v1.5.1 发布当前已接受的 Windows 本机产品状态，重点提升 Dashboard 自适应布局与自动恢复、服务生命周期安全、好友绑定稳定性，以及 Friends 与 Overview 的状态一致性。既有发送前身份验证、重复保护、全局浏览器锁和 fail-closed 边界保持不变。

## 主要改进

- Dashboard watchdog 验证本机服务身份，并仅对确认属于 AutoDy 的异常服务执行有界自动恢复：health 迟滞、graceful first、exact PID 再验证、3 次/10 分钟熔断和手动完整退出 suppression。
- 托盘启动、服务停止、升级与卸载使用一致 ownership 检查，避免操作无关进程或错误安装目录。
- 好友绑定将 durable friend proof、discovery candidate 和当前 conversation locator 分离；失效绑定通过显式“重新关联”恢复，不使用名称/头像建立权威证明。
- 多账号资料按本地 profile 隔离，切换时保存/恢复账号级 Target、计划、browser profile 和 runtime snapshot；账号写操作与受管 browser action 互斥。
- 好友管理取消或重新加入续火目标后，Overview 的今日任务、好友状态行与标题计数、本机资源计数从同一 executable target set 收敛更新。
- Dashboard“好友状态”使用“需要处理”和底部统计之间的剩余视口高度；窗口纵向调整时自然伸缩，好友行超出可用空间后仅表体内部滚动，表头保持粘性。
- Dashboard 的待处理动作、当前绑定与今日发送结果保持单一 owner，并保留身份不明确或发送结果不确定时停止的安全语义。
- 普通 CI 与正式 Release validation 已分层：日常 push/PR 不再重复构建大型 MSI/Portable；重型 reproducibility、lifecycle 和公开资产校验只在 Release 执行。
- 软件工程文档补齐可行性、开发计划、概要/详细设计、接口、数据字典、测试计划/报告、安装/运维、安全与项目总结，并纠正候选版本与正式 Release 的状态混淆。

## 安装与升级

目标正式资产：

- `AutoDy-1.5.1-x64.msi`
- `AutoDy-1.5.1-x64.msi.sha256`
- `AutoDy-Windows-Portable-1.5.1.zip`
- `AutoDy-Windows-Portable-1.5.1.zip.sha256`
- `release-manifest.json`

**这些文件只有在 GitHub `ACCXhub/XXhub` v1.5.1 Release 页面正式出现并通过 public asset verification 后才属于公开发布件。CI 中间产物和失败 run 生成的 runner 候选不得当作正式下载包。**

v1.4.3 及后续版本均为 per-machine 安装；1.4.0–1.4.2 是 per-user。跨 scope 迁移应先正常移除旧程序登记/ProgramRoot并保留 `%LocalAppData%\AutoDy`，再安装当前正式 MSI。普通卸载和升级继续保留原交互用户 DataRoot。

`v1.5.0` 标签的正式 workflow 曾在 lifecycle 阶段失败且未创建 Release；该标签不移动、不发布资产。本次 `v1.5.1` 从最后已确认稳定的 v1.4.4 MSI 执行升级生命周期验收。

下载后应以同一 Release 的 `.sha256` 和 `release-manifest.json` 核对文件。MSI 当前未声明代码签名，Windows/组织策略可能显示信任提示。
