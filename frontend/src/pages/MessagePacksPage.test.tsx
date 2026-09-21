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
    nativeStickerCatalog: vi.fn(),
    scanNativeStickers: vi.fn(),
    addNativeSticker: vi.fn(),
    addNativeStickers: vi.fn(),
    reorderMessagePackEntries: vi.fn(),
    deleteMessagePackEntry: vi.fn(),
    deleteMessagePack: vi.fn(),
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
  vi.mocked(api.nativeStickerCatalog).mockResolvedValue({
    account_profile_id: "account-aaaaaaaaaaaaaaaaaaaaaaaa",
    revision: 0,
    scanned_at: null,
    stickers: []
  });
  vi.mocked(api.previewMessagePack).mockResolvedValue({
    pack: daily,
    messages: ["早安呀", "今天顺利"],
    duplicate_count: 0,
    entries: [
      { id: "m1", kind: "text", text: "早安呀", sticker: null, origin_pack_id: "daily", origin_pack_name: "日常问候", native: true },
      { id: "m2", kind: "text", text: "今天顺利", sticker: null, origin_pack_id: "source", origin_pack_name: "来源包", native: false }
    ]
  });
  vi.mocked(api.importMessagePack).mockResolvedValue({
    added_count: 2, duplicate_count: 0, total_count: 62,
    backup_path: "data/backups/messages.txt", mode: "merge", excluded_non_text_count: 0
  });
});

test("multi-selects stickers with blue card state and submits one batch mutation", async () => {
  vi.mocked(api.scanNativeStickers).mockResolvedValue({
    account_profile_id: "account-aaaaaaaaaaaaaaaaaaaaaaaa",
    revision: 1,
    scanned_at: "2026-09-20T10:00:00",
    stickers: [
      {
        logical_id: "native-sticker-heart", display_name: "比心",
        resource_key: "heart.webp", machine_id: null, accessible_name: "比心",
        category: null, preview_url: "https://example.test/heart.webp", diagnostic_index: 0,
        last_seen_at: "2026-09-20T10:00:00"
      },
      {
        logical_id: "native-sticker-fire", display_name: "续火花",
        resource_key: "fire.webp", machine_id: null, accessible_name: "续火花",
        category: null, preview_url: "https://example.test/fire.webp", diagnostic_index: 1,
        last_seen_at: "2026-09-20T10:00:00"
      }
    ]
  });
  vi.mocked(api.addNativeStickers).mockResolvedValue({
    revision: 8, pack: { ...daily, count: 4 },
    catalog: { revision: 8, packs: [{ ...daily, count: 4 }, other] },
    added_count: 2, duplicate_count: 0
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "添加原生表情" })[0]);
  const dialog = await screen.findByRole("dialog", { name: "添加原生表情" });
  expect(screen.getByRole("button", { name: "添加到当前文案包（0）" })).toBeDisabled();
  expect(dialog.querySelector('input[type="checkbox"]')).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "扫描原生表情" }));
  const heart = await screen.findByRole("button", { name: "选择 比心" });
  const fire = screen.getByRole("button", { name: "选择 续火花" });
  expect(heart).toHaveAttribute("aria-pressed", "false");
  expect(fire).toHaveAttribute("aria-pressed", "false");

  heart.focus();
  fireEvent.click(heart);
  fireEvent.click(fire);
  expect(heart).toHaveAttribute("aria-pressed", "true");
  expect(fire).toHaveAttribute("aria-pressed", "true");
  expect(heart).toHaveClass("selected");
  expect(fire).toHaveClass("selected");
  expect(screen.getByText("已选择 2 个")).toBeInTheDocument();
  expect(dialog.querySelector(".sticker-chooser svg")).toBeNull();

  fireEvent.click(heart);
  expect(heart).toHaveAttribute("aria-pressed", "false");
  expect(fire).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("已选择 1 个")).toBeInTheDocument();
  fireEvent.click(heart);
  fireEvent.click(screen.getByRole("button", { name: "添加到当前文案包（2）" }));

  await waitFor(() => expect(api.addNativeStickers).toHaveBeenCalledWith(
    "daily",
    [
      expect.objectContaining({ logical_id: "native-sticker-heart" }),
      expect.objectContaining({ logical_id: "native-sticker-fire" })
    ],
    7
  ));
  expect(api.addNativeStickers).toHaveBeenCalledTimes(1);
});

test("keeps selection by logical id when refreshed catalog order changes", async () => {
  const heart = { logical_id: "heart", display_name: "比心", resource_key: "heart.webp", machine_id: null, accessible_name: "比心", category: null, preview_url: null, diagnostic_index: 0, last_seen_at: null };
  const fire = { logical_id: "fire", display_name: "续火花", resource_key: "fire.webp", machine_id: null, accessible_name: "续火花", category: null, preview_url: null, diagnostic_index: 1, last_seen_at: null };
  vi.mocked(api.nativeStickerCatalog).mockResolvedValue({ account_profile_id: "account-aaaaaaaaaaaaaaaaaaaaaaaa", revision: 1, scanned_at: "2026-09-20T10:00:00", stickers: [heart, fire] });
  vi.mocked(api.scanNativeStickers).mockResolvedValue({ account_profile_id: "account-aaaaaaaaaaaaaaaaaaaaaaaa", revision: 2, scanned_at: "2026-09-20T10:01:00", stickers: [fire, heart] });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "添加原生表情" })[0]);
  fireEvent.click(await screen.findByRole("button", { name: "选择 比心" }));
  fireEvent.click(screen.getByRole("button", { name: "刷新原生表情" }));

  await waitFor(() => expect(screen.getByRole("button", { name: "选择 比心" })).toHaveAttribute("aria-pressed", "true"));
  expect(screen.getByRole("button", { name: "选择 续火花" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByText("已选择 1 个")).toBeInTheDocument();
});

test("adds selected stickers to the canonical automatic sticker pack", async () => {
  const heart = { logical_id: "heart", display_name: "比心", resource_key: "heart.webp", machine_id: null, accessible_name: "比心", category: null, preview_url: null, diagnostic_index: 0, last_seen_at: null };
  vi.mocked(api.nativeStickerCatalog).mockResolvedValue({ account_profile_id: "account-aaaaaaaaaaaaaaaaaaaaaaaa", revision: 1, scanned_at: "2026-09-20T10:00:00", stickers: [heart] });
  vi.mocked(api.addNativeStickers).mockResolvedValue({ revision: 8, pack: { ...other, id: "auto-native-stickers", name: "自动表情包" }, catalog: { revision: 8, packs: [daily, other] }, added_count: 1, duplicate_count: 0 });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "添加原生表情" })[0]);
  fireEvent.click(await screen.findByRole("button", { name: "选择 比心" }));
  fireEvent.click(screen.getByRole("button", { name: "添加到自动表情包（1）" }));

  await waitFor(() => expect(api.addNativeStickers).toHaveBeenCalledWith(
    "auto-native-stickers",
    [{
      logical_id: "heart",
      display_name: "比心",
      resource_key: "heart.webp",
      machine_id: null,
      accessible_name: "比心",
      category: null
    }],
    7
  ));
});

test("renders a fused native sticker with provenance in the existing preview", async () => {
  vi.mocked(api.previewMessagePack).mockResolvedValue({
    pack: daily,
    messages: ["早安呀"],
    duplicate_count: 0,
    entries: [
      { id: "m1", kind: "text", text: "早安呀", sticker: null, origin_pack_id: "daily", origin_pack_name: "日常问候", native: true },
      { id: "s1", kind: "native_sticker", text: null, sticker: { logical_id: "native-sticker-heart", display_name: "比心", resource_key: "heart.webp", machine_id: null, accessible_name: "比心", category: null }, origin_pack_id: "source", origin_pack_name: "来源包", native: false }
    ]
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "预览" })[0]);

  expect(await screen.findByText("原生表情 · 比心")).toBeInTheDocument();
  expect(screen.getByText(/来源：来源包/)).toBeInTheDocument();
});

test("reorders and removes a direct sticker by typed entry id", async () => {
  const mixedPreview = {
    pack: daily,
    messages: ["早安呀"],
    duplicate_count: 0,
    entries: [
      { id: "m1", kind: "text" as const, text: "早安呀", sticker: null, origin_pack_id: "daily", origin_pack_name: "日常问候", native: true },
      { id: "s1", kind: "native_sticker" as const, text: null, sticker: { logical_id: "native-sticker-heart", display_name: "比心", resource_key: "heart.webp", machine_id: null, accessible_name: "比心", category: null }, origin_pack_id: "daily", origin_pack_name: "日常问候", native: true }
    ]
  };
  vi.mocked(api.previewMessagePack).mockResolvedValue(mixedPreview);
  vi.mocked(api.reorderMessagePackEntries).mockResolvedValue({
    revision: 8, pack: daily, catalog: { revision: 8, packs: catalog.packs }
  });
  vi.mocked(api.deleteMessagePackEntry).mockResolvedValue({
    revision: 9, pack: { ...daily, count: 1 }, catalog: { revision: 9, packs: [{ ...daily, count: 1 }, other] }
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "预览" })[0]);
  fireEvent.click(await screen.findByRole("button", { name: "上移 比心" }));
  await waitFor(() => expect(api.reorderMessagePackEntries).toHaveBeenCalledWith("daily", ["s1", "m1"], 7));
  await waitFor(() => expect(screen.getByRole("button", { name: "删除 比心" })).not.toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "删除 比心" }));
  await waitFor(() => expect(api.deleteMessagePackEntry).toHaveBeenCalledWith("daily", "s1", 8));
});

test("blocks messages.txt replacement when a pack contains only stickers", async () => {
  vi.mocked(api.previewMessagePack).mockResolvedValue({
    pack: { ...daily, count: 1 },
    messages: [],
    duplicate_count: 0,
    entries: [{ id: "s1", kind: "native_sticker", text: null, sticker: { logical_id: "native-sticker-heart", display_name: "比心", resource_key: "heart.webp", machine_id: null, accessible_name: "比心", category: null }, origin_pack_id: "daily", origin_pack_name: "日常问候", native: true }]
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "覆盖全局文案" })[0]);

  const dialog = await screen.findByRole("dialog", { name: "确认覆盖全局文案" });
  expect(dialog).toHaveTextContent("没有文字内容，不能覆盖全局文案");
  expect(dialog).toHaveTextContent("1 条原生表情不会写入 messages.txt");
  expect(screen.getByRole("button", { name: "确认覆盖" })).toBeDisabled();
  expect(api.importMessagePack).not.toHaveBeenCalled();
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

test("requires confirmation and warns before recursively deleting a fused pack", async () => {
  vi.mocked(api.config).mockResolvedValue({ default_message_pack: null } as never);
  vi.mocked(api.deleteMessagePack).mockResolvedValue({
    revision: 8,
    pack: null,
    catalog: { revision: 8, packs: [other] }
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "删除文案包 日常问候" })[0]);
  const dialog = await screen.findByRole("dialog", { name: "删除文案包" });
  expect(dialog).toHaveTextContent("确定删除「日常问候」");
  expect(dialog).toHaveTextContent("包含 1 个已融合来源");
  expect(dialog).toHaveTextContent("来源包");
  expect(api.deleteMessagePack).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "取消删除" }));
  expect(screen.queryByRole("dialog", { name: "删除文案包" })).not.toBeInTheDocument();
  expect(api.deleteMessagePack).not.toHaveBeenCalled();

  fireEvent.click(screen.getAllByRole("button", { name: "预览" })[0]);
  await screen.findByText(/今天顺利/);
  fireEvent.click(screen.getAllByRole("button", { name: "删除文案包 日常问候" })[0]);
  fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));

  await waitFor(() => expect(api.deleteMessagePack).toHaveBeenCalledWith("daily", 7));
  expect(screen.queryByText(/今天顺利/)).not.toBeInTheDocument();
  expect(screen.queryByText("日常问候")).not.toBeInTheDocument();
});

test("blocks deleting the current default pack without silently clearing it", async () => {
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getByRole("button", { name: "删除文案包 日常问候" }));

  const dialog = await screen.findByRole("dialog", { name: "删除文案包" });
  expect(dialog).toHaveTextContent("无法删除「日常问候」");
  expect(dialog).toHaveTextContent("当前默认文案包");
  expect(screen.queryByRole("button", { name: "确认删除" })).not.toBeInTheDocument();
  expect(api.deleteMessagePack).not.toHaveBeenCalled();
  expect(api.saveConfig).not.toHaveBeenCalled();
});

test("reloads catalog after a revision conflict while deleting", async () => {
  const notify = vi.fn();
  vi.mocked(api.config).mockResolvedValue({ default_message_pack: null } as never);
  vi.mocked(api.deleteMessagePack).mockRejectedValue(new Error("文案包已被其他页面修改，请刷新后重试"));
  render(<MessagePacksPage notify={notify} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getByRole("button", { name: "删除文案包 其他" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));

  await waitFor(() => expect(api.messagePacks).toHaveBeenCalledTimes(2));
  expect(notify).toHaveBeenCalledWith("文案包已被其他页面修改，请刷新后重试");
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
    backup_path: "data/backups/messages.txt", mode: "replace", excluded_non_text_count: 0
  });
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "覆盖全局文案" })[0]);

  const dialog = await screen.findByRole("dialog", { name: "确认覆盖全局文案" });
  expect(dialog).toHaveTextContent("将用「日常问候」的 2 条文案覆盖当前全局文案。原有全局文案将被替换。");
  expect(api.importMessagePack).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认覆盖" }));
  await waitFor(() => expect(api.importMessagePack).toHaveBeenCalledWith("daily", "replace"));
});

test("cancelling global message replacement does not call the import api", async () => {
  render(<MessagePacksPage notify={vi.fn()} />);
  await screen.findAllByText("日常问候");

  fireEvent.click(screen.getAllByRole("button", { name: "覆盖全局文案" })[0]);
  fireEvent.click(await screen.findByRole("button", { name: "取消" }));

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
