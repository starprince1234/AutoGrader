/**
 * 常驻光晕的色带（组件与样式表共享的唯一事实来源）。
 *
 * 为什么单独拆一个模块：色相顺序与节奏要同时被组件（写行内变量与相位）和测试
 * （断言错开、整轮时长）用到，放在一处才不会出现"改了颜色但样式表还是旧的"。
 *
 * 颜色只存 RGB 通道，透明度交给每层的 opacity 动画——图层只需要合成，
 * 不需要在运行期重画任何渐变（旧版逐帧改写渐变几何，是卡顿的根因）。
 */

export type GlowColor = {
  /** 中文色名，仅用于调试与测试失败时的可读性。 */
  name: string;
  /** `rgb()` 里的三段通道值，空格分隔。 */
  rgb: string;
};

/**
 * 与设计稿一致的色相顺序：蔚蓝 → 青蓝 → 翠绿 → 亮黄绿 → 鹅黄 → 淡紫。
 *
 * **但明度是校准过的**：设计稿的原始值（鹅黄 `221 227 106`、淡紫 `168 127 212`）
 * 渲染出来的亮度在 0.49～0.79 之间跳，换色时整片背景会"忽然变亮/忽然变深"——
 * 前面几版的观感问题有一半来自这里，光靠"透明度总和恒定"是救不了的。
 * 这里保留色相与原顺序，把渲染亮度压进 0.52～0.60 的窄带（落差 0.30 → 0.08）：
 * 黄仍然比紫亮一点，但不会再跳。校准方法与实测数据见 `phase-11-resident-glow-fall.md`。
 */
export const GLOW_PALETTE: readonly GlowColor[] = [
  { name: "蔚蓝", rgb: "75 162 221" },
  { name: "青蓝", rgb: "58 176 168" },
  { name: "翠绿", rgb: "99 177 83" },
  { name: "亮黄绿", rgb: "136 177 64" },
  { name: "鹅黄", rgb: "174 179 84" },
  { name: "淡紫", rgb: "177 134 223" },
];

/**
 * 相邻两个色相交接的时长（秒）：每过这么久就有一个新颜色开始进场。
 * 3 秒是"看得出在换、又不抢注意力"的节奏；整轮 = 色相数 × 该值 = 18 秒。
 */
export const GLOW_PHASE_S = 3;

/** 走完六个色相的总时长（秒）。 */
export const GLOW_CYCLE_S = GLOW_PALETTE.length * GLOW_PHASE_S;

/**
 * 图层数。**只要两层**（双缓冲）：一层淡出时另一层淡入，两者斜坡互补，
 * 叠加的透明度恒为 1。层数从 6 降到 2 是为了显存——每个图层都是一张全屏纹理，
 * 六张常驻会白白吃掉几百 MB 的 GPU 内存。
 */
export const GLOW_LAYER_COUNT = 2;

/** 两层各跑一个"淡入 + 淡出"迭代，迭代时长 = 两个相位。 */
export const GLOW_ITERATION_S = GLOW_PHASE_S * 2;

/** 取色板里第 index 个色相（自动折返）。 */
export function glowColorAt(index: number): GlowColor {
  return GLOW_PALETTE[((index % GLOW_PALETTE.length) + GLOW_PALETTE.length) % GLOW_PALETTE.length];
}
