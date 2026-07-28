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

test("renders Test Center as a full Settings child page", () => {
  render(<ModuleHostPage onRemoved={() => undefined} />);

  expect(screen.getByText("设置 / 可选模块 / 测试中心")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "测试中心" })).toBeInTheDocument();
  expect(screen.getByTitle("测试中心")).toHaveClass("module-host");
});
