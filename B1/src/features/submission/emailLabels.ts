import { t } from "../../i18n";

/**
 * 核验码状态的一句话（面板、记录列表与工作区常驻状态共用）。
 *
 * 学生端只给「提交成功 / 提交失败」这一个结论：被拒的原因码对学生的下一步没有动作可做，
 * 而提交格式已经逐项写在面板里（正文五行 + `N.txt` 附件）。把内部核验细节摊在页面上只会
 * 让学生去猜哪些可以绕过。唯一例外是 `pending`：核验码还没收到邮件时仍写「等待收件」，
 * 否则刚申请就会被写成失败。
 */
export type EmailHintTone = "ok" | "bad" | "pending";

export function emailHintTone(state: string): EmailHintTone {
  if (state === "received") return "ok";
  if (state === "pending") return "pending";
  return "bad";
}

export function emailHintText(state: string): string {
  if (state === "received") return t("submit.email.hint.success");
  if (state === "pending") return t("submit.email.hint.pending");
  return t("submit.email.hint.failure");
}
