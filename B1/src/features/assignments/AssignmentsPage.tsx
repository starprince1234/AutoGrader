import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import type { AssignmentPublic, GradeRow } from "../../api/types";
import { useSession } from "../../auth/sessionContext";
import { canReviewQuarantine, canViewCohortGrades } from "../../auth/visibility";
import { useQuarantineMail } from "../review/useReviewMail";
import { formatDateTime, shortenId } from "../../ui/format";
import {
  Banner,
  EmptyState,
  ErrorState,
  Field,
  InlineLoader,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
  Table,
  Tabs,
} from "../../ui/components";
import { loadedCourses, useCourses, useMergedSections, useSections } from "../../api/directory";
import { useCohort } from "../grades/useCohort";
import { useAllGrades, gradesAvailability, type GradesAvailability } from "../grades/useGrades";
import { useAllAssignments } from "./useAssignments";
import { AssignmentComposer } from "./AssignmentComposer";
import {
  buildOwnProgress,
  perAssignmentCounts,
  type AssignmentProgressCounts,
} from "./useAssignmentProgress";
import { overallStateLabel, overallStateTone } from "./progressDisplay";
import { Select } from "../../ui/Select";

/**
 * 作业中心（CHG-0032 适配）。
 *
 * 两种视图（同一批数据，回答不同问题）：
 * - 学生「我的作业」：我该做什么、什么时候到期、我做到哪了 → 每个作业挂到**作业工作区**
 *   `/assignments/{assignment_id}`（旧单题作答页已删除）。
 * - 教师「提交情况」：这个课序里各作业的已交/部分/未交 → 复用 `useCohort` 的
 *   "取完全部页再判定"，并用 `perAssignmentCounts` 按新分类聚合。
 *
 * 不能假设作业只有一个课序：`section_ids` 是数组，列表显示全部目标课序，
 * 进度判定使用当前筛选课序的名单（列标题与说明都写明这一点）。
 */

/** 复核入口徽标的取数上限：一行待处理记录很小，100 条足够覆盖日常。 */
const PENDING_BADGE_LIMIT = 100;

type AssignmentStatus = AssignmentPublic["status"];

function assignmentStatusTone(status: AssignmentStatus): "ok" | "idle" {
  return status === "open" ? "ok" : "idle";
}

function isOverdue(dueAt: string | null | undefined, now: number): boolean {
  if (!dueAt) return false;
  const due = Date.parse(dueAt);
  return Number.isFinite(due) && due < now;
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

/** 作业目标课序的多值展示（`section_ids` 是数组，绝不当成单值）。 */
function SectionCells({
  assignment,
  sectionLabelOf,
}: {
  assignment: AssignmentPublic;
  sectionLabelOf: (sectionId: string) => string;
}) {
  return (
    <span data-testid={`assignment-sections-${assignment.assignment_id}`}>
      {assignment.section_ids.map((sectionId) => sectionLabelOf(sectionId)).join("、")}
    </span>
  );
}

function StudentAssignmentList({
  assignments,
  gradeRows,
  myProgress,
  now,
}: {
  assignments: AssignmentPublic[];
  gradeRows: GradeRow[];
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
                <span
                  className="muted"
                  data-testid={`my-progress-loading-${row.assignment_id}`}
                >
                  <InlineLoader />
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
            const own = buildOwnProgress({
              grades: gradeRows,
              questionVersionIds: row.question_version_ids ?? [],
              assignmentId: row.assignment_id,
            });
            return (
              <span className="inline">
                <StatusPill tone={overallStateTone(own.overall)}>
                  {overallStateLabel(own.overall)}
                </StatusPill>
                <span className="muted" data-testid={`my-progress-${row.assignment_id}`}>
                  {t("assignments.progressDone", {
                    done: own.completedCount,
                    total: own.total,
                  })}
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
            const own = buildOwnProgress({
              grades: gradeRows,
              questionVersionIds: row.question_version_ids ?? [],
              assignmentId: row.assignment_id,
            });
            const target =
              own.questions.find((question) => question.state === "not_submitted")
                ?.questionVersionId ??
              row.question_version_ids?.[0] ??
              null;
            const label =
              own.overall === "not_submitted"
                ? t("assignments.answer")
                : own.overall === "submitted"
                  ? t("assignments.actionResubmit")
                  : t("assignments.actionContinue");
            return (
              <Link
                className="button button--ghost"
                to="/assignments/$assignmentId"
                params={{ assignmentId: row.assignment_id }}
                search={{ question: target ?? undefined }}
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

function CountCell({
  counts,
  field,
  assignmentId,
}: {
  counts: Map<string, AssignmentProgressCounts> | null;
  field: keyof Pick<
    AssignmentProgressCounts,
    "notSubmitted" | "partial" | "submitted" | "evaluating" | "failed"
  >;
  assignmentId: string;
}) {
  const value = counts?.get(assignmentId);
  return (
    <span data-testid={`${field}-${assignmentId}`}>
      {value ? value[field] : t("common.none")}
    </span>
  );
}

function TeacherProgress({
  assignments,
  cohort,
  sectionLabelOf,
}: {
  assignments: AssignmentPublic[];
  cohort: ReturnType<typeof useCohort>;
  sectionLabelOf: (sectionId: string) => string;
}) {
  const complete =
    !cohort.isPending && !cohort.truncated && !cohort.incomplete && !cohort.error;
  const counts = useMemo(
    () =>
      complete
        ? perAssignmentCounts(assignments, cohort.roster, cohort.gradeRows)
        : null,
    [complete, assignments, cohort.roster, cohort.gradeRows],
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
            key: "sections",
            header: t("assignments.targetSections"),
            render: (row) => <SectionCells assignment={row} sectionLabelOf={sectionLabelOf} />,
          },
          {
            key: "due_at",
            header: t("assignments.col.due"),
            render: (row) => <DueCell dueAt={row.due_at} now={Date.now()} />,
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
            header: t("assignments.count.submitted"),
            align: "right",
            render: (row) => <CountCell counts={counts} field="submitted" assignmentId={row.assignment_id} />,
          },
          {
            key: "partial",
            header: t("assignments.count.partial"),
            align: "right",
            render: (row) => <CountCell counts={counts} field="partial" assignmentId={row.assignment_id} />,
          },
          {
            key: "missing",
            header: t("assignments.count.notSubmitted"),
            align: "right",
            render: (row) => (
              <CountCell counts={counts} field="notSubmitted" assignmentId={row.assignment_id} />
            ),
          },
        ]}
      />
    </div>
  );
}

export function AssignmentsPage() {
  const session = useSession();
  const cohortView = canViewCohortGrades(session);
  // 复核入口旁的数字：老师要的是"有几封要处理"，不是一个红点。列表接口没有 total，
  // 因此只数这一页（最多 100）；还有下一页就显示「100+」，不把一页计数当总数。
  const quarantineIdentity = session.subject ?? session.username ?? "anonymous";
  const pendingQuarantine = useQuarantineMail({
    identity: quarantineIdentity,
    enabled: canReviewQuarantine(session),
    filter: {
      courseId: null,
      sectionId: null,
      resolved: false,
      held: false,
      reasonCode: null,
      cursor: null,
      limit: PENDING_BADGE_LIMIT,
    },
  });
  const pendingRows = (pendingQuarantine.data?.pages ?? []).flatMap((page) => page.items);
  const pendingCount = pendingRows.length;
  const pendingOverflow = Boolean(pendingQuarantine.data?.pages.at(-1)?.nextCursor);
  const hasPendingQuarantine = pendingCount > 0;
  // 作用域：学生用会话里的课程/课序；教师的两个视图都由页面上的课程/课序选择器决定。
  const scope = session.scope[0] ?? null;
  const [tab, setTab] = useState<"list" | "progress">("list");
  const [composerOpen, setComposerOpen] = useState(false);
  const [createdNotice, setCreatedNotice] = useState<string | null>(null);
  const [courseId, setCourseId] = useState("");
  const [sectionId, setSectionId] = useState("");

  const courses = useCourses();
  const courseOptions = loadedCourses(courses.data?.pages);

  const selectedCourseId = courseId || (courseOptions[0]?.course_id ?? "");
  // 界面上的课程按**名字**出现（同名课程只出现一次）；区别由课序体现。
  const courseTitles = [...new Set(courseOptions.map((course) => course.title))];
  const selectedCourseTitle =
    courseOptions.find((course) => course.course_id === selectedCourseId)?.title ??
    courseTitles[0] ??
    "";
  // 同名课程的课序合并成一个下拉：否则第二门课的班级永远选不到。
  const mergedSections = useMergedSections(
    courseOptions
      .filter((course) => course.title === selectedCourseTitle)
      .map((course) => course.course_id),
  );

  const selectedSectionId = sectionId || scope?.section_id || "";
  const sections = useSections(
    cohortView && selectedCourseId !== "" ? selectedCourseId : null,
  );

  const courseScope = cohortView ? selectedCourseId : (scope?.course_id ?? "");
  const sectionScope = cohortView ? selectedSectionId : (scope?.section_id ?? "");

  const listFilter = useMemo(
    () => ({
      courseId: courseScope === "" ? null : courseScope,
      sectionId: sectionScope === "" ? null : sectionScope,
    }),
    [courseScope, sectionScope],
  );
  const assignments = useAllAssignments(listFilter);
  const mine = useAllGrades(scope?.course_id ?? null, scope?.section_id ?? null);

  const cohortFilter = useMemo(
    () => ({
      courseId: listFilter.courseId,
      sectionId: listFilter.sectionId,
      questionVersionIds: null,
    }),
    [listFilter],
  );
  const cohort = useCohort(cohortFilter);

  const sectionLabelOf = (targetSectionId: string) => {
    const option = (sections.data ?? []).find(
      (candidate) => candidate.sectionId === targetSectionId,
    );
    if (option) return option.label;
    const merged = mergedSections.options.find(
      (candidate) => candidate.sectionId === targetSectionId,
    );
    return merged ? merged.label : shortenId(targetSectionId);
  };

  const listView = (
    <div className="stack">
      <Panel title={cohortView ? t("assignments.tabs.list") : t("assignments.myTitle")}>
        {cohortView ? null : <p className="muted">{t("assignments.studentHint")}</p>}
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
            gradeRows={mine.rows}
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
      <div className="panel__head">
        <PageTitle>{t("nav.assignments")}</PageTitle>
        <div className="row-actions">
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
              // `.button--ghost` 的底色与页面底色相同，在页头看起来像纯文字、也显得没对齐；
              // 页面局部补一层卡面色与描边（不改共享 CSS），让它与"新建作业"是同一颗按钮。
              style={{
                background: "var(--color-surface)",
                boxShadow: "inset 0 0 0 1px var(--color-border)",
              }}
              data-testid="assignments-quarantine-link"
            >
              {t("review.mail.title")}
              {hasPendingQuarantine ? (
                <span
                  aria-label={t("review.pendingBadge", { count: pendingCount })}
                  data-testid="assignments-quarantine-badge"
                  style={{
                    minWidth: 18,
                    height: 18,
                    padding: "0 5px",
                    marginLeft: 6,
                    borderRadius: 9,
                    background: "var(--color-bad)",
                    color: "#fff",
                    fontSize: 11,
                    fontWeight: 600,
                    lineHeight: "18px",
                    textAlign: "center",
                    display: "inline-block",
                    verticalAlign: "middle",
                  }}
                >
                  {pendingOverflow ? `${pendingCount}+` : pendingCount}
                </span>
              ) : null}
            </Link>
          ) : null}
        </div>
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
                <Select
                  id="assignment-course"
                  value={selectedCourseTitle}
                  onChange={(event) => {
                    const next = courseOptions.find(
                      (course) => course.title === event.target.value,
                    );
                    setCourseId(next?.course_id ?? "");
                    setSectionId("");
                  }}
                >
                  {courseTitles.map((title) => (
                    <option key={title} value={title}>
                      {title}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={t("assignments.section")} htmlFor="assignment-section">
                <Select
                  id="assignment-section"
                  value={selectedSectionId}
                  onChange={(event) => {
                    const option = mergedSections.options.find(
                      (candidate) => candidate.sectionId === event.target.value,
                    );
                    // 课序自带课程：选中它同时确定"哪门课的哪个班"，避免只按序号定位。
                    if (option) {
                      setCourseId(option.row.course_id);
                    }
                    setSectionId(event.target.value);
                  }}
                  disabled={selectedCourseId === ""}
                >
                  {(mergedSections.options ?? []).map((section, index) => (
                    <option key={section.sectionId} value={section.sectionId}>
                      {index + 1}
                    </option>
                  ))}
                </Select>
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
                      sectionLabelOf={sectionLabelOf}
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
