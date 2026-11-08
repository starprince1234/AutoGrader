import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import type { AssignmentPublic, GradeRow } from "../../api/types";
import { useSession } from "../../auth/sessionContext";
import { canReviewQuarantine, canViewCohortGrades } from "../../auth/visibility";
import { useQuarantineMail, REVIEW_PAGE_SIZE } from "../review/useReviewMail";
import { formatDateTime } from "../../ui/format";
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
import {
  courseLabel,
  loadedCourses,
  useCourses,
  useSections,
} from "../../api/directory";
import { useCurrentUser } from "../../api/currentUser";
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
 * 作业中心（CHG-0032 适配 / v0.32.0 课程级作业）。
 *
 * 两种视图（同一批数据，回答不同问题）：
 * - 学生「我的作业」：我该做什么、什么时候到期、我做到哪了 → 每个作业挂到**作业工作区**
 *   `/assignments/{assignment_id}`（旧单题作答页已删除）。学生所属课程来自 `GET /me` 的
 *   `memberships`：多课程时提供课程切换，并列出每门课的课序与班级（不读教师名单）。
 * - 教师「提交情况」：当前筛选课序里各作业的已交/部分/未交 → 复用 `useCohort` 的
 *   "取完全部页再判定" + `perAssignmentCounts`。作业本身对课程全部课序/班级生效，
 *   这里的课序只是统计筛选，不是作业的受众范围。
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
              <span data-testid={`my-progress-${row.assignment_id}`}>
                <StatusPill tone={overallStateTone(own.overall)}>
                  {overallStateLabel(own.overall)}
                </StatusPill>
                <span className="muted">
                  {" · "}
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
}: {
  assignments: AssignmentPublic[];
  cohort: ReturnType<typeof useCohort>;
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

/** 学生本人的课程/课序/班级（`GET /me` 的 memberships）：姓名与学号也来自同一响应。 */
function MyMembershipsPanel() {
  const currentUser = useCurrentUser();
  const memberships = currentUser.data?.memberships ?? [];
  return (
    <Panel title={t("assignments.myMembershipsTitle")} testId="my-memberships">
      {currentUser.isPending ? (
        <p className="muted" data-testid="my-memberships-loading">
          <InlineLoader />
        </p>
      ) : currentUser.isError ? (
        <ErrorState error={currentUser.error} testId="my-memberships-error" />
      ) : (
        <div className="stack">
          <div className="inline" data-testid="my-identity">
            <span className="muted">
              {t("assignments.myName")}：{currentUser.data?.display_name ?? t("common.none")}
            </span>
            <span className="muted">
              {t("assignments.myStudentNumber")}：
              {currentUser.data?.student_number ?? t("common.none")}
            </span>
          </div>
          {memberships.length === 0 ? (
            <EmptyState>{t("assignments.myCoursesEmpty")}</EmptyState>
          ) : (
            <ul className="stack" data-testid="my-membership-list">
              {memberships.map((membership) => (
                <li
                  key={`${membership.course_id}:${membership.section_id}:${membership.class_id}`}
                  className="muted"
                  data-testid={`my-membership-${membership.course_id}`}
                >
                  {membership.course_title} · {t("assignments.col.section")}{" "}
                  {membership.section_code} · {t("assignments.col.class")}{" "}
                  {membership.class_name}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Panel>
  );
}

export function AssignmentsPage() {
  const session = useSession();
  const cohortView = canViewCohortGrades(session);
  const quarantineIdentity = session.subject ?? session.username ?? "anonymous";
  const [tab, setTab] = useState<"list" | "progress">("list");
  const [composerOpen, setComposerOpen] = useState(false);
  const [createdNotice, setCreatedNotice] = useState<string | null>(null);
  const [courseId, setCourseId] = useState("");
  const [sectionId, setSectionId] = useState("");
  const [studentCourseId, setStudentCourseId] = useState("");

  const courses = useCourses({ enabled: cohortView });
  const courseOptions = loadedCourses(courses.data?.pages);
  // 学生视图不需要 /me 之外的教师资源，教师视图也不需要本人的 memberships。
  const currentUser = useCurrentUser({ enabled: !cohortView });

  // ---- 教师：课程/课序筛选（课序只用于统计，不是作业受众范围） ----------
  // 会话里的 scope 作为初始课序提示（复核链接与徽标必须指向同一批记录）；它只影响
  // 默认筛选，真正的授权仍由服务端按当前归属判定。
  const sessionScope = session.scope[0] ?? null;
  const selectedCourseId = courseId || (courseOptions[0]?.course_id ?? "");
  const sections = useSections(cohortView && selectedCourseId !== "" ? selectedCourseId : null);
  const sectionOptions = sections.data ?? [];
  const selectedSectionId =
    sectionId || sessionScope?.section_id || (sectionOptions[0]?.sectionId ?? "");
  const sectionScope = cohortView ? selectedSectionId : "";

  // ---- 学生：本人 memberships（多课程时用课程切换） ----------------------
  const memberships = currentUser.data?.memberships ?? [];
  const selectedMembership =
    memberships.find((membership) => membership.course_id === studentCourseId) ??
    memberships[0] ??
    null;
  const studentCourseScope = cohortView ? "" : (selectedMembership?.course_id ?? "");

  const listFilter = useMemo(
    () => ({ courseId: cohortView ? (selectedCourseId === "" ? null : selectedCourseId) : (studentCourseScope === "" ? null : studentCourseScope) }),
    [cohortView, selectedCourseId, studentCourseScope],
  );
  const assignments = useAllAssignments(listFilter);
  /**
   * 复核入口旁的数字：必须和点进去看到的「待处理记录」是**同一批**——
   * 同样的课程与统计课序、同样的 `resolved=false & held=false`、同样的分页大小
   * （`REVIEW_PAGE_SIZE`，复核页第一页也是它）。课程目录未到手前先不请求。
   */
  const pendingQuarantine = useQuarantineMail({
    identity: quarantineIdentity,
    enabled: canReviewQuarantine(session) && !courses.isPending,
    filter: {
      courseId: listFilter.courseId,
      sectionId: sectionScope === "" ? null : sectionScope,
      resolved: false,
      held: false,
      reasonCode: null,
      cursor: null,
      limit: REVIEW_PAGE_SIZE,
    },
  });
  const pendingRows = (pendingQuarantine.data?.pages ?? []).flatMap((page) => page.items);
  const pendingCount = pendingRows.length;
  const pendingOverflow = Boolean(pendingQuarantine.data?.pages.at(-1)?.nextCursor);
  const hasPendingQuarantine = pendingCount > 0;
  const mine = useAllGrades(
    cohortView ? null : (selectedMembership?.course_id ?? null),
    null,
  );

  const cohortFilter = useMemo(
    () => ({
      // 学生视图不需要"课序名单 × 成绩"连接：传 null 让 useCohort 的查询停止，
      // 而不是多打一次与本人进度重复的成绩请求。
      courseId: cohortView ? listFilter.courseId : null,
      sectionId: sectionScope === "" ? null : sectionScope,
      questionVersionIds: null,
    }),
    [cohortView, listFilter.courseId, sectionScope],
  );
  const cohort = useCohort(cohortFilter);

  const listView = (
    <div className="stack">
      {cohortView ? null : <MyMembershipsPanel />}
      <Panel title={cohortView ? t("assignments.tabs.list") : t("assignments.myTitle")}>
        {cohortView ? (
          <Banner tone="info" testId="assignment-course-wide">
            {t("assignments.courseWideAudience")}
          </Banner>
        ) : (
          <p className="muted">{t("assignments.studentHint")}</p>
        )}
        {!cohortView && currentUser.isPending ? (
          <Spinner label={t("assignments.membershipLoading")} />
        ) : !cohortView && currentUser.isError ? (
          <ErrorState error={currentUser.error} testId="my-memberships-error" />
        ) : !cohortView && selectedMembership === null ? (
          <EmptyState>{t("assignments.noMemberships")}</EmptyState>
        ) : assignments.isPending ? (
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
      {!cohortView && mine.isError ? <ErrorState error={mine.error} /> : null}
    </div>
  );

  const teacherFilterPanel = (
    <Panel title={t("grades.filter")}>
      <div className="form-grid">
        <Field label={t("grades.course")} htmlFor="assignment-course">
          <Select
            id="assignment-course"
            value={selectedCourseId}
            onChange={(event) => {
              setCourseId(event.target.value);
              setSectionId("");
            }}
          >
            {courseOptions.map((course) => (
              <option key={course.course_id} value={course.course_id}>
                {courseLabel(course)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t("assignments.section")} htmlFor="assignment-section">
          <Select
            id="assignment-section"
            value={selectedSectionId}
            onChange={(event) => setSectionId(event.target.value)}
            disabled={selectedCourseId === ""}
          >
            {sectionOptions.length === 0 ? (
              <option value="">{t("roster.emptyNoSection")}</option>
            ) : null}
            {sectionOptions.map((option, index) => (
              <option key={option.sectionId} value={option.sectionId}>
                {index + 1} · {option.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {courses.isError ? <ErrorState error={courses.error} /> : null}
      {sections.isError ? <ErrorState error={sections.error} /> : null}
    </Panel>
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
                course_id: listFilter.courseId ?? undefined,
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

      {cohortView ? (
        <>
          {teacherFilterPanel}
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
                  sectionScope === "" ? (
                    <Panel title={t("assignments.progressTitle")}>
                      <EmptyState>{t("assignments.progressNeedsSection")}</EmptyState>
                    </Panel>
                  ) : (
                    <TeacherProgress assignments={assignments.assignments} cohort={cohort} />
                  ),
              },
            ]}
          />
        </>
      ) : (
        <>
          {memberships.length > 1 ? (
            <Panel title={t("assignments.courseSwitch")} testId="student-course-switch">
              <Field label={t("grades.course")} htmlFor="student-course">
                <Select
                  id="student-course"
                  value={selectedMembership?.course_id ?? ""}
                  onChange={(event) => setStudentCourseId(event.target.value)}
                >
                  {memberships.map((membership) => (
                    <option key={membership.course_id} value={membership.course_id}>
                      {membership.course_title} · {membership.section_code} ·{" "}
                      {membership.class_name}
                    </option>
                  ))}
                </Select>
              </Field>
            </Panel>
          ) : null}
          {listView}
        </>
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
