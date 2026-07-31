import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Sidebar } from "./Sidebar";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

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

test("opens a compact account popover from the avatar identity", () => {
  const { container } = render(
    <Sidebar
      active="dashboard"
      onChange={vi.fn()}
      account={{
        display_name: "当前账号", avatar_url: "/avatar", avatar_version: "1",
        is_self: true, profile_status: "verified", verification_source: "test",
        logged_in: true, cached: true, last_updated_at: "2026-07-30",
        refresh_running: false
      }}
      accounts={{
        active_profile_id: "account-a",
        profiles: [
          { profile_id: "account-a", display_name: "当前账号", active: true, logged_in: true, profile_status: "verified" },
          { profile_id: "account-b", display_name: "本地账号二", active: false, logged_in: false, profile_status: "unverified" }
        ]
      }}
      onRefreshAccount={vi.fn()}
      onSwitchAccount={vi.fn()}
      onAddAccount={vi.fn()}
      onLogoutAccount={vi.fn()}
    />
  );

  fireEvent.click(screen.getByRole("button", { name: "打开账号切换器" }));

  const dialog = screen.getByRole("dialog", { name: "账号切换器" });
  expect(dialog).toHaveClass("account-popover");
  expect(container.querySelectorAll("svg.lucide-chevron-down")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "打开账号切换器" }).querySelector(".account-copy-name")).toHaveTextContent("当前账号");
  expect(screen.getByRole("button", { name: "打开账号切换器" }).querySelector(".account-copy-meta")).toHaveTextContent("当前抖音账号");
  expect(screen.getByRole("button", { name: "当前账号 当前账号" }).querySelector(".account-profile-name")).toHaveTextContent("当前账号");
  expect(screen.getByRole("button", { name: "当前账号 当前账号" }).querySelector(".account-profile-meta")).toHaveTextContent("已登录");
  expect(screen.getAllByText("当前账号").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: /切换到 本地账号二/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "添加账号" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "刷新账号资料" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "退出当前账号" })).toBeInTheDocument();
});

test("requires confirmation before sidebar account logout", () => {
  const logout = vi.fn();
  render(
    <Sidebar
      active="dashboard"
      onChange={vi.fn()}
      account={null}
      accounts={{ active_profile_id: "account-a", profiles: [] }}
      onRefreshAccount={vi.fn()}
      onSwitchAccount={vi.fn()}
      onAddAccount={vi.fn()}
      onLogoutAccount={logout}
    />
  );

  fireEvent.click(screen.getByRole("button", { name: "打开账号切换器" }));
  fireEvent.click(screen.getByRole("button", { name: "退出当前账号" }));

  expect(screen.getByRole("dialog", { name: "确认退出当前账号" })).toBeInTheDocument();
  expect(logout).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认退出" }));
  expect(logout).toHaveBeenCalledTimes(1);
});

test("keeps the account popover open for an inside pointerdown", () => {
  render(
    <Sidebar
      active="dashboard"
      onChange={vi.fn()}
      account={null}
      onRefreshAccount={vi.fn()}
    />
  );
  fireEvent.click(screen.getByRole("button", { name: "打开账号切换器" }));

  fireEvent.pointerDown(screen.getByRole("dialog", { name: "账号切换器" }));

  expect(screen.getByRole("dialog", { name: "账号切换器" })).toBeInTheDocument();
});

test("closes the account popover on an outside pointerdown", () => {
  render(
    <div>
      <button>页面内容</button>
      <Sidebar
        active="dashboard"
        onChange={vi.fn()}
        account={null}
        onRefreshAccount={vi.fn()}
      />
    </div>
  );
  fireEvent.click(screen.getByRole("button", { name: "打开账号切换器" }));

  fireEvent.pointerDown(screen.getByRole("button", { name: "页面内容" }));

  expect(screen.queryByRole("dialog", { name: "账号切换器" })).not.toBeInTheDocument();
});

test("closes the account popover on Escape", () => {
  render(
    <Sidebar
      active="dashboard"
      onChange={vi.fn()}
      account={null}
      onRefreshAccount={vi.fn()}
    />
  );
  fireEvent.click(screen.getByRole("button", { name: "打开账号切换器" }));

  fireEvent.keyDown(document, { key: "Escape" });

  expect(screen.queryByRole("dialog", { name: "账号切换器" })).not.toBeInTheDocument();
});

test("closes the account popover when the current route changes", () => {
  const props = {
    onChange: vi.fn(),
    account: null,
    onRefreshAccount: vi.fn()
  };
  const { rerender } = render(<Sidebar active="dashboard" {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "打开账号切换器" }));

  rerender(<Sidebar active="friends" {...props} />);

  expect(screen.queryByRole("dialog", { name: "账号切换器" })).not.toBeInTheDocument();
});

test("never renders more than one account popover", () => {
  render(
    <Sidebar
      active="dashboard"
      onChange={vi.fn()}
      account={null}
      onRefreshAccount={vi.fn()}
    />
  );
  const trigger = screen.getByRole("button", { name: "打开账号切换器" });

  fireEvent.click(trigger);
  fireEvent.click(trigger);
  fireEvent.click(trigger);

  expect(screen.getAllByRole("dialog", { name: "账号切换器" })).toHaveLength(1);
});

test("removes account popover document listeners on unmount", () => {
  const addListener = vi.spyOn(document, "addEventListener");
  const removeListener = vi.spyOn(document, "removeEventListener");
  const { unmount } = render(
    <Sidebar
      active="dashboard"
      onChange={vi.fn()}
      account={null}
      onRefreshAccount={vi.fn()}
    />
  );
  const pointerHandler = addListener.mock.calls.find(
    ([type]) => type === "pointerdown"
  )?.[1];
  const keyHandler = addListener.mock.calls.find(
    ([type]) => type === "keydown"
  )?.[1];

  unmount();

  expect(pointerHandler).toBeDefined();
  expect(keyHandler).toBeDefined();
  expect(removeListener).toHaveBeenCalledWith("pointerdown", pointerHandler);
  expect(removeListener).toHaveBeenCalledWith("keydown", keyHandler);
});
