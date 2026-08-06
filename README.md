# AutoDy

AutoDy 是运行在 Windows 本机的 Douyin 私信工作流管理台。它管理本地目标、文案、计划、运行历史与诊断，并将稳定会话身份校验、重复保护、确认、浏览器锁和不确定结果保护放在执行流程之前。

当前版本状态：AutoDy 1.4.2 已准备但尚未发布；最终 Release CI 仍待完成。已存在 v1.4.0 与 v1.4.1 标签，不会被移动。官方 Test Center 为 1.2.0，模块 API 为 1，兼容范围为 >=1.3.0,<2.0.0；模块和核心版本独立演进。

## 核心能力

- 本地 Dashboard：账号资料、目标、文案、计划、执行历史、诊断和可选模块信息。
- 受管浏览器资料：浏览器 profile 与日常浏览器分离，账号资料按受管目录隔离。
- 安全执行：全局浏览器锁、稳定身份校验、重复保护、确认与不确定结果停止。
- 延迟安全重试：只有可证明未发生发送动作的错误才会进入 retry_pending；恢复后压制旧失败通知。
- Windows 托盘：单实例、打开管理台、状态和日志入口、服务复用、受控重启与安全退出。
- 安装生命周期：MSI 的安装、修复、升级和卸载；程序与可写数据分离。
- 服务发现：优先端口 8765；无关监听者占用时安全回退至 8766–8799，不结束无关进程。

## 架构摘要

React/Vite Dashboard 通过 FastAPI 本地 API 访问任务、配置、历史和模块。Python 领域逻辑负责计划、状态与安全边界；Playwright 使用受管浏览器资料；PowerShell 托盘负责本地单实例、端口与服务身份验证；WiX MSI 负责当前用户安装、升级和数据目录分离。完整设计见[系统设计](docs/software-engineering/system-design.md)。

## 安装

普通用户应在正式 Release 下载 MSI 和同名 SHA-256 sidecar。MSI 包含固定 Python 运行时、依赖和 Chromium。新安装优先 D:\AutoDy；D: 不可用时回退到 %LocalAppData%\Programs\AutoDy，并可在向导中选择其他可写目录。用户可写数据始终保留在 %LocalAppData%\AutoDy，升级和卸载不会默认清除它。

portable 包适合技术用户：包含生产前端，但需要 Python 3.11 和网络来恢复依赖。源码归档用于开发，不能作为普通用户安装器。详情见[安装与使用指南](docs/software-engineering/installation-and-user-guide.md)。

## 快速开始

安装完成后，从开始菜单、桌面快捷方式或 AutoDy 托盘图标打开管理台。管理台地址是 127.0.0.1:<selected-port>。通常端口是 8765；若该端口由无关监听者占用，应用会安全选择 8766–8799 中的空闲端口并在下次启动复核。请优先使用托盘“打开管理台”，不要手动结束端口进程。

开发环境使用项目 .venv：

~~~powershell
..venvScriptspython.exe -m autody.cli doctor
cd frontend
npm test
npm run build
cd ..
~~~

真实私信操作不属于快速开始或测试流程。开发、演示、排障和文档验收只能使用假页面、夹具或只读检查，不得在真实编辑器中输入、准备、粘贴、模拟或发送内容。

## 安装目录与数据边界

| 类型 | 位置 | 说明 |
| --- | --- | --- |
| MSI 程序 | D:\AutoDy 或 %LocalAppData%\Programs\AutoDy | 由安装器登记，升级/修复复用。 |
| MSI 数据 | %LocalAppData%\AutoDy | 包含本地状态、计划、受管资料与备份；卸载默认保留。 |
| portable/源码数据 | 配置目录下 data | 不应提交、复制到发布包或随意删除。 |
| 本地管理台 | 127.0.0.1:<selected-port> | 只监听本机回环地址。 |

## 测试与发布状态

已记录的验证基线包括 Python 378 passed，1 warning；前端 49 passed；MSI 打包 10 passed；本次聚焦托盘/端口与 MSI 打包单元测试为 23 passed。另有 MSI 安装、修复、卸载、v1.4.1 升级、同 PID 服务复用、8765 冲突回退至 8766、D3DCompiler_47.dll payload 和字节级 MSI 可重复性的完成验证证据。

这些结果不替代最终 Release CI。v1.4.2 在 CI 成功前不得打标签或发布。详情见[测试与验收报告](docs/software-engineering/test-and-acceptance-report.md)。

## 隐私与限制

AutoDy 的运行、浏览器资料和可写数据保留在本机。Git、发布包、日志导出与文档不得包含真实账号、目标、消息、Cookie、浏览器 profile、备份或私有路径。MSI 当前未声称代码签名，可能受到 Windows 信任策略提示；请通过正式 Release 与 SHA-256 校验来源。完整边界见[隐私与安全设计](docs/software-engineering/privacy-and-security.md)。

## 文档

- [文档索引](docs/README.md)
- [软件需求规格说明](docs/software-engineering/software-requirements-specification.md)
- [系统设计](docs/software-engineering/system-design.md)
- [安装与使用指南](docs/software-engineering/installation-and-user-guide.md)
- [测试与验收报告](docs/software-engineering/test-and-acceptance-report.md)
- [维护与排障指南](docs/software-engineering/maintenance-and-troubleshooting.md)
- [隐私与安全设计](docs/software-engineering/privacy-and-security.md)
- [项目交接](docs/PROJECT_HANDOFF.md)、[发布说明](docs/RELEASE_NOTES.md)、[变更记录](CHANGELOG.md)

## 许可证

见 [LICENSE](LICENSE)。
