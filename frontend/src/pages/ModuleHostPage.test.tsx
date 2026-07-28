import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { ModuleHostPage, isValidModuleHeightMessage } from "./ModuleHostPage";

test("accepts only bounded resize messages from the Test Center iframe", () => {
  const source = window;

  expect(isValidModuleHeightMessage({
    origin: window.location.origin,
    source,
    data: { type: "autody-test-center:resize", moduleId: "autody-test-center", height: 920 }
  } as unknown as MessageEvent, source)).toBe(920);
  expect(isValidModuleHeightMessage({
    origin: "https://untrusted.example",
    source,
    data: { type: "autody-test-center:resize", moduleId: "autody-test-center", height: 920 }
  } as unknown as MessageEvent, source)).toBeNull();
  expect(isValidModuleHeightMessage({
    origin: window.location.origin,
    source,
    data: { type: "autody-test-center:resize", moduleId: "wrong-module", height: 920 }
  } as unknown as MessageEvent, source)).toBeNull();
  expect(isValidModuleHeightMessage({
    origin: window.location.origin,
    source,
    data: { type: "autody-test-center:resize", moduleId: "autody-test-center", height: 99999 }
  } as unknown as MessageEvent, source)).toBeNull();
});

test("renders Test Center without a duplicated host header or nested scroll shell", () => {
  render(<ModuleHostPage onRemoved={() => undefined} />);

  expect(screen.queryByText("设置 / 可选模块 / 测试中心")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "测试中心" })).not.toBeInTheDocument();
  expect(screen.getByTitle("测试中心").parentElement).toHaveClass("editor-page");
  expect(screen.getByTitle("测试中心").parentElement).not.toHaveClass("module-host-page");
  expect(screen.getByTitle("测试中心")).toHaveClass("module-host");
  expect(screen.getByTitle("测试中心")).not.toHaveStyle({ overflow: "auto" });
});
