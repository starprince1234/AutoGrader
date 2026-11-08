import { t, type MessageKey } from "../../i18n";
import type {
  AssignmentNotificationDelivery,
  AssignmentNotificationSummary,
  AssignmentPublic,
} from "../../api/types";

/**
 * 作业通知的状态文案与色调（面板与逐人矩阵共用）。
 *
 * 两个来源必须分开命名：`scheduleStateLabel` 描述 B3 交给 B4 的**调度**状态，
 * `deliveryStatusLabel` / `summaryStatusLabel` 描述 B4 记录的**投递**状态。
 * 混用会把"事件已写入"显示成"已送达"。
 */

export type NotificationTone = "ok" | "warn" | "bad" | "idle";

const DELIVERY_LABEL: Record<AssignmentNotificationDelivery["status"], MessageKey> = {
  pending: "assignments.notification.pending",
  sending: "assignments.notification.sending",
  sent: "assignments.notification.sent",
  failed: "assignments.notification.failed",
  skipped_unbound: "assignments.notification.skipped_unbound",
  skipped_no_contact: "assignments.notification.skipped_no_contact",
  skipped_inactive: "assignments.notification.skipped_inactive",
};

const DELIVERY_TONE: Record<AssignmentNotificationDelivery["status"], NotificationTone> = {
  pending: "idle",
  sending: "warn",
  sent: "ok",
  failed: "bad",
  skipped_unbound: "warn",
  skipped_no_contact: "warn",
  skipped_inactive: "warn",
};

const SUMMARY_LABEL: Record<AssignmentNotificationSummary["status"], MessageKey> = {
  pending: "assignments.notification.status.pending",
  sending: "assignments.notification.status.sending",
  completed: "assignments.notification.status.completed",
  partial: "assignments.notification.status.partial",
  failed: "assignments.notification.status.failed",
};

const SUMMARY_TONE: Record<AssignmentNotificationSummary["status"], NotificationTone> = {
  pending: "idle",
  sending: "warn",
  completed: "ok",
  partial: "warn",
  failed: "bad",
};

const SCHEDULE_LABEL: Record<AssignmentPublic["notification_state"], MessageKey> = {
  pending: "assignments.notificationState.pending",
  scheduled: "assignments.notificationState.scheduled",
  unavailable: "assignments.notificationState.unavailable",
};

const REASON_LABEL: Record<
  NonNullable<AssignmentNotificationDelivery["reason_code"]>,
  MessageKey
> = {
  smtp_error: "assignments.notification.reason.smtp_error",
  mail_not_configured: "assignments.notification.reason.mail_not_configured",
  no_account: "assignments.notification.reason.no_account",
  no_email: "assignments.notification.reason.no_email",
  inactive_account: "assignments.notification.reason.inactive_account",
};

export function deliveryStatusLabel(
  status: AssignmentNotificationDelivery["status"],
): string {
  return t(DELIVERY_LABEL[status]);
}

export function deliveryStatusTone(
  status: AssignmentNotificationDelivery["status"],
): NotificationTone {
  return DELIVERY_TONE[status];
}

export function summaryStatusLabel(status: AssignmentNotificationSummary["status"]): string {
  return t(SUMMARY_LABEL[status]);
}

export function summaryStatusTone(status: AssignmentNotificationSummary["status"]): NotificationTone {
  return SUMMARY_TONE[status];
}

export function scheduleStateLabel(state: AssignmentPublic["notification_state"]): string {
  return t(SCHEDULE_LABEL[state]);
}

export function notificationReasonLabel(
  code: NonNullable<AssignmentNotificationDelivery["reason_code"]>,
): string {
  return t(REASON_LABEL[code]);
}
