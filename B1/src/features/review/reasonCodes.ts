import type { IntakeQuarantineView } from "../../api/types";

/**
 * 隔离记录的 `reason_code` 里**唯一**需要界面知道的部分：哪些可以人工归属。
 *
 * 原因码本身不在复核页上展示：学生必须按工作区给出的格式提交，能进入系统的邮件已经是合法
 * 形态；复核页只需要回答"这条能不能匹配用户 / 要不要打回"。把内部原因码铺成下拉与说明，
 * 只会把老师的注意力引到实现细节上。
 *
 * 但规则仍必须与服务端一致，因此这里保留那一条规则，且只保留它：
 *
 * - 可归属集合来自 B2 的 `ATTRIBUTABLE_REASON_CODES`（只有"身份没推出来"的两项）；
 * - 其余原因码是报文本身被拒，人工补一个学生修不好它（服务端会 422）。
 */

export type ReasonCode = IntakeQuarantineView["reason_code"];

export const ATTRIBUTABLE_REASON_CODES: readonly ReasonCode[] = [
  "not_uniquely_matched",
  "ambiguous_match",
];

/**
 * 是否可人工归属。**fail-closed**：不认识的原因码返回 `false`，这样"服务端新增了一个原因码"
 * 不会变成"教师点了一个必然 422 的按钮"。
 */
export function isAttributableReason(reasonCode: string): boolean {
  return (ATTRIBUTABLE_REASON_CODES as readonly string[]).includes(reasonCode);
}
