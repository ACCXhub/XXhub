# AutoDy

AutoDy 是面向 Windows 本机运行的 Douyin 私信工作流管理台。它在本地管理目标、文案、计划和执行历史，并把身份校验、重复保护、确认、浏览器锁与不确定结果保护放在发送流程之前。

当前开发版本：AutoDy `1.4.0`；官方 Test Center `1.2.0`（模块 API `1`，兼容 `>=1.3.0,<2.0.0`）。模块版本独立发布，版本号不同并不代表不兼容。

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

## 快速开始（开发）

```powershell
git clone git@github.com:Siqihub/hlhub.git
cd autody
.\.venv\Scripts\python.exe -m autody.cli doctor
```

请始终使用项目 `.venv`。如需前端开发或构建：

```powershell
cd frontend
npm install
npm test
npm run build
cd ..
```

## 验证

```powershell
.\.venv\Scripts\python.exe -m autody.cli doctor
.\.venv\Scripts\pytest.exe -q
cd frontend
npm test
npm run build
cd ..
.\scripts\build-portable.ps1
```

前端变更还需要在新浏览器上下文中检查本机生产页面 `http://127.0.0.1:8765`。检查仅限界面、请求和只读状态，不能进行真实消息操作。

## 文档

- [工程手册](docs/AUTODY_ENGINEERING_MANUAL.md)：架构、安全、模块、托盘、测试与发布流程。
- [项目交接](docs/PROJECT_HANDOFF.md)：当前工作副本状态和下一步事项。
- [发布说明](docs/RELEASE_NOTES.md)：待发布版本的用户可见变更。
- [安全政策](SECURITY.md)、[第三方声明](THIRD_PARTY_NOTICES.md)、[变更记录](CHANGELOG.md)。

## 许可证

见 [LICENSE](LICENSE)。
