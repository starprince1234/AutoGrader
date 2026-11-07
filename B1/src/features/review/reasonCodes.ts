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
  email_verification_rejected: "邮件核验失败",
  email_sender_mismatch: "发件邮箱与账号不匹配",
  email_verification_expired: "核验码已过期",
  email_body_missing_field: "邮件正文缺字段",
  email_body_mismatch: "邮件正文身份不符",
  attachment_mapping_invalid: "附件题号映射无效",
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

/** 给老师看的"为什么失败"，一句话说清后果；不显示内部字段名或原始错误文本。 */
const REASON_HINTS: Record<ReasonCode, string> = {
  not_uniquely_matched: "发件邮箱对应不止一个学生，系统无法判断该算谁的。",
  ambiguous_match: "有多个学生匹配到这封邮件，需要人工确认。",
  malformed_message: "邮件格式损坏，无法读取附件。",
  missing_message_id: "邮件缺少唯一标识，无法安全地避免重复提交。",
  no_attachments: "邮件里没有可提交的附件。",
  too_many_attachments: "附件数量超过上限。",
  message_too_large: "邮件体积超过上限。",
  attachment_name_not_allowed: "附件名包含不允许的字符。",
  duplicate_attachment_name: "有多个同名附件，无法区分。",
  unsupported_attachment_encoding: "附件编码格式不支持。",
  malformed_attachment: "附件损坏，无法读取。",
  attachment_too_large: "单个附件超过大小上限。",
  attachment_total_too_large: "附件总大小超过上限。",
  unsupported_attachment_type: "附件类型不在允许范围内。",
  nested_message_attachment: "附件是另一封邮件，不能直接作为作业。",
  executable_attachment: "附件包含可执行文件，安全策略拒绝。",
  archive_bomb: "压缩包解压后体积异常，安全策略拒绝。",
  archive_attachment: "压缩包不能作为提交。",
  not_plain_text: "附件不是纯文本，无法安全读取。",
  email_verification_rejected: "邮件没有携带可用的一次性核验码，或核验码已被使用。",
  email_sender_mismatch: "发件邮箱与提出核验码的账号不一致。",
  email_verification_expired: "核验码已过期，需要学生重新申请。",
  email_body_missing_field: "邮件正文缺少核验码/作业编号/课序/姓名/学号中的必填项。",
  email_body_mismatch: "邮件正文里的作业编号、姓名或学号与账号记录不一致。",
  attachment_mapping_invalid: "附件名不是 1.txt、2.txt 这类按题号命名的文件，无法映射到作业题目。",
};

export function reasonHint(reasonCode: string): string {
  return (REASON_HINTS as Record<string, string | undefined>)[reasonCode] ?? "附件未通过安全检查。";
}

/**
 * 是否可人工归属。**fail-closed**：不认识的原因码返回 `false`，这样"服务端新增了一个原因码"
 * 不会变成"教师点了一个必然 422 的按钮"。
 */
export function isAttributableReason(reasonCode: string): boolean {
  return (ATTRIBUTABLE_REASON_CODES as readonly string[]).includes(reasonCode);
}
