import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FriendsPage } from "./FriendsPage";

const apiMocks = vi.hoisted(() => ({
  config: vi.fn(),
  friends: vi.fn(),
  discoveredFriends: vi.fn(),
  scanFriends: vi.fn(),
  refreshFriendAvatars: vi.fn(),
  waitForAction: vi.fn(),
  addCandidateToTargets: vi.fn(),
  relinkCandidate: vi.fn(),
  ignoreFriendOrphan: vi.fn(),
  logoutAccount: vi.fn(),
  friendBatch: vi.fn(),
  saveConfig: vi.fn(),
  preflightLatest: vi.fn(),
  runPreflight: vi.fn(),
  saveTargetSettings: vi.fn()
}));

vi.mock("../api", () => ({ api: apiMocks }));

const config = {
  targets: ["小明"], retry_count: 3, timeout_ms: 30000, headless: true,
  message_suffix: { enabled: true, text: "gpt小助手", style: "dash" as const },
  message_pack_index_url: null,
  daily_send_time: "07:30", daily_health_check_time: "07:20", weekly_health_check_enabled: true,
  weekly_health_check_weekday: "Sunday", weekly_health_check_time: "20:00",
  startup_recovery_enabled: true, recovery_deadline: "23:59", min_delay_seconds: 1,
  max_delay_seconds: 3, page_load_timeout_ms: 30000, friend_search_timeout_ms: 30000,
  confirmation_timeout_ms: 12000, friend_order: "configured" as const,
  message_selection: "one_for_all" as const, completion_notifications_enabled: true,
  log_retention_days: 30, mask_log_friend_names: true
};

const discovered = {
  scanned_at: "2026-07-04T12:30:00",
  stale: true,
  refresh_running: true,
  last_result: { status: "completed", candidates_found: 2, avatars_updated: 1, avatars_failed: 0 },
  candidates: [
    {
      candidate_id: "friend-xiaoming", display_name: "小明", avatar_url: "/api/avatars/friend-xiaoming",
      avatar_status: "cached" as const, discovered_at: "2026-07-04T12:30:00", match_status: "configured" as const,
      configured: true, target_id: "friend-xiaoming", enabled: true,
      configured_target_id: "friend-xiaoming", configured_enabled: true
    },
    {
      candidate_id: "candidate-new", display_name: "新朋友", avatar_url: "/api/avatars/candidate-new",
      avatar_status: "cached" as const, discovered_at: "2026-07-04T12:30:00", match_status: "unconfigured" as const,
      configured: false, target_id: null, enabled: null,
      configured_target_id: null, configured_enabled: null
    }
  ]
};

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.config.mockResolvedValue(config);
  apiMocks.friends.mockResolvedValue({
    friends: [{
      id: "friend-xiaoming", display_name: "小明", enabled: true,
      avatar_url: "/api/avatars/friend-xiaoming", avatar_status: "cached",
      today_status: "success", last_success_date: "2026-07-04", note: ""
    }]
  });
  apiMocks.discoveredFriends.mockResolvedValue(discovered);
  apiMocks.scanFriends.mockResolvedValue({ id: "scan-1", action: "scan-friends", status: "running" });
  apiMocks.refreshFriendAvatars.mockResolvedValue({ id: "avatar-1", action: "refresh-friend-avatars", status: "running" });
  apiMocks.waitForAction.mockResolvedValue({ id: "scan-1", action: "scan-friends", status: "success" });
  apiMocks.addCandidateToTargets.mockResolvedValue({
    created: true,
    target: { target_id: "candidate-new", display_name: "新朋友", enabled: true }
  });
  apiMocks.friendBatch.mockResolvedValue({ affected: 1 });
  apiMocks.relinkCandidate.mockResolvedValue({ target_id: "target-orphan", candidate_id: "candidate-new", display_name: "新朋友" });
  apiMocks.ignoreFriendOrphan.mockResolvedValue({ ignored: true });
  apiMocks.logoutAccount.mockResolvedValue({
    display_name: null,
    avatar_url: null,
    avatar_version: null,
    is_self: false,
    profile_status: "unverified",
    verification_source: null,
    logged_in: false,
    cached: false,
    last_updated_at: null,
    refresh_running: false
  });
  apiMocks.saveConfig.mockResolvedValue(config);
  apiMocks.preflightLatest.mockResolvedValue({ result: null });
  apiMocks.runPreflight.mockResolvedValue({ id: "preflight-1", action: "preflight", status: "running" });
  apiMocks.saveTargetSettings.mockResolvedValue({ target_id: "friend-xiaoming", settings: { message_source: "全局本地文案库", suffix: "已禁用", delay_offset_minutes: 0 } });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("shows local avatars, adds a discovered friend on click, and removes candidate checkboxes", async () => {
  render(<FriendsPage notify={vi.fn()} />);

  expect((await screen.findAllByAltText("小明 的头像"))[0]).toHaveAttribute("loading", "lazy");
  expect(screen.getByText(/今日已完成/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /导入好友|导出 CSV|导出 JSON/ })).not.toBeInTheDocument();
  expect(screen.getByText(/候选好友来自本地缓存/)).toBeInTheDocument();
  expect(screen.getByText("正在后台更新候选好友和头像…")).toBeInTheDocument();

  expect(screen.queryByRole("checkbox", { name: "选择 新朋友" })).not.toBeInTheDocument();
  const candidate = await screen.findByRole("button", { name: "添加 新朋友" });
  fireEvent.click(candidate);

  await waitFor(() => expect(apiMocks.addCandidateToTargets).toHaveBeenCalledWith("candidate-new"));
  expect((await screen.findAllByText("已添加")).length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "添加 新朋友" })).toBeDisabled();
  const newFriendAvatars = await screen.findAllByAltText("新朋友 的头像");
  expect(newFriendAvatars.every((avatar) => avatar.getAttribute("loading") === "lazy")).toBe(true);
  expect(newFriendAvatars.map((avatar) => avatar.getAttribute("src"))).toEqual(["/api/avatars/candidate-new", "/api/avatars/candidate-new"]);
});


test("starts the avatar-correction scan without a send action", async () => {
  render(<FriendsPage notify={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "重新扫描并修正头像对应关系" }));

  await waitFor(() => expect(apiMocks.refreshFriendAvatars).toHaveBeenCalledTimes(1));
  expect(apiMocks.scanFriends).not.toHaveBeenCalled();
});

test("selects target cards by click and keyboard without letting nested controls toggle twice", async () => {
  render(<FriendsPage notify={vi.fn()} />);

  const checkbox = await screen.findByRole("checkbox", { name: "选择 小明" });
  const card = checkbox.closest(".friend-editor-row");
  const deleteButton = screen.getByRole("button", { name: "删除目标 小明" });

  expect(card).not.toBeNull();
  expect(checkbox).not.toBeChecked();
  expect(screen.queryByLabelText("已选择")).not.toBeInTheDocument();
  fireEvent.click(card!);
  expect(checkbox).toBeChecked();
  expect(screen.getByLabelText("已选择")).toBeInTheDocument();
  expect(screen.getByText("已选择 1 个目标")).toBeInTheDocument();
  fireEvent.keyDown(card!, { key: "Enter" });
  expect(checkbox).not.toBeChecked();
  fireEvent.keyDown(card!, { key: " " });
  expect(checkbox).toBeChecked();

  fireEvent.click(checkbox);
  expect(checkbox).not.toBeChecked();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(deleteButton);
  expect(confirm).toHaveBeenCalledWith("删除目标「小明」？");
  expect(apiMocks.friendBatch).toHaveBeenCalledWith(["friend-xiaoming"], "delete");
  expect(checkbox).not.toBeChecked();
});

test("renders the target delete control as a compact absolute card action", async () => {
  render(<FriendsPage notify={vi.fn()} />);

  const button = await screen.findByRole("button", { name: "删除目标 小明" });
  expect(button).toHaveAttribute("title", "删除目标");
  expect(button).toHaveClass("icon-button", "danger");
  expect(button.parentElement).toHaveClass("friend-editor-row");
});

test("does not expose single-target preflight controls or request preflight data", async () => {
  render(<FriendsPage notify={vi.fn()} />);

  await screen.findByRole("checkbox", { name: "选择 小明" });
  expect(screen.queryByText("测试可发送状态")).not.toBeInTheDocument();
  expect(apiMocks.preflightLatest).not.toHaveBeenCalled();
  expect(apiMocks.runPreflight).not.toHaveBeenCalled();
});

test("keeps advanced target overrides out of the normal friend page", async () => {
  render(<FriendsPage notify={vi.fn()} />);

  await screen.findByText("续火目标");
  expect(screen.queryByRole("button", { name: "编辑目标设置 小明" })).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "编辑目标设置" })).not.toBeInTheDocument();
  expect(apiMocks.saveTargetSettings).not.toHaveBeenCalled();
});

test("offers explicit relink and ignore actions for a genuine orphan binding", async () => {
  apiMocks.discoveredFriends.mockResolvedValueOnce({
    ...discovered,
    refresh_running: false,
    candidates: [{
      ...discovered.candidates[1],
      match_status: "needs_reassociation",
      reassociation_target_id: "target-orphan"
    }],
    orphans: [{ target_id: "target-orphan", display_name: "待关联目标", enabled: true }]
  });
  const onDataChanged = vi.fn();
  render(<FriendsPage notify={vi.fn()} onDataChanged={onDataChanged} />);

  fireEvent.click(await screen.findByRole("button", { name: "重新关联 新朋友" }));

  await waitFor(() => expect(apiMocks.relinkCandidate).toHaveBeenCalledWith("candidate-new", "target-orphan"));
  expect(onDataChanged).toHaveBeenCalled();
});

test("keeps account refresh, switching and logout out of Friend Management", async () => {
  render(<FriendsPage notify={vi.fn()} onDataChanged={vi.fn()} />);

  expect(await screen.findByRole("heading", { name: "好友管理" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /退出账号|退出当前账号/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "刷新当前账号资料" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "扫描好友" })).toBeInTheDocument();
});

test("shows configured targets as pending revalidation after managed logout", async () => {
  apiMocks.friends.mockResolvedValueOnce({
    friends: [{
      id: "friend-xiaoming", display_name: "小明", enabled: true,
      avatar_url: "/api/avatars/friend-xiaoming", avatar_status: "cached",
      today_status: "pending", last_success_date: null, note: "",
      binding_status: "revalidation_required"
    }]
  });

  render(<FriendsPage notify={vi.fn()} />);

  expect(await screen.findByText("绑定待重新验证")).toBeInTheDocument();
});
