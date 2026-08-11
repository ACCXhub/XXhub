# AutoDy v1.4.4

这是针对 Windows MSI 安装的聚焦可靠性热修。

- Task Scheduler 快照读取超时、PowerShell 查询失败或返回无效数据时，不再被误判为“三项任务均缺失”。
- 安装程序注册任务后的验证会对瞬时 Scheduler 读取失败执行有限重试；成功快照中的真实配置漂移仍会阻止安装。
- failed fresh install 会在 MSI rollback 时移除本次安装创建的 AutoDy 任务，避免程序文件回滚后留下孤儿任务。
- Repair 与同安装范围升级不会执行 fresh-install 清理，保留已有 Scheduler 状态、原交互用户 SID、ProgramRoot、DataRoot 和 Limited Principal 语义。

本热修不改变 Dashboard、消息发送、好友管理、Portable 架构或本地数据边界。用户数据继续仅保存在本机，卸载仍默认保留 DataRoot。
