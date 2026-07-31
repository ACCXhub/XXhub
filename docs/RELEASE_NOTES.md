# 发布说明

## AutoDy 1.4.1

发布日期：2026-07-31。官方 Test Center 版本：`1.2.0`。

### Overview

- 历史目标失败现在会依据稳定目标 ID 和后续确认成功状态显示“已解决”。
- 已解决卡片保留历史和重复数量，但不再显示“仅重试此目标”主操作。
- 只有同一目标存在更晚确认成功、且之后没有更新失败时才会标记为已解决；未解决项继续保持原有安全重试入口。

### MSI 安装器

- 增加 Welcome、许可、安装目录、Ready、Progress 和完成页面。
- 安装目录页显示默认路径 `%LocalAppData%\Programs\AutoDy`，支持 Browse 选择其他程序目录。
- 所选程序目录会持久化，修复、升级和卸载均能继续定位实际 payload。
- 可写数据仍固定保存在 `%LocalAppData%\AutoDy`，卸载默认保留用户数据。
- 保持 per-user 安装、静默安装和原有 UpgradeCode；`1.4.0` 可直接升级到 `1.4.1`。

### 发布资产

公开 Release 仅包含：

- `AutoDy-1.4.1-x64.msi`
- `AutoDy-1.4.1-x64.msi.sha256`
- `AutoDy-Windows-Portable-1.4.1.zip`
- `AutoDy-Windows-Portable-1.4.1.zip.sha256`

MSI 与 portable 继续使用显式 allowlist，不包含本地账号、好友、消息、Cookie、浏览器资料、日志、备份、截图、运行时数据、测试、`.venv` 或 `node_modules`。

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
