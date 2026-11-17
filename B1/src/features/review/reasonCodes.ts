import { t, type MessageKey } from "../../i18n";
import type { MailRecordReasonCode } from "../../api/types";

/**
 * 邮件记录的 `reason_code` → 界面文案。
 *
 * 页面只展示**一句中文说明**，不展示契约里的英文原因码，也不把 19/25 项词表铺成错误码表：
 * 老师要看的是"这封信为什么没变成作业"，不是枚举名。
 *
 * 类型取自生成类型（`MailRecordView["reason_code"]`），因此映射表的键由编译期核对：
 * 契约新增/删除原因码时这里会直接报错，而不是在运行时悄悄少一句说明。
 */

export type ReasonCode = MailRecordReasonCode;

/** 原因码 → i18n 键。`Record<ReasonCode, …>` 保证契约 enum 的每一项都有文案。 */
const REASON_MESSAGE_KEYS: Record<ReasonCode, MessageKey> = {
  // 身份没有推出来
  not_uniquely_matched: "review.reason.not_uniquely_matched",
  ambiguous_match: "review.reason.ambiguous_match",
  // 报文结构
  malformed_message: "review.reason.malformed_message",
  missing_message_id: "review.reason.missing_message_id",
  no_attachments: "review.reason.no_attachments",
  too_many_attachments: "review.reason.too_many_attachments",
  message_too_large: "review.reason.message_too_large",
  // 附件策略
  attachment_name_not_allowed: "review.reason.attachment_name_not_allowed",
  duplicate_attachment_name: "review.reason.duplicate_attachment_name",
  unsupported_attachment_encoding: "review.reason.unsupported_attachment_encoding",
  malformed_attachment: "review.reason.malformed_attachment",
  attachment_too_large: "review.reason.attachment_too_large",
  attachment_total_too_large: "review.reason.attachment_total_too_large",
  unsupported_attachment_type: "review.reason.unsupported_attachment_type",
  nested_message_attachment: "review.reason.nested_message_attachment",
  // 内容闸门
  executable_attachment: "review.reason.executable_attachment",
  archive_bomb: "review.reason.archive_bomb",
  archive_attachment: "review.reason.archive_attachment",
  not_plain_text: "review.reason.not_plain_text",
  // 一次性核验码 / 正文规则
  email_verification_rejected: "review.reason.email_verification_rejected",
  email_sender_mismatch: "review.reason.email_sender_mismatch",
  email_verification_expired: "review.reason.email_verification_expired",
  email_body_missing_field: "review.reason.email_body_missing_field",
  email_body_mismatch: "review.reason.email_body_mismatch",
  attachment_mapping_invalid: "review.reason.attachment_mapping_invalid",
};

/**
 * 原因码 → 一句中文说明。**fail-closed**：为 `null`（例如已处理记录）或不认识的原因码
 * 返回固定说明，不动态拼字段名、不把英文原因码透出到界面。
 */
export function reasonText(reasonCode: string | null | undefined): string {
  if (reasonCode === null || reasonCode === undefined || reasonCode === "") {
    return t("review.reason.unknown");
  }
  const key = (REASON_MESSAGE_KEYS as Record<string, MessageKey | undefined>)[reasonCode];
  return key === undefined ? t("review.reason.unknown") : t(key);
}
