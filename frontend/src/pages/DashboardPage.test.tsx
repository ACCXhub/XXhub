import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { FailureDetail } from "../types";
import { DashboardPage } from "./DashboardPage";

const apiMocks = vi.hoisted(() => ({
  preflightStatus: vi.fn(), runPreflight: vi.fn(), cancelPreflight: vi.fn(),
  todayPlan: vi.fn(), failedTargets: vi.fn(), serviceIdentity: vi.fn(), retryFailedTarget: vi.fn()
}));
vi.mock("../api", () => ({ api: apiMocks }));

const status = { today: { date: "2026-07-16", message: "", succeeded: 0, failed: 1, total: 1, complete: false }, friends: [], history: [], scheduler: [], next_run: null, login: { status: "success" }, message_count: 2, issues: [], statistics: { last_completed_run: null, successful_today: 0, failed_today: 1, consecutive_successful_days: 0, success_rate_7d: 0, success_rate_30d: 0, enabled_friend_count: 1, local_message_count: 2, active_message_pack_count: 1, configured_friend_count: 1, next_health_check: null, next_daily_send: null, most_recent_issue: null, log_summary: { active_errors: 0, warnings_24h: 0, successful_tasks_7d: 0, last_health_check: null, last_send: null, last_error_time: null } } };

beforeEach(() => {
  apiMocks.preflightStatus.mockResolvedValue({ running: false, progress: null, result: null });
  apiMocks.todayPlan.mockResolvedValue({ main_scheduled_time: "07:30", enabled_target_count: 1, completed_count: 0, pending_count: 1, blocked_count: 0, generated_at: "2026-07-16T07:00:00", estimated_finish: "2026-07-16T07:30", configuration_source: "current", targets: [{ target_id: "t1", display_name: "测试目标", planned_at: "2026-07-16T07:30", message_source: "全局本地文案库", suffix: "全局后缀", status: "pending", blocked_reason: null }] });
  apiMocks.failedTargets.mockResolvedValue({ summary: { success: 0, failed: 1, uncertain: 1, needs_attention: 1 }, items: [{ target_id: "t1", display_name: "测试目标", explanation: "发送结果不确定，为避免重复发送，已禁止自动重试。", reason_code: "confirmation_failed_uncertain", uncertain: true, safe_retry_available: false }] });
  apiMocks.serviceIdentity.mockResolvedValue({ application: "AutoDy", version: "1.0.0", git_commit: "abc123", python_executable: "python.exe", package_path: "src/autody", project_path: "project", frontend_build_version: "1.0.0" });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers(); });

test("shows healthy binding and the current send failure without duplicate health copy", () => {
  const currentStatus = {
    ...status,
    friends: [{
      target_id: "target-one", name: "当前有效目标", status: "failed" as const,
      error: "历史会话定位失败",
      failure: failure("target-one"),
      current_health: { status: "healthy" as const, reason_code: "binding_valid", summary_zh: "绑定有效" }
    }],
    issues: [{
      id: "send_failure_retryable", status: "warning" as const,
      explanation: "当前有效目标：无法在当前会话列表中找到目标。",
      action: "retry_target", action_label: "安全补发", target_ids: ["target-one"]
    }]
  };

  render(<DashboardPage status={currentStatus} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} />);

  const panel = screen.getByText("好友状态").closest("section");
  expect(panel).not.toBeNull();
  expect(within(panel as HTMLElement).getByText("正常")).toBeInTheDocument();
  expect(within(panel as HTMLElement).queryByText("绑定有效")).not.toBeInTheDocument();
  expect(within(panel as HTMLElement).getByText("今日失败")).toBeInTheDocument();
  expect(within(panel as HTMLElement).getByText("无法在当前会话列表中找到目标")).toBeInTheDocument();
  expect(within(panel as HTMLElement).queryByText("历史会话定位失败")).not.toBeInTheDocument();
});

test("uses resolved avatar metadata and keeps operational sections in priority order", () => {
  const currentStatus = {
    ...status,
    friends: [{
      target_id: "target-one", name: "当前好友", avatar_url: "/api/avatars/candidate-current?v=1",
      status: "pending" as const,
      current_health: { status: "healthy" as const, reason_code: "binding_valid", summary_zh: "绑定有效" }
    }]
  };

  render(<DashboardPage status={currentStatus} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} />);

  expect(screen.getByRole("img", { name: "当前好友 的头像" })).toHaveAttribute(
    "src", "/api/avatars/candidate-current?v=1"
  );
  const labels = Array.from(document.querySelectorAll(".dashboard-page > section"))
    .map((node) => node.getAttribute("aria-label") || node.querySelector("h2")?.textContent);
  expect(labels).toEqual(["核心状态", "需要处理", "好友状态", "运行统计"]);
});

test("removes run-by-run history and retry-count noise from the dashboard", () => {
  render(<DashboardPage status={status} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} />);

  expect(screen.queryByRole("table", { name: "结构化运行记录" })).not.toBeInTheDocument();
  expect(screen.queryByText(/7 天重试/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重试所有目标" })).not.toBeInTheDocument();
});

test("renders tomorrow and later next-run dates without M/D ambiguity", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-08-09T12:00:00"));
  const tomorrow = { ...status, next_run: "2026-08-10T07:30:00" };
  const { rerender } = render(
    <DashboardPage status={tomorrow} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} />
  );

  expect(screen.getByText("明日 07:30")).toBeInTheDocument();

  rerender(
    <DashboardPage status={{ ...status, next_run: "2026-08-12T07:30:00" }} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} />
  );
  expect(screen.getByText("8月12日 07:30")).toBeInTheDocument();
  expect(screen.queryByText(/8\/12/)).not.toBeInTheDocument();
});

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

test("starts the safe one-click diagnosis and repair action", () => {
  const onAction = vi.fn();
  render(<DashboardPage status={status} busy={null} onAction={onAction} onNavigate={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: "一键诊断与修复" }));

  expect(onAction).toHaveBeenCalledWith("diagnose-and-repair");
  expect(screen.queryByRole("button", { name: "修复运行时" })).not.toBeInTheDocument();
});

function failure(
  targetId: string,
  overrides: Partial<FailureDetail> = {}
): FailureDetail {
  return {
    category: "navigation", stage: "conversation_located",
    reason_code: "conversation_not_found",
    user_summary_zh: "无法在当前会话列表中找到目标",
    user_detail_zh: "会话定位：尚未访问输入框。",
    retryable: true, send_attempted: false, send_attempts: 0,
    uncertain_send: false, suggested_action: "retry",
    suggested_action_zh: "仅重试此目标",
    timestamp: "2026-07-16T07:30:10", run_id: "run-partial",
    target_stable_id: targetId, account_scope: "account-one",
    binding_valid: true, account_scope_matches: true,
    safe_retry_available: true, diagnostic_details: {},
    ...overrides
  };
}

test("hides the resend action when no current target is safely retryable", () => {
  render(<DashboardPage status={status} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} onRetryTargets={vi.fn()} />);

  expect(screen.queryByRole("button", { name: "补发" })).not.toBeInTheDocument();
  expect(screen.queryByText("仅补发今日尚未成功且可安全重试的目标")).not.toBeInTheDocument();
});

test("safe resend includes only unresolved current failures allowed by safety gates", () => {
  const retryTargets = vi.fn();
  const mixed = {
    ...status,
    friends: [
      {
        target_id: "target-safe", name: "安全目标", status: "failed" as const,
        failure: failure("target-safe", { run_id: "run-mixed" })
      },
      {
        target_id: "target-missing", name: "缺少绑定", status: "failed" as const,
        failure: failure("target-missing", {
          category: "identity", stage: "target_binding_resolved",
          reason_code: "binding_missing", retryable: false,
          suggested_action: "reassociate", suggested_action_zh: "重新关联",
          binding_valid: false, safe_retry_available: false, run_id: "run-mixed"
        })
      },
      {
        target_id: "target-resolved", name: "已解决", status: "success" as const,
        failure: failure("target-resolved", {
          resolved: true, retry_action_available: false,
          resolved_at: "2026-07-16T09:00:00", run_id: "run-mixed"
        })
      }
    ],
    issues: [{
      id: "send_failure_retryable", status: "warning" as const,
      explanation: "安全目标：无法在当前会话列表中找到目标。",
      action: "retry_target", action_label: "安全补发", target_ids: ["target-safe"]
    }],
    history: [{
      run_id: "run-mixed", date: "2026-07-16", task_type: "daily_send",
      trigger_source: "retry" as const, success_count: 1, failed_count: 2,
      skipped_count: 0, total_targets: 3, retry_count: 1,
      final_status: "retry_pending", end_time: "2026-07-16T08:31:00",
      target_failures: {
        "target-safe": failure("target-safe", { run_id: "run-mixed" }),
        "target-missing": failure("target-missing", {
          category: "identity", stage: "target_binding_resolved",
          reason_code: "binding_missing", user_summary_zh: "目标缺少稳定绑定",
          user_detail_zh: "需要重新关联。", retryable: false,
          suggested_action: "reassociate", suggested_action_zh: "重新关联",
          binding_valid: false, safe_retry_available: false, run_id: "run-mixed"
        }),
        "target-resolved": failure("target-resolved", {
          resolved: true, retry_action_available: false,
          resolved_at: "2026-07-16T09:00:00",
          resolution_zh: "已通过后续成功补发解决", run_id: "run-mixed"
        })
      }
    }]
  };

  render(<DashboardPage status={mixed} busy={null} onAction={vi.fn()} onNavigate={vi.fn()} onRetryTargets={retryTargets} />);

  expect(screen.getByText("安全目标：无法在当前会话列表中找到目标。")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "安全补发" }));
  expect(retryTargets).toHaveBeenCalledWith(["target-safe"]);
});
