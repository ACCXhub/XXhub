import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SettingsPage } from "./SettingsPage";

vi.mock("../api", () => ({
  api: {
    config: vi.fn().mockResolvedValue({
      targets: [], retry_count: 3, timeout_ms: 30000, headless: true,
      message_suffix: { enabled: true, text: "gpt小助手", style: "dash" },
      message_pack_index_url: null,
      daily_send_time: "07:30", recovery_deadline: "23:59", mask_log_friend_names: true
    }),
    saveConfig: vi.fn(),
    modules: vi.fn().mockResolvedValue({ modules: [{ id: "autody-test-center", display_name: "测试中心", installed: false, version: null, compatible: true, bundled_available: true, bundled_version: "1.2.0", core_version: "1.4.0", required_autody_version: ">=1.3.0,<2.0.0" }] }),
    installTestCenter: vi.fn().mockResolvedValue({ installed: true, version: "1.2.0" }),
    uninstallTestCenter: vi.fn().mockResolvedValue({ installed: false })
  }
}));

afterEach(cleanup);

test("shows a live suffix preview for every style", async () => {
  render(<SettingsPage notify={vi.fn()} />);
  expect(await screen.findByText("你好 —— gpt小助手")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("后缀样式"), { target: { value: "bracket" } });

  expect(screen.getByText("你好【gpt小助手】")).toBeInTheDocument();
});

test("shows Test Center as an optional uninstalled module and installs it on request", async () => {
  const onTestCenterStateChange = vi.fn();
  render(<SettingsPage notify={vi.fn()} onTestCenterStateChange={onTestCenterStateChange} />);

  expect(await screen.findByRole("heading", { name: "可选模块" })).toBeInTheDocument();
  expect(screen.getByText("测试中心")).toBeInTheDocument();
  expect(screen.getByText("官方模块版本")).toBeInTheDocument();
  expect(screen.getByText("1.2.0")).toBeInTheDocument();
  expect(screen.getByText("AutoDy 核心")).toBeInTheDocument();
  expect(screen.getByText("1.4.0")).toBeInTheDocument();
  expect(screen.getByText("模块版本独立于 AutoDy 核心版本，以兼容范围为准。")).toBeInTheDocument();
  expect(screen.getByText("当前未安装")).toBeInTheDocument();
  expect(screen.getByText("获取与安装")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "获取与安装" }));

  const { api } = await import("../api");
  expect(api.installTestCenter).toHaveBeenCalledOnce();
  expect(onTestCenterStateChange).toHaveBeenCalledWith(true);
});
