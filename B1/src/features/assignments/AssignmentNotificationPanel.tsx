import { t } from "../../i18n";
import type {
  AssignmentNotificationDelivery,
  AssignmentPublic,
  RosterSummary,
} from "../../api/types";
import { shortenId } from "../../ui/format";
import {
  Banner,
  EmptyState,
  ErrorState,
  InlineLoader,
  Panel,
  StatusPill,
  Table,
} from "../../ui/components";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import {
  deliveryStatusLabel,
  deliveryStatusTone,
  notificationReasonLabel,
  scheduleStateLabel,
  summaryStatusLabel,
  summaryStatusTone,
} from "./notificationDisplay";
import {
  useScheduleAssignmentNotifications,
  type AssignmentNotificationsState,
} from "./useAssignmentNotifications";

/**
 * 作业通知面板（v0.32.0）。
 *
 * 只展示**通知服务（B4）记录的投递事实**：
 * - `sent_count` / `status=sent` 才表示 SMTP 已接受；
 * - `skipped_*` 是"没有可用通知渠道"（未注册 / 无邮箱 / 账号停用），永远不显示成已送达；
 * - 没有通知记录（404）显示"通知未安排"，并允许教师重新调度。
 *
 * 调度入口调 B3 的幂等端点：成功只代表"通知事件已写入通知服务"，所以成功提示里不会出现
 * "已送达"，投递结果必须继续从本面板的 summary / 逐人状态读取。
 */

function CountCell({
  label,
  value,
  testId,
}: {
  label: string;
  value: number;
  testId: string;
}) {
  return (
    <span className="muted" data-testid={testId}>
      {label}：{value}
    </span>
  );
}

export function AssignmentNotificationPanel({
  courseId,
  assignmentId,
  notificationState,
  notifications,
  rosterSummary,
}: {
  courseId: string;
  assignmentId: string;
  notificationState: AssignmentPublic["notification_state"];
  notifications: AssignmentNotificationsState;
  /** 用于把 delivery 的 section_id/class_id 显示成课序 code / 班级名（可为 null）。 */
  rosterSummary: RosterSummary | null;
}) {
  const schedule = useScheduleAssignmentNotifications(courseId, assignmentId);
  const sectionCodeOf = (sectionId: string) => {
    const section = rosterSummary?.sections.find((row) => row.section_id === sectionId);
    return section?.section_code ?? shortenId(sectionId);
  };
  const classNameOf = (classId: string) => {
    for (const section of rosterSummary?.sections ?? []) {
      const match = section.classes.find((row) => row.class_id === classId);
      if (match) return match.class_name;
    }
    return shortenId(classId);
  };

  const summary = notifications.summary;
  // 重试入口只在"B4 没有这条通知记录"时出现：此时调 B3 的幂等调度端点把受众交给 B4。
  // 已有记录时不再给"重试失败投递"按钮——那需要 B4 的 retry 语义（重置 failed），
  // 而 B3 的调度端点是幂等的，重复调用不会重发，按钮会变成一个没有效果的动作。
  const retryButton = (
    <button
      type="button"
      className="button"
      data-testid="notification-schedule"
      disabled={schedule.isPending || notifications.isPending}
      onClick={() => schedule.mutate()}
    >
      {schedule.isPending ? t("assignments.notification.retrying") : t("assignments.notification.retry")}
    </button>
  );

  return (
    <Panel title={t("assignments.notification.title")} testId="assignment-notifications">
      <div className="stack">
        <p className="muted" data-testid="notification-discipline">
          {t("assignments.notification.intro")}
        </p>
        <p className="muted" data-testid="notification-schedule-state">
          {t("assignments.notificationState")}：{scheduleStateLabel(notificationState)}
          {" — "}
          {t("assignments.notificationStateHint")}
        </p>

        {notifications.truncated ? (
          <Banner tone="warn" testId="notification-deliveries-truncated">
            {t("common.paginationTruncated", { pages: MAX_AUTO_PAGES })}{" "}
            {t("assignments.notification.truncated")}
          </Banner>
        ) : null}

        {notifications.isPending ? (
          <p className="muted" data-testid="notification-loading">
            <InlineLoader />
          </p>
        ) : notifications.notScheduled ? (
          <div className="stack" data-testid="notification-not-scheduled">
            <Banner tone="warn">{t("assignments.notification.notScheduled")}</Banner>
            <p className="muted">
              {t("assignments.notification.notScheduledHint", {
                state: scheduleStateLabel(notificationState),
              })}
            </p>
            {retryButton}
          </div>
        ) : notifications.isError ? (
          <ErrorState error={notifications.error} testId="notification-error" />
        ) : summary !== null ? (
          <>
            <div className="inline" data-testid="notification-summary">
              <StatusPill
                tone={summaryStatusTone(summary.status)}
                testId="notification-status"
              >
                {summaryStatusLabel(summary.status)}
              </StatusPill>
              <CountCell
                label={t("assignments.notification.recipientTotal")}
                value={summary.recipient_total}
                testId="notification-recipient-total"
              />
              <CountCell
                label={t("assignments.notification.sentCount")}
                value={summary.sent_count}
                testId="notification-sent"
              />
              <CountCell
                label={t("assignments.notification.pendingCount")}
                value={summary.pending_count}
                testId="notification-pending"
              />
              <CountCell
                label={t("assignments.notification.failedCount")}
                value={summary.failed_count}
                testId="notification-failed"
              />
              <CountCell
                label={t("assignments.notification.skippedUnbound")}
                value={summary.skipped_unbound_count}
                testId="notification-skipped-unbound"
              />
              <CountCell
                label={t("assignments.notification.skippedNoContact")}
                value={summary.skipped_no_contact_count}
                testId="notification-skipped-no-contact"
              />
              <CountCell
                label={t("assignments.notification.skippedInactive")}
                value={summary.skipped_inactive_count}
                testId="notification-skipped-inactive"
              />
            </div>

            {notifications.complete && notifications.deliveries.length === 0 ? (
              <EmptyState>{t("assignments.notification.empty")}</EmptyState>
            ) : (
              <Table<AssignmentNotificationDelivery>
                testId="notification-delivery-table"
                label={t("assignments.notification.tableLabel")}
                rows={notifications.deliveries}
                getRowKey={(row) => row.delivery_id}
                columns={[
                  {
                    key: "student_number",
                    header: t("roster.studentNumber"),
                    render: (row) => row.student_number,
                  },
                  {
                    key: "display_name",
                    header: t("roster.displayName"),
                    render: (row) => row.display_name,
                  },
                  {
                    key: "section",
                    header: t("assignments.col.section"),
                    render: (row) => sectionCodeOf(row.section_id),
                  },
                  {
                    key: "class",
                    header: t("assignments.col.class"),
                    render: (row) => classNameOf(row.class_id),
                  },
                  {
                    key: "status",
                    header: t("assignments.matrixStatus"),
                    render: (row) => (
                      <StatusPill
                        tone={deliveryStatusTone(row.status)}
                        testId={`delivery-status-${row.delivery_id}`}
                      >
                        {deliveryStatusLabel(row.status)}
                      </StatusPill>
                    ),
                  },
                  {
                    key: "reason",
                    header: t("assignments.notification.reason"),
                    render: (row) =>
                      row.reason_code
                        ? notificationReasonLabel(row.reason_code)
                        : (row.reason_detail ?? t("common.none")),
                  },
                  {
                    key: "attempts",
                    header: t("assignments.notification.attempts"),
                    align: "right",
                    render: (row) => row.attempts,
                  },
                ]}
              />
            )}
          </>
        ) : (
          <EmptyState>{t("assignments.notification.empty")}</EmptyState>
        )}

        {schedule.isError ? (
          <Banner tone="error" testId="notification-schedule-error">
            {t("assignments.notification.retryError")}
          </Banner>
        ) : null}
        {schedule.isSuccess ? (
          <Banner
            tone={schedule.data.notification_state === "unavailable" ? "warn" : "info"}
            testId="notification-schedule-result"
          >
            {schedule.data.notification_state === "unavailable"
              ? t("assignments.notification.retryUnavailable")
              : t("assignments.notification.retryScheduled", {
                  state: scheduleStateLabel(schedule.data.notification_state),
                })}
          </Banner>
        ) : null}
      </div>
    </Panel>
  );
}
