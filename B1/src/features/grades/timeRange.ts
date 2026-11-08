/**
 * 成绩时间范围（RQ-B-1-60）：`<input type="datetime-local">` 的本地时间 ↔ 契约要求的 UTC RFC 3339。
 *
 * 两件事必须分开：
 * - **输入**是本地时区（教师看到的是自己钟表上的时间）；
 * - **请求**是 UTC RFC 3339（契约 `Timestamp`）。
 *
 * 中间不做任何"猜时区"的动作：`new Date("2026-09-15T08:00")` 按浏览器本地时区解释，`toISOString()`
 * 给 UTC。转换失败返回 `null`，调用方据此**不发请求**并给出字段级提示——把无效时间静默当成
 * "没有筛选"会悄悄扩大范围，而用户以为自己在看某一段时间。
 */

/** 本地 `date`/`datetime-local` 值 → UTC RFC 3339；空串返回 `""`（= 不筛选），非法返回 `null`。 */
export function localInputToUtc(value: string): string | null {
  const trimmed = value.trim();
  if (trimmed === "") return "";
  // `<input type="date">` 的值是 `YYYY-MM-DD`。直接 `new Date("YYYY-MM-DD")` 会按 UTC
  // 解析，而这里必须按用户本地时区解释，否则跨时区会把日期挪一天。
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(trimmed);
  const parsed = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString();
}

/**
 * 范围校验：`null` = 合法。
 *
 * 返回的是**错误种类**而不是文案：i18n 在组件层（`t("grades.timeInvalid")` 等）。
 * `from > to` 必须拒绝而不是交换——交换会把一次写错的筛选变成一个"看起来正常"的结果。
 */
export type TimeRangeProblem = "fromInvalid" | "toInvalid" | "order";

export function rangeProblem(from: string, to: string): TimeRangeProblem | null {
  const fromUtc = localInputToUtc(from);
  const toUtc = localInputToUtc(to);
  if (fromUtc === null) return "fromInvalid";
  if (toUtc === null) return "toInvalid";
  if (fromUtc !== "" && toUtc !== "") {
    if (new Date(fromUtc).getTime() > new Date(toUtc).getTime()) return "order";
  }
  return null;
}

/** 时间输入的展示标签（导出摘要用；`""` 表示未限制）。 */
export function rangeLabel(fromUtc: string | null, toUtc: string | null): string {
  if ((fromUtc ?? "") === "" && (toUtc ?? "") === "") return "";
  return `${fromUtc ?? "—"} → ${toUtc ?? "—"}`;
}
