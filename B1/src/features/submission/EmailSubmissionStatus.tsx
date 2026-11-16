import { t } from "../../i18n";
import { useEmailSubmissionRecords } from "./useSubmission";
import { emailHintText, emailHintTone } from "./emailLabels";

/**
 * 工作区顶部的邮件提交状态：最近一次申请核验码的结果，页面刷新/关掉弹窗后仍然可见。
 *
 * 记录来自服务端（一次申请 = 一条记录），因此不依赖浏览器本地存储；只要记录里有
 * `pending`，查询每 5 秒刷新一次。
 */
export function EmailSubmissionStatus({ assignmentId }: { assignmentId: string }) {
  const records = useEmailSubmissionRecords(assignmentId);
  const latest = records.data?.items[0] ?? null;
  if (latest === null) return null;
  const tone = emailHintTone(latest.state);
  return (
    <span
      className={`email-submit-hint email-submit-hint--${tone} workspace-email-status`}
      data-testid="workspace-email-status"
    >
      {t("submit.email.statusPrefix")}：{emailHintText(latest.state)}
    </span>
  );
}
