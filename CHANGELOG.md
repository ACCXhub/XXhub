# Changelog

所有显著变更记录在此文件中。版本状态以标签和 GitHub Release 为准；候选版本、构建产物或本地验证均不代表已发布。

## [Unreleased]

暂无已发布变更。

## [1.4.2] - 已准备，未发布

最终 Release CI 尚未完成。不得创建 v1.4.2 标签或 Release，也不得把候选 MSI/portable 当作正式资产。

### Added

- 增加幂等 clean-source bootstrap、固定开发依赖、规范 Release 输出目录和机器可读发布 manifest。
- 增加 MSI-native 安装路径解析：登记实际程序目录，用于后续修复、升级与卸载定位。
- 增加首次安装对 D:\AutoDy 的优先选择，以及不可用时的 %LocalAppData%\Programs\AutoDy 回退。
- 增加开始菜单卸载快捷方式、安装目录向导和升级/修复目录复用。
- 增加确定性 MSI 输出与统一的 SHA-256、manifest、allowlist 验证。
- CI 在发行检查失败时保留受控隐私诊断，便于定位不合规 payload。

### Changed

- 工程交付文档迁移为中文文件名，并保留仅供构建、发布和既有维护流程使用的英文兼容入口。
- 8765 不再是唯一启动端口：无关监听者占用时，在 8766–8799 中安全选择空闲端口；选中端口可持久化并复核。
- 重复启动会复用经 service identity、当前用户、路径和解释器验证的同一 AutoDy 服务。
- MSI 构建显式使用 Release，Debug 中间 MSI 使用明显的非发布文件名。
- portable 与官方模块 ZIP 使用排序、固定时间戳和规范 LF 行尾生成可复现归档。
- 文档明确区分普通用户 MSI、需要 Python 的 portable 与开发者 Source ZIP/TAR。

### Fixed

- 拒绝陈旧或失效的已登记安装路径，避免向未知旧目录写入 payload。
- 修复干净源码缺少 .venv/config.yaml 时 README 首条命令必然失败的问题。
- 修复 PowerShell 5.1 将成功 native 命令的 stderr warning 当作终止错误的问题。
- 发布脚本拒绝 obj、Debug、work、陈旧版本、错误 ProductVersion、外部 CAB 和非当前干净提交。

### Security

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
