import type { CSSProperties } from "react";
import { GLOW_CYCLE_S, GLOW_PALETTE, GLOW_PHASE_S } from "./glowPalette";

/**
 * 常驻背景光晕（Gemini 式色彩浸染）的宿主组件。
 *
 * 它不属于任何页面：`Root` 在最外层渲染一次，`position: fixed; z-index: -1`，
 * 因此一直存在、不需要触发，路由切换也不会重挂动画。
 *
 * ## 为什么不再用 rAF 逐帧改写渐变
 *
 * 旧版用 `requestAnimationFrame`（30fps）把色带的颜色与几何位置写进 CSS 变量，
 * 再由样式表拼成全屏渐变。变量一变，整块全屏渐变就要**重新光栅化**——等于每秒重画
 * 30 次全屏渐变，帧率一掉就是肉眼可见的"一卡一卡"；而且两条色带的 alpha 叠加与
 * 5 秒呼吸会让深浅在交接处跳一下。
 *
 * 现在每个色相是一个**固定图层**（六层，颜色与渐变都不变），动画只跑 `opacity`
 * 与 `transform` 两个合成器属性：
 * - 不需要每帧重画渐变，也不需要 rAF，JS 一次都不跑；
 * - 相邻两层错开六分之一轮、斜坡互补（见 `styles.css` 的 `glow-fall`），
 *   **任意时刻叠加的透明度总和恒为 1**，所以深浅全程不变，没有"忽然变深"；
 * - 每层在透明状态下淡入、再淡出，同时向下漂移，"出现/消失"都发生在 alpha=0 处，
 *   所以没有"色带突然重置"的观感。
 *
 * 三条硬约束保持不变：
 * 1. **不干扰可读性**：整组再乘一次 `--glow-strength`（亮色 0.6、深色 0.3）；
 *    卡片与表格是不透明表面，光只从面板缝隙透出来。
 * 2. **尊重动效偏好**：`prefers-reduced-motion: reduce` 时样式表直接停掉动画，
 *    只留一层静态色（不再呼吸、不再漂移）。
 * 3. **不阻塞首屏**：颜色与相位都是行内静态值，首帧就是最终状态，不存在"先白后彩"。
 */
export function GeminiGlow() {
  return (
    <div className="gemini-glow" aria-hidden="true" data-testid="gemini-glow">
      <div className="gemini-glow__bands">
        {GLOW_PALETTE.map((color, index) => (
          <div
            key={color.name}
            // 偶数层用左偏光源、奇数层用右偏光源，保留"两个光源"的体积感。
            className={
              index % 2 === 0 ? "gemini-glow__band" : "gemini-glow__band gemini-glow__band--b"
            }
            data-testid="gemini-glow-band"
            data-color={color.name}
            style={
              {
                "--band-color": `rgb(${color.rgb})`,
                animationDuration: `${GLOW_CYCLE_S}s`,
                // 负延迟 = 直接从中间相位开始，所以六层一开始就均匀铺满整轮。
                animationDelay: `${-index * GLOW_PHASE_S}s`,
              } as CSSProperties
            }
          />
        ))}
      </div>
    </div>
  );
}
