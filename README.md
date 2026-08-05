# AutoDy

AutoDy 是面向 Windows 本机运行的 Douyin 私信工作流管理台。它在本地管理目标、文案、计划和执行历史，并把身份校验、重复保护、确认、浏览器锁与不确定结果保护放在发送流程之前。

当前版本：AutoDy `1.4.2`；官方 Test Center `1.2.0`（模块 API `1`，兼容 `>=1.3.0,<2.0.0`）。模块版本独立发布，版本号不同并不代表不兼容。

![AutoDy 管理台](docs/screenshots/dashboard-overview.png)

## 能力

- 本地 Dashboard、目标管理、文案、计划和执行历史。
- 串行浏览器访问与稳定会话身份校验。
- 重复保护、确认路径和明确的 `uncertain` 停止结果。
- 延迟安全重试：仅在能证明没有发送动作时重试，默认间隔为 2、5、10 分钟。
- 可选 Test Center：用于受控、非发送的身份与编辑器安全验证。
- Windows 托盘宿主：服务监督、单实例、启动项和安全退出。

## 安全边界

AutoDy 不应被用于绕过平台限制或发送未经授权的消息。正常开发、测试和验收只能使用假页面、夹具或只读检查，绝不能输入、粘贴、准备、模拟或发送真实 Douyin 私信。

只有用户明确发起的 Test Center 单目标受控干跑，在已取得浏览器锁、身份一致、编辑器为空且无附件的前提下，才允许使用内存中的临时值验证编辑器；它不会按 Enter、点击发送、调用发送管道或创建发送尝试，且必须清理并确认编辑器为空。任何草稿、附件、身份歧义或不确定情况都会停止并保留现场。

## 普通用户：安装 MSI

普通用户只应从 GitHub Release 下载 `AutoDy-1.4.2-x64.msi` 和同名 `.sha256`。不要把 GitHub 自动生成的 Source code ZIP/TAR 当作安装器，也不要从仓库的 `obj`、`bin\Debug`、`output\work` 或测试目录取文件。MSI 是唯一自带固定 Python 运行时与 Chromium 的安装包。

MSI 为 per-user 安装：新安装会在当前用户可写时优先使用 `D:\AutoDy`，否则自动回退至 `%LocalAppData%\Programs\AutoDy`；安装向导始终允许改选其他可写目录。升级与修复会复用既有程序目录；用户数据始终位于 `%LocalAppData%\AutoDy`，卸载默认保留。

## 开发者：从源码构建

GitHub 自动生成的 Source code ZIP/TAR 是开发者源码，不是双击即用的应用。需要 Windows x64、PowerShell 5.1 或更高版本、Python 3.11、Node.js 22 LTS/npm 和网络访问。解压或克隆后运行：

```powershell
git clone git@github.com:ACCXhub/hlhub.git
cd hlhub
.\scripts\bootstrap-source.ps1
```

bootstrap 会创建或复用项目 `.venv`，按 `requirements-dev.lock` 安装固定 Python 依赖，使用 `npm ci` 恢复前端依赖，把固定 Playwright Chromium 安装到源码树的隔离目录，构建前端并运行轻量 doctor；它不会读取或复制个人配置。

从干净源码构建发布产物还需要 .NET SDK、WiX 7 依赖恢复能力和网络访问：

```powershell
.\scripts\build-release-from-clean-source.ps1 -Version 1.4.2 -BuildOnly `
  -Commit <40-character-commit>
```

完整发布验收还必须传入已验证的 v1.4.1 MSI 执行升级生命周期测试；只有 `output\release\v1.4.2` 中通过 manifest 守卫的文件可发布。

## 便携版：用途和限制

`AutoDy-Windows-Portable-1.4.2.zip` 是不绑定安装目录的便携源码包，不包含预建 `.venv` 或 Chromium。它需要 Windows x64、Python 3.11 和网络访问，但已包含生产前端，因此普通安装不需要 Node.js。解压后运行 `install.cmd`；缺少 Python 或依赖下载失败时会显示明确阶段错误。若需要完全自带运行时、无需 Python 的体验，请使用 MSI。

## 校验 SHA-256

下载后先读取同名 `.sha256`，再运行：

```powershell
Get-FileHash -Algorithm SHA256 .\AutoDy-1.4.2-x64.msi
```

输出必须与 sidecar 完全一致；传输前后也应分别计算哈希。哈希不一致时不要安装或重试。

## 验证

```powershell
.\.venv\Scripts\python.exe -m autody.cli doctor
.\.venv\Scripts\pytest.exe -q
cd frontend
npm test
npm run build
cd ..
.\scripts\build-portable.ps1 -Version 1.4.2
.\scripts\build-msi.ps1 -Version 1.4.2
.\scripts\verify-release-artifacts.ps1 -Version 1.4.2
```

前端变更还需要在新浏览器上下文中检查本机生产页面 `http://127.0.0.1:8765`。检查仅限界面、请求和只读状态，不能进行真实消息操作。

## 文档

- [工程手册](docs/AUTODY_ENGINEERING_MANUAL.md)：架构、安全、模块、托盘、测试与发布流程。
- [项目交接](docs/PROJECT_HANDOFF.md)：当前工作副本状态和下一步事项。
- [发布说明](docs/RELEASE_NOTES.md)：待发布版本的用户可见变更。
- [安全政策](SECURITY.md)、[第三方声明](THIRD_PARTY_NOTICES.md)、[变更记录](CHANGELOG.md)。

## 许可证

见 [LICENSE](LICENSE)。
