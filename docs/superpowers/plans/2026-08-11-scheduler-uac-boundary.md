# Scheduler UAC Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让非提升的 AutoDy Dashboard 在用户明确执行 Scheduler 写操作时，以一次性 UAC 子进程调用既有任务脚本，并把真实结果返回页面。

**Architecture:** Dashboard 启动时从自身进程令牌捕获原交互用户 SID，并把 SID、ProgramRoot、DataRoot 显式交给 `SchedulerService`。正常进程保持 Medium Integrity；Scheduler 写操作使用受约束的 `runas` PowerShell 子进程，子进程只分派到现有 `install-task.ps1` 或 `remove-task.ps1`，以短生命周期 JSON 文件回传结果后退出。现有脚本继续唯一持有三项 Windows 任务定义，任务 Principal 仍为原用户且 `RunLevel Limited`。

**Tech Stack:** Python 3.11、FastAPI、ctypes/Win32 ShellExecuteEx、Windows PowerShell 5.1、pytest、Windows Task Scheduler。

## Global Constraints

- 基线为 `778e29c7da16161a3b12d4c51f747beaaec65d9b`，不重新设计 1.4.3 产品行为。
- 仅修复 Scheduler 安装、修复、移除和应用设置的权限路径；不继续 MSI、Portable、Chromium 或产物优化。
- 提升前捕获并显式传递原交互用户 SID、`D:\AutoDy` ProgramRoot 和 `%LocalAppData%\AutoDy` DataRoot；提升后不得从环境或当前身份重新推断。
- 提升只用于注册、修改或移除任务；Dashboard 保持非提升，任务 Principal 保持原交互用户且 `RunLevel Limited`。
- 不建立常驻管理员服务，不使用 `schtasks.exe`，不复制三项任务定义。
- UAC 取消必须返回明确结果，不得再次弹出用于无效回滚的 UAC。
- 验收不得手动触发发送任务，不得输入、准备或发送真实抖音消息。
- 不创建标签、Release、PR，不上传产物，不恢复打包优化。

---

### Task 1: Capture the failing permission boundary

**Files:**
- Modify: `docs/superpowers/plans/2026-08-11-scheduler-uac-boundary.md`

**Interfaces:**
- Consumes: 真实安装服务 `http://127.0.0.1:8765`、Windows Task Scheduler。
- Produces: 可复现的 409、HRESULT、令牌和任务归属证据。

- [x] **Step 1: Confirm service identity and baseline**

  确认 listener 使用 `D:\AutoDy\runtime\python\python.exe`、版本 1.4.3、规范 DataRoot，源码 HEAD 为 `778e29c` 且工作树干净。

- [x] **Step 2: Reproduce all four writes through the installed API and UI**

  安装、修复、移除和应用分别返回 HTTP 409；页面提示与 API 一致。

- [x] **Step 3: Capture the shared native error**

  `Register-ScheduledTask` 与 `Unregister-ScheduledTask` 均返回 `HRESULT 0x80070005`；Dashboard 为 Medium Integrity、非提升，任务文件 owner 为 Administrators。

- [x] **Step 4: Verify identity and runtime roots are not the cause**

  三项任务均为 Ready，Principal 匹配原交互用户，`RunLevel Limited`，ProgramRoot/DataRoot 与恢复重复设置正确。

### Task 2: Specify the elevation contract with failing tests

**Files:**
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `SchedulerService`, `create_app`, Win32 elevation runner boundary。
- Produces: 对显式 SID/根路径、受约束操作、取消语义和 API wiring 的回归契约。

- [x] **Step 1: Add a failing service test for the explicit elevated payload**

  构造非提升环境并截获 elevation runner，断言安装 payload 使用字面值 `program_root`、`data_root`、`task_user_id` 和六项已验证 schedule 字段；生产代码缺少 runner 时测试必须失败。

- [x] **Step 2: Run the focused test and verify RED**

  Run: `.\.venv\Scripts\pytest.exe -q tests/test_scheduler.py -k elevated_payload`

  Expected: FAIL because SchedulerService still directly invokes `install-task.ps1` without an elevation boundary.

- [x] **Step 3: Add failing tests for remove and UAC cancellation**

  断言 remove payload 同样携带原 SID/两类 root 且 operation 只能为 `remove`；断言 apply 遇到 UAC cancel 时不调用 previous-config restore。

- [x] **Step 4: Add a failing API test for capture-once SID wiring**

  在 `create_app` 前将当前进程 SID 固定为 `S-1-5-21-1000`，调用 Scheduler 路由并断言 service 收到该固定 SID，而不是在提升子进程内重新计算。

- [x] **Step 5: Run both test files and verify failures have the intended cause**

  Run: `.\.venv\Scripts\pytest.exe -q tests/test_scheduler.py tests/test_web_api.py -k "scheduler and (elevat or sid or cancel)"`

  Expected: only the new elevation/SID contracts fail; fixtures and imports succeed.

### Task 3: Implement one-shot constrained Scheduler elevation

**Files:**
- Modify: `src/autody/scheduler.py`

**Interfaces:**
- Consumes: validated operation (`install` or `remove`), explicit ProgramRoot/DataRoot/SID, optional `AppConfig` schedule values。
- Produces: `current_process_user_sid() -> str | None`, one-shot elevated result, `SchedulerElevationCancelled`。

- [x] **Step 1: Read the original SID from the normal process token**

  使用 `OpenProcessToken`、`GetTokenInformation(TokenUser)` 和 `ConvertSidToStringSidW` 获取当前 Dashboard 进程用户 SID；非 Windows 返回 `None`，错误失败关闭而不从 `%LOCALAPPDATA%` 或提升身份猜测。

- [x] **Step 2: Build the constrained PowerShell command**

  仅接受 `install`/`remove`；payload 包含显式 roots、SID 和 schedule。提升脚本校验绝对 ProgramRoot/DataRoot、SID 和 `config.yaml`，然后分别调用：

  ```powershell
  & (Join-Path $ProgramRoot 'scripts\install-task.ps1') -ProgramRoot $ProgramRoot -DataRoot $DataRoot -TaskUserId $TaskUserId @ScheduleArguments
  & (Join-Path $ProgramRoot 'scripts\remove-task.ps1')
  ```

  不包含 `Register-ScheduledTask`、`Unregister-ScheduledTask` 或 `schtasks.exe` 任务定义。

- [x] **Step 3: Launch through Win32 `ShellExecuteExW` with `runas`**

  仅在当前进程未提升时执行 `powershell.exe -EncodedCommand ...`；等待子进程退出，并把 UAC error 1223 映射为 `SchedulerElevationCancelled`。已提升进程继续直接调用既有脚本，不再次弹出 UAC。

- [x] **Step 4: Return structured results through DataRoot**

  在 `DataRoot/data/scheduler-operations` 预建唯一结果文件；提升子进程写入 UTF-8 JSON，正常进程校验 operation nonce、读取真实 success/cancel/error 后删除文件。API 只收到脱敏后的用户提示。

- [x] **Step 5: Preserve apply rollback semantics without double-prompt on cancel**

  对普通执行失败保留 candidate→previous 补偿恢复；对 `SchedulerElevationCancelled` 直接返回，不启动第二次提升，因为取消前没有执行任务脚本或修改 config。

- [x] **Step 6: Run the focused service tests and verify GREEN**

  Run: `.\.venv\Scripts\pytest.exe -q tests/test_scheduler.py`

  Expected: all scheduler service tests pass.

### Task 4: Wire the captured identity through every Dashboard route

**Files:**
- Modify: `src/autody/web_api.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `scheduler.current_process_user_sid()` at `create_app` time。
- Produces: one SchedulerService factory used by preview, apply, install/update/repair/remove and status drift validation。

- [x] **Step 1: Capture the SID once during app creation**

  在 `program_root`/`root` 确定后读取 SID 并闭包保存，不在任何 elevated child 中重新读取。

- [x] **Step 2: Use one local SchedulerService factory**

  Preview、apply 和 operation routes 均通过同一 factory 传入 `program_root`、`data_root=root`、`task_user_id`。

- [x] **Step 3: Include the same SID in status drift validation**

  `scheduler_status_rows(..., task_user_id=scheduler_task_user_id)` 必须检查真实任务 Principal 仍属于原交互用户。

- [x] **Step 4: Run focused API tests and verify GREEN**

  Run: `.\.venv\Scripts\pytest.exe -q tests/test_web_api.py -k scheduler`

  Expected: all Scheduler API/status tests pass.

### Task 5: Update focused product and security documentation

**Files:**
- Modify: `docs/软件工程/03-安装部署与用户手册.md`
- Modify: `docs/软件工程/04-测试与验收报告.md`
- Modify: `docs/软件工程/05-运维维护与故障排查.md`
- Modify: `docs/软件工程/06-隐私与安全设计.md`
- Modify: `docs/软件工程/07-项目交接说明.md`

**Interfaces:**
- Consumes: verified implementation and acceptance evidence。
- Produces: user-visible UAC expectation、support diagnosis、security boundary and current handoff state。

- [x] **Step 1: Document the user-visible UAC behavior**

  说明 Dashboard 日常运行不提升；仅 Scheduler 写操作会显示 UAC，取消不会修改任务或配置。

- [x] **Step 2: Document the security boundary**

  说明一次性 child 只调用现有任务脚本、显式继承原 SID/roots、任务本身仍 Limited，结果文件短生命周期且不含账号/消息数据。

- [x] **Step 3: Record fresh test evidence after validation**

  仅写最终实际命令与计数；不得预填通过数字，不记录用户身份、PID、DataRoot 绝对路径或私人页面内容。

### Task 6: Validate source and the real installed application

**Files:**
- No repository file required for the acceptance actions.

**Interfaces:**
- Consumes: completed source, installed runtime `D:\AutoDy`, real Scheduler UI。
- Produces: automated and live evidence that all Scheduler writes work and final tasks are restored。

- [x] **Step 1: Run focused Python and PowerShell validation**

  Run Scheduler/API tests and parse every changed `.ps1` with the Windows PowerShell 5.1 parser. Run `git diff --check`.

- [x] **Step 2: Run the full non-packaging project validation allowed by scope**

  Run: `.\.venv\Scripts\python.exe -m autody.cli doctor`

  Run the Python suite while excluding installer、MSI、portable and release-pipeline test files, because the user explicitly paused packaging work.

  No frontend source changes are planned; if runtime evidence forces a frontend change, additionally run `npm test` and `npm run build`.

- [x] **Step 3: Deploy only the changed runtime files to the verified installation**

  Reconfirm port ownership and `/api/service-identity`, stop/restart only the verified AutoDy service, then install/copy the current AutoDy Python package and required Scheduler runtime files without running MSI/Portable build optimization.

- [x] **Step 4: Accept install and repair through the real UI**

  Click 安装任务 and approve the operation-scoped UAC; verify success and refreshed state. Click 修复任务; verify success and no drift.

- [x] **Step 5: Accept remove and restore through the same UI path**

  Click 移除任务, approve UAC, verify all three tasks are absent; then click 安装任务 and approve UAC to restore them. Do not run any task manually.

- [x] **Step 6: Accept apply using a harmless schedule change and restore**

  Preview a small non-send-triggering time change, apply via UAC, verify Windows state, then restore the original intended setting through the same UI path. Do not choose a time that can immediately trigger the send task.

- [x] **Step 7: Verify the final safety state**

  Dashboard token remains Medium/non-elevated; three tasks are Ready; Principal matches original user; RunLevel is Limited; ProgramRoot/DataRoot, triggers, repetition/recovery and status drift all match config; no split-root exists.

- [x] **Step 8: Review the final diff and repository state**

  Confirm only Scheduler runtime/tests/docs changed, no private/runtime files are tracked, and no tag、Release、PR、artifact upload or package optimization occurred.
