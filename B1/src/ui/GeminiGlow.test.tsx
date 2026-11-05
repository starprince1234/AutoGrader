import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GeminiGlow } from "./GeminiGlow";
import { GLOW_CYCLE_S, GLOW_PALETTE, GLOW_PHASE_S } from "./glowPalette";

/**
 * 常驻光晕的可断言性质（不是"看起来好看"，而是能锁住的结构）：
 * - 一个色相一个图层，层数与色板一致；
 * - 每层的颜色、整轮时长、错开相位都写在行内，六层均匀铺满整轮；
 * - 组件不再写 `:root` 变量、不再申请 rAF——动画完全由样式表驱动，
 *   这是"不再每帧重画全屏渐变"的硬性前提；
 * - 容器是 aria-hidden 的装饰层。
 */

describe("GeminiGlow", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.removeAttribute("style");
  });

  it("渲染 aria-hidden 的容器，一个色相一层", () => {
    render(<GeminiGlow />);

    const glow = screen.getByTestId("gemini-glow");
    expect(glow).toHaveClass("gemini-glow");
    expect(glow).toHaveAttribute("aria-hidden", "true");

    const bands = screen.getAllByTestId("gemini-glow-band");
    expect(bands).toHaveLength(GLOW_PALETTE.length);
    expect(bands.map((band) => band.dataset.color)).toEqual(
      GLOW_PALETTE.map((color) => color.name),
    );
  });

  it("每层带上自己的颜色，且六层均匀错开一整轮", () => {
    render(<GeminiGlow />);

    const bands = screen.getAllByTestId("gemini-glow-band");
    bands.forEach((band, index) => {
      expect(band.style.getPropertyValue("--band-color")).toBe(
        `rgb(${GLOW_PALETTE[index].rgb})`,
      );
      expect(band.style.animationDuration).toBe(`${GLOW_CYCLE_S}s`);
      // 负延迟：第 index 层从 index/层数 处开始，六层正好铺满一轮。
      expect(band.style.animationDelay).toBe(`${-index * GLOW_PHASE_S}s`);
    });

    expect(GLOW_CYCLE_S).toBe(GLOW_PALETTE.length * GLOW_PHASE_S);
  });

  it("不再写 :root 变量、也不再申请 rAF：动画完全交给样式表", () => {
    const raf = vi.fn(() => 1);
    vi.stubGlobal("requestAnimationFrame", raf);

    render(<GeminiGlow />);

    expect(raf).not.toHaveBeenCalled();
    // 旧实现会把 --glow-a / --glow-b-head / --glow-opacity 写进 <html> 的行内样式。
    expect(document.documentElement.style.getPropertyValue("--glow-a")).toBe("");
    expect(document.documentElement.style.getPropertyValue("--glow-opacity")).toBe("");
  });
});
