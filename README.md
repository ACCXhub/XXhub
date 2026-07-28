# AutoDy v1.3.0

AutoDy 是仅在 Windows 本机运行的续火管理台，用于管理浏览器自动化、目标、文案、定时任务和运行记录。账号资料、浏览器登录状态、好友缓存、头像、日志和个人文案均只保存在本机。

![AutoDy 演示总览](docs/screenshots/dashboard.png)

图：安全夹具生成的演示总览，不包含真实账号或好友资料。

![AutoDy 演示好友管理](docs/screenshots/friends.png)

图：紧凑配置好友卡片与候选好友区域。

![AutoDy 测试中心](docs/screenshots/test-center.png)

图：安装后作为“设置”的子页面显示；预检始终只读。

## 功能概览

- 可视化管理已配置目标和只读好友发现结果；昵称与头像按同一聊天列表行绑定。
- 支持文案库、内置文案包和发送后缀；每个目标可独立设置启用状态、备注、文案包、后缀、顺序和 0–30 分钟延迟。
- 每日定时登录检查与自动任务，同日成功记录不会重复发送；错过运行窗口时按既有保护规则恢复。
- 总览提供结构化运行统计；发送前自检、今日发送计划和异常目标属于可选测试中心，普通管理台不会加载测试模块。
- 发送结果不确定时禁止重试；只有能够确认尚未触发发送动作的失败才允许走原有保护流程重试。
- 可筛选、脱敏、按日期保存和可归档的日志中心；支持安全备份与迁移。
- 运行环境中心可查看本机服务身份、Chromium、登录、计划任务和运行状态，并提供安全修复入口。

测试中心 1.1.0 默认未安装。需要只读预检、受控失败夹具或运行环境诊断时，可在“设置 > 可选模块”安装官方 `AutoDy-Test-Center.autody-module.zip`，安装后从“设置 > 测试中心”打开。卸载会永久删除模块自己的测试历史、设置和目标覆盖，不会影响普通好友、文案、发送记录或浏览器资料。详细说明见 [文档索引](docs/INDEX.md)。

## Windows 安装与首次启动

1. 下载并解压 `AutoDy-Windows-Portable.zip`。
2. 双击 `install.cmd`，安装项目专用 Python 环境和 Chromium。
3. 打开桌面的 `AutoDy 管理台` 快捷方式。
4. 在管理台中完成抖音登录、配置续火目标、检查文案库并安装定时任务。

安装器会创建项目内 `.venv`、`data/ms-playwright` 和稳定的桌面快捷方式。日常使用只需打开管理台；移动到新电脑后需重新登录。

## 使用说明

### 目标与好友发现

“好友管理”上方是当前续火目标。每张目标卡可打开“编辑目标设置”，保存只会影响该目标。下方候选来自本机缓存；点击候选即可加入目标，已加入候选会保留并显示状态。重复昵称会被明确标记，自动化不会猜测聊天对象。

### 可选测试中心

测试中心以独立 iframe 运行，样式和脚本不会影响普通总览或好友管理。它提供只读预检、计划、异常目标、模块专属高级设置、环境和启动器诊断、测试历史与受控夹具。预检不输入、不准备、不发送任何消息；v1.2.0 仅支持官方第一方模块包。

### 异常处理

“今日异常目标”来自结构化状态和任务历史，不依赖解析原始日志。确认失败、可能已触发发送或重名歧义都会显示为不确定并禁止重试。明确未发送的失败仍会复用全局锁、身份校验、同日去重和发送确认管线。

### 日志、备份与运行环境

日志按日期写入 `data/logs/`，可按日期、级别和状态筛选；保留策略支持预览、确认后整理和归档。备份不包含浏览器资料、Cookie、账号资料、头像、好友缓存或日志。运行环境页提供 Chromium 修复、登录、账号资料刷新和计划任务重建入口，不会覆盖 `config.yaml` 或删除浏览器资料。

## 定时任务

默认任务：

- `AutoDy-Health-Daily`：每日 07:20 登录健康检查。
- `AutoDy-DailySpark`：每日 07:30 主任务。
- `AutoDy-Health-Weekly`：每周日 20:00 健康检查。

Windows 任务使用 `IgnoreNew`，浏览器操作共用全局锁。每个目标的延迟由主任务内部处理，不会创建额外的 Windows 计划任务。

## 开发与验证

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item config.example.yaml config.yaml
Copy-Item messages.example.txt messages.txt
.\.venv\Scripts\python.exe -m autody.cli ui

.\.venv\Scripts\pytest.exe -q
cd frontend
npm ci
npm test
npm run build
cd ..
.\scripts\build-portable.ps1
```

管理台只监听 `127.0.0.1`。自动化测试使用本地伪页面和模拟记录，不会连接抖音或发送真实消息。

## 隐私与平台使用

不要提交或上传 `config.yaml`、`messages.txt`、`data/`、浏览器资料、Cookie、账号资料、头像、好友缓存、日志、截图、备份、`.venv` 或 `node_modules`。发布包只包含源代码、通用示例、安装脚本和公开文档。

本项目仅适用于低频本地个人使用。平台页面、登录和规则可能变化；请遵守平台规则，不要将其用于绕过验证、批量营销或未经授权的自动化。

## 许可与致谢

参见 [LICENSE](LICENSE) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

当前版本：AutoDy 1.3.0 / Test Center 1.1.0。完整版本记录见 [CHANGELOG](CHANGELOG.md)，安全说明见 [SECURITY](SECURITY.md)。
