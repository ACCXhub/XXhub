# AutoDy 1.4.3 Stable Product Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变真实发送安全门和历史事实的前提下，完成 AutoDy 最后一轮产品/UI 清理、按历史日统计、内置文案包收敛和提升安装器身份修复，并将验证通过的源码冻结为 1.4.3 后推送 `main`。

**Architecture:** `daily_status.py` 成为按日状态与统计的唯一投影层，只消费历史日事实和 `TaskRunRecord`；Dashboard 只消费投影结果和当前安全重试事实。文案包服务只读取安装内置目录。MSI 在提升前捕获交互用户 SID 与 LocalAppData，通过显式属性和 `CustomActionData` 传给非模拟的延迟修复动作，Scheduler 注册后再读取 Windows 任务并验证无 drift。

**Tech Stack:** Python 3.11、Pydantic 2、FastAPI、Typer、pytest、React 19、TypeScript、Vitest、Windows PowerShell 5.1、Windows Task Scheduler、WiX Toolset 7。

## Global Constraints

- 历史日期只能使用该日期的 `state.daily[day]` 与该日期的 `TaskRunRecord`；任何接口不得接收或引用今天的当前启用目标配置来回算过去。
- 缺失自然日不进入成功率分母，但必须中断连续成功天数。
- MSI 提升后不得从提升进程的 `%LOCALAPPDATA%`、profile 或 `WindowsIdentity.GetCurrent()` 推断原始用户；必须显式使用提升前捕获的 SID 和 LocalAppData。
- Task Principal 使用原始用户 SID，DataRoot 固定为原始用户 LocalAppData 下的 `AutoDy`，任务 `RunLevel` 保持 `Limited`。
- WiX 安装动作继续 `Return="check"`；注册后的任务存在性、启用状态、脚本、ProgramRoot、DataRoot、WorkingDirectory、时间、重复间隔、恢复持续时间与 Principal 任一 drift 都必须令修复失败。
- 统计与 Scheduler 身份/验证先写失败测试；明显 UI、文案和死配置清理先改生产代码，再补聚焦回归。
- 不改变身份校验、重复保护、确认、不确定发送保护或全局浏览器锁；绝不触发真实发送、补发、重试或编辑器输入。
- 不创建 PR、tag 或 GitHub Release，不上传构建产物，不 force-push，不做 MSI/Portable 体积、Chromium、嵌入式运行时或跨机器便携性优化。
- 冻结前必须完成一次本地 1.4.3 MSI 构建及结构、哈希、隐私验证；本地输出只保留在已忽略的 `output/`。
- 保留当前全部未提交修复，不 reset、clean、stash 或覆盖无关变更；最终提交完整、已验证的修复工作树。

---

## File Responsibility Map

- `src/autody/daily_status.py`：今天的目标状态和历史日统计权威投影。
- `src/autody/history.py`：只负责不可变运行记录模型、追加、查询和旧日状态迁移，不再计算 Dashboard 指标。
- `src/autody/web_api.py`：把历史日事实传入统计投影，提供当前 issues、配置和内置文案包 API。
- `frontend/src/pages/FriendsPage.tsx`：保留整行启停和全部删除，移除单项目标删除入口。
- `frontend/src/pages/DashboardPage.tsx`：只展示当前健康、日级指标、条件式安全补发、Needs Attention 和好友状态。
- `src/autody/message_packs.py` 与 `message-packs/index.json`：仅解析和读取内置文案包。
- `src/autody/scheduler.py`：Scheduler 命令生成、任务快照、drift 判定和修复后验证。
- `src/autody/cli.py`：`repair-scheduler` 的显式 ProgramRoot、DataRoot、TaskUserId 边界。
- `scripts/install-task.ps1`：唯一 Task Scheduler 注册脚本，接收显式 SID 并保持 `RunLevel Limited`。
- `packaging/wix/Product.wxs`：提升前身份捕获、首次配置种子、原始用户注册表和延迟修复动作。
- `tests/` 与 `frontend/src/pages/*.test.tsx`：边界测试和产品回归。
- 版本入口、中文工程文档和生成前端静态资源：统一为未发布的 1.4.3 稳定源码基线。

### Task 1: 建立历史日级统计权威投影

**Files:**
- Modify: `src/autody/daily_status.py`
- Modify: `src/autody/history.py`
- Modify: `src/autody/web_api.py`
- Modify: `tests/test_statistics.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `Mapping[str, Mapping[str, object]]` 形式的 `state.daily`、`Sequence[TaskRunRecord]`、统计基准日 `date`。
- Produces: `dashboard_statistics(daily_facts, records, today=None) -> dict[str, float | int | str | None]`，字段为 `last_completed_run`、`consecutive_successful_days`、`success_rate_7d`、`success_rate_30d`。
- Invariant: 函数签名不接收 `AppConfig`、目标列表、当前绑定或当前启用数。

- [ ] **Step 1: 先写历史日边界失败测试**

在 `tests/test_statistics.py` 把导入改为 `from autody.daily_status import dashboard_statistics`，并增加以下独立断言：

```python
def test_historical_consumed_day_is_not_recomputed_from_current_targets():
    daily = {"2026-08-08": {"consumed": True, "succeeded": ["旧目标"], "failures": {}}}
    stats = dashboard_statistics(daily, [], date(2026, 8, 10))
    assert stats["success_rate_7d"] == 100.0


def test_missing_calendar_day_breaks_streak_but_not_rate_denominator():
    daily = {
        "2026-08-08": {"consumed": True},
        "2026-08-10": {"consumed": True},
    }
    stats = dashboard_statistics(daily, [], date(2026, 8, 10))
    assert stats["success_rate_7d"] == 100.0
    assert stats["consecutive_successful_days"] == 1


def test_partial_recovery_uses_explicit_historical_confirmations():
    records = [
        run("2026-08-10", status="retry_pending", success_count=1, failed_count=1,
            confirmation_results={"target-a": "confirmed"}),
        run("2026-08-10", status="retry_pending", success_count=1, failed_count=0,
            skipped_count=1, confirmation_results={"target-b": "retry_confirmed"}),
    ]
    stats = dashboard_statistics({}, records, date(2026, 8, 10))
    assert stats["success_rate_7d"] == 100.0
    assert stats["consecutive_successful_days"] == 1
```

扩展测试 helper 的关键字参数以接收 `confirmation_results` 和 `total_targets`；再覆盖 `completed`、`already_done`、`recovered` 均为成功，有事实但仍为 `failed`/`pending` 的日为失败，非 `daily_send` 记录不进入候选日，今天无事实时从昨天开始计算 streak。

在 `tests/test_web_api.py` 增加一条集成回归：先用历史 `state.daily["2026-08-08"].consumed = true` 请求该窗口统计，再把当前 config 的目标改为不同数量和启用状态后再次请求；两次 `success_rate_7d`、`success_rate_30d` 与该历史日成功事实必须完全一致。

- [ ] **Step 2: 运行测试并确认旧算法失败**

Run:

```powershell
.\.venv\Scripts\pytest.exe -q tests/test_statistics.py
```

Expected: FAIL；旧函数仍位于 `history.py`、缺少 `daily_facts` 参数，且按目标次数而非按日计算。

- [ ] **Step 3: 在 `daily_status.py` 实现按日投影**

使用以下明确边界，避免引入当前配置：

```python
SUCCESS_FINAL_STATUSES = {"completed", "already_done", "recovered"}
SUCCESS_CONFIRMATIONS = {"confirmed", "retry_confirmed"}


def dashboard_statistics(
    daily_facts: Mapping[str, Mapping[str, object]],
    records: Sequence[TaskRunRecord],
    today: date | None = None,
) -> dict[str, float | int | str | None]:
    ...
```

实现规则固定为：窗口候选日是该窗口内 `daily_facts` 的键与 `daily_send` 记录日期的并集；每天只贡献一个分母；`daily.consumed is True` 或任一当日记录的 `final_status` 在成功集合中即为成功；否则仅当当日历史 `total_targets > 0` 且显式 `confirmed`/`retry_confirmed` 目标 ID 的并集达到该日历史最大 `total_targets` 时才为成功。失败/重试次数不改变分母，显式成功一旦成立不被同日稍后的中间失败降级。streak 按自然日逐日倒推，遇到缺失或非成功候选日立即停止。

- [ ] **Step 4: 清除 `history.py` 的展示统计职责并接通 API**

从 `src/autody/history.py` 删除 `dashboard_statistics` 及不再需要的 `timedelta` 导入；在 `src/autody/web_api.py` 从 `autody.daily_status` 导入统计函数，并将调用改为：

```python
statistics = dashboard_statistics(
    state.daily,
    records,
    date.fromisoformat(key),
)
```

`/api/status` 不再生成 `retries_7d`，但仍返回 `history`，以保持底层历史/API 兼容和 Logs 的详细事实。

- [ ] **Step 5: 运行统计和 API 聚焦回归**

Run:

```powershell
.\.venv\Scripts\pytest.exe -q tests/test_statistics.py tests/test_web_api.py -k "statistics or status"
```

Expected: PASS；测试证明当前 config 变化没有进入历史统计，缺失日只影响 streak，不影响成功率分母。

### Task 2: 清理 Friends 与 Dashboard 产品界面

**Files:**
- Modify: `frontend/src/pages/FriendsPage.tsx`
- Modify: `frontend/src/pages/FriendsPage.test.tsx`
- Modify: `frontend/src/pages/DashboardPage.tsx`
- Modify: `frontend/src/pages/DashboardPage.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `DashboardStatus.friends[*].failure.retry_action_available ?? safe_retry_available`。
- Produces: `onRetryTargets(targetIds: string[])` 只在非空安全目标集合上由“补发”按钮调用；固定说明为“仅补发今日尚未成功且可安全重试的目标”。
- Preserves: `api.friendBatch(..., "delete")` 仅保留给顶部“全部删除”；整行启用/停用、重新关联和忽略缓存不变。

- [ ] **Step 1: 直接删除 Friends 单项删除生产入口**

从 `FriendsPage.tsx` 删除 `Trash2` 导入，以及启用目标行和停用候选卡片上的两个 `删除目标` 按钮、单项确认框和 `mutateTargets([targetId], "delete", targetId)` 调用。保留：

```tsx
<button
  className="text-button danger-link"
  disabled={!allTargetIds.length || targetMutationId !== null}
  onClick={() => {
    if (window.confirm(`删除全部 ${allTargetIds.length} 位好友记录？此操作不会作为取消续火处理。`)) {
      void mutateTargets(allTargetIds, "delete", "all");
    }
  }}
>
  全部删除
</button>
```

不得修改 `needs_reassociation` 分支的“重新关联”和“忽略此缓存”。

- [ ] **Step 2: 直接把 Dashboard 收敛为当前健康视图**

删除 `Fragment`/展开状态、`groupedHistoryFailures`、结构化运行记录表、逐次异常展开、trigger/stage 标签和“7 天重试”文案。保留四组日级/当前资源卡、快捷操作、Needs Attention 与好友状态；在统计区和快捷操作之间增加条件式安全补发块：

```tsx
{safeRetryTargetIds.length > 0 && onRetryTargets ? (
  <section className="panel retry-panel" aria-label="今日安全补发">
    <div>
      <h2>今日补发</h2>
      <p>仅补发今日尚未成功且可安全重试的目标</p>
    </div>
    <button
      className="action-button primary"
      disabled={!!busy}
      onClick={() => onRetryTargets(safeRetryTargetIds)}
    >
      补发
    </button>
  </section>
) : null}
```

`safeRetryTargetIds` 必须继续要求：有 `target_id`、今日状态为 `failed`、failure 未 resolved，且 `retry_action_available ?? safe_retry_available` 为真。同步将 `App.tsx` 的完成提示改为“已完成 N 个安全目标的补发”，不改 `api.retryFailedTarget` 的安全门和逐目标等待逻辑。

- [ ] **Step 3: 删除只服务旧 Dashboard 的样式和类型字段**

从 `DashboardStatus.statistics` 删除 `retries_7d`；删除仅被旧结构化历史区域使用的 `.history-*`/展开箭头样式，但保留 Logs 页使用的 `.structured-logs`。`DashboardStatus.history` 与 `HistoryRow` 暂时保留，因为状态 API 和历史兼容仍在范围内。

- [ ] **Step 4: 在生产修改后补 Friends 聚焦回归**

删除原来验证单项删除和“删除后立即重新添加”的测试，替换为：

```tsx
test("removes every single-target delete control but keeps whole-set delete", async () => {
  render(<FriendsPage notify={vi.fn()} />);
  await screen.findByRole("button", { name: "取消续火 小明" });
  expect(screen.queryByRole("button", { name: "删除目标 小明" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "全部删除" })).toBeInTheDocument();
});
```

保留并运行整行取消/重新加入、全部启用/停用/删除、真实 orphan 的重新关联/忽略测试。

- [ ] **Step 5: 在生产修改后补 Dashboard 聚焦回归**

用三个明确测试替换旧历史展开测试：页面不存在“结构化运行记录”“7 天重试”“重试所有目标”；无安全目标时不存在“今日补发”；安全/不安全/已解决混合目标时只显示一个“补发”按钮和固定说明，点击后只传 `target-safe`。

```tsx
expect(screen.queryByRole("table", { name: "结构化运行记录" })).not.toBeInTheDocument();
expect(screen.queryByText(/7 天重试/)).not.toBeInTheDocument();
expect(screen.getByText("仅补发今日尚未成功且可安全重试的目标")).toBeInTheDocument();
fireEvent.click(screen.getByRole("button", { name: "补发" }));
expect(retryTargets).toHaveBeenCalledWith(["target-safe"]);
```

- [ ] **Step 6: 运行前端聚焦回归**

Run:

```powershell
Push-Location frontend
try {
  npm.cmd test -- src/pages/FriendsPage.test.tsx src/pages/DashboardPage.test.tsx src/App.test.tsx
} finally {
  Pop-Location
}
```

Expected: PASS；任何测试都不得调用真实发送或浏览器编辑器。

### Task 3: 将内置文案包收敛为唯一来源

**Files:**
- Modify: `src/autody/message_packs.py`
- Modify: `src/autody/config.py`
- Modify: `src/autody/web_api.py`
- Modify: `message-packs/index.json`
- Modify: `config.example.yaml`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/pages/SettingsPage.test.tsx`
- Modify: `frontend/src/pages/MessagePacksPage.tsx`
- Modify: `frontend/src/pages/MessagePacksPage.test.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.test.tsx`
- Modify: `tests/test_message_packs.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Produces: `MessagePackService(root: Path, now: Callable[[], datetime] | None = None)`；`list_packs()`、`preview()`、`import_pack()` 保持方法名和导入语义。
- Produces: `MessagePack` 只含 `id`、`name`、`description`、`version`、`file`、`count`、`category`。
- Removes: `message_pack_index_url`、`remote_index_url`、`fetch_text`、`relative_url`、`raw_url`、`source`、`warning`、Dashboard `remote_library` issue。

- [ ] **Step 1: 直接简化后端文案包生产实现**

删除 `httpx`、`urljoin`、`_default_fetch_text` 和所有远程/fallback 分支。构造函数固定为：

```python
class MessagePackService:
    def __init__(
        self,
        root: Path,
        now: Callable[[], datetime] | None = None,
    ):
        self.root = root.resolve()
        self.pack_dir = self.root / "message-packs"
        self.now = now or datetime.now
```

`list_packs()` 只返回内置索引；`_local_pack_text()` 只用 `pack.file`，继续用 `resolve()` + 父目录检查阻断越界；索引缺失、JSON 无效、重复 ID、文件越界/缺失、空包继续抛出明确 `MessagePackError`。`PackCatalog`、`PackPreview`、`ImportResult` 不再包含 `source`/`warning`。

- [ ] **Step 2: 直接删除配置、API 和 Dashboard 的远程索引面**

从 `AppConfig`、validator、`save_config()`、`ConfigUpdate`、`_config_payload()`、API 手工序列化和 `config.example.yaml` 删除 `message_pack_index_url`。所有服务调用改为 `MessagePackService(root)`；删除 `remote_library` issue。Pydantic 默认忽略旧 YAML 字段，因此不写迁移器、不读取或修改真实配置；下一次正常 `save_config()` 后旧字段自然消失。

- [ ] **Step 3: 直接清理前端文案与类型**

删除 Settings 的“GitHub 文案索引 URL”和 `AppConfig.message_pack_index_url`。把 Message Packs 页标题/说明改为：

```tsx
<div>
  <h1>文案包</h1>
  <p>从 AutoDy 内置示例包导入本机，您的私人文案不会上传。</p>
</div>
```

删除 warning notice，并从 `PackCatalog`、`PackPreview`、`PackImportResult` 类型与前端 mock 中删除 `source`/`warning`。

- [ ] **Step 4: 简化内置索引 schema**

在 `message-packs/index.json` 的五个 pack 项中保留 `file`，删除 `relative_url` 与 `raw_url`。测试 fixture 使用同一最小 schema：

```json
{
  "id": "sample",
  "name": "示例文案",
  "description": "测试",
  "version": "1.0.0",
  "file": "sample.txt",
  "count": 3,
  "category": "daily"
}
```

- [ ] **Step 5: 在生产修改后补聚焦回归**

删除远程网络失败回退测试；增加旧配置兼容测试：带 `message_pack_index_url` 的 YAML 仍能加载，但模型无该属性，正常保存后的 YAML 不含该键。增加 `../outside.txt` 被拒绝、API 配置响应不含旧键、status issues 不含 `remote_library`、文案包响应不含 `source`/`warning` 的断言。

- [ ] **Step 6: 运行文案包/配置/API/前端回归**

Run:

```powershell
.\.venv\Scripts\pytest.exe -q tests/test_message_packs.py tests/test_config.py tests/test_web_api.py -k "message_pack or config or remote_library"
Push-Location frontend
try {
  npm.cmd test -- src/pages/MessagePacksPage.test.tsx src/pages/SettingsPage.test.tsx src/App.test.tsx
} finally {
  Pop-Location
}
```

Expected: PASS；随后执行 `rg -n "message_pack_index_url|remote_library|relative_url|raw_url|在线文案库" src frontend message-packs tests config.example.yaml`，期望无命中。

### Task 4: 显式传递 Scheduler 用户身份并验证注册结果

**Files:**
- Modify: `src/autody/scheduler.py`
- Modify: `src/autody/cli.py`
- Modify: `scripts/install-task.ps1`
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_scheduler_scripts.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `SchedulerService(..., data_root: Path | None = None, task_user_id: str | None = None, task_rows: Callable[[], list[dict]] = windows_task_rows)`。
- Produces: `scheduler_status_rows(..., program_root=None, data_root=None, task_user_id=None)`，可返回 `drift_reason="principal_mismatch"`。
- Produces: `repair-scheduler --config PATH --program-root PATH --data-root PATH --task-user-id SID`；`--task-user-id` 对普通非提升修复可省略，MSI 调用必须提供。
- Produces: `install-task.ps1 -TaskUserId SID`；传值时不得调用当前提升身份推断 Principal。

- [ ] **Step 1: 先写 Python 身份传递和修复后验证失败测试**

在 `tests/test_scheduler.py` 增加：

```python
def test_scheduler_repair_passes_explicit_task_user_sid(tmp_path, monkeypatch):
    # fake subprocess captures command; task_rows returns三个无 drift 的显式 SID 任务
    service = SchedulerService(
        tmp_path / "program",
        data_root=tmp_path / "data",
        task_user_id="S-1-5-21-1000",
        task_rows=lambda: expected_task_rows(tmp_path, "S-1-5-21-1000"),
    )
    service.repair(AppConfig(targets=[Target(name="fixture")]))
    assert captured[captured.index("-TaskUserId") + 1] == "S-1-5-21-1000"


def test_scheduler_repair_fails_when_registered_task_drifts(tmp_path):
    service = SchedulerService(
        tmp_path / "program",
        install=lambda _config: None,
        data_root=tmp_path / "data",
        task_user_id="S-1-5-21-1000",
        task_rows=lambda: rows_with_wrong_data_root(tmp_path),
    )
    with pytest.raises(RuntimeError, match="定时任务修复后验证失败"):
        service.repair(AppConfig(targets=[Target(name="fixture")]))
```

再分别覆盖缺失/禁用任务、错误脚本、ProgramRoot、DataRoot、WorkingDirectory、时间、重复间隔、恢复持续时间和 Principal SID。helper 必须生成三项固定任务，weekly disabled 时生成“未安装且 configured disabled”的正确状态。

- [ ] **Step 2: 先写 CLI 与 PowerShell 跨边界失败测试**

把 `tests/test_cli.py` 的 repair 断言扩展为捕获 `service.data_root` 与 `service.task_user_id`；删除 `--if-config-exists` 成功跳过测试，改为缺失 config 明确失败。`tests/test_scheduler_scripts.py` 增加脚本契约：

```python
assert "[string]$TaskUserId" in text
assert "New-ScheduledTaskPrincipal -UserId $PrincipalUserId" in text
assert "-RunLevel Limited" in text
assert "WindowsIdentity]::GetCurrent().Name" not in text
```

并用 PowerShell mock 调用显式 SID，断言三个注册任务的 `Principal.UserId` 全部为该 SID。

- [ ] **Step 3: 运行新测试并确认失败**

Run:

```powershell
.\.venv\Scripts\pytest.exe -q tests/test_scheduler.py tests/test_scheduler_scripts.py tests/test_cli.py -k "scheduler or task_user"
```

Expected: FAIL；当前服务没有 TaskUserId、没有 repair 后验证，脚本仍从当前 WindowsIdentity 名称推断。

- [ ] **Step 4: 实现 SchedulerService 显式 SID 和无 drift 验证**

`windows_task_rows()` 的 PowerShell 快照增加 `principal_user_id=[string]$task.Principal.UserId`。`scheduler_status_rows()` 在提供 `task_user_id` 时比较规范化 SID；修复验证逻辑固定为：

```python
statuses = scheduler_status_rows(
    config,
    self._task_rows(),
    len(enabled_execution_targets(config)),
    program_root=self.root,
    data_root=self.data_root,
    task_user_id=self.task_user_id,
)
invalid = [
    row for row in statuses
    if row["drift"] or row["installed"] != row["configured_enabled"]
]
if invalid:
    names = ", ".join(row["name"] for row in invalid)
    raise RuntimeError(f"定时任务修复后验证失败：{names}")
```

`_install_windows_tasks()` 只有在 `task_user_id` 非空时追加 `-TaskUserId`，普通 Dashboard 修复仍由当前普通用户注册；`repair()` 必须安装后立即读取并验证，不允许静默成功。

- [ ] **Step 5: 实现 CLI 和安装脚本边界**

删除 `if_config_exists`。CLI 用显式参数构造：

```python
SchedulerService(
    program_root.resolve(),
    data_root=data_root.resolve(),
    task_user_id=task_user_id,
).repair(loaded)
```

CLI 校验 `config.resolve().parent == data_root.resolve()`，避免命令行自身把配置与数据根分裂。`install-task.ps1` 将显式 SID 解析为 `SecurityIdentifier` 并把 `.Value` 传给 `New-ScheduledTaskPrincipal -LogonType Interactive -RunLevel Limited`；仅在 `TaskUserId` 未提供的普通调用中使用当前普通用户的 `.User.Value`。

- [ ] **Step 6: 运行 Scheduler 聚焦回归**

Run:

```powershell
.\.venv\Scripts\pytest.exe -q tests/test_scheduler.py tests/test_scheduler_scripts.py tests/test_cli.py
```

Expected: PASS；错误任务快照必须产生非零 CLI 结果。

### Task 5: 将 WiX 提升语义绑定到原始交互用户

**Files:**
- Modify: `packaging/wix/Product.wxs`
- Modify: `scripts/build-msi.ps1`
- Modify: `tests/test_msi_packaging.py`
- Modify: `tests/test_release_pipeline.py`
- Modify: `scripts/verify-msi-lifecycle.ps1`

**Interfaces:**
- Produces MSI properties: `AUTODY_INTERACTIVE_USER_SID`、`AUTODY_INTERACTIVE_LOCALAPPDATA`、`AUTODYDATAROOT`，前两者为 `Secure="yes"`。
- Produces deferred action data: 显式 `--config`、`--program-root`、`--data-root`、`--task-user-id`，通过与延迟动作同名的属性传入 `CustomActionData`。
- Preserves: `StopExistingAutoDyTray` 仍只停止已验证 AutoDy tray；`RepairInstalledAutoDyTasks` 为 `Execute="deferred" Impersonate="no" Return="check"`。

- [ ] **Step 1: 先写 WiX 结构失败测试**

把 `test_wix_project_is_sdk_style_and_per_user` 改名并断言 `Scope="perMachine"`。扩展 XML 测试，要求：

```python
assert properties["AUTODY_INTERACTIVE_USER_SID"].attrib["Secure"] == "yes"
assert properties["AUTODY_INTERACTIVE_LOCALAPPDATA"].attrib["Secure"] == "yes"
assert repair.attrib["Execute"] == "deferred"
assert repair.attrib["Impersonate"] == "no"
assert repair.attrib["Return"] == "check"
assert "--data-root" in command_data
assert "--task-user-id" in command_data
assert "--if-config-exists" not in command_data
```

再断言 `config.yaml` File 资源 `NeverOverwrite="yes"`，位于 `AUTODYDATAROOT`，其安装顺序早于 Scheduler repair；原始用户注册表使用 `Root="HKU"` 和捕获 SID 组成的键，不写提升账户 HKCU。

- [ ] **Step 2: 运行 WiX 测试并确认旧语义失败**

Run:

```powershell
.\.venv\Scripts\pytest.exe -q tests/test_msi_packaging.py
```

Expected: FAIL；当前 MSI 是 per-user，修复动作 `Impersonate="yes"`，且仍含 `--if-config-exists`。

- [ ] **Step 3: 实现提升前捕获和明确的 DataRoot**

在 AppSearch/CostFinalize 之后、进入提升执行上下文之前，将 `[UserSID]` 与 `[LocalAppDataFolder]` 捕获到 secure properties，并用捕获的 LocalAppData 明确设置：

```text
AUTODYDATAROOT = [AUTODY_INTERACTIVE_LOCALAPPDATA]AutoDy\
```

不得在延迟动作命令中引用提升进程环境变量。`Package Scope` 改为 `perMachine`；ProgramRoot 仍使用既有 D 盘优先/回退和升级目录解析，不改变选择策略。

- [ ] **Step 4: 以 NeverOverwrite 语义种下首次配置和原用户注册信息**

在 `AUTODYDATAROOT` 组件中从 `$(var.StageDir)\config.example.yaml` 安装名为 `config.yaml` 的 File，设置 `NeverOverwrite="yes"`，保持数据组件 `Permanent="yes"`。将 InstallFolder、DataRoot、InstalledVersion 的注册表目标改为 `HKU\[AUTODY_INTERACTIVE_USER_SID]\Software\AutoDy`，确保普通用户后续从 HKCU 看到同一 hive。Upgrade、Repair 和 uninstall 不覆盖或删除 DataRoot/config。

- [ ] **Step 5: 让生成的 payload 组件符合 per-machine 语义**

`scripts/build-msi.ps1` 生成每个 payload Component 时把对应 File 设为 `KeyPath="yes"`，删除原来的每文件 `Root="HKCU"` Installer marker；`PayloadDirectoryCleanup` 若仍需 registry keypath，则使用内部 `HKLM\Software\AutoDy\Installer` marker，不得写提升账户 HKCU。增加脚本级回归，断言生成模板不再发出 payload HKCU keypath，并保持稳定 component GUID、目录清理和可重复构建逻辑不变。

- [ ] **Step 6: 用 CustomActionData 执行非模拟修复**

在立即执行阶段构造 `RepairInstalledAutoDyTasks` 的 action data，值包含：

```text
"[INSTALLFOLDER]runtime\python\python.exe" -m autody.cli repair-scheduler --config "[AUTODYDATAROOT]config.yaml" --program-root "[INSTALLFOLDER]." --data-root "[AUTODYDATAROOT]." --task-user-id "[AUTODY_INTERACTIVE_USER_SID]"
```

延迟动作只执行 `[CustomActionData]`，设为 `Impersonate="no"` 以拥有注册任务权限，且继续 `Return="check"`。顺序必须是 InstallFiles（含缺失 config）→ 已验证 tray stop → Scheduler repair/verify。

- [ ] **Step 7: 同步本地生命周期结构检查并运行聚焦回归**

更新 `scripts/verify-msi-lifecycle.ps1` 的 MSI 表断言，使它检查 per-machine、显式原用户属性、config NeverOverwrite 和修复 action data，但本轮不在当前真实用户资料上运行安装/升级生命周期。运行：

```powershell
.\.venv\Scripts\pytest.exe -q tests/test_msi_packaging.py tests/test_scheduler.py tests/test_scheduler_scripts.py tests/test_cli.py
```

Expected: PASS。

### Task 6: 统一 1.4.3 版本、工程文档和生产前端资源

**Files:**
- Modify: `pyproject.toml`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `packaging/wix/AutoDy.Package.wixproj`
- Modify: `scripts/build-msi.ps1`
- Modify: `scripts/build-portable.ps1`
- Modify: `scripts/build-release-from-clean-source.ps1`
- Modify: `scripts/verify-release-artifacts.ps1`
- Modify: `scripts/verify-msi-lifecycle.ps1`
- Modify: `scripts/write-release-manifest.ps1`
- Modify: `.github/workflows/release.yml`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/AUTODY_ENGINEERING_MANUAL.md`
- Modify: `docs/PROJECT_HANDOFF.md`
- Modify: `docs/RELEASE_NOTES.md`
- Modify: `docs/文档总览.md`
- Modify: `docs/软件工程/01-软件需求规格说明书.md`
- Modify: `docs/软件工程/03-安装部署与用户手册.md`
- Modify: `docs/软件工程/04-测试与验收报告.md`
- Modify: `docs/软件工程/05-运维维护与故障排查.md`
- Modify: `docs/软件工程/06-隐私与安全设计.md`
- Modify: `docs/软件工程/07-项目交接说明.md`
- Rename: `docs/软件工程/08-v1.4.2发布说明.md` to `docs/软件工程/08-v1.4.3发布说明.md`
- Modify: version assertions in `tests/test_msi_packaging.py`, `tests/test_release_pipeline.py`, `tests/test_tray_controller.py`, `tests/test_web_api.py`, `frontend/src/pages/SettingsPage.test.tsx`
- Regenerate: `src/autody/web/static/index.html`
- Regenerate: `src/autody/web/static/assets/index-*.js`

**Interfaces:**
- Produces: 所有产品/构建默认版本为 `1.4.3`，前端 service identity 与 Python package version 一致。
- Documents: `1.4.3` 是“已验证的稳定源码基线、尚未发布”，不是已有 tag/Release。
- Preserves: Test Center 版本 `1.2.0` 和核心兼容范围 `>=1.3.0,<2.0.0` 不变；`AUTODY_PREVIOUS_VERSION` 仍为 `1.4.1`。

- [ ] **Step 1: 更新所有代码和构建版本入口**

把产品默认版本、Release workflow 目标 tag/env、路径与测试断言从 `1.4.2` 改为 `1.4.3`。不得改变已发布 v1.4.0/v1.4.1 的哈希或移动任何 tag。

- [ ] **Step 2: 更新中文文档的状态语义**

CHANGELOG 新增 1.4.3 条目，概述日级统计、UI 清理、内置文案包和安装身份修复。README、总览、交接、安装、测试、运维、安全和发布说明统一使用以下事实：

```text
AutoDy 1.4.3 是已完成本地验证的稳定源码基线，尚未创建 v1.4.3 标签或 GitHub Release；本轮本地 MSI 仅用于冻结验证，不是公开发布资产。
```

测试报告只写本次真实观察到的命令结果和数量；在验证完成前用“待本轮验证”描述，不预填成功数字。

- [ ] **Step 3: 更新版本回归并扫描陈旧入口**

Run:

```powershell
rg -n "1\.4\.2" pyproject.toml frontend packaging scripts .github tests README.md CHANGELOG.md docs
```

Expected: 只允许历史 CHANGELOG/已发布背景中明确指向旧版本的必要描述；默认参数、当前状态、workflow、文件名、测试期望不得残留 1.4.2。

- [ ] **Step 4: 运行前端全量测试和生产构建**

Run:

```powershell
Push-Location frontend
try {
  npm.cmd test
  if ($LASTEXITCODE -ne 0) { throw "frontend tests failed" }
  npm.cmd run build
  if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
} finally {
  Pop-Location
}
```

Expected: PASS；`src/autody/web/static/index.html` 只引用新生成的 hash bundle，旧 bundle 不再受 index 引用。

### Task 7: 执行完整回归和真实安装版只读 UI 验收

**Files:**
- Verify only: repository test/build inputs
- Sync only after ownership verification: `D:\AutoDy` 的明确生产文件
- Never modify: `%LocalAppData%\AutoDy\config.yaml`、历史、日志、浏览器资料、好友/文案数据

**Interfaces:**
- Consumes: `/api/service-identity`、8765 listener PID/command line、已确认安装根 `D:\AutoDy`。
- Produces: 本地命令输出和只读 UI 观察证据；不提交私人截图、日志或运行时数据。

- [ ] **Step 1: 运行 doctor、完整 Python 与静态差异检查**

Run:

```powershell
.\.venv\Scripts\python.exe -m autody.cli doctor
.\.venv\Scripts\pytest.exe -q
git diff --check
```

Expected: doctor 成功、全量 pytest PASS、diff check 无输出。

- [ ] **Step 2: 解析所有本轮 PowerShell 脚本**

Run:

```powershell
$parseFiles = @(
  'scripts\install-task.ps1',
  'scripts\build-msi.ps1',
  'scripts\build-portable.ps1',
  'scripts\build-release-from-clean-source.ps1',
  'scripts\verify-release-artifacts.ps1',
  'scripts\verify-msi-lifecycle.ps1',
  'scripts\write-release-manifest.ps1'
)
foreach ($file in $parseFiles) {
  $tokens = $null
  $errors = $null
  [void][Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path $file), [ref]$tokens, [ref]$errors
  )
  if ($errors.Count) { throw "$file PowerShell parse failed: $($errors.Message -join '; ')" }
}
```

Expected: 无解析错误，保持 Windows PowerShell 5.1 兼容。

- [ ] **Step 3: 只读确认 8765 服务确属当前 AutoDy 安装**

先读取 `Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8765 -State Listen`，再用 listener PID 查询 `Get-CimInstance Win32_Process` 的 executable/command line，并请求 `http://127.0.0.1:8765/api/service-identity`。只有当 application、project/package path、Python executable 全部指向 `D:\AutoDy` 时才继续；否则停止同步和重启，不终止任何进程。

- [ ] **Step 4: 显式同步生产文件并通过受管 tray 重启**

只复制本轮实际修改且安装版需要的 Python 模块、`src/autody/web/static/index.html` 和新 hash asset、`message-packs/index.json`、`scripts/install-task.ps1` 到 `D:\AutoDy` 对应位置；不递归复制仓库、不删除安装目录、不触碰 DataRoot。然后使用已确认的 `D:\AutoDy\scripts\autody-tray.ps1 -StopExisting` 停止该受管实例，并以隐藏窗口启动 `D:\AutoDy\scripts\start-dashboard.vbs`。重新验证新 PID 的 `/api/service-identity` 为 1.4.3 且仍归属同一安装。

- [ ] **Step 5: 在新浏览器上下文执行只读验收**

使用 `browser:control-in-app-browser` 打开本地 UI，仅检查：Friends 无任何“删除目标”按钮且有“全部删除”，重新关联/忽略仍存在；Dashboard 无结构化运行表和重试次数；日级指标存在；若当前没有安全可补发目标则按钮不出现，条件式安全目标过滤由 Task 2 的前端 fixture 回归提供证据；`remote_library` 与远程索引设置消失；Logs 仍显示详细历史；Scheduler API/页面与 Windows 任务无 drift；控制台无错误；常用桌面宽度无横向溢出。不得注入或修改真实状态，不得点击“立即运行”“补发”“扫码登录”，不得聚焦或检查真实编辑器。

- [ ] **Step 6: 将实际验证结果写回测试报告**

只在 `docs/软件工程/04-测试与验收报告.md` 写命令、通过数量、构建/浏览器观察和限制，不写账号、好友、消息、PID、用户路径或私人截图。文档仍声明未发布。

### Task 8: 提交冻结源码、本地构建验证 1.4.3 MSI 并推送 main

**Files:**
- Commit: 本轮完整预期源码、测试、文档、脚本和生成静态资源
- Local ignored output: `output/release/v1.4.3/`

**Interfaces:**
- Produces: 一个 clean HEAD、一次本地 BuildOnly 发行物验证、正常推送后的 `origin/main == HEAD`。
- Does not produce: tag、Release、PR、上传资产或公开下载链接。

- [ ] **Step 1: 做提交前隐私和范围审计**

Run:

```powershell
git status --short
git diff --check
git diff --stat
git diff --name-only
```

逐项确认没有 `data/`、`.venv/`、`node_modules/`、`output/`、日志、cookies、浏览器 profile、模块注册表、真实截图或私人绝对路径。确认 `src/autody/web/static/index.html` 引用的 bundle 已 tracked，旧 bundle 删除是构建产物替换而非用户文件删除。

- [ ] **Step 2: 提交完整修复工作树**

Run:

```powershell
git add --all
git diff --cached --check
git commit -m "fix: stabilize AutoDy 1.4.3 product cleanup"
git status --short
```

Expected: commit 成功，tracked/untracked 工作树为空。若发现无关或私人文件，先从 index 移除该文件并重新审计，不删除用户数据。

- [ ] **Step 3: 从 clean HEAD 本地构建和验证 1.4.3**

Run:

```powershell
$sourceCommit = (git rev-parse HEAD).Trim()
.\scripts\build-release-from-clean-source.ps1 `
  -Version 1.4.3 `
  -Commit $sourceCommit `
  -BuildOnly
```

Expected: wrapper 重跑 bootstrap/doctor、Python/前端测试，构建 portable（仅为现有统一 artifact verifier 的输入）和 1.4.3 MSI，并运行 MSI/portable 文件清单、内嵌 CAB、SHA-256 sidecar、D3DCompiler、allowlist 与隐私扫描；输出 `[SUCCESS] Canonical build completed without lifecycle publication approval.`。本步骤不运行发布、tag 或上传，也不开始包体积优化。

- [ ] **Step 4: 复核本地 MSI 证据和仓库清洁度**

Run:

```powershell
$releaseRoot = Resolve-Path 'output\release\v1.4.3'
$msi = Join-Path $releaseRoot 'AutoDy-1.4.3-x64.msi'
$actual = (Get-FileHash -Algorithm SHA256 $msi).Hash.ToLowerInvariant()
$sidecar = (Get-Content -Raw "$msi.sha256").Trim().Split(' ')[0].ToLowerInvariant()
if ($actual -ne $sidecar) { throw 'MSI SHA-256 sidecar mismatch' }
$privacy = Get-Content -Raw (Join-Path $releaseRoot 'release-privacy-report.json') | ConvertFrom-Json
if (-not $privacy.passed) { throw 'Local release privacy verification did not pass' }
git status --short
```

Expected: hash 一致、privacy `passed=true`、构建输出因 `.gitignore` 不污染工作树。保留本地输出作为证据但不上传。

- [ ] **Step 5: 正常推送 main 并停止**

Run:

```powershell
git push origin main
$head = (git rev-parse HEAD).Trim()
$remote = (git rev-parse origin/main).Trim()
if ($head -ne $remote) { throw 'origin/main does not match local HEAD' }
```

Expected: push 成功且哈希一致。不得继续创建 tag、GitHub Release、PR 或上传 `output/release/v1.4.3`。
