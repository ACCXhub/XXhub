import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";

const apiMocks = vi.hoisted(() => ({
  status: vi.fn().mockResolvedValue({
    today: { date: "2026-06-24", message: "测试文案", succeeded: 9, failed: 0, total: 9, complete: true },
    friends: [{ name: "小明", status: "success" }],
    history: [{ run_id: "run-1", date: "2026-06-24", task_type: "daily_send", trigger_source: "scheduled", success_count: 9, failed_count: 0, skipped_count: 0, total_targets: 9, retry_count: 0, final_status: "completed", end_time: "2026-06-24T07:31:00" }],
    scheduler: [],
    next_run: "2026-06-25T07:30:00",
    login: { status: "normal" },
    message_count: 60,
    issues: [],
    statistics: { last_completed_run: "2026-06-24T07:31:00", consecutive_successful_days: 7, success_rate_7d: 100, success_rate_30d: 98, successful_today: 9, failed_today: 0, configured_friend_count: 9, enabled_friend_count: 9, local_message_count: 60, active_message_pack_count: 5, next_health_check: null, next_daily_send: "2026-06-25T07:30:00", most_recent_issue: null }
  }),
  action: vi.fn(),
  waitForAction: vi.fn(),
  accountProfile: vi.fn().mockResolvedValue({
    display_name: "本人", avatar_url: "/api/account-profile/avatar?v=test", avatar_version: "test",
    is_self: true, profile_status: "verified", verification_source: "bootstrap_current_login_user",
    logged_in: true, cached: true, last_updated_at: "2026-07-15T08:00:00", refresh_running: false
  }),
  refreshAccountProfile: vi.fn(),
  logoutAccount: vi.fn(),
  accountProfiles: vi.fn().mockResolvedValue({
    active_profile_id: "account-a",
    profiles: [
      { profile_id: "account-a", display_name: "本人", active: true, logged_in: true, profile_status: "verified" },
      { profile_id: "account-b", display_name: "账号乙", active: false, logged_in: true, profile_status: "verified" }
    ],
    migration_required: false
  }),
  switchAccountProfile: vi.fn(),
  addAccountProfile: vi.fn(),
  retryFailedTarget: vi.fn(),
  config: vi.fn(),
  friends: vi.fn(),
  discoveredFriends: vi.fn(),
  addCandidateToTargets: vi.fn(),
  modules: vi.fn().mockResolvedValue({ modules: [{ id: "autody-test-center", installed: false }] }),
  messagePacks: vi.fn().mockResolvedValue({
    packs: [{ id: "daily", name: "日常问候", description: "自然短问候", version: "1.0.0", count: 50, category: "daily" }],
  }),
  preflightLatest: vi.fn().mockResolvedValue({ result: { global_status: "ready", total_targets: 1, ready_count: 1, failed_count: 0, blocked_count: 0, completed_at: "2026-07-16T07:20:00", targets: [] } }),
  preflightStatus: vi.fn().mockResolvedValue({ running: false, result: { global_status: "ready", total_targets: 1, ready_count: 1, failed_count: 0, blocked_count: 0, completed_at: "2026-07-16T07:20:00", targets: [] } }),
  runPreflight: vi.fn(),
  cancelPreflight: vi.fn(),
  todayPlan: vi.fn().mockResolvedValue({ main_scheduled_time: "07:30", enabled_target_count: 0, completed_count: 0, pending_count: 0, blocked_count: 0, generated_at: "2026-07-16T07:00:00", estimated_finish: "2026-07-16T07:30", configuration_source: "current", targets: [] }),
  failedTargets: vi.fn().mockResolvedValue({ summary: { success: 0, failed: 0, uncertain: 0, needs_attention: 0 }, items: [] }),
  serviceIdentity: vi.fn().mockResolvedValue({ application: "AutoDy", version: "1.1.2", git_commit: "test", python_executable: "python.exe", package_path: "src/autody", project_path: "project", frontend_build_version: "1.1.2" })
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

vi.mock("./api", () => ({
  api: {
    status: apiMocks.status,
    action: apiMocks.action,
    waitForAction: apiMocks.waitForAction,
    accountProfile: apiMocks.accountProfile,
    refreshAccountProfile: apiMocks.refreshAccountProfile,
    logoutAccount: apiMocks.logoutAccount,
    accountProfiles: apiMocks.accountProfiles,
    switchAccountProfile: apiMocks.switchAccountProfile,
    addAccountProfile: apiMocks.addAccountProfile,
    retryFailedTarget: apiMocks.retryFailedTarget,
    config: apiMocks.config,
    friends: apiMocks.friends,
    discoveredFriends: apiMocks.discoveredFriends,
    addCandidateToTargets: apiMocks.addCandidateToTargets,
    modules: apiMocks.modules,
    messagePacks: apiMocks.messagePacks,
    preflightLatest: apiMocks.preflightLatest,
    preflightStatus: apiMocks.preflightStatus,
    runPreflight: apiMocks.runPreflight,
    cancelPreflight: apiMocks.cancelPreflight,
    todayPlan: apiMocks.todayPlan,
    failedTargets: apiMocks.failedTargets,
    serviceIdentity: apiMocks.serviceIdentity
  }
}));

test("renders the primary dashboard status", async () => {
  render(<App />);
  expect(await screen.findByText("运行总览")).toBeInTheDocument();
  expect(screen.getByText("当前抖音账号")).toBeInTheDocument();
  expect(screen.getByAltText("当前账号头像")).toHaveAttribute("src", "/api/account-profile/avatar?v=test");
  expect(screen.getByText("已完成")).toBeInTheDocument();
  expect(screen.getByText("9/9")).toBeInTheDocument();
  expect(screen.getByText("检查登录")).toBeInTheDocument();
  expect(screen.queryByText("发送前自检")).not.toBeInTheDocument();
  expect(apiMocks.preflightStatus).not.toHaveBeenCalled();
});

test("shows a sanitized retryable error instead of loading forever", async () => {
  apiMocks.status.mockRejectedValueOnce(
    new Error("Traceback C:\\private\\profile token=secret-value")
  );

  render(<App />);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "无法加载 AutoDy 状态"
  );
  expect(screen.getByText("状态总览 API 暂时不可用。")).toBeInTheDocument();
  const diagnostics = screen.getByText("诊断信息").closest("details");
  expect(diagnostics).not.toHaveAttribute("open");
  expect(screen.queryByText(/Traceback|private|secret-value/)).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "重试加载" }));

  expect(await screen.findByText("运行总览")).toBeInTheDocument();
  expect(apiMocks.status).toHaveBeenCalledTimes(2);
});

test("times out the initial status request with an explained fallback", async () => {
  vi.useFakeTimers();
  apiMocks.status.mockReturnValueOnce(new Promise(() => undefined));

  render(<App />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10_000);
  });

  expect(screen.getByRole("alert")).toHaveTextContent("无法加载 AutoDy 状态");
  expect(screen.getByText("原因：请求超时")).toBeInTheDocument();
});

test("keeps browser action buttons disabled until the action finishes", async () => {
  let finish: (value: { status: string }) => void = () => undefined;
  apiMocks.action.mockResolvedValue({ id: "job-1", action: "run", status: "running" });
  apiMocks.waitForAction.mockImplementation(
    () => new Promise((resolve) => { finish = resolve; })
  );
  render(<App />);
  const runButton = await screen.findByRole("button", { name: "立即运行" });
  fireEvent.click(runButton);

  await waitFor(() => expect(apiMocks.action).toHaveBeenCalledWith("run"));
  expect(runButton).toBeDisabled();
  expect(screen.getByRole("button", { name: "检查登录" })).toBeDisabled();

  finish({ status: "success" });
  await waitFor(() => expect(runButton).not.toBeDisabled());
});

test("does not expose a preflight action from the normal dashboard", async () => {
  render(<App />);

  await screen.findByText("运行总览");
  expect(screen.queryByRole("button", { name: "测试全部续火目标" })).not.toBeInTheDocument();
  expect(apiMocks.runPreflight).not.toHaveBeenCalled();
});

test("opens the online message library from navigation", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "文案包" }));

  expect(await screen.findByRole("heading", { name: "文案包" })).toBeInTheDocument();
  expect(screen.getByText("日常问候")).toBeInTheDocument();
});

test("shows a localized account-profile route error instead of FastAPI detail", async () => {
  apiMocks.refreshAccountProfile.mockRejectedValueOnce(new Error('{"detail":"Not Found"}'));
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "刷新当前账号资料" }));

  expect(await screen.findByText("当前账号资料接口不可用，请重启 AutoDy 管理台。"))
    .toBeInTheDocument();
  expect(screen.queryByText(/detail.*Not Found/)).not.toBeInTheDocument();
});

test("refreshes the overview resource count immediately after a friend mutation", async () => {
  const status = (count: number) => ({
    today: { date: "2026-07-30", message: "", succeeded: 0, failed: 0, total: count, complete: false },
    friends: [],
    history: [],
    scheduler: [],
    next_run: null,
    login: { status: "normal" },
    message_count: 2,
    issues: [],
    statistics: {
      last_completed_run: null, consecutive_successful_days: 0, success_rate_7d: 0,
      success_rate_30d: 0, successful_today: 0, failed_today: 0,
      configured_friend_count: count, enabled_friend_count: count, local_message_count: 2,
      active_message_pack_count: 1, next_health_check: null, next_daily_send: null,
      most_recent_issue: null
    }
  });
  apiMocks.status.mockResolvedValueOnce(status(1)).mockResolvedValue(status(2));
  apiMocks.config.mockResolvedValue({
    targets: ["已有目标"], retry_count: 3, timeout_ms: 30000, headless: true,
    message_suffix: { enabled: true, text: "gpt小助手", style: "dash" },
    daily_send_time: "07:30", daily_health_check_time: "07:20",
    weekly_health_check_enabled: true, weekly_health_check_weekday: "Sunday",
    weekly_health_check_time: "20:00",
    recovery_deadline: "23:59", min_delay_seconds: 1, max_delay_seconds: 3,
    page_load_timeout_ms: 30000, friend_search_timeout_ms: 30000,
    confirmation_timeout_ms: 12000, friend_order: "configured",
    message_selection: "one_for_all", completion_notifications_enabled: true,
    log_retention_days: 30, mask_log_friend_names: true
  });
  apiMocks.friends.mockResolvedValue({ friends: [] });
  apiMocks.discoveredFriends.mockResolvedValue({
    scanned_at: "2026-07-30T08:00:00", stale: false, refresh_running: false,
    last_result: {}, candidates: [{
      candidate_id: "candidate-new", display_name: "新候选", avatar_url: "",
      avatar_status: "missing", discovered_at: "2026-07-30T08:00:00",
      match_status: "unconfigured", presence_status: "current"
    }]
  });
  apiMocks.addCandidateToTargets.mockResolvedValue({
    created: true,
    target: { target_id: "target-new", display_name: "新候选", enabled: true }
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "好友管理" }));
  fireEvent.click(await screen.findByRole("button", { name: "添加 新候选" }));

  await waitFor(() => expect(apiMocks.status).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole("button", { name: "总览" }));
  expect(await screen.findByText("2 位好友")).toBeInTheDocument();
});

test("switches a saved account and refreshes every shared view immediately", async () => {
  apiMocks.switchAccountProfile.mockResolvedValue({
    active_profile_id: "account-b",
    profiles: [
      { profile_id: "account-a", display_name: "本人", active: false, logged_in: true, profile_status: "verified" },
      { profile_id: "account-b", display_name: "账号乙", active: true, logged_in: true, profile_status: "verified" }
    ]
  });
  apiMocks.accountProfile
    .mockResolvedValueOnce({
      display_name: "本人", avatar_url: "/api/account-profile/avatar?v=a", avatar_version: "a",
      is_self: true, profile_status: "verified", verification_source: "test",
      logged_in: true, cached: true, last_updated_at: "2026-07-30T08:00:00", refresh_running: false
    })
    .mockResolvedValue({
      display_name: "账号乙", avatar_url: "/api/account-profile/avatar?v=b", avatar_version: "b",
      is_self: true, profile_status: "verified", verification_source: "test",
      logged_in: true, cached: true, last_updated_at: "2026-07-30T08:01:00", refresh_running: false
    });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "打开账号切换器" }));
  fireEvent.click(await screen.findByRole("button", { name: "切换到 账号乙" }));

  await waitFor(() => {
    expect(apiMocks.switchAccountProfile).toHaveBeenCalledWith("account-b");
    expect(screen.getByText("账号乙")).toBeInTheDocument();
  });
  expect(apiMocks.status).toHaveBeenCalledTimes(2);
  expect(apiMocks.accountProfiles).toHaveBeenCalledTimes(2);
  expect(apiMocks.modules).toHaveBeenCalledTimes(2);
});
