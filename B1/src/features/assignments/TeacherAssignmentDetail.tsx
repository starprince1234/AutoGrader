import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type {
  AssignmentNotificationDelivery,
  AssignmentPublic,
  GradeRow,
} from "../../api/types";
import { useRosterSummary } from "../../api/directory";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import { formatDateTime, shortenId } from "../../ui/format";
import {
  Banner,
  EmptyState,
  ErrorState,
  InlineLoader,
  Modal,
  Panel,
  StatusPill,
  Table,
} from "../../ui/components";
import { readExecutionMode } from "../authoring/execution";
import {
  questionStateLabel,
  questionStateTone,
  overallStateLabel,
  overallStateTone,
} from "./progressDisplay";
import { useAssignmentProgress } from "./useAssignmentProgress";
import { useQuestionVersionRows } from "../questions/useQuestions";
import { answerFileName } from "../submission/validation";
// 答案预览与批改界面共用同一份实现（带鉴权读取、文本转义、字符上限）。
import { AnswerViewer } from "./AnswerViewer";
import {
  useAssignmentNotifications,
} from "./useAssignmentNotifications";
import { AssignmentNotificationPanel } from "./AssignmentNotificationPanel";
import {
  deliveryStatusLabel,
  deliveryStatusTone,
} from "./notificationDisplay";

/**
 * 教师作业详情（CHG-0032 §7.2 / v0.32.0 课程级受众）。
 *
 * 概览 + 服务端受众统计 + 题目表 + 逐人逐题矩阵 + 作业通知面板。
 *
 * 判定纪律（唯一不能出错的地方）：
 * - **名单/成绩未取全时不得显示"未交"**：`counts` 只有在 `complete` 时才非 null，
 *   不完整时整块换成加载/截断说明；
 * - 顶部"这门课多少人/已绑定/未绑定"来自服务端 `roster-summary`，不由已加载页加总；
 * - 矩阵包含**未绑定账号**的名单行并注明原因（他们不参与"未交"判定）；
 * - 历史提交（`assignment_id === null`）不属于任何作业，不出现在矩阵里；
 * - 通知列与通知面板只读 B4 的投递记录：`sent` 才是已送达，`skipped_*` 不是。
 * - 答案文件带 `Authorization` 读取（不能裸 `<a href>`），以 `<pre>` 文本转义展示，
 *   有字符上限，内容不写日志。
 */

/** 名单/成绩读取失败：403 是权限结论，必须说清楚而不是一个通用错误框。 */
function progressErrorContent(error: unknown, testId: string) {
  if (isApiError(error) && error.status === 403) {
    return (
      <Banner tone="error" testId={`${testId}-forbidden`}>
        {t("assignments.progressForbidden")}
      </Banner>
    );
  }
  return <ErrorState error={error} testId={testId} />;
}

function ScoreCell({ grade }: { grade: GradeRow | null }) {
  if (grade === null) return <span className="muted">{t("common.none")}</span>;
  if (grade.state !== "completed") {
    return (
      <span className="muted" data-testid="teacher-unscored">
        {t("grades.unfinished")}
      </span>
    );
  }
  if (typeof grade.score !== "number") {
    return <span className="muted">{t("grades.notScored")}</span>;
  }
  return (
    <span>
      {grade.score} / {grade.max_score ?? "—"}
    </span>
  );
}

export function TeacherAssignmentDetail({ assignment }: { assignment: AssignmentPublic }) {
  const rows = useQuestionVersionRows(assignment.question_version_ids);
  const progress = useAssignmentProgress(assignment);
  const summary = useRosterSummary(assignment.course_id);
  const notifications = useAssignmentNotifications(
    assignment.course_id,
    assignment.assignment_id,
  );
  const [expandedEnrollmentId, setExpandedEnrollmentId] = useState<string | null>(null);
  const [answerTarget, setAnswerTarget] = useState<{
    submissionId: string;
    path: string;
    title: string;
  } | null>(null);

  const sectionCodeOf = (sectionId: string) =>
    summary.data?.sections.find((section) => section.section_id === sectionId)?.section_code ??
    shortenId(sectionId);
  const classNameOf = (classId: string) => {
    for (const section of summary.data?.sections ?? []) {
      const match = section.classes.find((row) => row.class_id === classId);
      if (match) return match.class_name;
    }
    return shortenId(classId);
  };

  const titleByVersion = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of rows) {
      map.set(
        row.questionVersionId,
        row.question?.title ?? t("questions.titleUnavailable"),
      );
    }
    return map;
  }, [rows]);

  const deliveryByStudentNumber = useMemo(() => {
    const map = new Map<string, AssignmentNotificationDelivery["status"]>();
    for (const delivery of notifications.deliveries) {
      map.set(delivery.student_number, delivery.status);
    }
    return map;
  }, [notifications.deliveries]);

  /** 通知列：只显示 B4 记录的投递状态；没有记录时明确区分"未安排"与"发布后加入"。 */
  const notificationCell = (studentNumber: string, testId: string) => {
    if (notifications.isPending) {
      return (
        <span className="muted" data-testid={testId}>
          <InlineLoader />
        </span>
      );
    }
    if (notifications.notScheduled) {
      return (
        <span className="muted" data-testid={testId}>
          {t("assignments.notification.notArranged")}
        </span>
      );
    }
    if (notifications.isError) {
      return (
        <span className="muted" data-testid={testId}>
          {t("assignments.notification.readFailed")}
        </span>
      );
    }
    const status = deliveryByStudentNumber.get(studentNumber);
    if (status === undefined) {
      // 投递记录可能还没取全：此时"没有这个人的记录"还不能下结论（不能写成"发布后加入"）。
      if (notifications.truncated) {
        return (
          <span className="muted" data-testid={testId}>
            {t("assignments.notification.incomplete")}
          </span>
        );
      }
      if (!notifications.complete) {
        return (
          <span className="muted" data-testid={testId}>
            <InlineLoader />
          </span>
        );
      }
      return (
        <span className="muted" data-testid={testId}>
          {t("assignments.notification.notInSnapshot")}
        </span>
      );
    }
    return (
      <StatusPill tone={deliveryStatusTone(status)} testId={testId}>
        {deliveryStatusLabel(status)}
      </StatusPill>
    );
  };

  const expanded =
    progress.rows.find((row) => row.enrollmentId === expandedEnrollmentId) ?? null;

  return (
    <div className="stack">
      <Panel title={t("assignments.detailOverview")} testId="teacher-overview">
        <div className="stack">
          <h2 className="panel__title" data-testid="assignment-title">
            {assignment.title}
          </h2>
          <div className="inline">
            <StatusPill tone={assignment.status === "open" ? "ok" : "idle"}>
              {t(`assignments.status.${assignment.status}`)}
            </StatusPill>
            <span className="muted" data-testid="assignment-due">
              {t("assignments.due")}：
              {assignment.due_at
                ? formatDateTime(assignment.due_at, t("common.none"))
                : t("assignments.noDue")}
            </span>
            <span className="muted">
              {t("assignments.versionCount")}：{rows.length}
            </span>
          </div>
          <p className="muted" data-testid="assignment-audience-summary">
            {t("assignments.courseWideAudience")}
          </p>
          <span className="muted" data-testid="assignment-notification-state">
            {t("assignments.notificationState")}：
            {t(`assignments.notificationState.${assignment.notification_state}`)}
          </span>
        </div>
      </Panel>

      <Panel title={t("assignments.summaryTitle")} testId="assignment-summary">
        <p className="muted">{t("assignments.summaryNotice")}</p>

        <div className="inline" data-testid="assignment-audience-counts">
          {summary.isPending ? (
            <p className="muted" data-testid="assignment-roster-summary-loading">
              <InlineLoader />
            </p>
          ) : summary.isError ? (
            <Banner tone="warn" testId="assignment-roster-summary-error">
              {t("assignments.audienceSummaryIncomplete")}
            </Banner>
          ) : summary.data ? (
            <>
              <span className="muted" data-testid="roster-section-count">
                {t("assignments.audienceSectionCount")}：{summary.data.section_count}
              </span>
              <span className="muted" data-testid="roster-class-count">
                {t("assignments.audienceClassCount")}：{summary.data.class_count}
              </span>
              <span className="muted" data-testid="roster-student-total">
                {t("assignments.audienceStudentTotal")}：{summary.data.student_total}
              </span>
              <span className="muted" data-testid="roster-bound-count">
                {t("assignments.audienceBound")}：{summary.data.bound_count}
              </span>
              <span className="muted" data-testid="roster-unbound-count">
                {t("assignments.audienceUnbound")}：{summary.data.unbound_count}
              </span>
            </>
          ) : null}
        </div>

        {progress.truncated ? (
          <Banner tone="warn" testId="assignment-summary-truncated">
            {t("common.paginationTruncated", { pages: MAX_AUTO_PAGES })}{" "}
            {t("assignments.summaryIncomplete")}
          </Banner>
        ) : progress.isError ? (
          progressErrorContent(progress.error, "assignment-summary-error")
        ) : progress.counts === null ? (
          <p className="muted" data-testid="assignment-summary-loading">
            <InlineLoader /> {t("assignments.summaryIncomplete")}
          </p>
        ) : (
          <div className="inline" data-testid="assignment-counts">
            <span className="muted" data-testid="count-all">
              {t("assignments.count.all")}：{progress.counts.all}
            </span>
            <span className="muted" data-testid="count-not-submitted">
              {t("assignments.count.notSubmitted")}：{progress.counts.notSubmitted}
            </span>
            <span className="muted" data-testid="count-partial">
              {t("assignments.count.partial")}：{progress.counts.partial}
            </span>
            <span className="muted" data-testid="count-submitted">
              {t("assignments.count.submitted")}：{progress.counts.submitted}
            </span>
            <span className="muted" data-testid="count-evaluating">
              {t("assignments.count.evaluating")}：{progress.counts.evaluating}
            </span>
            <span className="muted" data-testid="count-failed">
              {t("assignments.count.failed")}：{progress.counts.failed}
            </span>
            {progress.counts.unbound > 0 ? (
              <span className="muted" data-testid="count-unbound">
                {t("grades.missing.statusUnbound")}：{progress.counts.unbound}
              </span>
            ) : null}
          </div>
        )}
      </Panel>

      <Panel
        title={t("assignments.versions", { count: rows.length })}
        testId="assignment-versions-panel"
      >
        {rows.length === 0 ? (
          <EmptyState>{t("assignments.noVersions")}</EmptyState>
        ) : (
          <Table
            testId="assignment-questions-table"
            label={t("assignments.versions", { count: rows.length })}
            rows={rows}
            getRowKey={(row) => row.questionVersionId}
            columns={[
              {
                key: "ordinal",
                header: t("assignments.col.ordinal"),
                render: (row) => row.ordinal,
              },
              {
                key: "title",
                header: t("assignments.col.question"),
                render: (row) => {
                  if (row.error) {
                    return <ErrorState error={row.error} testId={`question-row-error-${row.ordinal}`} />;
                  }
                  return row.question?.title ?? (row.isPending ? "—" : t("questions.titleUnavailable"));
                },
              },
              {
                key: "language",
                header: t("questions.language"),
                render: (row) =>
                  row.version ? t(`questions.language.${row.version.language}`) : "—",
              },
              {
                key: "execution",
                header: t("questions.executionMode"),
                render: (row) =>
                  row.version
                    ? t(`questions.executionMode.${readExecutionMode(row.version)}`)
                    : "—",
              },
            ]}
          />
        )}
      </Panel>

      <Panel title={t("assignments.matrixTitle")} testId="assignment-matrix">
        <p className="muted">{t("assignments.matrixNotice")}</p>
        {progress.truncated ? (
          <Banner tone="warn" testId="assignment-matrix-truncated">
            {t("common.paginationTruncated", { pages: MAX_AUTO_PAGES })}{" "}
            {t("assignments.matrixIncomplete")}
          </Banner>
        ) : progress.isError ? (
          progressErrorContent(progress.error, "assignment-matrix-error")
        ) : progress.counts === null ? (
          <p className="muted" data-testid="assignment-matrix-loading">
            <InlineLoader /> {t("assignments.matrixIncomplete")}
          </p>
        ) : progress.rows.length === 0 ? (
          <EmptyState>{t("assignments.emptyAudience")}</EmptyState>
        ) : (
          <>
            <Table
              testId="assignment-matrix-table"
              label={t("assignments.matrixTitle")}
              rows={progress.rows}
              getRowKey={(row) => row.enrollmentId}
              columns={[
                {
                  key: "student_number",
                  header: t("roster.studentNumber"),
                  render: (row) => row.studentNumber,
                },
                {
                  key: "name",
                  header: t("roster.displayName"),
                  render: (row) => row.displayName,
                },
                {
                  key: "section",
                  header: t("assignments.col.section"),
                  render: (row) => sectionCodeOf(row.sectionId),
                },
                {
                  key: "class",
                  header: t("assignments.col.class"),
                  render: (row) => row.className || classNameOf(row.classId),
                },
                {
                  key: "notification",
                  header: t("assignments.col.notification"),
                  render: (row) =>
                    notificationCell(
                      row.studentNumber,
                      `student-notification-${row.enrollmentId}`,
                    ),
                },
                {
                  key: "status",
                  header: t("assignments.matrixStatus"),
                  render: (row) => (
                    <StatusPill
                      tone={overallStateTone(row.status)}
                      testId={`student-status-${row.enrollmentId}`}
                    >
                      {overallStateLabel(row.status)}
                    </StatusPill>
                  ),
                },
                {
                  key: "completed",
                  header: t("assignments.matrixCompleted"),
                  align: "right",
                  render: (row) =>
                    row.studentId === null
                      ? t("common.none")
                      : `${row.completedCount} / ${row.total}`,
                },
                {
                  key: "submitted_at",
                  header: t("assignments.matrixLastSubmission"),
                  render: (row) =>
                    row.lastSubmittedAt
                      ? formatDateTime(row.lastSubmittedAt, t("common.none"))
                      : t("common.none"),
                },
                {
                  key: "expand",
                  header: t("assignments.col.detail"),
                  render: (row) => (
                    <button
                      type="button"
                      className="button button--ghost"
                      data-testid={`expand-student-${row.enrollmentId}`}
                      aria-expanded={row.enrollmentId === expandedEnrollmentId}
                      onClick={() =>
                        setExpandedEnrollmentId((previous) =>
                          previous === row.enrollmentId ? null : row.enrollmentId,
                        )
                      }
                    >
                      {row.enrollmentId === expandedEnrollmentId
                        ? t("assignments.collapseStudent")
                        : t("assignments.expandStudent")}
                    </button>
                  ),
                },
              ]}
            />
            {expanded ? (
              <Panel
                title={t("assignments.studentQuestionsTitle", {
                  name: expanded.displayName,
                })}
                testId="student-question-panel"
              >
                {expanded.studentId === null ? (
                  <Banner tone="info" testId="student-unbound">
                    {t("assignments.studentUnbound")}
                  </Banner>
                ) : (
                  <div className="table__wrap">
                    <table
                      className="table"
                      aria-label={t("assignments.studentQuestionsTitle", {
                        name: expanded.displayName,
                      })}
                      data-testid="student-question-table"
                    >
                      <thead>
                        <tr>
                          <th scope="col">{t("assignments.col.ordinal")}</th>
                          <th scope="col">{t("assignments.col.question")}</th>
                          <th scope="col">{t("assignments.matrixStatus")}</th>
                          <th scope="col">{t("grades.score")}</th>
                          <th scope="col">{t("grades.submittedAt")}</th>
                          <th scope="col">{t("assignments.col.evaluation")}</th>
                          <th scope="col">{t("assignments.col.answer")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {expanded.questions.map((question) => {
                          const submission = question.submission;
                          const title =
                            titleByVersion.get(question.questionVersionId) ??
                            t("questions.titleUnavailable");
                          return (
                            <tr
                              key={question.questionVersionId}
                              data-testid={`student-question-row-${question.ordinal}`}
                            >
                              <td>{question.ordinal}</td>
                              <td>{title}</td>
                              <td>
                                <StatusPill tone={questionStateTone(question.state)}>
                                  {questionStateLabel(question.state)}
                                </StatusPill>
                              </td>
                              <td className="is-right">
                                <ScoreCell grade={submission} />
                              </td>
                              <td>
                                {submission?.submitted_at
                                  ? formatDateTime(submission.submitted_at, t("common.none"))
                                  : t("common.none")}
                              </td>
                              <td>
                                {submission?.evaluation_run_id ? (
                                  <Link
                                    to="/grades"
                                    search={{ evaluation_run_id: submission.evaluation_run_id }}
                                    data-testid={`student-run-link-${question.ordinal}`}
                                  >
                                    {t("assignments.openRun")}
                                  </Link>
                                ) : (
                                  <span className="muted">{t("common.none")}</span>
                                )}
                              </td>
                              <td>
                                {submission ? (
                                  <button
                                    type="button"
                                    className="button button--ghost"
                                    data-testid={`view-answer-${question.ordinal}`}
                                    onClick={() =>
                                      setAnswerTarget({
                                        submissionId: submission.submission_id,
                                        path: answerFileName(question.ordinal),
                                        title,
                                      })
                                    }
                                  >
                                    {t("assignments.viewAnswer")}
                                  </button>
                                ) : (
                                  <span className="muted">{t("common.none")}</span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>
            ) : null}
          </>
        )}
      </Panel>

      <AssignmentNotificationPanel
        courseId={assignment.course_id}
        assignmentId={assignment.assignment_id}
        notificationState={assignment.notification_state}
        notifications={notifications}
        rosterSummary={summary.data ?? null}
      />

      {answerTarget !== null ? (
        <Modal
          title={t("assignments.answerTitle", { title: answerTarget.title })}
          testId="answer-modal"
          onClose={() => setAnswerTarget(null)}
        >
          <AnswerViewer submissionId={answerTarget.submissionId} path={answerTarget.path} />
        </Modal>
      ) : null}
    </div>
  );
}
