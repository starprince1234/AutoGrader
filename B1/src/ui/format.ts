/**
 * 展示层格式化（共享文件：改动前在 `docs/phases/active/` 登记）。
 *
 * 为什么不用 `toLocaleString()`：它按浏览器 locale 输出 "2026/2/5 09:13:07"
 * 这类非定宽、非零填充的字符串，同一列里宽度参差；时间列还依赖运行环境 locale，
 * 同一份数据在不同机器上渲染不一致。这里固定输出 `YYYY-MM-DD HH:mm:ss`（本地时区），
 * 既定宽又可直接排序比较。
 */

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** 把 ISO 时间戳格式化为定宽的本地时间 `YYYY-MM-DD HH:mm:ss`；缺失或不可解析时原样返回。 */
export function formatDateTime(value: string | null | undefined, fallback: string): string {
  if (value === null || value === undefined || value === "") return fallback;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  );
}

/** 需要视觉缩短的标识符列：UUID 全文 36 字符，两列就能把表格挤到横向滚动。 */
const ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * 标识符的**展示用**短形式（前 8 位 + `…`）。
 *
 * 标识符是拿来复制比对的，不是拿来阅读的；表格里放全文会把别的列挤出去。全文仍然完整
 * 保留在 DOM 上（调用方把它放进 `title` 与可选中文本），所以这不会隐藏任何值——`RosterPage`
 * 的 `ENROLLMENT_ID` 列因此仍是该行的真实标识。
 * 非 UUID（例如学号）原样返回：它们本来就不长，缩短反而损失信息。
 */
export function shortenId(value: string): string {
  return ID_PATTERN.test(value) ? `${value.slice(0, 8)}…` : value;
}

/**
 * 判断一个字符串是否是契约类型 `Uuid` 可接受的值。
 *
 * 用途是**在发请求之前**拦下必然得到 `422` 的输入（契约把 `student_id` 等字段声明为
 * `format: uuid`）：与名单页"拿不到 `updated_at` 就禁止保存"、出题页"受保护字段为空就不发请求"
 * 是同一条立场——B1 不发一个注定失败的请求，也不猜服务端会怎么容错。
 */
export function isUuid(value: string): boolean {
  return ID_PATTERN.test(value);
}
