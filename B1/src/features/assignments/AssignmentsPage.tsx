import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import type { AssignmentPublic, GradeRow } from "../../api/types";
import { useSession } from "../../auth/sessionContext";
import { canReviewQuarantine, canViewCohortGrades } from "../../auth/visibility";
import { formatDateTime, shortenId } from "../../ui/format";
import {
  Banner,
  EmptyState,
  ErrorState,
  Field,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
  Table,
  Tabs,
} from "../../ui/components";
import { loadedCourses, useCourses, useSections } from "../../api/directory";
import {
  buildCohort,
  cohortJudged,
  cohortStatusLabel,
  cohortStatusTone,
  type CohortSummary,
} from "../grades/cohort";
import { useCohort } from "../grades/useCohort";
import { useAllGrades, gradesAvailability, type GradesAvailability } from "../grades/useGrades";
import { useAllAssignments } from "./useAssignments";
import { AssignmentComposer } from "./AssignmentComposer";

/**
 * 作业中心（契约 `AssignmentPublic`，CHG-0007）。
 *
 * 为什么需要这一页：题库有上百道题，"从题海里自己找作业"不是可用性，而是缺失的功能。
 * 作业是 B3 提供的**集合**：`assignment_id` + `question_version_ids`，B1 只做展示与跳转，
 * 不新增契约字段。
 *
 * 两种视图（同一批数据，回答不同问题）：
 * - 学生「我的作业」：我该做什么、什么时候到期、我做完了没有 → 每题挂到既有作答流程
 *   `/submit/{question_version_id}`；"做完没有"来自**本人成绩行**（`listGrades` 对学生
 *   只返回本人记录，B1 从不发送 `student_id`）。
 * - 教师「提交情况」：这个作业多少人交了 / 没交 → 复用 `cohort.ts` 的连接与 `useCohort`
 *   的"取完全部页再判定"，不重新实现一遍名单×成绩。
 *
 * 「已提交」的判定口径：该作业的**任一**题目版本有提交记录（作业可以有多个题目版本，
 * 契约只给 `question_version_ids`，B1 不猜"必须全部完成"这种没有契约依据的规则）。
 */

type AssignmentStatus = AssignmentPublic["status"];

function assignmentStatusTone(status: AssignmentStatus): "ok" | "idle" {
  return status === "open" ? "ok" : "idle";
}

function isOverdue(dueAt: string | null | undefined, now: number): boolean {
  if (!dueAt) return false;
  const due = Date.parse(dueAt);
  return Number.isFinite(due) && due < now;
}

/** 作业级别的进度：已完成几个题目版本 / 共几个。 */
function assignmentProgress(
  assignment: AssignmentPublic,
  byVersion: Map<string, GradeRow>,
): { done: number; total: number; tone: "ok" | "warn" | "idle"; label: string } {
  const versions = assignment.question_version_ids ?? [];
  const done = versions.filter((versionId) => byVersion.has(versionId)).length;
  if (done === 0) {
    return { done, total: versions.length, tone: "idle", label: t("assignments.my.notSubmitted") };
  }
  if (done === versions.length) {
    return { done, total: versions.length, tone: "ok", label: t("assignments.my.submitted") };
  }
  return { done, total: versions.length, tone: "warn", label: t("assignments.my.partial") };
}

/** 下一个该做的题目版本：优先还没提交的那个，全交完了就回到第一个（可重交）。 */
function nextVersion(assignment: AssignmentPublic, byVersion: Map<string, GradeRow>): string | null {
  const versions = assignment.question_version_ids ?? [];
  return versions.find((versionId) => !byVersion.has(versionId)) ?? versions[0] ?? null;
}

function DueCell({ dueAt, now }: { dueAt: string | null | undefined; now: number }) {
  if (!dueAt) return <span className="muted">{t("assignments.noDue")}</span>;
  return (
    <span>
      {formatDateTime(dueAt, t("common.none"))}
      {isOverdue(dueAt, now) ? (
        <span className="muted" data-testid="assignment-overdue">
          {" "}
          · {t("assignments.overdue")}
        </span>
      ) : null}
    </span>
  );
}

function StudentAssignmentList({
  assignments,
  byVersion,
  myProgress,
  now,
}: {
  assignments: AssignmentPublic[];
  byVersion: Map<string, GradeRow>;
  myProgress: GradesAvailability;
  now: number;
}) {
  if (assignments.length === 0) return <EmptyState>{t("assignments.empty")}</EmptyState>;

  return (
    <Table<AssignmentPublic>
      testId="my-assignments-table"
      label={t("assignments.myTitle")}
      rows={assignments}
      getRowKey={(row) => row.assignment_id}
      columns={[
        {
          key: "title",
          header: t("assignments.col.title"),
          render: (row) => (
            <Link to="/assignments/$assignmentId" params={{ assignmentId: row.assignment_id }}>
              {row.title}
            </Link>
          ),
        },
        {
          key: "status",
          header: t("assignments.col.status"),
          render: (row) => (
            <StatusPill tone={assignmentStatusTone(row.status)}>
              {t(`assignments.status.${row.status}`)}
            </StatusPill>
          ),
        },
        {
          key: "due_at",
          header: t("assignments.col.due"),
          render: (row) => <DueCell dueAt={row.due_at} now={now} />,
        },
        {
          key: "questions",
          header: t("assignments.col.questions"),
          align: "right",
          render: (row) => (row.question_version_ids ?? []).length,
        },
        {
          key: "progress",
          header: t("assignments.col.progress"),
          render: (row) => {
            if (myProgress === "loading") {
              // 本人成绩行还没取完：此时任何"做完了没有"的判断都不可信。
              return (
                <span className="muted" data-testid={`my-progress-loading-${row.assignment_id}`}>
                  {t("common.loading")}
                </span>
              );
            }
            if (myProgress === "truncated") {
              // 终局：不再显示"加载中"，如实说明成绩没取完。
              return (
                <span className="muted" data-testid={`my-progress-truncated-${row.assignment_id}`}>
                  {t("assignments.gradesTruncatedShort")}
                </span>
              );
            }
            const progress = assignmentProgress(row, byVersion);
            return (
              <span className="inline">
                <StatusPill tone={progress.tone}>{progress.label}</StatusPill>
                <span className="muted" data-testid={`my-progress-${row.assignment_id}`}>
                  {t("assignments.progressDone", { done: progress.done, total: progress.total })}
                </span>
              </span>
            );
          },
        },
        {
          key: "action",
          header: t("assignments.col.action"),
          render: (row) => {
            // 判定未就绪时不渲染动作：动作标签（"作答"/"继续作答"）本身就是进度结论。
            if (myProgress !== "ready") return <span className="muted">{t("common.none")}</span>;
            const target = nextVersion(row, byVersion);
            if (!target) return <span className="muted">{t("common.none")}</span>;
            const progress = assignmentProgress(row, byVersion);
            const label =
              progress.done === 0
                ? t("assignments.answer")
                : progress.done === progress.total
                  ? t("assignments.actionResubmit")
                  : t("assignments.actionContinue");
            return (
              <Link
                className="button button--ghost"
                to="/submit/$questionVersionId"
                params={{ questionVersionId: target }}
              >
                {label}
              </Link>
            );
          },
        },
      ]}
    />
  );
}

/** 逐作业的提交/未提交计数：同一份已加载数据，只为每个作业再过滤一次成绩行。 */
function perAssignmentSummaries(
  assignments: AssignmentPublic[],
  roster: ReturnType<typeof useCohort>["roster"],
  gradeRows: GradeRow[],
): Map<string, CohortSummary> {
  const map = new Map<string, CohortSummary>();
  for (const assignment of assignments) {
    const versions = assignment.question_version_ids ?? [];
    map.set(
      assignment.assignment_id,
      buildCohort(
        roster,
        gradeRows.filter((grade) => versions.includes(grade.question_version_id)),
      ),
    );
  }
  return map;
}

function AssignmentRoster({
  assignment,
  summary,
}: {
  assignment: AssignmentPublic | null;
  summary: CohortSummary | undefined;
}) {
  if (!assignment || !summary) return null;
  if (summary.rows.length === 0) {
    return (
      <Panel title={t("assignments.progressTitle")} testId="assignment-roster">
        <EmptyState />
      </Panel>
    );
  }
  return (
    <Panel title={t("assignments.progressTitle")} testId="assignment-roster">
      <p className="muted">{assignment.title}</p>
      <Table
        testId="assignment-roster-table"
        label={`${t("assignments.progressTitle")} · ${assignment.title}`}
        rows={summary.rows}
        getRowKey={(row) => row.enrollmentId}
        columns={[
          {
            key: "student_number",
            header: t("roster.studentNumber"),
            render: (row) => row.studentNumber,
          },
          { key: "name", header: t("roster.displayName"), render: (row) => row.displayName },
          {
            key: "student_id",
            header: t("grades.missing.studentId"),
            render: (row) =>
              row.studentId === null ? (
                <span className="muted">{t("grades.missing.statusUnbound")}</span>
              ) : (
                <span className="mono-id" title={`学生 ID：${row.studentId}`}>
                  {shortenId(row.studentId)}
                </span>
              ),
          },
          {
            key: "status",
            header: t("grades.state"),
            render: (row) => (
              <StatusPill tone={cohortStatusTone(row.status)}>
                {cohortStatusLabel(row.status)}
              </StatusPill>
            ),
          },
          {
            key: "submitted_at",
            header: t("grades.submittedAt"),
            render: (row) =>
              row.grade ? (
                formatDateTime(row.grade.submitted_at, t("common.none"))
              ) : (
                <span className="muted">{t("common.none")}</span>
              ),
          },
        ]}
      />
    </Panel>
  );
}

function TeacherProgress({
  assignments,
  cohort,
  sectionLabelOf,
}: {
  assignments: AssignmentPublic[];
  cohort: ReturnType<typeof useCohort>;
  /** course scope 内的课序显示名：作业行必须说清它属于哪个课序。 */
  sectionLabelOf: (sectionId: string) => string;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const summaries = useMemo(
    () => perAssignmentSummaries(assignments, cohort.roster, cohort.gradeRows),
    [assignments, cohort.roster, cohort.gradeRows],
  );

  if (cohort.error) return <ErrorState error={cohort.error} />;
  if (cohort.isPending) {
    return (
      <>
        <Spinner />
        <Banner tone="info" testId="assignment-progress-loading">
          {t("assignments.notComplete")}
        </Banner>
      </>
    );
  }
  if (cohort.truncated) {
    // 永久状态：不渲染 spinner，也不渲染任何"已提交 / 未提交"数字。
    return (
      <Banner tone="warn" testId="assignment-progress-truncated">
        {t("common.paginationTruncated", { pages: MAX_AUTO_PAGES })}{" "}
        {t("assignments.gradesTruncated")}
      </Banner>
    );
  }
  if (cohort.incomplete) {
    return (
      <>
        <Spinner />
        <Banner tone="info" testId="assignment-progress-loading">
          {t("assignments.notComplete")}
        </Banner>
      </>
    );
  }
  if (assignments.length === 0) {
    return (
      <Panel title={t("assignments.progressTitle")}>
        <EmptyState>{t("assignments.empty")}</EmptyState>
      </Panel>
    );
  }

  return (
    <div className="stack">
      <Banner tone="info" testId="assignment-progress-rule">
        {t("assignments.progressIntro")} {t("assignments.progressRule")}
      </Banner>
      <Table<AssignmentPublic>
        testId="assignment-progress-table"
        label={t("assignments.progressTitle")}
        rows={assignments}
        getRowKey={(row) => row.assignment_id}
        columns={[
          {
            key: "title",
            header: t("assignments.col.title"),
            render: (row) => (
              <Link to="/assignments/$assignmentId" params={{ assignmentId: row.assignment_id }}>
                {row.title}
              </Link>
            ),
          },
          {
            key: "section",
            header: t("assignments.col.section"),
            render: (row) => sectionLabelOf(row.section_id),
          },
          {
            key: "due_at",
            header: t("assignments.col.due"),
            render: (row) => (
              <DueCell dueAt={row.due_at} now={Date.now()} />
            ),
          },
          {
            key: "status",
            header: t("assignments.col.status"),
            render: (row) => (
              <StatusPill tone={assignmentStatusTone(row.status)}>
                {t(`assignments.status.${row.status}`)}
              </StatusPill>
            ),
          },
          {
            key: "questions",
            header: t("assignments.col.questions"),
            align: "right",
            render: (row) => (row.question_version_ids ?? []).length,
          },
          {
            key: "submitted",
            header: t("assignments.col.submitted"),
            align: "right",
            render: (row) => {
              const summary = summaries.get(row.assignment_id);
              const judged = summary ? cohortJudged(summary) : null;
              return (
                <span data-testid={`submitted-${row.assignment_id}`}>
                  {judged ? `${judged.submitted} / ${judged.judged}` : t("common.none")}
                </span>
              );
            },
          },
          {
            key: "missing",
            header: t("assignments.col.missing"),
            align: "right",
            render: (row) => {
              const summary = summaries.get(row.assignment_id);
              return (
                <span data-testid={`missing-${row.assignment_id}`}>
                  {summary ? summary.counts.not_submitted : t("common.none")}
                </span>
              );
            },
          },
          {
            key: "detail",
            header: t("assignments.col.detail"),
            render: (row) => (
              <button
                type="button"
                className="button button--ghost"
                data-testid={`toggle-roster-${row.assignment_id}`}
                aria-expanded={expanded === row.assignment_id}
                onClick={() =>
                  setExpanded((previous) =>
                    previous === row.assignment_id ? null : row.assignment_id,
                  )
                }
              >
                {expanded === row.assignment_id
                  ? t("assignments.hideRoster")
                  : t("assignments.viewRoster")}
              </button>
            ),
          },
        ]}
      />
      {expanded ? (
        <AssignmentRoster
          assignment={assignments.find((item) => item.assignment_id === expanded) ?? null}
          summary={summaries.get(expanded)}
        />
      ) : null}
    </div>
  );
}

export function AssignmentsPage() {
  const session = useSession();
  const cohortView = canViewCohortGrades(session);
  // 作用域：学生用会话里的课程/课序（与作答页同一约定）；教师的两个视图都由页面上的
  // 课程/课序选择器决定——列表与提交情况必须看同一个作用域，否则计数与列表对不上。
  const scope = session.scope[0] ?? null;
  const [tab, setTab] = useState<"list" | "progress">("list");
  const [composerOpen, setComposerOpen] = useState(false);
  const [createdNotice, setCreatedNotice] = useState<string | null>(null);
  const [courseId, setCourseId] = useState("");
  const [sectionId, setSectionId] = useState("");

  const courses = useCourses();
  const courseOptions = loadedCourses(courses.data?.pages);
  const selectedCourseId = courseId || (courseOptions[0]?.course_id ?? "");
  const selectedSectionId = sectionId || scope?.section_id || "";
  const sections = useSections(
    cohortView && selectedCourseId !== "" ? selectedCourseId : null,
  );

  const courseScope = cohortView ? selectedCourseId : (scope?.course_id ?? "");
  const sectionScope = cohortView
    ? selectedSectionId
    : (scope?.section_id ?? "");

  const listFilter = useMemo(
    () => ({
      courseId: courseScope === "" ? null : courseScope,
      sectionId: sectionScope === "" ? null : sectionScope,
    }),
    [courseScope, sectionScope],
  );
  const assignments = useAllAssignments(listFilter);
  const mine = useAllGrades(scope?.course_id ?? null, scope?.section_id ?? null);
  const byVersion = useMemo(() => {
    const map = new Map<string, GradeRow>();
    for (const row of mine.rows) {
      const previous = map.get(row.question_version_id);
      if (!previous || (row.submitted_at ?? "") > (previous.submitted_at ?? "")) {
        map.set(row.question_version_id, row);
      }
    }
    return map;
  }, [mine.rows]);

  const cohortFilter = useMemo(
    () => ({
      courseId: listFilter.courseId,
      sectionId: listFilter.sectionId,
      questionVersionIds: null,
    }),
    [listFilter],
  );
  const cohort = useCohort(cohortFilter);

  const listView = (
    <div className="stack">
      <Panel title={t("assignments.myTitle")}>
        <p className="muted">{t("assignments.studentHint")}</p>
        {assignments.isPending ? (
          <Spinner label={t("assignments.loadingAll")} />
        ) : assignments.isError ? (
          <ErrorState error={assignments.error} />
        ) : assignments.truncated ? (
          // 作业列表被截断：进度与"未提交"判定都不成立，但这里不再是"还在加载"。
          <Banner tone="warn" testId="assignments-truncated">
            {t("common.paginationTruncated", { pages: MAX_AUTO_PAGES })}{" "}
            {t("assignments.truncated")}
          </Banner>
        ) : !assignments.complete ? (
          <>
            <Spinner label={t("assignments.loadingAll")} />
            <Banner tone="info" testId="assignments-loading-all">
              {t("assignments.notComplete")}
            </Banner>
          </>
        ) : (
          <StudentAssignmentList
            assignments={assignments.assignments}
            byVersion={byVersion}
            myProgress={gradesAvailability(mine)}
            now={Date.now()}
          />
        )}
      </Panel>
      {mine.isError ? <ErrorState error={mine.error} /> : null}
    </div>
  );

  return (
    <div className="stack">
      {/* 邮件隔离复核不占主导航：它从作业上下文进入（P13 的 mail 标签），
          把当前课程/课序作为筛选上下文带过去；授权仍由服务端按令牌与记录 scope 判定。 */}
      <div className="inline">
        <PageTitle>{t("nav.assignments")}</PageTitle>
        {cohortView ? (
          <button
            type="button"
            className="button"
            data-testid="assignments-create-entry"
            onClick={() => setComposerOpen(true)}
          >
            {t("assignments.create.entry")}
          </button>
        ) : null}
        {canReviewQuarantine(session) ? (
          <Link
            to="/review"
            search={{
              tab: "mail",
              course_id: courseScope === "" ? undefined : courseScope,
              section_id: sectionScope === "" ? undefined : sectionScope,
            }}
            className="button button--ghost"
            data-testid="assignments-quarantine-link"
          >
            {t("review.mail.title")}
          </Link>
        ) : null}
      </div>

      {!cohortView && scope === null ? (
        <Panel title={t("assignments.title")}>
          <EmptyState>{t("assignments.scopeMissing")}</EmptyState>
        </Panel>
      ) : cohortView ? (
        <>
          <Panel title={t("grades.filter")}>
            <div className="form-grid">
              <Field label={t("grades.course")} htmlFor="assignment-course">
                <select
                  id="assignment-course"
                  value={selectedCourseId}
                  onChange={(event) => {
                    setCourseId(event.target.value);
                    setSectionId("");
                  }}
                >
                  {courseOptions.map((course) => (
                    <option key={course.course_id} value={course.course_id}>
                      {course.course_code} · {course.title}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("grades.section")} htmlFor="assignment-section">
                <select
                  id="assignment-section"
                  value={selectedSectionId}
                  onChange={(event) => setSectionId(event.target.value)}
                  disabled={selectedCourseId === ""}
                >
                  <option value="">{t("common.any")}</option>
                  {(sections.data ?? []).map((section) => (
                    <option key={section.sectionId} value={section.sectionId}>
                      {section.label}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            {courses.isError ? <ErrorState error={courses.error} /> : null}
            {sections.isError ? <ErrorState error={sections.error} /> : null}
          </Panel>

          <Tabs
            label={t("assignments.tabs.label")}
            testId="assignments-tabs"
            activeId={tab}
            onChange={(id) => setTab(id as "list" | "progress")}
            tabs={[
              { id: "list", label: t("assignments.tabs.list"), content: listView },
              {
                id: "progress",
                label: t("assignments.tabs.progress"),
                content:
                  listFilter.sectionId === null ? (
                    <Panel title={t("assignments.progressTitle")}>
                      <EmptyState>{t("assignments.progressNeedsSection")}</EmptyState>
                    </Panel>
                  ) : (
                    <TeacherProgress
                      assignments={assignments.assignments}
                      cohort={cohort}
                      sectionLabelOf={(sectionId) =>
                        (sections.data ?? []).find((option) => option.sectionId === sectionId)
                          ?.label ?? shortenId(sectionId)
                      }
                    />
                  ),
              },
            ]}
          />
        </>
      ) : (
        listView
      )}

      {createdNotice ? (
        <Banner tone="success" testId="assignments-created-notice">
          {createdNotice}
        </Banner>
      ) : null}

      {composerOpen ? (
        <AssignmentComposer
          onClose={() => setComposerOpen(false)}
          onCreated={() => setCreatedNotice(t("assignments.create.success"))}
        />
      ) : null}
    </div>
  );
}
