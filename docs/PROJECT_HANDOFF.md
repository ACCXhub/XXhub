# AutoDy 项目交接

更新时间：2026-07-31
工作目录：`C:\Users\Administrator\Documents\autody`

## 当前基线

- 当前核心代码版本：AutoDy `1.4.1`。
- 当前官方 Test Center：`1.2.0`，模块 API `1`，核心兼容范围 `>=1.3.0,<2.0.0`。
- 当前发布系列：AutoDy `1.4.1` / 官方 Test Center `1.2.0`；标签与公开资产状态以 GitHub Releases 为准。
- 已发布的 `v1.4.0` 及更早标签不得移动、删除或强制更新。
- 远程：`origin` → `git@github.com:ACCXhub/hlhub.git`。

## 已实现能力

### 安全执行与重试

- 浏览器流程使用全局锁、稳定会话身份校验、重复保护和确认路径。
- 任务状态：`scheduled`、`running`、`retry_pending`、`recovered`、`completed`、`final_failed`、`uncertain`、`cancelled`。
- 仅明确发生在发送动作之前的失败可按 2、5、10 分钟进入延迟重试；不得越过任务完成窗口。
- 可能发送、确认不确定、身份歧义或无法证明重复保护安全时进入 `uncertain`，绝不重试。
- `retry_pending` 可跨重启恢复；最终通知按任务运行 ID 与终态去重。
- Overview 的历史目标失败只有在同一稳定目标存在更晚确认成功、且之后没有更新失败时才标为“已解决”；已解决项保留历史但不再提供重试主操作。

### Test Center 与模块包

- Test Center 是 Settings 下的可选模块，不是主导航入口；未安装时不得加载模块页面或轮询其 API。
- 模块版本与核心版本独立显示。正确组合为 AutoDy `1.4.1` / 官方模块 `1.2.0` / 兼容 `>=1.3.0,<2.0.0`。
- 模块安装和升级使用注册表、清单、包哈希和原子替换；诊断应显示实际加载包的路径与哈希。
- 启动时若服务或模块来自旧安装位置，应给出可操作错误，而不是把问题归因于浏览器缓存。
- Test Center 受控干跑必须由用户明确发起并满足锁、身份、空编辑器和无附件条件；它不发送、不调用发送管道，也不自动重试。

### Windows 启动和托盘

- `scripts/autody-tray.ps1` 提供单实例托盘宿主，可打开管理台、查看状态/日志、重启、切换启动项和退出。
- 托盘只可复用或停止已验证属于 AutoDy 的服务；不得接管无关的 8765 端口拥有者。
- 退出托盘不会删除计划任务或用户数据；计划任务可以独立于托盘运行。
- 源开发使用项目 `.venv`；便携式/安装路径必须使用当前应用目录、当前前端构建与当前官方模块 ZIP。

### Windows 发布包

- portable 与 MSI 都使用显式 allowlist，不递归打包仓库，也不包含运行时数据、账号资料、浏览器资料、日志、备份或开发夹具。
- MSI 为 per-user 安装：程序位于 `%LocalAppData%\Programs\AutoDy`，可写数据位于 `%LocalAppData%\AutoDy`，卸载默认保留用户数据。
- MSI 提供完整安装向导和自定义程序目录；所选路径会持久化，供修复、升级和卸载定位实际 payload。
- MSI 内置固定 Python 3.11.9、固定依赖和 Chromium；portable 安装不要求 Node.js。
- `scripts/verify-release-artifacts.ps1` 默认验证 `output` 中的本地产物；可用 `-ArtifactDirectory` 对重新下载的公开 MSI 和 portable 再执行同一套隐私检查。

## 当前工作树注意事项

开始工作前必须执行：

```powershell
git status --short
git log -8 --oneline --decorate
git remote -v
```

不要假定工作树干净。开始任务时应重新盘点所有修改和未跟踪文件；它们归当前操作者所有，后续任务必须保留，不能使用 reset、clean、stash 或覆盖式构建清理它们。

运行时数据、模块注册表、浏览器资料、日志、缓存、备份、`.venv` 和 `output/` 均不应提交或删除；尤其不得读取、暴露或纳入真实账号、好友、消息、Cookie 或资料文件。

## 验证基线

发布或产品修改前应执行：

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

UI 修改还要用新浏览器上下文检查 `http://127.0.0.1:8765` 的生产构建、网络请求、控制台、尺寸和弹窗；该检查仅限只读或夹具路径，不能触发真实私信编辑器操作。

## 下一位维护者的起点

1. 先阅读根目录 `AGENTS.md` 与本文件，再只读取当前任务直接相关的文件。
2. 先确认 8765 的 PID、命令行、工作目录、导入包路径和 `/api/service-identity`；确认属于本仓库后才可重启。
3. 版本不一致时检查 `/api/modules`、模块清单、当前捆绑 ZIP 的路径/哈希和启动器来源；不要先假定浏览器缓存。
4. 任何与真实浏览器交互相关的任务均以安全停止为默认结果；不确定即停止、不重试。
5. 发布前等待主分支 CI，创建新的版本标签；不推送、不发布、不移动已存在标签，除非任务明确授权。

## 文档入口

- 使用说明：`README.md`
- 工程架构与维护：`docs/AUTODY_ENGINEERING_MANUAL.md`
- 用户可见待发布变更：`docs/RELEASE_NOTES.md`
- 完整历史：`CHANGELOG.md`
