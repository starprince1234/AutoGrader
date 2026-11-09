import "./gradingLayout.css";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import { useSession } from "../../auth/sessionContext";
import { canViewCohortGrades } from "../../auth/visibility";
import { formatDateTime } from "../../ui/format";
import {
  Banner,
  EmptyState,
  ErrorState,
  InlineLoader,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
} from "../../ui/components";
import { SafeMarkdown } from "../questions/SafeMarkdown";
import { useQuestionVersionRows } from "../questions/useQuestions";
import { GradeDrilldown } from "../grades/GradeDrilldown";
import { ManualGradeDrawer } from "../grades/ManualGradeDrawer";
import { answerFileName } from "../submission/validation";
import { AnswerViewer } from "./AnswerViewer";
import {
  overallStateLabel,
  overallStateTone,
  questionStateLabel,
  questionStateTone,
} from "./progressDisplay";
import { ReminderPanel } from "./ReminderPanel";
import { useAssignment } from "./useAssignments";
import { useAssignmentProgress, type StudentProgress } from "./useAssignmentProgress";
import {
  assignmentScorePercent,
  judgedTotal,
  splitByCompletion,
  type CompletionGroups,
} from "./useGrading";

/**
 * 批改界面 = PDF 的「作业情况（子界面）」：
 *
 *   ┌ 左栏 ─────────────┬ 右栏（大窗口）──────────────────────────┐
 *   │ 作业题目内容       │ 默认：作业详情（题干）                   │
 *   │ 已完成（x/y）      │ 点学生后：左上角「返回」+ 作业详情 +      │
 *   │   └ 展开学生       │ 作答情况 + 评测结果与 ai 评价 + 老师改分 │
 *   │ 未完成（x/y）      │                                          │
 *   │   └ 提交情况       │                                          │
 *   └───────────────────┴──────────────────────────────────────────┘
 *
 * 纪律（与作业详情同一口径，不在此处重新发明判定）：
 * - 名单或成绩**未取全 / 被截断**时不给任何「已交 / 未交」数字，只显示加载或截断；
 * - 未绑定账号的名单行单列，既不算已交也不算未交；
 * - 未出分的题不猜分，`failed/rejected` 显示状态而不是 0 分；
 * - 权限结论（403）与不存在（404）分开渲染。
 */

function assignmentErrorContent(error: unknown) {
  if (isApiError(error)) {
    if (error.status === 403) {
      return <EmptyState>{t("assignments.detailForbidden")}</EmptyState>;
    }
    if (error.status === 404) {
      return <EmptyState>{t("assignments.detailMissing")}</EmptyState>;
    }
  }
  return <ErrorState error={error} testId="grading-assignment-error" />;
}

function GroupCount({ groups, complete }: { groups: CompletionGroups; complete: boolean }) {
  if (!complete) return <span className="muted">{t("grading.countPending")}</span>;
  return (
    <span className="grading-score" data-testid="grading-group-counts">
      {groups.completed.length} / {judgedTotal(groups)}
    </span>
  );
}

function StudentRow({
  selected,
  name,
  meta,
  tone,
  label,
  onSelect,
  testId,
}: {
  selected: boolean;
  name: string;
  meta: string;
  tone: "ok" | "warn" | "bad" | "idle";
  label: string;
  onSelect: () => void;
  testId: string;
}) {
  return (
    <li>
      <button
        type="button"
        aria-current={selected}
        data-testid={testId}
        onClick={onSelect}
      >
        <span>{name}</span>
        <span className="grading-student-list__meta muted">{meta}</span>
        <StatusPill tone={tone}>{label}</StatusPill>
      </button>
    </li>
  );
}

export function GradingPage({
  assignmentId,
  initialVersionId = null,
  initialStudentId = null,
}: {
  assignmentId: string;
  /** 深链预选题目（属于本作业时才生效）。 */
  initialVersionId?: string | null;
  /** 深链预选学生（`student_id`，未绑定账号的学生无法预选）。 */
  initialStudentId?: string | null;
}) {
  const assignmentQuery = useAssignment(assignmentId);
  const assignment = assignmentQuery.data ?? null;
  const session = useSession();
  const teacherView = canViewCohortGrades(session);
  const navigate = useNavigate();
  const [versionId, setVersionId] = useState<string | null>(initialVersionId);
  const [studentId, setStudentId] = useState<string | null>(initialStudentId);
  const [openGroup, setOpenGroup] = useState<"completed" | "pending" | null>("pending");
  const [gradeTarget, setGradeTarget] = useState<string | null>(null);

  const versions = assignment?.question_version_ids ?? [];
  const currentVersionId =
    versionId !== null && versions.includes(versionId) ? versionId : (versions[0] ?? null);

  const progress = useAssignmentProgress(assignment);
  const groups = useMemo(() => splitByCompletion(progress.rows), [progress.rows]);
  const versionRows = useQuestionVersionRows(versions);
  const currentRow =
    versionRows.find((row) => row.questionVersionId === currentVersionId) ?? null;

  const selectedStudent =
    studentId === null
      ? null
      : (progress.rows.find((row) => row.studentId === studentId) ?? null);

  /** 选中状态写回 URL：刷新、复制链接、浏览器后退都回到同一处（只放对象 id）。 */
  const select = (nextVersion: string | null, nextStudent: string | null) => {
    setVersionId(nextVersion);
    setStudentId(nextStudent);
    void navigate({
      to: "/assignments/$assignmentId/grading",
      params: { assignmentId },
      search: {
        version: nextVersion ?? undefined,
        student: nextStudent ?? undefined,
      },
    });
  };

  if (!teacherView) {
    return (
      <div className="stack">
        <PageTitle>{t("grading.title")}</PageTitle>
        <EmptyState>{t("assignments.detailForbidden")}</EmptyState>
      </div>
    );
  }

  return (
    <div className="stack">
      <nav className="muted" aria-label={t("assignments.breadcrumb")}>
        <Link to="/assignments">{t("nav.assignments")}</Link>
        <span aria-hidden="true"> / </span>
        <Link to="/assignments/$assignmentId" params={{ assignmentId }}>
          {assignment?.title ?? t("assignments.detailTitle")}
        </Link>
        <span aria-hidden="true"> / </span>
        <span data-testid="grading-breadcrumb-current">{t("grading.title")}</span>
      </nav>

      <div className="inline page-head">
        <PageTitle>
          {t("grading.heading", { title: assignment?.title ?? t("assignments.detailTitle") })}
        </PageTitle>
        <div className="row-actions">
          <span className="muted" data-testid="grading-processed">
            {t("grading.processed")}：
            <GroupCount groups={groups} complete={progress.complete} />
          </span>
          <Link to="/assignments" className="button button--ghost">
            {t("grading.backToList")}
          </Link>
        </div>
      </div>

      {assignmentQuery.isPending ? (
        <Spinner label={t("assignments.detailLoading")} />
      ) : assignmentQuery.isError ? (
        assignmentErrorContent(assignmentQuery.error)
      ) : progress.isError ? (
        isApiError(progress.error) && progress.error.status === 403 ? (
          <Banner tone="error" testId="grading-progress-forbidden">
            {t("assignments.progressForbidden")}
          </Banner>
        ) : (
          <ErrorState error={progress.error} testId="grading-progress-error" />
        )
      ) : progress.truncated ? (
        <Banner tone="warn" testId="grading-truncated">
          {t("common.paginationTruncated", { pages: MAX_AUTO_PAGES })}{" "}
          {t("grading.truncated")}
        </Banner>
      ) : progress.isPending || !progress.complete ? (
        <>
          <Spinner />
          <Banner tone="info" testId="grading-incomplete">
            {t("grading.incomplete")}
          </Banner>
        </>
      ) : (
        <div className="grading-workspace">
          <aside className="grading-workspace__rail">
            <Panel title={t("grading.questionsTitle")} testId="grading-questions">
              <ul className="grading-question-list">
                {versionRows.map((row, index) => (
                  <li key={row.questionVersionId}>
                    <button
                      type="button"
                      aria-current={row.questionVersionId === currentVersionId}
                      data-testid={`grading-question-${row.ordinal}`}
                      onClick={() =>
                        select(row.questionVersionId, selectedStudent?.studentId ?? null)
                      }
                    >
                      <span>
                        {index + 1}. {row.question?.title ?? t("questions.titleUnavailable")}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel
              title={t("grading.completedTitle")}
              testId="grading-completed-group"
            >
              <button
                type="button"
                className="grading-group__head"
                aria-expanded={openGroup === "completed"}
                data-testid="grading-toggle-completed"
                onClick={() => setOpenGroup(openGroup === "completed" ? null : "completed")}
              >
                <span className="muted">
                  {t("grading.completedCount", {
                    done: groups.completed.length,
                    total: judgedTotal(groups),
                  })}
                </span>
                <span className="muted" aria-hidden="true">
                  {openGroup === "completed" ? t("common.collapse") : t("common.expand")}
                </span>
              </button>
              {openGroup === "completed" ? (
                groups.completed.length === 0 ? (
                  <p className="muted">{t("grading.completedEmpty")}</p>
                ) : (
                  <ul className="grading-student-list">
                    {groups.completed.map((row) => (
                      <StudentRow
                        key={row.enrollmentId}
                        selected={row.studentId === selectedStudent?.studentId}
                        name={row.displayName}
                        meta={`${row.studentNumber} · ${row.className} · ${t(
                          "grading.submittedOf",
                          { done: row.submittedCount, total: row.total },
                        )}`}
                        tone={overallStateTone(row.status)}
                        label={overallStateLabel(row.status)}
                        testId={`grading-student-completed-${row.enrollmentId}`}
                        onSelect={() => select(currentVersionId, row.studentId)}
                      />
                    ))}
                  </ul>
                )
              ) : null}
            </Panel>

            <Panel title={t("grading.pendingTitle")} testId="grading-pending-group">
              <button
                type="button"
                className="grading-group__head"
                aria-expanded={openGroup === "pending"}
                data-testid="grading-toggle-pending"
                onClick={() => setOpenGroup(openGroup === "pending" ? null : "pending")}
              >
                <span className="muted">
                  {t("grading.pendingCount", {
                    missing: groups.pending.length,
                    total: judgedTotal(groups),
                  })}
                </span>
                <span className="muted" aria-hidden="true">
                  {openGroup === "pending" ? t("common.collapse") : t("common.expand")}
                </span>
              </button>
              {openGroup === "pending" ? (
                <>
                  <p className="muted">{t("grading.submissionState")}</p>
                  {groups.pending.length === 0 ? (
                    <p className="muted" data-testid="grading-pending-empty">
                      {t("grading.pendingEmpty")}
                    </p>
                  ) : (
                    <ul className="grading-student-list" data-testid="grading-pending-list">
                      {groups.pending.map((row) => (
                        <StudentRow
                          key={row.enrollmentId}
                          selected={row.studentId === selectedStudent?.studentId}
                          name={row.displayName}
                          meta={`${row.studentNumber} · ${row.className}`}
                          tone="idle"
                          label={t("grades.missing.statusNotSubmitted")}
                          testId={`grading-student-pending-${row.enrollmentId}`}
                          onSelect={() => select(currentVersionId, row.studentId)}
                        />
                      ))}
                    </ul>
                  )}
                </>
              ) : null}
            </Panel>

            {groups.unbound.length > 0 ? (
              <Banner tone="warn" testId="grading-unbound">
                {t("grading.unbound", { count: groups.unbound.length })}
              </Banner>
            ) : null}

            {/* 「未完成」的第二个动作：邮件提醒（PDF 的「自动包括提示语 / 一键提醒」）。
                收件人就是上面这份未完成名单；未绑定账号的行没有可投递的账号，单独说明。 */}
            {assignment !== null ? (
              <ReminderPanel
                courseId={assignment.course_id}
                assignmentId={assignment.assignment_id}
                title={assignment.title}
                dueAt={assignment.due_at ?? null}
                pending={groups.pending}
                unboundCount={groups.unbound.length}
              />
            ) : null}
          </aside>

          <section className="grading-workspace__window">
            {selectedStudent !== null ? (
              <Panel
                title={t("grading.studentWindow", { name: selectedStudent.displayName })}
                testId="grading-student-window"
              >
                <div className="stack">
                  <div className="inline">
                    <button
                      type="button"
                      className="button button--ghost grading-answer__back"
                      data-testid="grading-back-to-question"
                      onClick={() => select(currentVersionId, null)}
                    >
                      <span aria-hidden="true">←</span> {t("grading.backToQuestion")}
                    </button>
                    <span className="muted">
                      {selectedStudent.studentNumber} · {selectedStudent.className}
                    </span>
                    <StatusPill tone={overallStateTone(selectedStudent.status)}>
                      {overallStateLabel(selectedStudent.status)}
                    </StatusPill>
                    <span className="grading-score" data-testid="grading-student-score">
                      {t("grading.scorePercent")}：
                      {(() => {
                        const percent = assignmentScorePercent(selectedStudent);
                        return percent === null ? t("grading.scoreUnknown") : `${percent}`;
                      })()}
                    </span>
                  </div>

                  <QuestionAnswer
                    student={selectedStudent}
                    versionId={currentVersionId}
                    versionTitle={
                      currentRow?.question?.title ?? t("questions.titleUnavailable")
                    }
                    ordinal={
                      versionRows.find((row) => row.questionVersionId === currentVersionId)
                        ?.ordinal ?? 1
                    }
                    stemMarkdown={currentRow?.question?.stem_markdown ?? null}
                    onGrade={(submissionId) => setGradeTarget(submissionId)}
                  />
                </div>
              </Panel>
            ) : (
              <Panel title={t("grading.questionWindow")} testId="grading-question-window">
                <div className="stack">
                  <div className="inline">
                    <span className="muted">
                      {t("questions.version", {
                        version: currentRow?.version?.version ?? 1,
                      })}
                    </span>
                    <span className="muted">
                      {t(`questions.language.${currentRow?.version?.language ?? "python"}`)}
                    </span>
                  </div>
                  <h2 className="panel__title" data-testid="grading-question-title">
                    {currentRow?.question?.title ?? t("questions.titleUnavailable")}
                  </h2>
                  {currentRow?.question?.stem_markdown ? (
                    <SafeMarkdown
                      source={currentRow.question.stem_markdown}
                      testId="grading-question-stem"
                    />
                  ) : currentRow?.isPending ? (
                    <InlineLoader />
                  ) : (
                    <p className="muted">{t("grading.stemUnavailable")}</p>
                  )}
                  <p className="muted">{t("grading.pickStudentHint")}</p>
                </div>
              </Panel>
            )}
          </section>
        </div>
      )}

      {gradeTarget !== null ? (
        <ManualGradeDrawer
          submissionId={gradeTarget}
          onClose={() => setGradeTarget(null)}
        />
      ) : null}
    </div>
  );
}

/** 右栏大窗口里的"一名学生 × 一道题"：作业详情 + 作答情况 + 评测结果与 ai 评价 + 改分。 */
function QuestionAnswer({
  student,
  versionId,
  versionTitle,
  ordinal,
  stemMarkdown,
  onGrade,
}: {
  student: StudentProgress;
  versionId: string | null;
  versionTitle: string;
  ordinal: number;
  stemMarkdown: string | null;
  onGrade: (submissionId: string) => void;
}) {
  const question =
    student.questions.find((item) => item.questionVersionId === versionId) ?? null;
  const submission = question?.submission ?? null;

  return (
    <div className="stack">
      <Panel title={t("grading.detailTitle")} testId="grading-student-detail">
        <div className="stack">
          <div className="inline">
            <strong>
              {ordinal}. {versionTitle}
            </strong>
            {question ? (
              <StatusPill tone={questionStateTone(question.state)}>
                {questionStateLabel(question.state)}
              </StatusPill>
            ) : null}
          </div>
          {stemMarkdown ? (
            <SafeMarkdown source={stemMarkdown} testId="grading-student-stem" />
          ) : (
            <p className="muted">{t("grading.stemUnavailable")}</p>
          )}
        </div>
      </Panel>

      <Panel title={t("grading.answerTitle")} testId="grading-answer">
        {submission === null ? (
          <EmptyState>{t("grading.answerNotSubmitted")}</EmptyState>
        ) : (
          <div className="stack">
            <div className="inline">
              <span className="muted">
                {t("grades.submittedAt")}：
                {formatDateTime(submission.submitted_at, t("common.none"))}
              </span>
              <button
                type="button"
                className="button button--ghost"
                data-testid="grading-manual-grade"
                onClick={() => onGrade(submission.submission_id)}
              >
                {t("grading.manualGrade")}
              </button>
            </div>
            <AnswerViewer
              submissionId={submission.submission_id}
              path={answerFileName(ordinal)}
            />
          </div>
        )}
      </Panel>

      <Panel title={t("grading.evaluationTitle")} testId="grading-evaluation">
        {submission?.evaluation_run_id ? (
          // `GradeDrilldown` 同时给出权威分、逐项证据与 ai 评语轨（`CommentarySection`），
          // 因此这里不再单独重复渲染评语，避免同一段模型输出出现两次。
          <GradeDrilldown
            runId={submission.evaluation_run_id}
            view="teacher"
            runState={submission.state}
            questionVersionId={versionId ?? undefined}
          />
        ) : (
          <p className="muted" data-testid="grading-no-run">
            {t("grading.noRun")}
          </p>
        )}
      </Panel>
    </div>
  );
}
