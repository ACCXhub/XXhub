import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Sidebar } from "./Sidebar";

afterEach(cleanup);

test("keeps settings blue only on the settings page", () => {
  render(
    <Sidebar
      active="settings"
      onChange={vi.fn()}
      account={null}
      onRefreshAccount={vi.fn()}
      testCenterInstalled
    />
  );

  expect(screen.getByRole("button", { name: "设置" })).toHaveClass("active");
  expect(screen.getByRole("button", { name: "测试中心" })).not.toHaveClass("test-center-active");
});

test("uses the soft pink child state without marking settings blue", () => {
  render(
    <Sidebar
      active="settings"
      onChange={vi.fn()}
      account={null}
      onRefreshAccount={vi.fn()}
      testCenterInstalled
      testCenterActive
    />
  );

  expect(screen.getByRole("button", { name: "设置" })).toHaveClass("parent-active");
  expect(screen.getByRole("button", { name: "设置" })).not.toHaveClass("active");
  expect(screen.getByRole("button", { name: "测试中心" })).toHaveClass("test-center-active");
});
