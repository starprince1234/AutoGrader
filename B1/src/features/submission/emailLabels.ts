import { t, type MessageKey } from "../../i18n";
import type { EmailVerificationReasonCode } from "../../api/types";

/**
 * 核验码状态/原因码的短文案（面板、记录列表与工作区常驻状态共用）。
 *
 * 失败原因只给一句短的：状态查询、记录列表、工作区顶部看到的是同一句话，不会出现
 * “三处各说各的”。
 */
export const EMAIL_REASON_KEYS = {
  subject_mismatch: "submit.email.reason.subject_mismatch",
  attachment_missing: "submit.email.reason.attachment_missing",
  email_sender_mismatch: "submit.email.reason.email_sender_mismatch",
  email_verification_rejected: "submit.email.reason.email_verification_rejected",
  email_verification_expired: "submit.email.reason.email_verification_expired",
  email_body_missing_field: "submit.email.reason.email_body_missing_field",
  email_body_mismatch: "submit.email.reason.email_body_mismatch",
  attachment_mapping_invalid: "submit.email.reason.attachment_mapping_invalid",
  intake_unavailable: "submit.email.reason.intake_unavailable",
} as const satisfies Record<EmailVerificationReasonCode, MessageKey>;

export type EmailHintTone = "ok" | "bad" | "pending";

export function emailHintTone(state: string): EmailHintTone {
  if (state === "received") return "ok";
  if (state === "pending") return "pending";
  return "bad";
}

export function emailHintText(
  state: string,
  reasonCode: string | null | undefined,
): string {
  if (state === "received") return t("submit.email.hint.success");
  if (state === "pending") return t("submit.email.hint.pending");
  if (state === "expired") return t("submit.email.hint.expired");
  if (reasonCode !== null && reasonCode !== undefined && reasonCode in EMAIL_REASON_KEYS) {
    const key = EMAIL_REASON_KEYS[reasonCode as EmailVerificationReasonCode];
    return t("submit.email.hint.failure", { reason: t(key) });
  }
  return t("submit.email.hint.failureGeneric");
}
