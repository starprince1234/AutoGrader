import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GeminiGlow } from "./GeminiGlow";
import {
  GLOW_ITERATION_S,
  GLOW_LAYER_COUNT,
  GLOW_PALETTE,
  GLOW_PHASE_S,
  glowColorAt,
} from "./glowPalette";

/**
 * 常驻光晕的可断言性质（不是"看起来好看"，而是能锁住的结构）：
 * - **只有两层**（双缓冲）：每个图层都是一张全屏纹理，层数直接决定显存；
 * - 两层相位差半个迭代、迭代时长 = 两个相位；
 * - 换色由 `animationiteration` 驱动，每次只改一个 CSS 变量；
 * - 组件不写 `:root`、不申请 rAF——动画完全交给样式表。
 */

function fireIteration(node: HTMLElement) {
  node.dispatchEvent(new Event("animationiteration", { bubbles: false }));
}

describe("GeminiGlow", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.removeAttribute("style");
  });

  it("渲染 aria-hidden 的容器，并且只有两层色带", () => {
    render(<GeminiGlow />);

    const glow = screen.getByTestId("gemini-glow");
    expect(glow).toHaveClass("gemini-glow");
    expect(glow).toHaveAttribute("aria-hidden", "true");
    expect(GLOW_LAYER_COUNT).toBe(2);

    const bands = screen.getAllByTestId("gemini-glow-band");
    expect(bands).toHaveLength(GLOW_LAYER_COUNT);
    expect(bands.map((band) => band.dataset.color)).toEqual([
      GLOW_PALETTE[0].name,
      GLOW_PALETTE[1].name,
    ]);
  });

  it("两层相位差半个迭代，迭代时长是两个相位", () => {
    render(<GeminiGlow />);

    const bands = screen.getAllByTestId("gemini-glow-band");
    expect(GLOW_ITERATION_S).toBe(GLOW_PHASE_S * 2);

    bands.forEach((band, index) => {
      expect(band.style.animationDuration).toBe(`${GLOW_ITERATION_S}s`);
      expect(band.style.animationDelay).toBe(`${-index * GLOW_PHASE_S}s`);
      expect(band.style.getPropertyValue("--band-color")).toBe(
        `rgb(${GLOW_PALETTE[index].rgb})`,
      );
    });
  });

  it("每过一次迭代换一次颜色，且只在不可见时换（顺序沿色板推进）", () => {
    render(<GeminiGlow />);
    const [first, second] = screen.getAllByTestId("gemini-glow-band");

    // 交替触发两次：第一次轮到第三色（前两色已被初始层占用），第二次轮到第四色。
    fireIteration(first);
    expect(first.style.getPropertyValue("--band-color")).toBe(
      `rgb(${glowColorAt(2).rgb})`,
    );
    expect(first.dataset.color).not.toBe(GLOW_PALETTE[2].name); // data-* 只反映初始值

    fireIteration(second);
    expect(second.style.getPropertyValue("--band-color")).toBe(
      `rgb(${glowColorAt(3).rgb})`,
    );
  });

  it("颜色沿色板循环：走完一轮会回到同一个色相", () => {
    render(<GeminiGlow />);
    const [first] = screen.getAllByTestId("gemini-glow-band");

    // 初始两层用掉 0/1，第一次迭代给出色板第 3 个。
    fireIteration(first);
    const afterFirst = first.style.getPropertyValue("--band-color");
    expect(afterFirst).toBe(`rgb(${glowColorAt(2).rgb})`);

    // 再走满一整轮（色板长度次），落点与第一次相同 → 说明在循环。
    for (let index = 0; index < GLOW_PALETTE.length; index += 1) {
      fireIteration(first);
    }
    expect(first.style.getPropertyValue("--band-color")).toBe(afterFirst);
  });

  it("不再写 :root 变量、也不再申请 rAF", () => {
    const raf = vi.fn(() => 1);
    vi.stubGlobal("requestAnimationFrame", raf);

    render(<GeminiGlow />);

    expect(raf).not.toHaveBeenCalled();
    expect(document.documentElement.style.getPropertyValue("--glow-a")).toBe("");
    expect(document.documentElement.style.getPropertyValue("--glow-opacity")).toBe("");
  });
});
