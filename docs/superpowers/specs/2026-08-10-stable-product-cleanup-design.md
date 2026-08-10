# AutoDy 1.4.3 稳定产品清理设计

## 目标

在不改变发送安全边界、不改写历史事实、不进入 MSI/Portable 体积或便携性优化的前提下，完成 AutoDy 最后一轮产品与 UI 清理，并把验证通过的源码冻结为 1.4.3 后提交、推送到 `main`。

## 范围与非目标

本轮包含 Friends 单项删除入口清理、Dashboard 信息层级简化、按日成功率、内置文案包权威化、安装时自动修复 Scheduler、真实安装版验收、版本更新、提交和推送。

本轮不创建标签、GitHub Release、PR，不发布 MSI/Portable，不优化安装包大小、嵌入式运行时、Chromium 或跨机器便携性，也不调整真实消息发送安全门。由于本轮会改变 WiX 安装语义，冻结源码前必须完成一次本地 1.4.3 MSI 构建和验证；该产物只作为本地证据，不上传、不发布。

## Friends 管理

启用目标行和已配置但停用的候选卡片不再显示单项目标删除图标，也不再保留相应的单项确认与删除调用。点击整行继续承担启用/停用续火的主要交互。

顶部“全部删除”保留，作为明确的批量数据管理操作。其确认语义和后端批量删除安全边界不变。

`needs_reassociation` 候选的“重新关联”和“忽略此缓存”操作保持不变；本轮不得通过昵称或头像自动猜测绑定。

## Dashboard 信息架构

Dashboard 只展示当前产品健康：今日最终完成情况、当前成功目标数、连续成功天数、近 7 天按日成功率、近 30 天按日成功率、当前需要处理的问题和好友当前状态。

“结构化运行记录”表格、逐次失败展开和 7 天重试次数从 Dashboard 移除。底层 `TaskRunRecord`、历史文件与日志内容保持不变，详细尝试、恢复和失败继续由运行日志及历史 API 承载。

仅当今天存在尚未成功且通过现有安全门判定可重试的目标时，Dashboard 显示“补发”操作，并同时显示固定说明“仅补发今日尚未成功且可安全重试的目标”。按钮只能收到已有 `safe_retry_available` / `retry_action_available` 过滤后的目标 ID；已成功目标和不确定发送不得进入调用。

## 按日成功统计

日统计由 `daily_status.py` 的权威投影层提供，不在 Dashboard、历史存储或前端另建状态算法。历史日期只能使用属于该日期的 `state.daily[day]` 和该日期 `TaskRunRecord`；不得把今天的启用目标或当前绑定投影到过去，当前配置变化不得追溯改变历史成功日。

候选日期是统计窗口内存在 daily-send 历史记录或日状态事实的日期。每个候选日期只贡献一个分母单位：

- 当日持久事实表明当时要求的目标已全部完成（`daily.consumed`），或该日运行记录具有 `completed`、`already_done`、`recovered` 等终态成功结果时，该日成功。
- `confirmed` 与 `retry_confirmed` 都是终态成功；安全恢复后全部目标成功时，该日成功。
- 中间失败、重试次数和执行次数不进入成功率分母。
- 有执行事实但最终仍有 `failed` 或 `pending` 的日期不成功。
- 没有执行或日状态事实的日期不进入分母，避免计划时间之前把当天计为失败。

连续成功天数使用同一日最终状态，从今天（若今天已有事实）或昨天开始按连续自然日倒推。缺失的自然日即使不进入成功率分母，也必须中断连续成功天数。历史记录保持只追加，不为统计结果回写或改写。

## 内置文案包

内置 `message-packs/index.json` 与同目录文本文件成为唯一正常文案包来源。

删除 `message_pack_index_url` 的配置模型、API 输入输出、前端类型和设置项；删除远程索引下载、远程文案下载、回退警告及 Dashboard `remote_library` issue。现有配置中的旧字段由配置模型忽略，并在下一次正常保存时自然消失，不专门迁移或修改用户数据。

文案包列表、预览和导入仍使用同一 `MessagePackService`，但只读取并校验安装内置文件。内置索引不存在、越界或无效时继续明确失败，不用网络来源兜底。

## 安装器与 Scheduler

MSI 安装和升级请求一次 Windows UAC，安装后的 AutoDy 服务、浏览器自动化和计划任务仍以发起安装的原始交互用户与 `RunLevel Limited` 运行，不要求日常永久管理员权限。

安装器必须在切换到提升执行上下文之前捕获原始交互用户的 SID 和 `LocalAppDataFolder`，并把二者作为显式安装属性/延迟动作数据传入任务修复。提升后的进程不得从自己的 `%LOCALAPPDATA%`、用户 profile 或 `WindowsIdentity.GetCurrent()` 推断目标用户。任务 Principal 使用捕获的原始用户 SID；DataRoot 使用捕获的原始用户 LocalAppData 下的 `AutoDy`，从而保证 ProgramRoot、Task Principal、注册信息和 `%LocalAppData%\AutoDy` 不会再次分裂。

首次安装必须在该显式 DataRoot 中以 NeverOverwrite 语义建立缺失的 `config.yaml`，再执行任务修复；升级和 Repair 不得覆盖已有配置。

任务注册继续只有一条写路径：WiX 安装动作调用 `autody.cli repair-scheduler`，CLI 调用 `SchedulerService.repair()`，最终由 `scripts/install-task.ps1` 注册任务。不得在 WiX 或其他脚本复制一套 Task Scheduler 定义。

安装动作继续使用 `Return="check"`；提升后的任务修复动作使用显式原始用户 SID 和 DataRoot，任务修复失败必须使安装/升级失败。修复后 CLI 使用现有 `windows_task_rows()` 与 `scheduler_status_rows()` 核对三个任务的存在/启用状态、动作脚本、ProgramRoot、DataRoot、WorkingDirectory、每日/每周时间、DailySpark 重复间隔与恢复持续时间。任一 drift 均返回失败，防止静默半安装。

WiX 安装上下文调整为需要提升的机器级安装，但用户数据继续位于 `%LocalAppData%\AutoDy`，任务主体继续绑定发起安装的交互用户。跨版本升级、数据保留和卸载行为只做维持本轮安装语义所需的调整；更广的便携性和包装重构留到下一阶段。

## 真实验收

前端构建后只同步确认过归属的 `D:\AutoDy` 安装版文件，并通过受管托盘路径重启服务。验收不得触发真实发送、重试或编辑器输入。

真实 UI/API 验收包括：

- Friends 无目标级垃圾桶，重新关联控件仍完整。
- Dashboard 无逐次运行表；日级统计、条件式“补发”和说明符合设计。
- `remote_library` 不再出现在“需要处理”或设置页。
- Logs 仍保留详细历史错误与恢复事实。
- Scheduler 页面、API 与真实 Windows 任务一致且无 drift。
- 浏览器控制台无相关错误，常用桌面宽度无横向溢出。

## 测试与稳定版本

测试策略按风险分层。历史日统计、缺失日期中断连续成功、原始用户/DataRoot 传递和 Scheduler 安装后验证属于边界密集逻辑，先写失败测试再实现。删除垃圾桶、精简 Dashboard、改文案和移除明显死配置等直接产品清理先修改生产代码，再补聚焦回归。聚焦覆盖 Friends、Dashboard、statistics/daily status、message packs/config/web API、Scheduler CLI/脚本、WiX 安装语义。

最终运行项目要求的完整 Python 测试、前端测试与生产构建、doctor、PowerShell 解析和 `git diff --check`。完成一次本地 1.4.3 MSI 构建，并运行现有 MSI/发行物结构、清单、哈希和隐私验证；不得上传或发布该 MSI，也不得借此开始便携性或包体积优化。

所有版本入口统一更新为 1.4.3，工程文档明确其为已验证的稳定源码基线而非已发布标签。验证通过后提交完整工作树并正常推送 `main`，不 force-push；推送完成即停止本轮工作。
