import { useEffect } from "react";
import { glowFrame, type GlowFrame } from "./glowAnimation";

/**
 * 常驻背景光晕（Gemini 式色彩浸染）的宿主组件。
 *
 * 它不属于任何页面：`Root` 在最外层渲染一次，`position: fixed; z-index: -1`，
 * 因此**一直存在、不需要触发**，路由切换也不会重挂动画。
 *
 * 参考文档的关键特征是"颜色从屏幕边缘向内浸染"而不是整屏同时换色。CSS 的
 * `transition` 不支持对 `background-image` 做插值，所以这里用 `requestAnimationFrame`
 * 逐帧改写 CSS 自定义属性，再由样式表把这些变量拼成"径向 + 纵向"两层渐变
 * （见 `styles.css` 的「常驻光晕层」一节与 `glowAnimation.ts` 的色带模型）。
 *
 * 三条硬约束：
 * 1. **不干扰可读性**：色带整体不透明度被 `--glow-strength` 再压一次（亮色 ≈0.72、深色 ≈0.32），
 *    且最深只到 56%；卡片与表格是 opaque 表面，光只从面板缝隙透出来。
 * 2. **尊重动效偏好**：`prefers-reduced-motion: reduce` 时只写一帧静态值，不开循环。
 * 3. **不阻塞首屏**：首帧立刻写一次变量，避免"先白后彩"的闪烁；页面不可见时暂停 rAF。
 */

const CSS_PREFIX = "--glow-";

/** 把一帧写进 `:root`：两条色带各自是"颜色（alpha=权重）/ 顶端满色位置 / 淡出位置"。 */
function applyFrame(root: HTMLElement, frames: GlowFrame): void {
  const bands = [
    ["a", frames.vanishing],
    ["b", frames.rising],
  ] as const;
  for (const [key, band] of bands) {
    root.style.setProperty(`${CSS_PREFIX}${key}`, band.color);
    root.style.setProperty(`${CSS_PREFIX}${key}-head`, `${band.head.toFixed(2)}%`);
    root.style.setProperty(`${CSS_PREFIX}${key}-tail`, `${band.tail.toFixed(2)}%`);
  }
  root.style.setProperty(`${CSS_PREFIX}opacity`, frames.opacity.toFixed(3));
}

export function GeminiGlow() {
  useEffect(() => {
    const root = document.documentElement;
    const reduceMotion =
      typeof globalThis.matchMedia === "function" &&
      globalThis.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reduceMotion) {
      // 静态一帧：色带停在初始位置、不呼吸，也不启动 rAF。
      applyFrame(root, glowFrame(0));
      return undefined;
    }

    let frame = 0;
    let startedAt: number | null = null;
    let lastPaint = Number.NEGATIVE_INFINITY;
    /**
     * 30fps 上限：色相变化本来就慢，60fps 只是让每一帧都重算一次大面积渐变。
     * 降到 30fps 肉眼几乎无差，但省一半的样式重算与合成。
     */
    const MIN_FRAME_MS = 1000 / 30;

    const tick = (now: number) => {
      if (startedAt === null) startedAt = now;
      if (now - lastPaint >= MIN_FRAME_MS) {
        lastPaint = now;
        applyFrame(root, glowFrame(now - startedAt));
      }
      frame = globalThis.requestAnimationFrame(tick);
    };
    frame = globalThis.requestAnimationFrame(tick);

    // 标签页不可见时停掉循环，回来时从当前时刻继续（不补帧、不跳变）。
    const onVisibility = () => {
      globalThis.cancelAnimationFrame(frame);
      if (document.visibilityState === "hidden") return;
      startedAt = null;
      frame = globalThis.requestAnimationFrame(tick);
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      globalThis.cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return <div className="gemini-glow" aria-hidden="true" data-testid="gemini-glow" />;
}
