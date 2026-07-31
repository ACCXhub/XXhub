import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { DashboardPage } from "./DashboardPage";

const apiMocks = vi.hoisted(() => ({
  preflightStatus: vi.fn(), runPreflight: vi.fn(), cancelPreflight: vi.fn(),
  todayPlan: vi.fn(), failedTargets: vi.fn(), serviceIdentity: vi.fn(), retryFailedTarget: vi.fn()
}));
vi.mock("../api", () => ({ api: apiMocks }));

const status = { today: { date: "2026-07-16", message: "", succeeded: 0, failed: 1, total: 1, complete: false }, friends: [], history: [], scheduler: [], next_run: null, login: { status: "success" }, message_count: 2, issues: [], statistics: { last_completed_run: null, successful_today: 0, failed_today: 1, consecutive_successful_days: 0, success_rate_7d: 0, success_rate_30d: 0, retries_7d: 0, enabled_friend_count: 1, local_message_count: 2, active_message_pack_count: 1, configured_friend_count: 1, next_health_check: null, next_daily_send: null, most_recent_issue: null, log_summary: { active_errors: 0, warnings_24h: 0, successful_tasks_7d: 0, last_health_check: null, last_send: null, last_error_time: null } } };

beforeEach(() => {
  apiMocks.preflightStatus.mockResolvedValue({ running: false, progress: null, result: null });
  apiMocks.todayPlan.mockResolvedValue({ main_scheduled_time: "07:30", enabled_target_count: 1, completed_count: 0, pending_count: 1, blocked_count: 0, generated_at: "2026-07-16T07:00:00", estimated_finish: "2026-07-16T07:30", configuration_source: "current", targets: [{ target_id: "t1", display_name: "测试目标", planned_at: "2026-07-16T07:30", message_source: "全局本地文案库", suffix: "全局后缀", status: "pending", blocked_reason: null }] });
  apiMocks.failedTargets.mockResolvedValue({ summary: { success: 0, failed: 1, uncertain: 1, needs_attention: 1 }, items: [{ target_id: "t1", display_name: "测试目标", explanation: "发送结果不确定，为避免重复发送，已禁止自动重试。", reason_code: "confirmation_failed_uncertain", uncertain: true, safe_retry_available: false }] });
  apiMocks.serviceIdentity.mockResolvedValue({ application: "AutoDy", version: "1.0.0", git_commit: "abc123", python_executable: "python.exe", package_path: "src/autody", project_path: "project", frontend_build_version: "1.0.0" });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

test("does not render a preflight card or request preflight status on the normal dashboard", async () => {
  render(<DashboardPage status={status} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} />);

  expect(await screen.findByRole("heading", { name: "运行总览" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "发送前自检" })).not.toBeInTheDocument();
  expect(apiMocks.preflightStatus).not.toHaveBeenCalled();
});

test("keeps Test Center panels out of the normal dashboard", async () => {
  render(<DashboardPage status={status} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} />);

  await screen.findByRole("heading", { name: "运行总览" });
  expect(screen.queryByRole("heading", { name: "今日发送计划" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "今日异常目标" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "运行环境" })).not.toBeInTheDocument();
  expect(apiMocks.todayPlan).not.toHaveBeenCalled();
  expect(apiMocks.failedTargets).not.toHaveBeenCalled();
  expect(apiMocks.serviceIdentity).not.toHaveBeenCalled();
});

test("shows target-level Chinese reason, stage, retryability and action under partial history", () => {
  const partial = {
    ...status,
    history: [{
      run_id: "run-partial", date: "2026-07-16", task_type: "daily_send",
      trigger_source: "scheduled" as const, success_count: 8, failed_count: 1,
      skipped_count: 0, total_targets: 9, retry_count: 0,
      final_status: "retry_pending", end_time: "2026-07-16T07:31:00",
      target_failures: {
        "target-one": {
          category: "navigation", stage: "conversation_located",
          reason_code: "conversation_not_found",
          user_summary_zh: "无法在当前会话列表中找到目标",
          user_detail_zh: "会话定位：尚未访问输入框。",
          retryable: true, send_attempted: false, send_attempts: 0,
          uncertain_send: false, suggested_action: "retry",
          suggested_action_zh: "仅重试此目标",
          timestamp: "2026-07-16T07:30:10", run_id: "run-partial",
          target_stable_id: "target-one", account_scope: "account-one",
          binding_valid: true, account_scope_matches: true,
          safe_retry_available: true, diagnostic_details: {}
        }
      }
    }]
  };

  render(<DashboardPage status={partial} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} />);

  expect(screen.getByText("8/9")).toBeInTheDocument();
  expect(screen.getByText("无法在当前会话列表中找到目标")).toBeInTheDocument();
  expect(screen.getByText(/失败阶段：会话定位/)).toBeInTheDocument();
  expect(screen.getByText(/可安全重试/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "仅重试此目标" })).toBeInTheDocument();
  expect(screen.queryByText(/退出码 3/)).not.toBeInTheDocument();
});

test("shows reassociation or details instead of retry for unsafe failures", () => {
  const makeFailure = (reasonCode: string, action: string, actionZh: string, uncertain: boolean) => ({
    category: "identity", stage: "identity_verified", reason_code: reasonCode,
    user_summary_zh: reasonCode === "binding_stale" ? "目标绑定已过期，需要重新关联" : "消息发送状态无法确认，为防止重复发送已停止",
    user_detail_zh: "已安全停止。", retryable: false,
    send_attempted: uncertain, send_attempts: uncertain ? 1 : 0,
    uncertain_send: uncertain, suggested_action: action,
    suggested_action_zh: actionZh, timestamp: "2026-07-16T07:30:10",
    run_id: "run", target_stable_id: reasonCode, account_scope: "account",
    binding_valid: reasonCode !== "binding_stale", account_scope_matches: true,
    safe_retry_available: false, diagnostic_details: {}
  });
  const partial = {
    ...status,
    history: [{
      run_id: "run-actions", date: "2026-07-16", task_type: "daily_send",
      trigger_source: "scheduled" as const, success_count: 7, failed_count: 2,
      skipped_count: 0, total_targets: 9, retry_count: 0,
      final_status: "uncertain", end_time: "2026-07-16T07:31:00",
      target_failures: {
        stale: makeFailure("binding_stale", "reassociate", "重新关联", false),
        uncertain: makeFailure("confirmation_failed_uncertain", "details", "查看详情", true)
      }
    }]
  };

  render(<DashboardPage status={partial} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} />);

  expect(screen.getByRole("button", { name: "重新关联" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "查看详情" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "仅重试此目标" })).not.toBeInTheDocument();
});

test("groups only identical same-day target failures and expands newest first", () => {
  const makeFailure = (
    targetId: string,
    reasonCode: string,
    timestamp: string,
    summary: string
  ) => ({
    category: "navigation", stage: "conversation_located", reason_code: reasonCode,
    user_summary_zh: summary, user_detail_zh: `${timestamp} 的失败原因`,
    retryable: true, send_attempted: false, send_attempts: 0,
    uncertain_send: false, suggested_action: "retry",
    suggested_action_zh: "仅重试此目标", timestamp, run_id: timestamp,
    target_stable_id: targetId, account_scope: "account",
    binding_valid: true, account_scope_matches: true,
    safe_retry_available: true, diagnostic_details: {}
  });
  const row = (
    runId: string,
    endTime: string,
    targetId: string,
    failure: ReturnType<typeof makeFailure>
  ) => ({
    run_id: runId, date: "2026-07-16", task_type: "daily_send",
    trigger_source: "retry" as const, success_count: 0, failed_count: 1,
    skipped_count: 0, total_targets: 1, retry_count: 1,
    final_status: "retry_pending", end_time: endTime,
    target_failures: { [targetId]: failure }
  });
  const groupedStatus = {
    ...status,
    friends: [
      { target_id: "target-one", name: "目标一", status: "failed" as const },
      { target_id: "target-two", name: "目标二", status: "failed" as const }
    ],
    history: [
      row("run-new", "2026-07-16T09:01:00", "target-one", makeFailure("target-one", "conversation_not_found", "2026-07-16T09:00:00", "最新重复失败")),
      row("run-middle", "2026-07-16T08:01:00", "target-one", makeFailure("target-one", "conversation_not_found", "2026-07-16T08:00:00", "最新重复失败")),
      row("run-old", "2026-07-16T07:01:00", "target-one", makeFailure("target-one", "conversation_not_found", "2026-07-16T07:00:00", "最新重复失败")),
      row("run-other-target", "2026-07-16T06:01:00", "target-two", makeFailure("target-two", "conversation_not_found", "2026-07-16T06:00:00", "其他目标失败")),
      row("run-other-reason", "2026-07-16T05:01:00", "target-one", makeFailure("target-one", "page_load_timeout", "2026-07-16T05:00:00", "不同原因失败"))
    ]
  };

  render(<DashboardPage status={groupedStatus} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} />);

  const group = screen.getByRole("button", { name: /目标一 最新重复失败/ });
  expect(screen.getAllByText("最新重复失败")).toHaveLength(1);
  expect(screen.getByLabelText("共 3 条重复通知")).toHaveTextContent("3");
  expect(screen.getByText("2026-07-16T09:00:00 的失败原因")).toBeInTheDocument();
  expect(screen.getByText("其他目标失败")).toBeInTheDocument();
  expect(screen.getByText("不同原因失败")).toBeInTheDocument();
  expect(screen.queryByText(/点击展开|点击查看全部|点击卡片可展开/)).not.toBeInTheDocument();

  fireEvent.click(group);
  expect(screen.getAllByText("最新重复失败")).toHaveLength(3);
  const expandedReasons = screen.getAllByText(/T0[789]:00:00 的失败原因/);
  expect(expandedReasons.map((item) => item.textContent)).toEqual([
    "2026-07-16T09:00:00 的失败原因",
    "2026-07-16T08:00:00 的失败原因",
    "2026-07-16T07:00:00 的失败原因"
  ]);

  fireEvent.click(group);
  expect(screen.getAllByText("最新重复失败")).toHaveLength(1);
});

test("target retry action keeps the stable target id", () => {
  const retry = vi.fn();
  const partial = {
    ...status,
    history: [{
      run_id: "run-partial", date: "2026-07-16", task_type: "daily_send",
      trigger_source: "scheduled" as const, success_count: 0, failed_count: 1,
      skipped_count: 0, total_targets: 1, retry_count: 0,
      final_status: "retry_pending", end_time: "2026-07-16T07:31:00",
      target_failures: {
        "stable-target-id": {
          category: "navigation", stage: "conversation_located",
          reason_code: "conversation_not_found", user_summary_zh: "失败",
          user_detail_zh: "失败原因", retryable: true, send_attempted: false,
          send_attempts: 0, uncertain_send: false, suggested_action: "retry",
          suggested_action_zh: "仅重试此目标", timestamp: "2026-07-16T07:30:10",
          run_id: "run-partial", target_stable_id: "stable-target-id",
          account_scope: "account", binding_valid: true,
          account_scope_matches: true, safe_retry_available: true,
          diagnostic_details: {}
        }
      }
    }]
  };

  render(<DashboardPage status={partial} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} onRetryTarget={retry} />);
  fireEvent.click(screen.getByRole("button", { name: "仅重试此目标" }));

  expect(retry).toHaveBeenCalledWith("stable-target-id");
});
