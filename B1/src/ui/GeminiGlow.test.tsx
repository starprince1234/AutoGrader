import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GeminiGlow } from "./GeminiGlow";

/**
 * 常驻光晕的宿主组件行为：首帧就要写变量（避免"先白后彩"），
 * 低动态偏好下不启动 rAF。色带本身的数学约束见 `glowAnimation.test.ts`。
 */

const REDUCED = "(prefers-reduced-motion: reduce)";
const NO_REDUCTION = "(prefers-reduced-motion: no-preference)";
const originalMatchMedia = globalThis.matchMedia;

/**
 * rAF 桩：只执行有限帧（默认 1 帧），不回调自身。
 * 真实 rAF 在每帧里再次申请下一帧，jsdom 桩若同步回调就会无限递归。
 */
function stubAnimationFrame(frames = 1) {
  let remaining = frames;
  vi.spyOn(globalThis, "requestAnimationFrame").mockImplementation((callback) => {
    if (remaining > 0) {
      remaining -= 1;
      callback(0);
    }
    return 1;
  });
  vi.spyOn(globalThis, "cancelAnimationFrame").mockImplementation(() => undefined);
}

function fakeMatchMedia(reduced: boolean) {
  globalThis.matchMedia = ((query: string) => ({
    matches: query.includes("reduced-motion") ? reduced : false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof globalThis.matchMedia;
}

describe("GeminiGlow", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    if (originalMatchMedia) globalThis.matchMedia = originalMatchMedia;
    else delete (globalThis as { matchMedia?: unknown }).matchMedia;
    document.documentElement.removeAttribute("style");
  });

  it("渲染一个 aria-hidden 的空容器：不参与读屏，也不占布局语义", () => {
    fakeMatchMedia(false);
    stubAnimationFrame();

    render(<GeminiGlow />);
    const glow = screen.getByTestId("gemini-glow");
    expect(glow).toHaveClass("gemini-glow");
    expect(glow).toHaveAttribute("aria-hidden", "true");
    expect(glow).toBeEmptyDOMElement();
    // 首帧就把变量写到 :root 上。
    expect(document.documentElement.style.getPropertyValue("--glow-opacity")).not.toBe("");
    expect(document.documentElement.style.getPropertyValue("--glow-a")).not.toBe("");
    expect(document.documentElement.style.getPropertyValue("--glow-b-head")).not.toBe("");
  });

  it("低动态偏好下不启动 rAF，只写一帧静态值", () => {
    fakeMatchMedia(true);
    stubAnimationFrame();
    const raf = vi.spyOn(globalThis, "requestAnimationFrame");

    render(<GeminiGlow />);

    expect(raf).not.toHaveBeenCalled();
    expect(document.documentElement.style.getPropertyValue("--glow-a")).toContain("rgba(");
  });

  it(`没有媒体查询能力时按"无低动态偏好"处理（${NO_REDUCTION}）`, () => {
    delete (globalThis as { matchMedia?: unknown }).matchMedia;
    stubAnimationFrame();

    render(<GeminiGlow />);
    expect(globalThis.requestAnimationFrame).toHaveBeenCalled();
  });
});

describe("REDUCED 常量与 CSS 一致", () => {
  it("使用与样式表相同的媒体查询文本", () => {
    expect(REDUCED).toBe("(prefers-reduced-motion: reduce)");
  });
});
