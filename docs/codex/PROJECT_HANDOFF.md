# AutoDy 项目交接

## 当前权威状态

AutoDy 当前稳定版本为 v1.4.4，是 v1.4.x 产品线的最终基线，后续只做必要的可靠性、安全和安装维护。本仓库不继续开展 AutoDy 2.0 或 BrowserWeave 功能；相关通用浏览器工作流研究属于独立项目。

v1.4.4 Scheduler/MSI 热修由两个生产提交组成：

- `8aa49eb5e92e3b6e8555f153c3ea70d4d4bf538a`：区分快照成功空结果与读取失败、增加有限验证重试和安全 rollback 清理。
- `bb511b441b679d65685a1ef871d89da9eba349e1`：安装器内嵌 Python 使用 `-B`，避免 rollback 字节码残留。

已验证 MSI 来自 `bb511b441b679d65685a1ef871d89da9eba349e1`，文件名为 `AutoDy-1.4.4-x64.msi`，大小 `301,540,220` bytes，SHA-256 为 `bea7a7e7495c0137d33463f504d2999dcef250e7df7766c90eb0dbcb4a1daa10`。后续文档提交不改变该二进制来源。

当前 `[Unreleased]` 本地维护状态已把好友身份与会话定位收敛为两个独立概念。当前抖音 React conversation model 的 `toParticipantSecUserId` 是持久好友身份证明，conversation `id`/`shortId` 是当前会话 locator；discovery/cache 的 `candidate_id` 只负责本地候选关联。`binding_recovery.resolve_stable_binding` 是权威绑定解析入口，负责用账号范围和持久 proof 唯一匹配完整 discovery，再返回当前 locator 给 Runner 与 Chat。旧 `row_attribute` proof 仅保留兼容；没有权威 proof、账号不一致、扫描不完整或匹配不唯一时继续 fail closed。

当旧绑定因安全迁移被清空 proof、但仍保留当前 `candidate_id` 时，好友管理必须显示“重新关联”，而不能把该行误作健康的已配置状态。`binding_recovery.reassociate_stable_binding` 是唯一的显式重关联写入入口：它只接受完整且当前的 discovery 中唯一、未被其他 Target 占用的权威身份，并通过账号范围和稳定绑定解析后才持久化 identity key/source、账号范围与当前 locator；旧 name/avatar 证据不会借此成为 durable proof。历史失败不被清除，当前 action/health 在重关联后从新的绑定重新计算。

当前 Dashboard 的“需要处理”由未解决的当前 FailureDetail 生成：可安全重试时只提供目标级安全补发，绑定问题进入好友管理，账号问题进入登录，`uncertain` 只进入人工查看且不自动重试。好友表分别显示当前绑定与今日发送，健康绑定不再重复显示“绑定有效”；表格按当前行数自然展开，统计卡保持其后的既有间距。

Overview 的今日总数、好友状态行、好友状态标题与本机资源计数都从同一 enabled execution target set 派生；好友管理取消续火或重新启用后，刷新 `/api/status` 即收敛这些投影。好友状态表不再设置固定/最大高度或内部纵向滚动，按当前行数自然展开，统计卡保持其后既有间距。

普通 Dashboard 启动现在由 `/api/startup-readiness` 驱动，只对已启用续火 Target 执行 targeted refresh。结果写回同一个 `discovered_friends.json` 的 `target_refresh` 证据，不冒充完整扫描或改写完整扫描的 `scanned_at`；权威 proof 唯一匹配时可安全更新当前 candidate/locator。显式“刷新当前账号资料”仍负责完整账号资料和全好友 discovery。Dashboard 与好友管理都从 `resolve_stable_binding` 返回的当前 candidate 读取显示名称和本地头像。

旧 Target 迁移不得用唯一显示名建立权威 proof。允许的自动连续性只有同账号下已有权威 identity，或旧 candidate 与当前 candidate 唯一相同的 conversation locator；否则保留 Target 但清空新 proof、设置 revalidation guard，并要求在好友管理中显式重新关联。当前本机 10 个 legacy Target 因找不到独立于名称的连续性证据，已仅回退 `binding_identity_key`、`binding_identity_source`、`binding_account_scope`，其他用户配置保持不变。

targeted refresh 不等待完整 discovery 的初始虚拟列表稳定窗口，并只在目标头像缓存缺失或超过 7 天时重新抓取；仍会在列表底部等待真实 lazy-load 增长。本机隔离剖析以 9 个配置目标、19 行为样本，完整浏览器启动、账号校验、目标刷新和关闭由约 11.1 秒降至约 6.5 秒，其中约 4.3 秒为真实聊天页启动/加载。

当前默认文案包由 `AppConfig.default_message_pack` 的稳定 ID `daily-greeting` 表示，单 Target 的显式 pack 选择仍优先；只读预览不会为缺失 catalog 创建文件或锁。新配置及缺失旧字段的好友间最小/最大延迟默认均为 0 秒，已有显式用户值继续保留。

当前存储/映射所有权如下：

| 持久对象 | 稳定键 | 引用关系 | 运行期/当前数据与更新者 |
| --- | --- | --- | --- |
| Target | `stable_id` | `candidate_id` 仅关联当前缓存；binding 三字段引用权威好友 proof | 配置/API 显式管理 |
| 好友 binding proof | `binding_account_scope + binding_identity_source + binding_identity_key` | 归属于一个 Target | 显式关联或有 locator/identity 连续性的 discovery 更新 |
| 好友缓存项 | `candidate_id` | 反向标记 `configured_target_id` 仅供当前缓存展示 | discovery/targeted refresh 更新名称、头像和当前性 |
| Conversation locator | 当前 candidate 的 `conversation_id` | resolver 由 proof 找到 candidate 后返回 | 每次 discovery/targeted refresh 更新，不作为永久好友 proof |
| Failure/history | Target `stable_id` | 关联当日状态和运行记录 | Runner/失败存储更新；显示名不参与 join |
| 默认文案包 | pack `id` | `default_message_pack` 或 Target 显式 `message_pack` | 文案包 catalog/API 更新，内容不复制进配置 |

WiX `MainFeature` 禁止 Advertise，普通安装、升级和修复必须把主 payload 注册为 Local 并实际更新 ProgramRoot；不得把 `ADDLOCAL=MainFeature` 作为正式安装依赖。最终安装验收仍需同时证明 DataRoot 保留、三项 Scheduler 契约和桌面入口到当前安装 payload 的身份链。

文案库的 TXT/CSV/JSON 本地预览与合并、文案包的 TXT 本地导入仍复用现有 `MessagePackService` 和对应 API/UI，消息内容不通过新增远程上传系统。日常安装仍以 MSI 维护的 `D:\AutoDy` ProgramRoot 与原交互用户 `%LocalAppData%\AutoDy` DataRoot 为准；本地维护构建只能在验证源码服务后通过既有 MSI 路径替换程序资源，不能覆盖 DataRoot 或改变 Scheduler 的 Limited Principal/roots 语义。

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
