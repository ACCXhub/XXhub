import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../api";
import { MessagesPage } from "./MessagesPage";

vi.mock("../api", () => ({
  api: {
    messages: vi.fn(),
    saveMessages: vi.fn(),
    nativeStickerCatalog: vi.fn(),
    scanNativeStickers: vi.fn(),
    saveGlobalNativeStickers: vi.fn()
  }
}));

const heart = {
  logical_id: "heart",
  display_name: "比心",
  resource_key: "heart.webp",
  machine_id: null,
  accessible_name: "比心",
  category: null,
  preview_url: null,
  diagnostic_index: 0,
  last_seen_at: null
};
const fire = {
  logical_id: "fire",
  display_name: "续火花",
  resource_key: "fire.webp",
  machine_id: null,
  accessible_name: "续火花",
  category: null,
  preview_url: null,
  diagnostic_index: 1,
  last_seen_at: null
};

const library = {
  messages: ["早安", "晚安"],
  selected_native_stickers: [{
    logical_id: "heart",
    display_name: "比心",
    resource_key: "heart.webp",
    machine_id: null,
    accessible_name: "比心",
    category: null
  }],
  account_profile_id: "account-aaaaaaaaaaaaaaaaaaaaaaaa",
  text_count: 2,
  native_sticker_count: 1,
  total_count: 3,
  default_message_pack: "daily-greeting",
  default_message_pack_name: "日常问候",
  default_pack_overrides_global: true
};

beforeEach(() => {
  vi.mocked(api.messages).mockResolvedValue(library as never);
  vi.mocked(api.nativeStickerCatalog).mockResolvedValue({
    account_profile_id: "account-aaaaaaaaaaaaaaaaaaaaaaaa",
    revision: 2,
    scanned_at: "2026-09-22T10:00:00",
    stickers: [heart, fire]
  });
  vi.mocked(api.saveMessages).mockResolvedValue(library as never);
});

test("renders only selected stickers inline and manages the full catalog separately", async () => {
  const notify = vi.fn();
  vi.mocked(api.saveGlobalNativeStickers).mockImplementation(async (ids) => ({
    ...library,
    selected_native_stickers: ids.map((id) => id === "heart" ? library.selected_native_stickers[0] : {
      logical_id: "fire", display_name: "续火花", resource_key: "fire.webp",
      machine_id: null, accessible_name: "续火花", category: null
    }),
    native_sticker_count: ids.length,
    total_count: 2 + ids.length
  } as never));
  render(<MessagesPage notify={notify} onNavigate={vi.fn()} />);

  const inline = await screen.findByRole("region", { name: "表情包" });
  expect(inline).toHaveTextContent("表情包");
  expect(inline).toHaveTextContent("比心");
  expect(inline).not.toHaveTextContent("续火花");
  expect(inline.querySelector(".selected-sticker-card")).not.toHaveClass("selected");
  expect(screen.queryByText("全局随机候选")).not.toBeInTheDocument();
  expect(screen.queryByText(/条文字|参与全局随机发送|正在覆盖全局文案库发送来源/)).not.toBeInTheDocument();
  expect(inline.querySelector('input[type="checkbox"]')).toBeNull();
  expect(inline.querySelector("svg")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "管理原生表情" }));
  const manager = await screen.findByRole("dialog", { name: "管理原生表情" });
  const selected = screen.getByRole("button", { name: "选择 比心" });
  const unselected = screen.getByRole("button", { name: "选择 续火花" });
  expect(selected).toHaveAttribute("aria-pressed", "true");
  expect(selected).toHaveClass("selected");
  expect(unselected).toHaveAttribute("aria-pressed", "false");
  expect(manager.querySelector('input[type="checkbox"]')).toBeNull();
  expect(manager.querySelector("svg")).toBeNull();

  fireEvent.click(unselected);
  await waitFor(() => expect(api.saveGlobalNativeStickers).toHaveBeenCalledWith(["heart", "fire"]));
  expect(inline).toHaveTextContent("续火花");
  expect(Array.from(inline.querySelectorAll(".selected-sticker-card span")).map((item) => item.textContent)).toEqual(["比心", "续火花"]);
  expect(unselected).toHaveAttribute("aria-pressed", "true");

  fireEvent.click(selected);
  await waitFor(() => expect(api.saveGlobalNativeStickers).toHaveBeenLastCalledWith(["fire"]));
  expect(inline).not.toHaveTextContent("比心");
  expect(inline).toHaveTextContent("续火花");
  expect(notify).not.toHaveBeenCalled();
});

test("keeps the persisted selection visible when a global selection update fails", async () => {
  const notify = vi.fn();
  vi.mocked(api.saveGlobalNativeStickers).mockRejectedValue(new Error("选择保存失败"));
  render(<MessagesPage notify={notify} onNavigate={vi.fn()} />);
  await screen.findByRole("region", { name: "表情包" });
  fireEvent.click(screen.getByRole("button", { name: "管理原生表情" }));
  const fireButton = await screen.findByRole("button", { name: "选择 续火花" });

  fireEvent.click(fireButton);

  await waitFor(() => expect(notify).toHaveBeenCalledWith("选择保存失败"));
  expect(fireButton).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByRole("region", { name: "表情包" })).toHaveTextContent("比心");
  expect(screen.getByRole("region", { name: "表情包" })).not.toHaveTextContent("续火花");
});

test("shows a concise empty state without rendering the catalog", async () => {
  vi.mocked(api.messages).mockResolvedValue({
    ...library,
    selected_native_stickers: [],
    native_sticker_count: 0,
    total_count: 2,
    default_message_pack: null,
    default_message_pack_name: null,
    default_pack_overrides_global: false
  } as never);
  render(<MessagesPage notify={vi.fn()} onNavigate={vi.fn()} />);

  const inline = await screen.findByRole("region", { name: "表情包" });
  expect(inline).toHaveTextContent("暂未选择原生表情");
  expect(inline).not.toHaveTextContent("比心");
  expect(inline).not.toHaveTextContent("续火花");
  fireEvent.click(screen.getByRole("button", { name: "管理原生表情" }));
  expect(await screen.findByRole("button", { name: "选择 比心" })).toBeInTheDocument();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
