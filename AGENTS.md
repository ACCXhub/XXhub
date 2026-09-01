# AutoDy 仓库指令

## 产品与恢复入口

- AutoDy 是 Windows 本机 Dashboard 与每日抖音消息自动化产品；canonical 源仓库是当前 checkout：`C:\Users\Administrator\Documents\autody`。
- 新会话从 `docs/codex/PROJECT_HANDOFF.md` 恢复；它拥有当前开发检查点。可执行事实以源码、配置和脚本为准，checkout 与工作树事实以 Git 为准。
- 软件工程文档地图是 `docs/软件工程/00-文档总览.md`。公开使用说明在 `README.md`，版本历史在 `CHANGELOG.md`，Release body 在 `docs/RELEASE_NOTES.md`。

## 运行时根目录

保持以下职责分离：

| 根目录 | 职责 |
| --- | --- |
| `C:\Users\Administrator\Documents\autody` | canonical 源码、源码模式脚本与源码模式 `.venv`。 |
| `D:\AutoDy` | 已安装的 ProgramRoot 与部署后的应用运行时。 |
| `C:\Users\Administrator\AppData\Local\AutoDy` | 持久用户 DataRoot：由运行时解析的配置、状态、历史、日志与浏览器/运行时数据。 |

`src/autody/runtime.py` 与 `scripts/resolve-runtime-roots.ps1` 解析权威的 program/data/browser roots。MSI 的 ProgramRoot 与 DataRoot 分离；日常维护保留 DataRoot。

## 稳定模块边界

- `src/autody/config.py` 拥有产品配置与 targets；`state.py`、`history.py`、`retry_state.py` 与 `failures.py` 拥有持久投递状态。
- `friend_discovery.py` 拥有当前 discovery；`binding_recovery.py` 拥有稳定 binding 解析与显式重新关联；`account_profile.py` 与 `account_profiles.py` 拥有 account scope 和本地 profile。
- `runner.py` 与 `chat.py` 拥有自动投递状态机；`preflight.py` 拥有只读发送前检查；`web_api.py` 是本地 API 聚合入口。
- `frontend/src/` 拥有 Web UI。`scripts/` 拥有托盘、服务生命周期、Scheduler 和源码/安装入口。`packaging/wix/` 拥有 MSI 定义；`.github/workflows/release.yml` 与 release scripts 拥有正式 Release 编排。

持久好友身份必须由权威 binding proof 与 account scope 构成；discovery candidate、conversation locator、显示名和头像均不能替代。部分、失败、取消或陈旧 discovery 不得改写稳定 binding。

## 有副作用的边界

- 发送抖音消息、修改 Scheduler task、启动/停止/恢复服务、终止进程、替换已安装运行时、变更 DataRoot，以及构建/发布 release asset 都会产生外部或系统副作用。遵循上述 canonical owner 的既有 identity、locking、confirmation 与 recovery contract。
- 开发与验证使用 fixture、mock 或只读浏览器检查，不发送真实抖音消息。可能已发送或结果不确定时保持 fail-closed，且不自动 retry。
- 操作本机 listener 或进程前，先由 service identity 与 runtime roots 建立 AutoDy ownership；端口、PID 或可执行文件名本身不足以证明归属。
- 已发布 tag 与 GitHub Release asset 不可变。Release identity 以 canonical metadata 与正式 workflow 为准，不以本机构建或源码版本推断。
