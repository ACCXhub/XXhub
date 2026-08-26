# Changelog

所有显著变更记录在此文件中。版本状态以标签和 GitHub Release 为准；候选版本、构建产物或本地验证均不代表已发布。

## [Unreleased]

## [1.5.0] - 2026-08-26

### Changed

- 好友绑定现在把持久好友身份证明、discovery/cache 候选键和当前会话 locator 明确分离；当前抖音会话行从 React conversation model 读取 `toParticipantSecUserId` 作为好友证明，并把 conversation `id`/`shortId` 仅作为打开会话所需的当前 locator。
- 好友发现、重新关联、单项/批量加入、绑定恢复、Runner 与 Chat 统一使用同一权威解析语义；虚拟列表到底后会等待后续 lazy-loaded 内容，避免把不完整扫描误判为完整发现。
- 普通桌面启动通过真实 readiness 状态只刷新已启用续火目标，并在权威身份证明匹配时安全更新当前候选与会话 locator；targeted refresh 会立即检查当前行并复用仍新鲜的本地头像缓存，将本机 9 个目标/19 行的实测工作段由约 11.1 秒降至约 6.5 秒。“刷新当前账号资料”继续执行完整账号及好友发现。
- Dashboard 的“需要处理”只展示当前仍需行动的发送失败，并按安全补发、重新关联、账号处理或人工查看提供单一动作；好友绑定健康与今日发送结果分栏呈现，页面在普通笔记本尺寸保持紧凑。
- Dashboard 与好友管理统一显示权威解析所得当前候选的名称和本地头像；好友状态按今日失败、绑定/账号异常、待发送、健康/成功排序，统计信息置于好友状态之后。
- 文案库继续通过现有本地导入入口接收 TXT/CSV/JSON，文案包通过现有 TXT 导入创建本地自定义包；存储与导入仍由既有消息服务统一负责，不引入第二套消息系统或远程上传路径。
- 现有 `daily-greeting` 文案包成为稳定 ID 指向的默认包，文案包页可查看或更改默认选择；新配置与缺失旧字段的好友间最小/最大延迟默认均为 0 秒。

### Fixed

- 修复当前抖音会话行不再提供旧 conversation `data-*` 属性时，真实好友只能退化为头像来源、Dashboard 可显示绑定正常但 Runner/Chat 无法证明并定位同一好友的问题。
- 修复当前失败与历史运行确认、通知文件或重复补发面板同时占用“需要处理”，导致健康绑定与今日发送失败的动作含义不一致的问题。
- 修复唯一显示名匹配可能把旧 Target/弱 candidate 直接升级为权威好友 proof 的问题；自动迁移现在只接受既有权威 identity 或唯一一致的 conversation locator 连续性，名称仅保留为诊断信息。
- 修复 MSI 将 `MainFeature` 注册为 Advertised、普通维护安装虽返回成功却未实际替换 `D:\AutoDy` payload 的问题；主功能现在禁止按需公告并始终本地安装。
- Dashboard watchdog 会验证服务身份并自动恢复已确认属于 AutoDy 的异常本机服务；托盘启动、停止与 MSI 卸载路径共享更严格的 lifecycle 所有权检查。
- 好友管理取消或重新加入续火目标后，Overview 的今日任务、好友状态行与标题计数、本机资源计数会从同一当前目标集合收敛更新。
- Overview 好友状态表按内容自然展开，不再使用内部纵向滚动条。

## [1.4.4] - 2026-08-11

1.4.4 是 v1.4.x 产品线的最终稳定基线，也是针对 Windows MSI 的聚焦 Scheduler 可靠性热修。

### Fixed

- 修复 Task Scheduler 快照读取超时、PowerShell 失败或 JSON 无效时被错误折叠为“无任务”，导致正确注册的任务被误判缺失并使 MSI 回滚的问题。
- Scheduler 修复后的验证现在对瞬时读取失败执行有限重试；成功快照中的真实任务漂移仍会明确失败。
- failed fresh install 的 MSI rollback 会清理由本次安装创建的 AutoDy 任务；Repair 和同安装范围升级不会盲目删除已有任务。
- 安装器内嵌 Scheduler 修复使用 Python `-B`，避免失败回滚后留下字节码文件。

### Security

- 保持原交互用户 SID、ProgramRoot、DataRoot、Scheduler 自动运行权威和 Limited Principal 不变；提升后的安装进程不重新推断用户身份或数据目录。

## [1.4.3] - 2026-08-11

1.4.3 已作为首个 standalone MSI/Portable 稳定版本发布。

### Added

- 增加按历史日期固化的 Dashboard 成功率与连续成功天数；缺失日不进入成功率分母，但会中断连续成功。
- 增加当日安全补发入口，仅向仍失败、未解决且明确允许安全重试的目标开放。
- Windows 每日任务在当日截止时间前按安全窗口重复触发，由运行时去重和重试状态决定是否执行。
- 增加幂等 clean-source bootstrap、固定开发依赖、规范 Release 输出目录和机器可读发布 manifest。
- 增加 MSI-native 安装路径解析：登记实际程序目录，用于后续修复、升级与卸载定位。
- 增加首次安装对 D:\AutoDy 的优先选择，以及不可用时的 %LocalAppData%\Programs\AutoDy 回退。
- 增加开始菜单卸载快捷方式、安装目录向导和升级/修复目录复用。
- 增加确定性 MSI 输出与统一的 SHA-256、manifest、allowlist 验证。
- CI 在发行检查失败时保留受控隐私诊断，便于定位不合规 payload。
- 增加真正 standalone 的 Windows portable：内含固定 Python、native 扩展、Playwright/Chromium、生产前端、文案包和官方模块，并提供顶层 `AutoDy.cmd`。

### Changed

- Dashboard 移除逐次运行历史表和重试次数噪音，详细历史统一留在日志页；好友页移除单项删除，仅保留明确的全量删除入口。
- 文案包只使用随 AutoDy 提供的内置索引和文件，移除远程索引 URL、远程回退状态和对应设置。
- Scheduler 修复命令显式接收 ProgramRoot、DataRoot 和目标用户 SID，并在注册后验证任务脚本、根目录、工作目录、时间、重复窗口和 Principal。
- MSI 改为 per-machine 安装语义；提升前捕获原交互用户 SID 与 LocalAppData，首次种下且永不覆盖该用户的 config.yaml。
- 工程交付文档迁移为中文文件名，并保留仅供构建、发布和既有维护流程使用的英文兼容入口。
- 8765 不再是唯一启动端口：无关监听者占用时，在 8766–8799 中安全选择空闲端口；选中端口可持久化并复核。
- 重复启动会复用经 service identity、当前用户、路径和解释器验证的同一 AutoDy 服务。
- Dashboard、今日计划与 Windows 调度状态统一使用当前可执行目标集合；目标级最新执行结果覆盖陈旧 daily 汇总，但保留历史失败记录。
- 好友管理保留整行一键加入/取消续火、取消后立即重新加入，以及全量启用、停用和删除操作。
- MSI 构建显式使用 Release，Debug 中间 MSI 使用明显的非发布文件名。
- portable 与官方模块 ZIP 使用排序、固定时间戳和规范 LF 行尾生成可复现归档。
- MSI 与 portable 复用同一受控 runtime staging；分发标记和共享 resolver 统一解析 ProgramRoot、DataRoot、Python 与 Chromium，portable 数据固定保存在解包目录。
- 文档明确区分普通用户 MSI、standalone portable 与开发者 Source ZIP/TAR。

### Fixed

- 修复非提升 Dashboard 无法安装、修复、移除或应用 Windows 计划任务的问题；Scheduler 写操作现在只为该次操作请求 UAC，并显式保留原用户 SID、ProgramRoot 与 DataRoot。
- 修复 Task Scheduler 将同一 Principal 读回为账户名时与 SID 原始字符串比较导致的虚假漂移，以及写操作成功后页面短暂保留旧任务缓存的问题。
- 修复历史成功日依赖今天启用目标配置、配置变化会追溯改写过去统计的问题。
- 修复提升 MSI 从管理员进程 %LOCALAPPDATA% 推断数据根，导致任务 Principal 与 DataRoot 分裂的问题。
- 修复 MSI 卸载后遗留指向已删除 ProgramRoot 的 Windows 计划任务；卸载现在调用既有移除脚本删除任务，同时继续保留用户 DataRoot。
- 拒绝陈旧或失效的已登记安装路径，避免向未知旧目录写入 payload。
- 修复干净源码缺少 .venv/config.yaml 时 README 首条命令必然失败的问题。
- 修复 PowerShell 5.1 将成功 native 命令的 stderr warning 当作终止错误的问题。
- 修复同一稳定账号重新登录后未能完整恢复本地目标、计划、历史、模块设置和账号元数据的回归保护缺口。
- 修复最近历史分页可能使 Dashboard 与今日计划重新出现成功、失败和 pending 数量分歧的问题。
- 修复无好友扫描缓存时取消续火后缺少立即重新加入入口，以及 Windows 任务缺少可解析触发时间却未标记漂移的问题。
- 修复 Release 隐私验证器对本地化 MSI 快捷方式名称和已审核托盘停止 CustomAction 的误报。
- 发布脚本拒绝 obj、Debug、work、陈旧版本、错误 ProductVersion、外部 CAB 和非当前干净提交。

### Security

- Scheduler 提升边界是立即退出的受约束子进程，只分派到既有安装/移除脚本；Dashboard 与计划任务本身保持非提升/Limited，短生命周期结果文件不包含业务资料。
- 无关端口监听者不会被结束；只有托盘确认管理的服务可被停止或重启。
- 发行物继续使用 allowlist，隐私扫描排除运行时数据、认证资料、浏览器 profile、日志、备份和开发夹具。
- 发布失败诊断保留最小必要信息，不应包含真实账号、目标、消息或 Cookie。

## [1.4.1] - 2026-07-31

### Changed

- Overview 会根据稳定目标身份和后续权威成功状态，将已经解决的历史失败标记为“已解决”并移除重试主操作，同时保留历史分组数量。
- MSI 增加 Welcome、安装目录、Ready、Progress 和完成向导，支持浏览并选择自定义程序目录。
- portable 公开资产使用带版本号的文件名；Release 工作流只发布 MSI、portable 及其两个 SHA-256 文件。

### Fixed

- 自定义 MSI 安装目录会持久化，维护、修复和卸载能够继续定位实际程序路径，不再把 payload 留在自定义目录。
- MSI 生命周期验证覆盖默认与自定义安装、取消表链、修复、1.4.0 到 1.4.1 升级、卸载、数据保留和现场恢复。

## [1.4.0] - 2026-07-31

### Added

- 增加延迟安全重试、通知去重、托盘服务所有权、模块包诊断和 per-user MSI 基础能力。

## [1.3.0] - Published

### Added

- Windows 本地 Dashboard、目标、文案、计划和执行历史基础能力。
- 可选官方 Test Center 1.1.0 模块与脱敏文档截图。
