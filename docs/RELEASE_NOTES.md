# AutoDy v1.5.0

AutoDy v1.5.0 发布当前已验收的 Windows 本机产品状态，重点提升 Dashboard 自动恢复、服务生命周期安全、好友绑定稳定性，以及 Friends 与 Overview 的状态一致性。既有发送前身份验证、重复保护、全局浏览器锁和 fail-closed 边界保持不变。

## 主要改进

- Dashboard watchdog 验证本机服务身份，并仅对确认属于 AutoDy 的异常服务执行自动恢复。
- 托盘启动、服务停止、升级与卸载使用更严格的一致所有权检查，避免操作无关进程或错误安装目录。
- 好友绑定将持久身份证明、discovery 候选键和当前会话 locator 分离；失效绑定通过显式“重新关联”恢复，不使用名称建立权威证明。
- 好友管理取消或重新加入续火目标后，Overview 的今日任务、好友状态行与标题计数、本机资源计数从同一当前目标集合收敛更新。
- Overview 好友状态表按内容自然展开，当前好友行无需在面板内滚动查看。
- Dashboard 的待处理动作、当前绑定与今日发送结果更清晰，并保留身份不明确或发送结果不确定时停止的安全语义。

## 安装与升级

- 正式安装器：`AutoDy-1.5.0-x64.msi`
- 校验文件：`AutoDy-1.5.0-x64.msi.sha256`
- Portable：`AutoDy-Windows-Portable-1.5.0.zip`

1.4.3 及后续版本均为 per-machine 安装，可通过正常 MSI 升级路径从 v1.4.4 升级到 v1.5.0。1.4.0–1.4.2 的 per-user 安装应先从 Windows 设置正常卸载旧程序，再安装 v1.5.0。普通卸载和升级继续保留原交互用户的 `%LocalAppData%\AutoDy` DataRoot；不要手动删除该目录。

下载后应以 Release 中同名 `.sha256` 文件核对 SHA-256。MSI 未声明代码签名，Windows 可能显示信任提示。
