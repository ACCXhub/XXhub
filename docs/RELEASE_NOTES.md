# 发布说明

## AutoDy 1.4.0

发布日期：2026-07-31。官方 Test Center 版本：`1.2.0`。

### 核心改进

- 增加可持久化的延迟安全重试状态机：只有明确发生在任何发送动作前的失败才会重试。
- 区分 `retry_pending`、`recovered`、`final_failed` 和 `uncertain`；不确定结果不会自动重试。
- 最终失败通知按任务运行 ID 与最终状态去重，等待重试期间不会弹出失败通知。
- 增加 Windows 托盘控制器，安全监督经验证的本地服务，并保留计划任务独立运行能力。
- 失败详情现在包含精确中文原因、失败阶段、是否尝试发送和建议动作；只有安全可重试状态才显示“仅重试此目标”。
- 通知按本地日期、类别、原因、稳定目标和建议动作分组；折叠时显示最新一条和数量，展开后按时间倒序保留每条详情。
- 增加账号隔离的本地资料、目标绑定、启用状态、文案分配、计划、好友发现缓存和 Test Center 选择。
- 新增 per-user MSI，内置固定版本的 Python 运行时和 Chromium；程序与可写数据分离，卸载默认保留用户数据。

### Dashboard 与本地运行

- 修复旧版 `rotation` 运行状态字段导致 `/api/status` 返回 500、Dashboard 永久停在加载页的问题。
- 历史状态只在内存中兼容读取，不重写现有运行时文件；可选状态区段失败时其余 Overview 仍可显示。
- Dashboard 请求增加 10 秒超时、中文错误说明、重试入口和折叠的脱敏诊断。
- 会话导航以右侧可见稳定 ID 为最高权威，并要求连续匹配；延迟、截断或装饰标题只产生辅助警告。
- 账号弹层支持内部点击保持、外部 pointerdown、Escape 和路由切换关闭，并在卸载时清理监听器。
- 好友成功扫描替换账号快照，失败扫描保留最近有效快照；同名不同身份继续保持独立。
- 日志页按标签独立跟随最新内容，向上滚动暂停，“回到最新”恢复跟随。

### Test Center

- 官方模块版本为 `1.2.0`，模块 API 为 `1`，兼容 AutoDy `>=1.3.0,<2.0.0`。
- 设置页分别显示核心版本、官方模块版本和兼容状态，避免将不同发布节奏误判为陈旧版本。
- 模块诊断可报告当前包路径与哈希，并拒绝从陈旧输出目录加载模块 ZIP。
- 批量执行继续复用单个 browser/context/page，安全暂停响应及时，并精确清理自身临时值。

### 安全说明

- 真实私信发送始终受身份校验、重复保护、确认和浏览器锁控制。
- Test Center 受控干跑不会调用发送管道；任何不确定条件都会停止。
- portable 与 MSI 使用显式 allowlist，不包含本地账号、好友、消息、Cookie、浏览器资料、日志、备份、截图或运行时数据。

### 发布前验证

```powershell
.\.venv\Scripts\python.exe -m autody.cli doctor
.\.venv\Scripts\pytest.exe -q
cd frontend
npm test
npm run build
cd ..
.\scripts\build-portable.ps1
.\scripts\build-msi.ps1
.\scripts\verify-msi-lifecycle.ps1
.\scripts\verify-release-artifacts.ps1
```
