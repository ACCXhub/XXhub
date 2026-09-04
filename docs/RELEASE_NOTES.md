# AutoDy 当前源码状态

当前 `main` 是维护与本机运行的 canonical source。源码版本线仍为 1.5.3，但 v1.5.2/v1.5.3 的公开 Release 与标签已经退役，不再作为可下载安装来源。

## 当前已收敛修复

- 恢复可靠的发送后确认与当天投递证据边界。
- 源码安装会先按 `package-lock.json` 执行 `npm ci`，再构建前端。
- 源码安装可容忍仅残留部分 HKCU AutoDy 注册值，不再因缺失 `InstallFolder` / `DataRoot` 直接失败。
- `install.cmd` 会把 PowerShell 安装异常正确传播为非零退出码。
- 托盘启动健康检查会先验证模块元数据字段是否存在；暂时缺少 `required_autody_version` 时按未就绪处理，不再弹出伪“AutoDy 启动失败”。

## 发布状态

当前公开推荐安装基线为 v1.4.4。下一次公开发布应使用新的版本身份，并从当前 `main` 重新构建、校验 MSI/Portable、checksum 与 release manifest 后再发布。
