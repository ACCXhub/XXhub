import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { LogsPage } from "./LogsPage";

vi.mock("../api", () => ({
  api: {
    logs: vi.fn().mockResolvedValue({
      items: [{ timestamp: "2026-07-13 08:00:00", date: "2026-07-13", level: "ERROR", task_type: "daily_send", summary: "发送失败：好友#1234", detail: "Traceback detail", source: "autody-2026-07-13.log" }],
      total: 1, page: 1, page_size: 50, start_date: "2026-07-11", end_date: "2026-07-13", scheduler: ""
    }),
    archiveLogs: vi.fn(),
    archiveHistoricalLogs: vi.fn(),
    openLogFolder: vi.fn(),
    logStorageSummary: vi.fn().mockResolvedValue({ active_files: 2, active_bytes: 1024, archived_files: 1, archived_bytes: 2048, total_bytes: 3072, oldest_date: "2026-06-01", last_cleanup_at: null, last_cleanup_result: null, next_cleanup_date: "2026-07-16", cleanup_enabled: true }),
    logCleanupPreview: vi.fn().mockResolvedValue({ to_archive: 2, to_delete: 1, bytes: 2048, skipped: 0 }),
    cleanupLogs: vi.fn().mockResolvedValue({ archived: 2, deleted: 1, bytes: 2048, skipped: 0 })
  }
}));

afterEach(cleanup);

test("shows masked summaries and keeps traceback details collapsed", async () => {
  render(<LogsPage />);

  expect(await screen.findByText("发送失败：好友#1234")).toBeInTheDocument();
  expect(screen.getByText("查看详情")).toBeInTheDocument();
  expect(screen.getByLabelText("日志开始日期")).toBeInTheDocument();
  expect(screen.queryByText("小明")).not.toBeInTheDocument();
});

test("reloads when filters change and can reset all active filters", async () => {
  render(<LogsPage />);
  const { api } = await import("../api");
  await screen.findByText("发送失败：好友#1234");
  fireEvent.change(screen.getByLabelText("日志级别"), { target: { value: "ERROR" } });
  await waitFor(() => expect(api.logs).toHaveBeenLastCalledWith(expect.objectContaining({ level: "ERROR" })));
  expect(screen.getByText(/筛选条件：ERROR/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重置筛选" }));
  await waitFor(() => expect(api.logs).toHaveBeenLastCalledWith(expect.objectContaining({ level: "" })));
});

test("previews cleanup, allows cancel, and confirms only explicitly", async () => {
  const alert = vi.spyOn(window, "alert").mockImplementation(() => undefined);
  render(<LogsPage />);

  fireEvent.click(await screen.findByText("整理预览"));
  expect(await screen.findByRole("dialog", { name: "日志整理确认" })).toBeInTheDocument();
  fireEvent.click(screen.getByText("取消"));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("整理预览"));
  fireEvent.click(await screen.findByText("确认整理"));
  await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining("已归档 2 个日志文件")));
  alert.mockRestore();
});

test("follows the latest log until the user scrolls up and resumes on request", async () => {
  const { api } = await import("../api");
  render(<LogsPage />);
  const container = await screen.findByRole("log", { name: "应用日志内容" });
  Object.defineProperties(container, {
    scrollHeight: { configurable: true, value: 1000 },
    clientHeight: { configurable: true, value: 200 },
    scrollTop: { configurable: true, writable: true, value: 0 }
  });

  container.scrollTop = 0;
  fireEvent.scroll(container);
  expect(await screen.findByRole("button", { name: "回到最新" })).toBeInTheDocument();

  vi.mocked(api.logs).mockResolvedValueOnce({
    items: [
      { timestamp: "2026-07-13 08:01:00", date: "2026-07-13", level: "INFO", task_type: "daily_send", summary: "新日志", detail: "", source: "autody-2026-07-13.log", status: "resolved", fingerprint: "new", occurrences: 1 },
      { timestamp: "2026-07-13 08:00:00", date: "2026-07-13", level: "ERROR", task_type: "daily_send", summary: "发送失败：好友#1234", detail: "Traceback detail", source: "autody-2026-07-13.log", status: "active", fingerprint: "old", occurrences: 1 }
    ],
    total: 2, page: 1, page_size: 50, start_date: "2026-07-11", end_date: "2026-07-13", scheduler: ""
  });
  fireEvent.click(screen.getByRole("button", { name: "刷新" }));

  expect(await screen.findByText("有新日志")).toBeInTheDocument();
  expect(container.scrollTop).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: "回到最新" }));
  expect(container.scrollTop).toBe(800);
  expect(screen.queryByText("有新日志")).not.toBeInTheDocument();
});

test("scrolls a newly activated log tab to its latest entry", async () => {
  render(<LogsPage />);
  await screen.findByRole("log", { name: "应用日志内容" });
  const scheduler = document.querySelector('[aria-label="调度日志内容"]') as HTMLElement;
  expect(scheduler).not.toBeNull();
  Object.defineProperties(scheduler, {
    scrollHeight: { configurable: true, value: 1000 },
    clientHeight: { configurable: true, value: 200 },
    scrollTop: { configurable: true, writable: true, value: 0 }
  });

  fireEvent.click(screen.getByRole("button", { name: "调度日志" }));

  expect(scheduler.scrollTop).toBe(800);
});
