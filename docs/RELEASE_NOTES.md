# 发布说明

## AutoDy 1.4.0（待发布）

> `v1.4.0` 尚未创建标签或发布。请先完成主分支 CI 与完整发布检查；不得移动既有 `v1.3.0` 标签。

### 核心改进

- 增加可持久化的延迟安全重试状态机：只有明确发生在任何发送动作前的失败才会重试。
- 区分 `retry_pending`、`recovered`、`final_failed` 和 `uncertain`；不确定结果不会自动重试。
- 最终失败通知按任务运行 ID 与最终状态去重，等待重试期间不会弹出失败通知。
- 增加 Windows 托盘控制器，安全监督经验证的本地服务，并保留计划任务独立运行能力。

### Test Center

- 官方模块版本为 `1.2.0`，模块 API 为 `1`，兼容 AutoDy `>=1.3.0,<2.0.0`。
- 设置页分别显示核心版本、官方模块版本和兼容状态，避免将不同发布节奏误判为陈旧版本。
- 模块诊断可报告当前包路径与哈希，并拒绝从陈旧输出目录加载模块 ZIP。

### 安全说明

- 真实私信发送始终受身份校验、重复保护、确认和浏览器锁控制。
- Test Center 受控干跑不会调用发送管道；任何不确定条件都会停止。

### 发布前验证

```powershell
.\.venv\Scripts\python.exe -m autody.cli doctor
.\.venv\Scripts\pytest.exe -q
cd frontend
npm test
npm run build
cd ..
.\scripts\build-portable.ps1
```
