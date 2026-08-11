# AutoDy 项目交接

## 当前权威状态

AutoDy 当前稳定版本为 v1.4.4，是 v1.4.x 产品线的最终基线，后续只做必要的可靠性、安全和安装维护。本仓库不继续开展 AutoDy 2.0 或 BrowserWeave 功能；相关通用浏览器工作流研究属于独立项目。

v1.4.4 Scheduler/MSI 热修由两个生产提交组成：

- `8aa49eb5e92e3b6e8555f153c3ea70d4d4bf538a`：区分快照成功空结果与读取失败、增加有限验证重试和安全 rollback 清理。
- `bb511b441b679d65685a1ef871d89da9eba349e1`：安装器内嵌 Python 使用 `-B`，避免 rollback 字节码残留。

已验证 MSI 来自 `bb511b441b679d65685a1ef871d89da9eba349e1`，文件名为 `AutoDy-1.4.4-x64.msi`，大小 `301,540,220` bytes，SHA-256 为 `bea7a7e7495c0137d33463f504d2999dcef250e7df7766c90eb0dbcb4a1daa10`。后续文档提交不改变该二进制来源。

## 仓库结构

- `src/autody`：FastAPI、本地配置、状态、历史、Scheduler、浏览器安全和模块。
- `frontend`：React/Vite Dashboard。
- `scripts`：bootstrap、托盘、Scheduler、构建与验证。
- `packaging/wix`：WiX MSI 定义。
- `tests`：领域、API、Windows 脚本、安装器和发布契约测试。
- `docs/软件工程`：当前中文工程文档。
- `docs/archive`：完成版本的历史计划、设计和发布说明。

`output`、`data`、`.venv`、`node_modules`、日志、缓存、模块注册表和浏览器资料属于运行/构建边界，不得提交，也不得在不明确内容与路径时删除。

## v1.4.4 热修语义

`windows_task_rows()` 只有在 Scheduler 查询成功且无匹配任务时才返回空列表。超时、PowerShell/Task Scheduler 错误和无效 JSON 会产生明确的快照读取错误。Scheduler repair 对注册后瞬时读取失败执行有限重试；一旦取得成功快照，真实缺失或漂移仍会失败。

fresh install 失败时，MSI rollback 只移除本次安装创建的三项 AutoDy 任务。Repair 与同安装范围升级不执行该 fresh-install 清理，不会盲目删除已有任务。原交互用户 SID、ProgramRoot、DataRoot 和 Limited Principal 继续显式跨越提升边界；Scheduler 仍是自动运行权威。

## 长期产品边界

- MSI 程序目录与原交互用户 `%LocalAppData%\AutoDy` DataRoot 分离；普通卸载保留 DataRoot。
- Portable 程序与数据位于解包目录，启动不依赖系统 Python、Node/npm 或在线恢复依赖。
- 计划任务始终使用原交互用户 Principal 和 `RunLevel Limited`。
- Dashboard 保持非提升；Scheduler 写操作只使用一次性受约束 UAC 子进程。
- 发送前必须保留稳定身份、重复保护、确认和全局浏览器锁；`uncertain` 不自动重试。
- 不按端口或进程名结束未知进程，不读取或导出真实账号、消息、Cookie、profile、日志或备份。

## 接手顺序

1. 运行 `git status --short`、`git log -8 --oneline --decorate` 和 `git remote -v`，保留用户现有工作。
2. 读取 `AGENTS.md`、[文档总览](../文档总览.md)和与任务直接相关的文件。
3. 涉及本机服务时，先验证 service identity、用户、ProgramRoot、DataRoot 和解释器；端口或 PID 本身不是归属证明。
4. 涉及真实浏览器时优先夹具或只读路径，绝不触碰真实消息编辑器。
5. 按改动范围选择 focused tests；发布变更再执行对应发行和隐私门禁。
6. 提交前审阅差异并运行 `git diff --check`；不得 force-push、移动既有标签或替换历史 Release 资产。

## 文档维护

公开用法写入 README，当前版本差异写入 CHANGELOG/RELEASE_NOTES，当前工程事实写入六份软件工程文档。完成后的设计与计划移入 `docs/archive`，不在根目录保留一次性工作台文件。不要复制本文件形成第二份当前交接；此路径是代理维护状态的唯一权威。
