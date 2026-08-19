import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api } from "../api";
import { MessagePacksPage } from "./MessagePacksPage";

vi.mock("../api", () => ({
  api: {
    messagePacks: vi.fn(),
    previewMessagePack: vi.fn(),
    createMessagePack: vi.fn(),
    importMessagePackFile: vi.fn(),
    renameMessagePack: vi.fn(),
    reorderMessagePacks: vi.fn(),
    fuseMessagePack: vi.fn(),
    splitMessagePack: vi.fn(),
    importMessagePack: vi.fn()
  }
}));

const source = { id: "source", name: "来源包", description: "", version: "user", count: 1, category: "custom", direct_fused_sources: [], fused_source_count: 0 };
const daily = { id: "daily", name: "日常问候", description: "自然短问候", version: "1.0.0", count: 2, category: "daily", direct_fused_sources: [source], fused_source_count: 1 };
const other = { id: "other", name: "其他", description: "", version: "user", count: 1, category: "custom", direct_fused_sources: [], fused_source_count: 0 };
const catalog = { revision: 7, packs: [daily, other] };

beforeEach(() => {
  vi.mocked(api.messagePacks).mockResolvedValue(catalog);
  vi.mocked(api.previewMessagePack).mockResolvedValue({
    pack: daily,
    messages: ["早安呀", "今天顺利"],
    duplicate_count: 0,
    entries: [
      { id: "m1", text: "早安呀", origin_pack_id: "daily", origin_pack_name: "日常问候", native: true },
      { id: "m2", text: "今天顺利", origin_pack_id: "source", origin_pack_name: "来源包", native: false }
    ]
  });
  vi.mocked(api.importMessagePack).mockResolvedValue({
    added_count: 2, duplicate_count: 0, total_count: 62,
    backup_path: "data/backups/messages.txt", mode: "merge"
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

test("lists provenance, previews a pack, and keeps global import action", async () => {
  render(<MessagePacksPage notify={vi.fn()} />);
  expect(await screen.findByText("已融合：来源包")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "预览" })[0]);
  expect(await screen.findByText(/今天顺利/)).toBeInTheDocument();
  expect(screen.getByText(/来源：来源包/)).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "导入全局库" })[0]);
  expect(await screen.findByText(/新增 2 条/)).toBeInTheDocument();
});

test("creates an empty pack and imports a txt as a new pack", async () => {
  const empty = { ...other, id: "empty", name: "新建文案包", count: 0 };
  vi.mocked(api.createMessagePack).mockResolvedValue({
    revision: 8, pack: empty, catalog: { revision: 8, packs: [...catalog.packs, empty] }
  });
  vi.mocked(api.importMessagePackFile).mockResolvedValue({
    revision: 8, pack: other, catalog: { revision: 8, packs: catalog.packs }
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getByRole("button", { name: "新建文案包" }));
  await waitFor(() => expect(api.createMessagePack).toHaveBeenCalledWith(7));
  const file = new File(["首条名称\n第二条"], "new.txt", { type: "text/plain" });
  fireEvent.change(screen.getByLabelText("导入 TXT 文案包"), { target: { files: [file] } });
  await waitFor(() => expect(api.importMessagePackFile).toHaveBeenCalledWith(file, 8));
});

test("renames without changing id and persists full reorder payload", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("晨间");
  vi.mocked(api.renameMessagePack).mockResolvedValue({
    revision: 8, pack: { ...daily, name: "晨间" }, catalog: { revision: 8, packs: [{ ...daily, name: "晨间" }, other] }
  });
  vi.mocked(api.reorderMessagePacks).mockResolvedValue({
    revision: 8, pack: null, catalog: { revision: 8, packs: [other, daily] }
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "重命名" })[0]);
  await waitFor(() => expect(api.renameMessagePack).toHaveBeenCalledWith("daily", "晨间", 7));
  fireEvent.click(screen.getByRole("button", { name: "下移 晨间" }));
  await waitFor(() => expect(api.reorderMessagePacks).toHaveBeenCalledWith(["other", "daily"], 8));
});

test("fuses by stable ids and splits a direct provenance source", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.fuseMessagePack).mockResolvedValue({
    revision: 8, pack: daily, catalog: { revision: 8, packs: [daily] }
  });
  vi.mocked(api.splitMessagePack).mockResolvedValue({
    revision: 8, pack: daily, catalog: { revision: 8, packs: catalog.packs }
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.change(screen.getByLabelText("融合 其他 到"), { target: { value: "daily" } });
  await waitFor(() => expect(api.fuseMessagePack).toHaveBeenCalledWith("other", "daily", 7));
  fireEvent.click(screen.getByRole("button", { name: "拆出 来源包" }));
  await waitFor(() => expect(api.splitMessagePack).toHaveBeenCalledWith("daily", "source", 8));
});
