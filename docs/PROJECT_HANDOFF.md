# AutoDy 项目精简交接

> 用途：让新的 Codex/ChatGPT 会话快速接手当前项目。
> 基线：2026-07-28，仓库 `Siqihub/hlhub` 的 `main`。
> 只记录当前状态与下一步，不替代 README、CHANGELOG、源码或 Git 历史。
## 1. 项目与版本

AutoDy 是 Windows 本地续火管理台，负责目标、好友发现、文案、定时任务、发送记录、日志、环境诊断、备份迁移和可选测试中心。

```text
本地仓库：C:\Users\Administrator\Documents\autody
GitHub：Siqihub/hlhub
管理台：http://127.0.0.1:8765
当前工作区：AutoDy 1.4.0、Test Center 1.2.0（均未发布）
已发布基线：AutoDy 1.3.0、Test Center 1.1.0
稳定标签：v1.3.0
```

版本由 `pyproject.toml`、`frontend/package.json`、模块 manifest、README 和 CHANGELOG 共同约束。

最近关键提交：

- `2d2a0a3`：重新生成脱敏截图；
- `9914286`：Release 构建不依赖本地 `.venv`；
- `7a8b258`：准备 1.3.0；
- `41fca2d`：完成 Test Center 自移除；
- `6942faa`：准备 1.1.2；
- `562728d` / `558a72e`：修复并验证启动器；
- `f733434` / `2668529` / `0a07640`：修复安装器与快捷方式。

`v1.3.0` 曾被错误地强制移动过一次。当前发布已验证，但以后绝不能移动任何已发布标签。
## 2. 技术栈

- 后端：Python 3.11+、FastAPI、Uvicorn、Pydantic、Typer、PyYAML、HTTPX、Pillow、Playwright。
- 前端：React 19、TypeScript、Vite 6、Vitest、Testing Library、Lucide React。
- Windows：项目 `.venv`、项目 Chromium、Windows Task Scheduler、PowerShell 5.1、ASCII 安全 `.cmd`。
- 发布：GitHub Actions、Windows runner、Python 3.11、Node.js 22、便携 ZIP、SHA-256、GitHub Release。
## 3. 关键结构

```text
src/autody/                 Python 核心、CLI、Web API、模块系统
src/autody/web/static/      后端实际提供的生产前端资源
frontend/                   React/Vite 源码
tests/                      后端与集成测试
scripts/                    安装、启动、健康检查、打包
docs/                       文档与脱敏截图
.github/workflows/          CI 与 Release
output/                     本地发布产物
data/                       本机运行数据，禁止提交
```

新会话只先读：

```text
AGENTS.md
docs/PROJECT_HANDOFF.md
与当前任务直接相关的源码和测试
```

不要先全仓库扫描。
## 4. 核心架构与安全

```text
React/Vite UI → FastAPI 本地 API
→ 配置、目标、历史、日志、调度、模块管理
→ 全局浏览器锁 → Playwright Chromium → 抖音 Web
```

管理台只监听 `127.0.0.1`。发送流程必须保留登录、目标身份、会话和编辑器可用性、发送控件、同日去重、不确定结果保护、发送确认和全局锁。

开发与测试不得输入、准备、粘贴或发送真实消息。只有能证明尚未触发发送的失败才允许安全重试。
## 5. 已稳定功能
### 好友、目标与文案

- 好友昵称和头像按同一聊天列表行绑定；
- 使用稳定 candidate/target 身份；
- 重名时明确提示，不能猜测；
- 已配置目标与候选好友分区；
- 删除按钮紧凑，不占独立布局行；
- 目标支持启用、备注、文案包、后缀、顺序和延迟；
- 同日成功记录阻止重复发送；
- 不确定结果禁止重试。
### 调度

```text
AutoDy-Health-Daily   每日 07:20
AutoDy-DailySpark     每日 07:30
AutoDy-Health-Weekly  每周日 20:00
```

Windows 任务使用 `IgnoreNew`；目标延迟由主任务内部处理。
### 安装与启动

- 使用项目 `.venv` 和项目 Chromium；
- 启动失败必须可见，不能闪退；
- 已修复 PowerShell 5.1 UTF-8、`.cmd` 编码和 `.venv` Errno 13；
- 更新只停止并重启确认属于本项目的服务；
- 源码安装可重建前端；
- 便携包不要求 Node.js；
- HTML 入口 `no-store`，JS/CSS 使用哈希文件名。
## 6. 当前 UI 基线

普通总览：无“发送前自检”、无测试中心卡片、不加载模块资源。

好友管理：无“测试可发送状态”、无单目标预检；好友卡片紧凑，删除按钮不增加高度；候选区保持普通业务结构。

测试中心：

- 位于“设置”下，不是一级侧栏；
- 使用隔离 iframe；
- 宿主 100% 宽度，最小高度 760 px；
- ResizeObserver 回传高度；
- 仅接受同源、同 iframe、正确模块 ID、760–4000 px；
- 自卸载位于底部“模块管理”；
- 未安装时无子导航、iframe、模块资源和轮询。
## 7. Test Center 数据边界

```text
模块 ID：autody-test-center
数据根：data/modules/autody-test-center
```

模块可拥有测试历史、只读预检历史、进度、夹具、设置、计划缓存、目标覆盖、诊断缓存、模块日志和临时文件。

卸载必须删除模块数据，但保留：

- `config.yaml`、普通目标与好友身份；
- 文案库、轮换、正常发送和任务历史；
- 核心日志、浏览器资料、账号与头像；
- 计划任务、备份、安装器和启动器；
- 核心发送安全逻辑。

测试中心预检必须只读，以下动作计数应为零：

```text
fill / type / press / keyboard input
send-control click / send_message
message rotation mutation
completion state mutation
recovery invocation
```
## 8. 隐私与发布排除

不得提交或发布：

```text
config.yaml、messages.txt、data/
Cookie、浏览器 profile、登录状态
账号资料、真实好友名和头像
好友缓存、日志、任务历史
模块注册表、模块历史和设置
本机截图、备份、.venv、node_modules
私有绝对路径
```

文档截图只使用“演示账号”“好友A/B/C”、通用头像和安全夹具。真实页面截图不得原样上传。
## 9. 常用命令

```powershell
# 启动
.\.venv\Scripts\python.exe -m autody.cli ui
# 后端
.\.venv\Scripts\python.exe -m autody.cli doctor
.\.venv\Scripts\pytest.exe -q
# 前端
cd frontend
npm ci
npm test
npm run build
cd ..
# 便携包
.\scripts\build-portable.ps1
```

安装器、启动器或发布脚本修改后，还要检查 PowerShell 5.1、ASCII `.cmd`、快捷方式、服务身份、8765 单监听器、无关端口保护和便携包无 Node.js 依赖。

最近一次 v1.3.0 报告：后端 190 passed，前端 22 passed；生产构建、PowerShell、便携包、CI、Release 和回下载校验成功；最终 Test Center 未安装；无真实消息操作。新改动必须重新验证。
## 10. 当前未解决问题
### P1 模块设置 UI 不统一

模块名称、版本、兼容性、说明和按钮排版与其他设置项不一致。应复用现有字体、间距、图标、按钮和状态徽标。
### P1 残留无效候选缓存

完整成功扫描后，以当前 candidate ID 集合协调缓存，删除未配置且已不存在的旧候选；保留暂时缺失的已配置目标。失败、取消、超时、部分或歧义扫描不得清理。
### P1 定时发送过早弹失败通知

需要 `retry_pending / recovered / completed / final_failed / uncertain` 状态。安全重试期限内不弹最终失败；成功后抑制旧失败；不确定结果立即提示且禁止重试；通知按 task-run ID 去重。
### P1 日志筛选不生效

日期、级别、任务和状态必须真正改变列表、数量、空状态和分页。前后端参数需一致，并提供活动筛选摘要和重置。
### P2 日志整理体验差

“按日期归档”缺少结果；“复制错误摘要”应改为详情内“复制诊断信息”；需要整理预览、结果、归档入口和重复错误分组，不能粗暴拼接日志。
### P2 文档过度拆分

多份工程文档内容过浅。建议保留 README、CHANGELOG、SECURITY、LICENSE，并合并为一个实质性的 `docs/AUTODY_ENGINEERING_MANUAL.md`。
### P2 Release 与 MSI

减少重复 Release 资产；Test Center 可继续内嵌主包。未来建立 `packaging/windows/` 再规划 WiX/MSI；运行数据必须与安装目录分离。
## 11. 推荐任务顺序

1. 模块设置 UI + Test Center 兼容；
2. 完整扫描后的旧缓存协调；
3. 安全重试状态机 + 延迟通知；
4. 日志筛选；
5. 日志归档、清理和诊断信息；
6. 文档合并与仓库清理；
7. MSI 打包。

每次只处理一个主题：复现、找根因、聚焦修改、针对性测试、真实页面验收，再决定是否统一发版。

普通任务优先 Terra Medium/High。只有跨核心状态机且难定位时再用 Sol High；通常不需要 Extra High。
## 12. 新会话模板

```text
Work in C:\Users\Administrator\Documents\autody.

Before editing, read only:
- AGENTS.md
- docs/PROJECT_HANDOFF.md
- files directly related to this task

Then run:
git status --short
git log -8 --oneline --decorate
git remote -v

Do not scan the entire repository unless required.
Preserve unrelated work.
Do not send real Douyin messages.
Do not move published tags.
```

随后只描述一个具体问题和验收结果，不再粘贴完整项目历史。
## 13. 维护规则

仅在发布新版本、技术栈/目录/命令改变、稳定行为或数据边界改变、P1/P2 问题新增或关闭时更新本文件。小样式、临时调试、单次测试和内部重构无需写入。
