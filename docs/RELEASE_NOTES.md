# AutoDy v1.5.3

AutoDy v1.5.3 修正与旧版持久投递记录的兼容投影。发送前身份验证、重复保护、全局浏览器锁和 fail-closed 边界保持不变。

## 修复

- Friends 的“最近成功”只接受具有 `post_send_observed` 的 `confirmed` 或 `retry_confirmed` 证据。
- 旧记录中的裸 `succeeded`、裸确认或 `consumed` 标记不再制造当天成功历史，也不会令当前每日状态错误显示为完成。

## 安装与升级

目标正式资产：

- `AutoDy-1.5.3-x64.msi`
- `AutoDy-1.5.3-x64.msi.sha256`
- `AutoDy-Windows-Portable-1.5.3.zip`
- `AutoDy-Windows-Portable-1.5.3.zip.sha256`
- `release-manifest.json`

**这些文件只有在 GitHub `ACCXhub/XXhub` v1.5.3 Release 页面正式出现并通过 public asset verification 后才属于公开发布件。CI 中间产物和失败 run 生成的 runner 候选不得当作正式下载包。**

v1.4.3 及后续版本均为 per-machine 安装；普通卸载和升级保留原交互用户 DataRoot。下载后应以同一 Release 的 `.sha256` 和 `release-manifest.json` 核对文件。MSI 当前未声明代码签名，Windows/组织策略可能显示信任提示。
