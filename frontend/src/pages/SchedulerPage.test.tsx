import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { DashboardStatus } from "../types";
import { SchedulerPage } from "./SchedulerPage";

const apiMocks = vi.hoisted(() => ({
  config: vi.fn(),
  schedulerPreview: vi.fn(),
  schedulerApply: vi.fn(),
  schedulerOperation: vi.fn(),
}));

vi.mock("../api", () => ({ api: apiMocks }));

const status = {
  today: { date: "2026-08-07", message: "", succeeded: 0, failed: 0, total: 8, complete: false },
  friends: [],
  history: [],
  scheduler: [
    {
      name: "AutoDy-DailySpark",
      state: "Ready",
      next_run: "2026-08-08T07:30:00",
      last_run: "2026-08-07T07:30:00",
      last_result: 0,
      installed: true,
      configured_enabled: true,
      configured_time: "07:30",
      target_count: 8,
      drift: false,
    },
    {
      name: "AutoDy-Health-Weekly",
      state: "Missing",
      next_run: "",
      last_run: "",
      last_result: null,
      installed: false,
      configured_enabled: false,
      configured_time: "20:00",
      target_count: null,
      drift: false,
    },
  ],
  next_run: "2026-08-08T07:30:00",
  login: { status: "healthy" },
  message_count: 3,
  issues: [],
  statistics: { enabled_friend_count: 8 },
} as unknown as DashboardStatus;

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.config.mockResolvedValue({
    daily_health_check_time: "07:20",
    daily_send_time: "07:30",
    weekly_health_check_enabled: false,
    weekly_health_check_weekday: "Sunday",
    weekly_health_check_time: "20:00",
    startup_recovery_enabled: true,
    recovery_deadline: "23:59",
  });
});

afterEach(cleanup);

test("shows one compact authoritative schedule row with state, time, targets, result and edit", async () => {
  render(<SchedulerPage status={status} notify={vi.fn()} onRefresh={vi.fn()} />);

  const row = await screen.findByRole("article", { name: "每日续火发送" });
  expect(row).toHaveTextContent("已启用");
  expect(row).toHaveTextContent("07:30");
  expect(row).toHaveTextContent("8 个目标");
  expect(row).toHaveTextContent("Windows：Ready");
  expect(row).toHaveTextContent("成功");
  expect(screen.getByRole("article", { name: "每周登录检查" })).toHaveTextContent("已停用");

  fireEvent.click(screen.getByRole("button", { name: "编辑 每日续火发送" }));
  expect(screen.getByLabelText("每日续火发送", { selector: "input" })).toHaveFocus();
});
