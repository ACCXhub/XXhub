import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api } from "../api";
import { MessagePacksPage } from "./MessagePacksPage";

vi.mock("../api", () => ({
  api: {
    messagePacks: vi.fn().mockResolvedValue({
      packs: [{ id: "daily", name: "日常问候", description: "自然短问候", version: "1.0.0", count: 50, category: "daily" }]
    }),
    previewMessagePack: vi.fn().mockResolvedValue({
      pack: { id: "daily", name: "日常问候", description: "自然短问候", version: "1.0.0", count: 50, category: "daily" },
      messages: ["早安呀", "今天顺利"],
      duplicate_count: 0
    }),
    importMessagePack: vi.fn().mockResolvedValue({
      added_count: 2,
      duplicate_count: 0,
      total_count: 62,
      backup_path: "data/backups/messages.txt",
      mode: "merge"
    })
  }
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("lists and previews a message pack", async () => {
  render(<MessagePacksPage notify={vi.fn()} />);
  expect(await screen.findByText("日常问候")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "预览" }));

  expect(await screen.findByText("早安呀")).toBeInTheDocument();
  expect(screen.getByText("今天顺利")).toBeInTheDocument();
});

test("replace import requires confirmation", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findByText("日常问候");

  fireEvent.click(screen.getByRole("button", { name: "替换导入" }));

  await waitFor(() => expect(window.confirm).toHaveBeenCalled());
  expect(api.importMessagePack).not.toHaveBeenCalled();
  confirm.mockRestore();
});

test("merge import reports structured result", async () => {
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findByText("日常问候");

  fireEvent.click(screen.getByRole("button", { name: "合并导入" }));

  expect(await screen.findByText(/新增 2 条/)).toBeInTheDocument();
  expect(screen.getByText(/本地共 62 条/)).toBeInTheDocument();
});
