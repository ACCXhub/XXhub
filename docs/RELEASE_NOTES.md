# AutoDy v1.4.4

AutoDy v1.4.4 是 v1.4.x 产品线的最终稳定基线，聚焦修复 Windows MSI 在较慢或冷启动 Task Scheduler 环境中的安装可靠性，不改变 Dashboard、消息发送、好友管理、Portable 架构或本地数据边界。

## 修复内容

- Task Scheduler 快照命令成功且确实没有匹配任务时才返回空列表。
- 快照超时、PowerShell/Task Scheduler 查询失败或无效 JSON 现在会产生明确的读取错误，不再被误判为“三项任务均缺失”。
- 注册任务后的验证对瞬时快照读取失败执行有限次数重试和小幅退避；成功快照中的真实缺失或配置漂移仍会明确失败。
- failed fresh install 的 MSI rollback 会移除本次安装创建的 AutoDy 任务，避免程序文件回滚后留下孤儿任务。
- Repair 与同安装范围升级不会盲目清理既有任务，保留升级前的 Scheduler 状态。
- 安装器内嵌 Scheduler 修复使用 Python `-B`，避免失败回滚后留下 `.pyc` 文件。
- 原交互用户 SID、ProgramRoot、DataRoot、Scheduler 自动运行权威和 `RunLevel Limited` 语义保持不变。

## 发布资产与来源

本次热修发布冻结且已验证的 MSI，不重新构建、不替换 v1.4.3 资产。

- 资产：`AutoDy-1.4.4-x64.msi`
- 大小：`301,540,220` bytes
- SHA-256：`bea7a7e7495c0137d33463f504d2999dcef250e7df7766c90eb0dbcb4a1daa10`
- MSI 源码提交：`bb511b441b679d65685a1ef871d89da9eba349e1`

Release 标签还包含随后完成的纯文档整理提交；二进制来源仍严格为上述 `bb511b4` 提交。v1.4.4 是 MSI 聚焦热修，因此本 Release 只包含 MSI 与同名 SHA-256 sidecar；v1.4.3 Portable 未发生代码变化。

## 数据与升级

1.4.0–1.4.2 为 per-user 安装，1.4.3/1.4.4 为 per-machine 安装。旧 per-user 用户应先正常卸载旧程序，再安装 v1.4.4；`%LocalAppData%\AutoDy` 默认保留。普通卸载仍会移除程序、快捷方式和三项 AutoDy Windows 任务，但不会删除 DataRoot。
