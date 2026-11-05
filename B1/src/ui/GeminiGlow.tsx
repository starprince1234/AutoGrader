import { useEffect, useRef } from "react";
import type { CSSProperties } from "react";
import { GLOW_ITERATION_S, GLOW_LAYER_COUNT, GLOW_PHASE_S, glowColorAt } from "./glowPalette";

/**
 * 常驻背景光晕（Gemini 式色彩浸染）的宿主组件。
 *
 * 它不属于任何页面：`Root` 在最外层渲染一次，`position: fixed; z-index: -1`，
 * 因此一直存在、不需要触发，路由切换也不会重挂动画。
 *
 * ## 双层缓冲：为什么只要两个图层
 *
 * 一屏里真正同时出现的只有两条色带——一条在退场、一条在进场。所以不需要"每个色相
 * 一个图层"（那要六张全屏纹理常驻显存），只需要**两层**：
 * - 两层的 `opacity` 斜坡互补（相位差半个迭代），叠加透明度恒为 1，深度全程不变；
 * - 每层跑到自己迭代的末尾时 opacity 正好回到 0，**在那一刻换颜色是看不见的**；
 * - 换色由 CSS 动画的 `animationiteration` 事件驱动：每 `GLOW_PHASE_S` 秒换一次，
 *   每次只改一个 CSS 变量，没有逐帧 JS，也不会随时间累积任何内存。
 *
 * ## 其余约束
 *
 * 1. **不逐帧重画**：动画只跑 `opacity` 与 `transform`（合成器属性）。
 *    旧版用 rAF 每帧改写渐变几何，等于每秒重画 30 次全屏渐变，那是卡顿的根因。
 * 2. **不干扰可读性**：整组再乘一次静态的 `--glow-strength`（亮色 0.6、深色 0.3）；
 *    卡片与表格是不透明表面，光只从面板缝隙透出来。
 * 3. **尊重动效偏好**：`prefers-reduced-motion: reduce` 时样式表停掉动画，
 *    自然不会派发 `animationiteration`，颜色就停在初始这一层，不再闪烁。
 * 4. **不阻塞首屏**：颜色与相位都是行内静态值，首帧就是最终状态。
 */
export function GeminiGlow() {
  const layers = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    let nextIndex = GLOW_LAYER_COUNT;
    const bound: { node: HTMLDivElement; handler: () => void }[] = [];

    for (const node of layers.current) {
      if (!node) continue;
      const handler = () => {
        // 迭代结束 = 这一层正好在 opacity 0，换色不会出现"跳变"。
        const color = glowColorAt(nextIndex);
        nextIndex += 1;
        node.style.setProperty("--band-color", `rgb(${color.rgb})`);
      };
      node.addEventListener("animationiteration", handler);
      bound.push({ node, handler });
    }

    return () => {
      for (const { node, handler } of bound) {
        node.removeEventListener("animationiteration", handler);
      }
    };
  }, []);

  return (
    <div className="gemini-glow" aria-hidden="true" data-testid="gemini-glow">
      <div className="gemini-glow__bands">
        {Array.from({ length: GLOW_LAYER_COUNT }, (_, index) => {
          const color = glowColorAt(index);
          return (
            <div
              key={index}
              ref={(node) => {
                layers.current[index] = node;
              }}
              // 偶数层用左偏光源、奇数层用右偏光源，保留"两个光源"的体积感。
              className={
                index % 2 === 0 ? "gemini-glow__band" : "gemini-glow__band gemini-glow__band--b"
              }
              data-testid="gemini-glow-band"
              data-color={color.name}
              style={
                {
                  "--band-color": `rgb(${color.rgb})`,
                  animationDuration: `${GLOW_ITERATION_S}s`,
                  // 负延迟 = 直接从半程开始，两层因此相差半个迭代（斜坡互补）。
                  animationDelay: `${-index * GLOW_PHASE_S}s`,
                } as CSSProperties
              }
            />
          );
        })}
      </div>
    </div>
  );
}
