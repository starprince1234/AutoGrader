import { describe, expect, it } from "vitest";
import { glowFrame } from "./glowAnimation";

/**
 * 常驻光晕的约束（不是"看起来好看"，而是可断言的性质）：
 * - 每条色带的色标位置严格递增（head < tail），否则浏览器会把后一个钳回前一个 → 硬边；
 * - 色带最深只到 56%，**下半屏永远是透明**，内容区因此不会被染色；
 * - 两条带的权重之和恒为 1（交接处没有亮度台阶）；
 * - 不透明度有呼吸，且永远落在 [0.36, 0.90]；
 * - 整轮会走完六个色相（淡紫之后回到蔚蓝）。
 */

describe("glowFrame", () => {
  it("每条色带的色标位置严格递增：head < tail，且 tail 不超过 56%", () => {
    for (let elapsed = 0; elapsed < 12000; elapsed += 97) {
      const frames = glowFrame(elapsed);
      for (const band of [frames.vanishing, frames.rising]) {
        expect(band.head).toBeLessThan(band.tail);
        expect(band.tail).toBeLessThanOrEqual(56);
        expect(band.head).toBeGreaterThanOrEqual(8);
      }
    }
  });

  it("退场带会真的往下浸染：head 与 tail 都单调下推", () => {
    const early = glowFrame(0).vanishing;
    const late = glowFrame(1400).vanishing;
    expect(late.head).toBeGreaterThan(early.head);
    expect(late.tail).toBeGreaterThan(early.tail);
  });

  it("两条带的权重之和恒为 1：交接处没有亮度台阶", () => {
    for (let elapsed = 0; elapsed < 12000; elapsed += 53) {
      const frames = glowFrame(elapsed);
      expect(frames.vanishing.weight + frames.rising.weight).toBeCloseTo(1, 6);
      for (const band of [frames.vanishing, frames.rising]) {
        expect(band.weight).toBeGreaterThanOrEqual(0);
        expect(band.weight).toBeLessThanOrEqual(1);
      }
    }
  });

  it("呼吸落在 [0.36, 0.90] 区间，并且确实在变化", () => {
    const samples = Array.from({ length: 40 }, (_, index) => glowFrame(index * 125).opacity);
    for (const value of samples) {
      expect(value).toBeGreaterThanOrEqual(0.36);
      // 浮点边界：正弦在整周期处会得到 0.9000000000000001。
      expect(value).toBeLessThanOrEqual(0.9 + 1e-9);
    }
    expect(Math.max(...samples) - Math.min(...samples)).toBeGreaterThan(0.2);
  });

  it("整轮会走完六个色相", () => {
    const seen = new Set<string>();
    // 1600ms × 6 = 9600ms 为一整轮；去掉 alpha（含逗号后的空格）后按色相计数。
    const hue = (value: string) => value.replace(/,\s*[\d.]+\)$/, ")");
    for (let elapsed = 0; elapsed < 9700; elapsed += 40) {
      const frames = glowFrame(elapsed);
      seen.add(hue(frames.vanishing.color));
      seen.add(hue(frames.rising.color));
    }
    expect(seen.size).toBe(6);
  });
});
