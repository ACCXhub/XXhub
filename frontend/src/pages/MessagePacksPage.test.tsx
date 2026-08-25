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
    importMessagePack: vi.fn(),
    config: vi.fn(), saveConfig: vi.fn()
  }
}));

const source = { id: "source", name: "来源包", description: "", version: "user", count: 1, category: "custom", direct_fused_sources: [], fused_source_count: 0 };
const daily = { id: "daily", name: "日常问候", description: "自然短问候", version: "1.0.0", count: 2, category: "daily", direct_fused_sources: [source], fused_source_count: 1 };
const other = { id: "other", name: "其他", description: "", version: "user", count: 1, category: "custom", direct_fused_sources: [], fused_source_count: 0 };
const catalog = { revision: 7, packs: [daily, other] };

beforeEach(() => {
  vi.mocked(api.config).mockResolvedValue({ default_message_pack: "daily" } as never);
  vi.mocked(api.saveConfig).mockImplementation(async (value) => value);
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

test("shows the canonical default pack and can change it by stable id", async () => {
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  expect(screen.getByText("当前默认")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "设为默认" }));

  await waitFor(() => expect(api.saveConfig).toHaveBeenCalledWith(
    expect.objectContaining({ default_message_pack: "other" })
  ));
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

test("requires confirmation before replacing the global message library", async () => {
  vi.mocked(api.importMessagePack).mockResolvedValue({
    added_count: 2, duplicate_count: 0, total_count: 2,
    backup_path: "data/backups/messages.txt", mode: "replace"
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "覆盖全局文案" })[0]);

  const dialog = screen.getByRole("dialog", { name: "确认覆盖全局文案" });
  expect(dialog).toHaveTextContent("将用「日常问候」的 2 条文案覆盖当前全局文案。原有全局文案将被替换。");
  expect(api.importMessagePack).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认覆盖" }));
  await waitFor(() => expect(api.importMessagePack).toHaveBeenCalledWith("daily", "replace"));
});

test("cancelling global message replacement does not call the import api", async () => {
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "覆盖全局文案" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "取消" }));

  expect(screen.queryByRole("dialog", { name: "确认覆盖全局文案" })).not.toBeInTheDocument();
  expect(api.importMessagePack).not.toHaveBeenCalled();
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

function dragPack(sourceName: string, targetName: string, intent: "after" | "fuse") {
  const sourceCard = screen.getByRole("article", { name: `文案包 ${sourceName}` });
  const targetCard = screen.getByRole("article", { name: `文案包 ${targetName}` });
  const targetSlot = targetCard.parentElement as HTMLElement;
  let draggedId = "";
  const dataTransfer = {
    effectAllowed: "move", dropEffect: "move",
    setData: vi.fn((_type: string, value: string) => { draggedId = value; }),
    getData: vi.fn(() => draggedId)
  };
  fireEvent.dragStart(sourceCard, { dataTransfer });
  const dropZone = targetSlot.querySelector(intent === "fuse" ? '[data-drop-kind="fuse"]' : '[data-drop-position="after"][data-drop-edge="right"]') as HTMLElement;
  fireEvent.dragOver(dropZone, { dataTransfer });
  fireEvent.drop(dropZone, { dataTransfer });
  fireEvent.dragEnd(sourceCard, { dataTransfer });
}

test("dragging a card to another card edge persists the full reordered id list", async () => {
  vi.mocked(api.reorderMessagePacks).mockResolvedValue({
    revision: 8, pack: null, catalog: { revision: 8, packs: [other, daily] }
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  dragPack("日常问候", "其他", "after");

  await waitFor(() => expect(api.reorderMessagePacks).toHaveBeenCalledWith(["other", "daily"], 7));
  expect(screen.queryByRole("button", { name: /上移|下移/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: /融合/ })).not.toBeInTheDocument();
});

test("dropping one card onto another card center confirms and fuses by stable ids", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.fuseMessagePack).mockResolvedValue({
    revision: 8, pack: daily, catalog: { revision: 8, packs: [daily] }
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  dragPack("其他", "日常问候", "fuse");

  expect(window.confirm).toHaveBeenCalledWith("将「其他」融合到「日常问候」？");
  await waitFor(() => expect(api.fuseMessagePack).toHaveBeenCalledWith("other", "daily", 7));
});

test("cancelling drag-to-fuse confirmation leaves both packs unchanged", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  dragPack("其他", "日常问候", "fuse");

  expect(window.confirm).toHaveBeenCalledWith("将「其他」融合到「日常问候」？");
  expect(api.fuseMessagePack).not.toHaveBeenCalled();
});

test("a package with direct fused sources exposes the split action", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.splitMessagePack).mockResolvedValue({
    revision: 8, pack: daily, catalog: { revision: 8, packs: catalog.packs }
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  expect(screen.getByText("拆出已融合包")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "拆出 来源包" }));
  await waitFor(() => expect(api.splitMessagePack).toHaveBeenCalledWith("daily", "source", 7));
});
