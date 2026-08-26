import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SettingsPage } from "./SettingsPage";

vi.mock("../api", () => ({
  api: {
    config: vi.fn().mockResolvedValue({
      targets: [], retry_count: 3, timeout_ms: 30000, headless: true,
      message_suffix: { enabled: true, text: "gpt小助手", style: "dash" },
      daily_send_time: "07:30", recovery_deadline: "23:59", mask_log_friend_names: true
    }),
    saveConfig: vi.fn(),
    modules: vi.fn().mockResolvedValue({ modules: [{ id: "autody-test-center", display_name: "测试中心", installed: false, version: null, compatible: true, bundled_available: true, bundled_version: "1.2.0", core_version: "1.5.0", required_autody_version: ">=1.3.0,<2.0.0" }] }),
    installTestCenter: vi.fn().mockResolvedValue({ id: "autody-test-center", display_name: "测试中心", installed: true, version: "1.2.0", compatible: true, bundled_version: "1.2.0", core_version: "1.5.0", required_autody_version: ">=1.3.0,<2.0.0" }),
    uninstallTestCenter: vi.fn().mockResolvedValue({ installed: false })
  }
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("shows a live suffix preview for every style", async () => {
  render(<SettingsPage notify={vi.fn()} />);
  expect(await screen.findByText("你好 —— gpt小助手")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("后缀样式"), { target: { value: "bracket" } });

  expect(screen.getByText("你好【gpt小助手】")).toBeInTheDocument();
});

test("renders a compact Test Center entry and installs before opening it", async () => {
  const onTestCenterStateChange = vi.fn();
  const onOpenTestCenter = vi.fn();
  render(<SettingsPage notify={vi.fn()} onOpenTestCenter={onOpenTestCenter} onTestCenterStateChange={onTestCenterStateChange} />);

  expect(await screen.findByRole("heading", { name: "可选模块" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "设置" }).closest("section")).toHaveClass("editor-page");
  expect(screen.getByRole("button", { name: "测试" })).toBeInTheDocument();
  expect(screen.queryByText("当前未安装")).not.toBeInTheDocument();
  expect(document.querySelector(".module-card")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "测试" }));

  const { api } = await import("../api");
  await waitFor(() => {
    expect(api.installTestCenter).toHaveBeenCalledOnce();
    expect(onTestCenterStateChange).toHaveBeenCalledWith(true);
    expect(onOpenTestCenter).toHaveBeenCalledOnce();
  });
});

test("upgrades an outdated installed Test Center before opening it", async () => {
  const { api } = await import("../api");
  vi.mocked(api.modules).mockResolvedValueOnce({
    modules: [{
      id: "autody-test-center",
      display_name: "测试中心",
      installed: true,
      version: "1.2.0",
      compatible: true,
      bundled_available: true,
      bundled_version: "1.2.0",
      core_version: "1.5.0",
      required_autody_version: ">=1.3.0,<2.0.0",
      update_available: true
    }]
  });
  const onTestCenterStateChange = vi.fn();
  const onOpenTestCenter = vi.fn();

  render(<SettingsPage notify={vi.fn()} onOpenTestCenter={onOpenTestCenter} onTestCenterStateChange={onTestCenterStateChange} />);
  fireEvent.click(await screen.findByRole("button", { name: "更新" }));

  await waitFor(() => {
    expect(api.installTestCenter).toHaveBeenCalledOnce();
    expect(onTestCenterStateChange).toHaveBeenCalledWith(true);
    expect(onOpenTestCenter).toHaveBeenCalledOnce();
  });
});

test("shows inline uninstall after installation and removes the module", async () => {
  vi.stubGlobal("confirm", vi.fn(() => true));
  const onTestCenterStateChange = vi.fn();
  render(<SettingsPage notify={vi.fn()} onTestCenterStateChange={onTestCenterStateChange} />);

  fireEvent.click(await screen.findByRole("button", { name: "测试" }));
  expect(await screen.findByRole("button", { name: "卸载" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "卸载" }));

  const { api } = await import("../api");
  expect(api.uninstallTestCenter).toHaveBeenCalledOnce();
  expect(onTestCenterStateChange).toHaveBeenLastCalledWith(false);
  vi.unstubAllGlobals();
});
