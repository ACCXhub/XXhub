# AutoDy 项目交接

更新时间：2026-08-06

## 当前分支与发布状态

- 当前分支：main；本任务只修改文档。
- 当前核心版本：AutoDy 1.4.2。
- 当前状态：v1.4.2 已准备但未发布，最终 Release CI 是唯一明确的发布门禁。
- 已存在标签：v1.4.0、v1.4.1；绝不移动、删除或强制更新。
- 官方 Test Center：1.2.0，模块 API 1，兼容 AutoDy >=1.3.0,<2.0.0。
- 远程：origin 使用正常推送；发布任务完成前不得创建 v1.4.2 标签或 Release。

## 仓库结构与主要组件

| 位置 | 作用 |
| --- | --- |
| src/autody | FastAPI、配置、任务/历史、浏览器安全、模块与本地 API。 |
| frontend | React/Vite Dashboard、组件与前端测试。 |
| scripts | 源码 bootstrap、托盘、便携式/MSI 构建、发行物与生命周期验证。 |
| packaging/wix | WiX 7 MSI 定义、目录、快捷方式、升级和数据目录模型。 |
| tests | Python/API、托盘、MSI 打包、发布管线与安全夹具测试。 |
| docs | 用户文档、工程文档、发布说明和脱敏截图。 |
| output、data、.venv | 忽略的构建或运行时内容；不得提交、清理或纳入文档。 |

Dashboard 是本地 React 页面；FastAPI 提供服务身份、状态、模块、资料和诊断 API；浏览器层受全局锁、稳定身份、重复保护和确认限制；PowerShell 托盘负责单实例、服务复用、端口选择与安全退出；WiX MSI 负责当前用户安装与目录注册。

## v1.4.2 已完成工作

- clean-source bootstrap、固定依赖、Release 输出目录和机器可读 manifest。
- MSI-native 安装路径解析：新安装优先 D:\AutoDy，回退到 %LocalAppData%\Programs\AutoDy；失效登记目录会被拒绝。
- 程序目录与 %LocalAppData%\AutoDy 数据目录分离；修复、升级和卸载定位实际 payload，卸载默认保留数据。
- 开始菜单卸载快捷方式、安装目录向导、修复、v1.4.1 到 v1.4.2 升级与数据保留的生命周期验证。
- 端口 8765 被无关监听者占用时，安全回退至 8766–8799；无关监听者保持存活。
- 重复启动仅复用通过 service identity、当前用户、路径和 Python 解释器验证的同一服务 PID。
- 固定 D3DCompiler_47.dll payload、确定性 MSI/portable/模块 ZIP、SHA-256 与发行物隐私验证。
- CI 发行检查失败时保留受控隐私诊断，支持排查而不纳入运行时私密资料。

## 安全与运行时边界

不要读取、打印、提交或复制真实账号、目标、消息、Cookie、浏览器 profile、日志、备份、头像缓存或模块注册表。开发、测试、预检和 UI 验收均不得在真实编辑器输入、准备、粘贴、模拟或发送内容；身份歧义、草稿、附件或结果不确定时停止。

服务端口和 PID 不能单独证明归属。先通过 /api/service-identity、当前用户、路径、模块与解释器确认服务，再决定是否复用或由托盘停止。绝不使用按名称批量终止 Python、PowerShell、Chrome 或 Edge 的命令。

## 常用构建与验证命令

~~~powershell
..venvScriptspython.exe -m autody.cli doctor
..venvScriptspytest.exe -q
cd frontend
npm test
npm run build
cd ..
.scriptsuild-portable.ps1 -Version 1.4.2
.scriptsuild-msi.ps1 -Version 1.4.2
.scriptserify-msi-lifecycle.ps1
.scriptserify-release-artifacts.ps1 -Version 1.4.2
~~~

文档任务不需要重跑完整套件、重装 AutoDy 或 MSI 生命周期。产品或发布修改则应按上列范围运行相应检查，并用全新浏览器上下文做只读 UI 验收。

## 发布流程与当前阻塞

1. 确认工作树、版本、manifest、SHA-256 和发布 allowlist 一致。
2. 从干净源码执行 bootstrap/doctor、后端/前端测试、生产构建、portable/MSI 构建和发行物验证。
3. 在干净 Windows 用户配置中，以已验证 v1.4.1 MSI 为基线运行 MSI 生命周期升级验证。
4. 触发并等待一次最终 Release CI。
5. 仅在 CI 通过后创建新的 v1.4.2 标签和 Release；正常推送，绝不 force-push。

当前剩余阻塞：最终 Release CI 尚未通过。不得以本地通过、候选产物或此前 CI 代替该门禁。

## 明确不纳入 1.4.2、留待 v1.5 规划确认的事项

- MSI 代码签名与企业证书运维。
- 更广泛的 Windows 版本、代理、终端防护与企业策略兼容矩阵。
- 新的浏览器自动化扩展或平台适配；任何此类工作必须先设计身份、去重和不确定状态边界。
- 更大的端口发现策略或跨机器服务发现；当前设计只支持本机回环服务和有限安全端口范围。

这些是未来工作，不是已实现承诺，也不应在 v1.4.2 发布说明中表述为功能。

## 文档入口

- [文档索引](README.md)
- [软件需求规格说明](software-engineering/software-requirements-specification.md)
- [系统设计](software-engineering/system-design.md)
- [安装与使用指南](software-engineering/installation-and-user-guide.md)
- [测试与验收报告](software-engineering/test-and-acceptance-report.md)
- [维护与排障指南](software-engineering/maintenance-and-troubleshooting.md)
- [隐私与安全设计](software-engineering/privacy-and-security.md)
- [发布说明](RELEASE_NOTES.md)
