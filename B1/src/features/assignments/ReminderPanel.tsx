import { useMemo, useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { AssignmentNotificationSummary } from "../../api/types";
import { loadedCourses, useCourses } from "../../api/directory";
import { Banner, Field, Modal } from "../../ui/components";
import { formatDateTime } from "../../ui/format";
import { useRemindAssignmentStudents } from "./useAssignmentNotifications";
import type { StudentProgress } from "./useAssignmentProgress";

/**
 * 「未完成」右侧「一键提醒」弹出的浮窗（PDF：邮件提醒 → 自动包括提示语 / 一键提醒）。
 *
 * 浮窗内容与原来的内嵌方框一致；区别只是它按需出现，不再长期占着题干下方那一列。
 *
 * 三条口径不能含糊：
 * 1. **提示语由服务端生成**。这里只做只读预览，措辞与 B4 的模板一致；教师最多追加一句补充语，
 *    不能删掉"你还没有提交"这句话——否则"提醒"就丢了它唯一要表达的事实。
 * 2. **收件人只来自「未完成」里已绑定账号的学生**。未绑定账号的名单行没有可投递的账号，
 *    服务端也会把它们记为 `skipped_unbound`，因此这里不把它们放进收件人，界面另行说明。
 * 3. **"已安排"不是"已送达"**。返回的 `pending_count` 只说明排入了投递队列；`sent_count` 才是
 *    SMTP 已接受，界面分开显示，绝不把两者合并成一句"已提醒 N 人"。
 */

const NOTE_LIMIT = 500;

export function ReminderPanel({
  courseId,
  assignmentId,
  title,
  dueAt,
  pending,
  onClose,
}: {
  courseId: string;
  assignmentId: string;
  title: string;
  dueAt: string | null;
  /** 关闭浮窗（Esc、遮罩点击、取消按钮都走这里）。 */
  onClose: () => void;
  /**
   * 「未完成」名单 = 收件人。**不再按是否绑定账号过滤**：没绑定账号的学生同样会出现在
   * 名单里并被提交为收件人，服务端把他们记为 `skipped_unbound/no_account` 并在结果里说明，
   * 而不是在页面上悄悄少一个人。
   */
  pending: readonly StudentProgress[];
}) {
  const courses = useCourses();
  const courseOptions = useMemo(() => loadedCourses(courses.data?.pages), [courses.data]);
  const courseTitle =
    courseOptions.find((course) => course.course_id === courseId)?.title ??
    t("grading.courseUnknown");

  const recipients = useMemo(() => [...pending], [pending]);
  const [note, setNote] = useState("");
  const [result, setResult] = useState<AssignmentNotificationSummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  const remind = useRemindAssignmentStudents(courseId, assignmentId);

  const dueText = dueAt
    ? formatDateTime(dueAt, t("assignments.noDue"))
    : t("assignments.noDue");
  const prompt = t("grading.remindPrompt", {
    course: courseTitle,
    title,
    due: dueText,
  });

  const send = () => {
    setError(null);
    setResult(null);
    remind.mutate(
      {
        title,
        dueAt,
        enrollmentIds: recipients.map((row) => row.enrollmentId),
        note,
      },
      {
        onSuccess: (summary) => setResult(summary),
        onError: (failure) => setError(failure),
      },
    );
  };

  const canSend = recipients.length > 0 && !remind.isPending && note.length <= NOTE_LIMIT;

  return (
    <Modal
      title={t("grading.remindTitle")}
      onClose={onClose}
      testId="grading-reminder"
      initialFocus="[data-testid='grading-remind-note']"
      footer={
        <div className="inline">
          <button
            type="button"
            className="button"
            data-testid="grading-remind-send"
            disabled={!canSend}
            onClick={send}
          >
            {remind.isPending ? t("grading.remindSending") : t("grading.remindSend")}
          </button>
          {recipients.length === 0 ? (
            <span className="muted" data-testid="grading-remind-empty">
              {t("grading.remindNoRecipient")}
            </span>
          ) : null}
          <button
            type="button"
            className="button button--ghost"
            data-testid="grading-remind-close"
            onClick={onClose}
          >
            {t("common.cancel")}
          </button>
        </div>
      }
    >
      <div className="stack">
        <p className="muted">{t("grading.remindIntro")}</p>
        <div className="stack" data-testid="grading-remind-prompt">
          <span className="muted">{t("grading.remindPromptLabel")}</span>
          {/* 用 pre-wrap 而不是 <pre>：提示语是一段长句，`<pre>` 不换行会把面板撑破。 */}
          <p className="grading-remind__prompt">{prompt}</p>
        </div>

        <Field label={t("grading.remindRecipients")} htmlFor="grading-remind-recipients">
          <p data-testid="grading-remind-recipients">
            {t("grading.remindRecipientCount", { count: recipients.length })}
          </p>
        </Field>

        <Field
          label={t("grading.remindNote")}
          htmlFor="grading-remind-note"
          hint={t("grading.remindNoteHint")}
        >
          <textarea
            id="grading-remind-note"
            className="input"
            rows={2}
            maxLength={NOTE_LIMIT}
            value={note}
            data-testid="grading-remind-note"
            onChange={(event) => setNote(event.target.value)}
            disabled={remind.isPending}
          />
        </Field>

        {error !== null ? (
          <Banner tone="error" testId="grading-remind-error">
            {isApiError(error) && error.status === 422
              ? t("grading.remindStaleRoster")
              : isApiError(error)
                ? error.message
                : t("common.requestFailed")}
          </Banner>
        ) : null}

        {result !== null ? (
          <Banner tone="info" testId="grading-remind-result">
            <div className="stack">
              <span data-testid="grading-remind-scheduled">
                {t("grading.remindScheduled", { count: result.recipient_total })}
              </span>
              <span className="muted" data-testid="grading-remind-counts">
                {t("grading.remindDelivered", {
                  sent: result.sent_count,
                  pending: result.pending_count,
                })}
              </span>
              {/* 未成功的人必须点名原因：未绑定账号 / 没有邮箱 / 账号停用 / 发送失败。 */}
              <ul className="stack" data-testid="grading-remind-reasons">
                {result.skipped_unbound_count > 0 ? (
                  <li data-testid="grading-remind-skipped-unbound">
                    {t("grading.remindSkippedUnbound", {
                      count: result.skipped_unbound_count,
                    })}
                  </li>
                ) : null}
                {result.skipped_no_contact_count > 0 ? (
                  <li data-testid="grading-remind-skipped-no-contact">
                    {t("grading.remindSkippedNoContact", {
                      count: result.skipped_no_contact_count,
                    })}
                  </li>
                ) : null}
                {result.skipped_inactive_count > 0 ? (
                  <li data-testid="grading-remind-skipped-inactive">
                    {t("grading.remindSkippedInactive", {
                      count: result.skipped_inactive_count,
                    })}
                  </li>
                ) : null}
                {result.failed_count > 0 ? (
                  <li data-testid="grading-remind-failed">
                    {t("grading.remindFailed", { count: result.failed_count })}
                  </li>
                ) : null}
              </ul>
            </div>
          </Banner>
        ) : null}

      </div>
    </Modal>
  );
}
