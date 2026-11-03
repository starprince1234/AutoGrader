/**
 * 常驻背景光晕的纯计算部分（与 React 无关，便于单测）。
 * 视觉变量怎么拼成渐变，见 `styles.css` 的「常驻光晕层」一节。
 *
 * 模型：同一时刻总有**两条色带**——上一条正在退场、下一条正在进场。
 * 每条色带在自己的渐变里都从顶部满色开始，到 `tail` 处淡到透明；
 * 因此单条色带的色标位置严格递增（head < tail），浏览器不会把后一个色标钳回前一个位置。
 * 这也是上一版出现"一条硬边紫色带"的根因：当时把 tail 放在了 head 之前。
 */

/** 色带顺序，与参考文档一致：蔚蓝 → 青蓝 → 翠绿 → 亮黄绿 → 鹅黄 → 淡紫。 */
const PALETTE = [
  [74, 159, 217], // #4A9FD9 蔚蓝
  [61, 184, 176], // #3DB8B0 青蓝
  [107, 191, 89], // #6BBF59 翠绿
  [156, 204, 74], // #9CCC4A 亮黄绿
  [221, 227, 106], // #DDE36A 鹅黄
  [168, 127, 212], // #A87FD4 淡紫
] as const;

/**
 * 单个色相在渐变里"存活"的时间（毫秒）。参考文档里每个阶段 0.8～1 秒、
 * 一整轮 5 秒左右；这里取 1600ms，让它在教学场景里足够慢，不会抢注意力。
 */
const PHASE_MS = 1600;
/** 光晕的不透明度上限：样式表里的 `--glow-strength` 会再乘一次。 */
const MAX_OPACITY = 0.9;
/** 安静期的下限：色带淡到几乎只剩底色，形成"呼吸"。 */
const MIN_OPACITY = 0.36;
/** 最深只推到视口 45% 附近；下半屏永远留给底色（参考文档的硬性要求）。 */
const MAX_TAIL = 56;
const TAU = Math.PI * 2;

export type GlowBand = {
  /** 含 alpha 的 RGBA 字符串。 */
  color: string;
  /** 该色带从顶部到多深处开始淡出（百分比）。 */
  head: number;
  /** 淡到全透明的位置（百分比）；必须大于 `head`。 */
  tail: number;
  /** 该色带的整体权重（0..1），退场带 < 进场带。 */
  weight: number;
};

export type GlowFrame = {
  /** `vanishing` 是正在退场的上一条色带，`rising` 是正在进场的新色带。 */
  vanishing: GlowBand;
  rising: GlowBand;
  /** 整体不透明度（呼吸）。 */
  opacity: number;
};

function lerp(from: number, to: number, t: number): number {
  return from + (to - from) * t;
}

/** ease-in-out：颜色进出都是软的，没有硬拐点。 */
function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
}

function rgba(color: readonly [number, number, number], alpha: number): string {
  return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${alpha.toFixed(3)})`;
}

/**
 * 给定已运行时长（毫秒），算出这一帧的两条色带与呼吸值。
 *
 * - 色带首尾衔接地循环（淡紫之后回到蔚蓝），画面因此**永远**在缓慢流动；
 * - 退场带的 `head` 从 18% 下推到 42%、`tail` 从 34% 下推到 56%，读作"颜色向下浸染"；
 * - 进场带的 `head` 从 8% 推到 18%、`tail` 从 22% 推到 40%，读作"新颜色从顶边长出来"；
 * - 两条带的权重之和恒为 1，交接处不会出现亮度台阶。
 */
export function glowFrame(elapsedMs: number): GlowFrame {
  const position = elapsedMs / PHASE_MS;
  const index = Math.floor(position);
  const eased = easeInOut(position - index);
  const vanishingColor = PALETTE[index % PALETTE.length];
  const risingColor = PALETTE[(index + 1) % PALETTE.length];

  const vanishing: GlowBand = {
    // alpha 就是权重：两条带同处屏幕顶部时，合成后的颜色恰好是两色按权重混合。
    color: rgba(vanishingColor, 1 - eased),
    head: lerp(18, 42, eased),
    tail: lerp(34, MAX_TAIL, eased),
    weight: 1 - eased,
  };
  const rising: GlowBand = {
    color: rgba(risingColor, eased),
    head: lerp(8, 18, eased),
    tail: lerp(22, 40, eased),
    weight: eased,
  };

  // 呼吸：5 秒一个正弦周期，波谷时几乎只剩底色。
  const breath = 0.5 - 0.5 * Math.cos((TAU * elapsedMs) / 5000);
  const opacity = MIN_OPACITY + (MAX_OPACITY - MIN_OPACITY) * breath;

  return { vanishing, rising, opacity };
}
