import { t } from "../../i18n";
import type { IntakeQuarantineView } from "../../api/types";

/**
 * 隔离记录的 `reason_code` 词表（P13-A）。
 *
 * 类型**取自生成类型**（`IntakeQuarantineView["reason_code"]`），因此 `REASON_LABELS` 的键由
 * 编译期核对：契约新增/删除原因码时，这里会直接报错，而不是在运行时悄悄少一个标签。
 *
 * 两个行为集合必须分开写，因为它们是**规则**而不是字段形状：
 *
 * - 词表（19 项）来自契约 `enum`；
 * - 可归属集合（只有两项）来自 B2 的 `ATTRIBUTABLE_REASON_CODES`，界面据此决定是否显示归属入口。
 *   其余原因码是"报文本身被拒"，人工补一个学生修不好它（服务端 422）。
 */

export type ReasonCode = IntakeQuarantineView["reason_code"];

/** 只有"身份没推出来"的两种原因码可以人工归属；其余一律不提供入口。 */
export const ATTRIBUTABLE_REASON_CODES: readonly ReasonCode[] = [
  "not_uniquely_matched",
  "ambiguous_match",
];

/** 稳定中文标签：枚举值不直接出现在界面上（原因码是机器可读字段，不是文案）。 */
export const REASON_LABELS: Record<ReasonCode, string> = {
  not_uniquely_matched: "未唯一匹配",
  ambiguous_match: "歧义匹配",
  malformed_message: "报文格式错误",
  missing_message_id: "缺少 Message-ID",
  no_attachments: "没有附件",
  too_many_attachments: "附件数量超限",
  message_too_large: "邮件体积超限",
  attachment_name_not_allowed: "附件名不被允许",
  duplicate_attachment_name: "附件名重复",
  unsupported_attachment_encoding: "附件编码不支持",
  malformed_attachment: "附件损坏",
  attachment_too_large: "单个附件超限",
  attachment_total_too_large: "附件总大小超限",
  unsupported_attachment_type: "附件类型不支持",
  nested_message_attachment: "嵌套邮件附件",
  executable_attachment: "可执行附件",
  archive_bomb: "压缩炸弹",
  archive_attachment: "压缩包附件",
  not_plain_text: "非纯文本",
};

/** 原因码筛选下拉的取值顺序（与词表声明顺序一致，便于与契约对照）。 */
export const REASON_CODES: readonly ReasonCode[] = Object.keys(
  REASON_LABELS,
) as ReasonCode[];

/** 原因码 → 中文标签；契约之外的未来值显示"其他原因"，**不**动态拼字段名。 */
export function reasonLabel(reasonCode: string): string {
  const label = (REASON_LABELS as Record<string, string | undefined>)[reasonCode];
  return label ?? t("review.mail.reasonUnknown");
}

/**
 * 是否可人工归属。**fail-closed**：不认识的原因码返回 `false`，这样"服务端新增了一个原因码"
 * 不会变成"教师点了一个必然 422 的按钮"。
 */
export function isAttributableReason(reasonCode: string): boolean {
  return (ATTRIBUTABLE_REASON_CODES as readonly string[]).includes(reasonCode);
}
