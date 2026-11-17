import { useEffect, useMemo, useState } from "react";
import { t } from "../../i18n";
import type { GradeRow } from "../../api/types";
import { describeApiError, isApiError } from "../../api/problem";
import { loadedCourses, useCourses, useSections } from "../../api/directory";
import { courseLabel as formatCourseLabel } from "../../api/directory";
import { useLoadAllPages } from "../../api/pagination";
import { useSession } from "../../auth/sessionContext";
import {
  canExportGrades,
  canGradeSubmission,
  canViewCohortGrades,
  canViewTeacherEvidence,
} from "../../auth/visibility";
import {
  Banner,
  ErrorState,
  Field,
  LoadMoreActions,
  Modal,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
  Table,
  Tabs,
} from "../../ui/components";
import { log, newRequestId } from "../../logging/logger";
import { formatDateTime, shortenId } from "../../ui/format";
import { scoreDistribution, summarizeLoadedRows } from "./aggregate";
import { buildCohort } from "./cohort";
import { GradeDrilldown } from "./GradeDrilldown";
import { GradesChart } from "./GradesChart";
import type { EChartsLoader } from "./echartsLoader";
import { SubmissionMatrix } from "./SubmissionMatrix";
import {
  downloadGradesExport,
  loadedGrades,
  useGrades,
  type ExportFormat,
  type GradeFilter,
} from "./useGrades";
import { ManualGradeDrawer } from "./ManualGradeDrawer";
import { useGradeIdentities, identityKey, type GradeIdentity } from "./useGradeIdentities";
import { useQuestionVersionOptions } from "./useQuestionVersionOptions";
import { localInputToUtc, rangeProblem, type TimeRangeProblem } from "./timeRange";
import {
  OPTIONAL_EXPORT_FIELDS,
  optionalExportFieldLabel,
  type OptionalExportField,
} from "./exportColumns";
import { Select } from "../../ui/Select";
import { useQuestionVersionRows } from "../questions/useQuestions";

/**
 * 成绩页（P11；RQ-B-1-30 成绩与证据 + RQ-B-1-60 导出）。
 *
 * 四条读法上的纪律：
 * 1. **未完成 ≠ 0 分**：`score` 为 null 的行只显示状态；平均分/通过率只用有权威分的行。
 * 2. **汇总只在数据完整时出现**：游标分页会自动取完；被截断、请求失败或仍在加载时显示
 *    "当前数据不完整，暂不提供全班汇总"，绝不用已加载页冒充全班。
 * 3. **身份关联经 B4 授权名单**：教师表格的学号/姓名按 `(section_id, student_id)` 从
 *    `GET /sections/{id}/enrollments` 关联；映射不到显示"名单中未找到"，不把平台 ID 当学号。
 *    关联只影响**显示**；导出文件仍完全由 B2 生成（同一筛选、同一口径）。
 * 4. **学生只看本人**：没有班级/姓名/导出入口；表格列与教师不同，且不显示任何他人数据。
 *
 * `initialRunId` 来自 URL（P10 的"查看本次结果"）：打开对应 report；关闭时清掉 URL。
 * 它**只决定打开哪一份报告**，不再参与列表筛选——否则打开详情会把明细表收窄成一行，
 * 页面高度骤减、滚动被夹回顶部（B1 上报的滚动 bug）。
 */

const STATE_TONE: Record<GradeRow["state"], "ok" | "warn" | "bad" | "idle"> = {
  received: "idle",
  validated: "idle",
  queued: "warn",
  running: "warn",
  completed: "ok",
  rejected: "warn",
  failed: "bad",
};

type GradesTab = "detail" | "missing";
type DisplayRow = { row: GradeRow; identity: GradeIdentity | null };

const TIME_PROBLEM_KEY: Record<TimeRangeProblem, Parameters<typeof t>[0]> = {
  fromInvalid: "grades.timeFromInvalid",
  toInvalid: "grades.timeToInvalid",
  order: "grades.timeOrder",
};

export function GradesPage({
  chartLoader,
  initialRunId = null,
  onRunChange,
}: {
  chartLoader?: EChartsLoader;
  initialRunId?: string | null;
  onRunChange?: (runId: string | null) => void;
} = {}) {
  const session = useSession();
  const teacherView = canViewTeacherEvidence(session);
  const cohortView = canViewCohortGrades(session);
  const exportAllowed = canExportGrades(session) && cohortView;
  const courses = useCourses();
  const courseOptions = loadedCourses(courses.data?.pages);
  const scopeCourse = session.scope[0]?.course_id ?? "";
  const [courseId, setCourseId] = useState("");
  const [sectionId, setSectionId] = useState("");
  const [questionVersionId, setQuestionVersionId] = useState("");
  const [fromInput, setFromInput] = useState("");
  const [toInput, setToInput] = useState("");
  const [drilldownRunId, setDrilldownRunId] = useState<string | null>(initialRunId);
  const [tab, setTab] = useState<GradesTab>("detail");
  const [exportOpen, setExportOpen] = useState(false);
  const [format, setFormat] = useState<ExportFormat>("xlsx");
  const [selectedColumns, setSelectedColumns] = useState<OptionalExportField[]>([]);
  const [exportError, setExportError] = useState<unknown>(null);
  const [exportMessage, setExportMessage] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  // 打分抽屉只记哪一次提交，其余状态由抽屉自己读（含乐观锁需要的 updated_at）。
  const [gradeTarget, setGradeTarget] = useState<string | null>(null);

  const effectiveCourseId = courseId === "" ? (courseOptions[0]?.course_id ?? scopeCourse) : courseId;
  // 学生没有课程/课序/题目筛选，也不需要读课程目录与题库：只对教师侧加载这两份目录。
  const sections = useSections(cohortView && effectiveCourseId !== "" ? effectiveCourseId : null);
  const questionOptions = useQuestionVersionOptions(
    cohortView && effectiveCourseId !== "" ? effectiveCourseId : null,
    cohortView,
  );
  const questionLabels = useMemo(() => {
    const map = new Map<string, string>();
    for (const option of questionOptions.options) {
      map.set(
        option.questionVersionId,
        t("grades.questionOption", { title: option.title, version: option.version }),
      );
    }
    return map;
  }, [questionOptions.options]);

  useEffect(() => {
    setDrilldownRunId(initialRunId);
  }, [initialRunId]);

  const timeProblem = rangeProblem(fromInput, toInput);
  const fromUtc = timeProblem === null ? localInputToUtc(fromInput) : null;
  // 结束日期按当天最后一刻处理：否则 `2026-11-08` 会被当成当天 00:00，当天的提交被排除。
  const toBoundary =
    /^\d{4}[-/]\d{2}[-/]\d{2}$/.test(toInput) ? `${toInput.replace(/\//g, "-")}T23:59:59` : toInput;
  const toUtc = timeProblem === null ? localInputToUtc(toBoundary) : null;
  const activeRunId = drilldownRunId;

  const filter: GradeFilter = useMemo(
    () => ({
      courseId: effectiveCourseId === "" ? null : effectiveCourseId,
      sectionId: sectionId === "" ? null : sectionId,
      questionVersionId: questionVersionId === "" ? null : questionVersionId,
      fromUtc: fromUtc === "" ? null : fromUtc,
      toUtc: toUtc === "" ? null : toUtc,
      // **不把"正在看的这一次"塞进列表筛选**：那会让打开详情时整张明细表被收窄成一行，
      // 页面高度骤减、滚动被夹回顶部（用户报的"点查看详细就跳回最上方"）。
      // 详情本身按 `runId` 单独取报告，列表保持用户当前的筛选范围不变。
      evaluationRunId: null,
    }),
    [effectiveCourseId, sectionId, questionVersionId, fromUtc, toUtc],
  );

  const grades = useGrades(filter, { enabled: timeProblem === null });
  const loaded = useMemo(() => loadedGrades(grades.data?.pages), [grades.data]);
  const { complete, capped } = useLoadAllPages({
    // 只有明细标签页负责自动翻页：两个视图同时自动翻页会让同一个游标被并发推进，
    // 请求数与顺序都会失控（而汇总只需要在明细视图里等全量）。
    hasNextPage: tab === "detail" && grades.hasNextPage,
    isFetchingNextPage: grades.isFetchingNextPage,
    fetchNextPage: grades.fetchNextPage,
    pageCount: grades.data?.pages.length ?? 0,
  });

  const courseSectionIds = useMemo(() => {
    if (filter.sectionId) return [filter.sectionId];
    return (sections.data ?? []).map((option) => option.sectionId);
  }, [filter.sectionId, sections.data]);

  const sectionIds = useMemo(() => {
    const ids = new Set<string>(courseSectionIds);
    for (const row of loaded) {
      if (row.section_id) ids.add(row.section_id);
    }
    return Array.from(ids);
  }, [courseSectionIds, loaded]);
  const identities = useGradeIdentities(sectionIds, cohortView);

  const displayRows = useMemo<DisplayRow[]>(() => {
    const rows = loaded.map((row) => ({
      row,
      identity: cohortView
        ? (identities.bySectionAndStudent.get(
            identityKey(row.section_id ?? "", row.student_id),
          ) ?? null)
        : null,
    }));
    return rows.sort((a, b) => {
      const numberA = a.identity?.studentNumber ?? a.row.student_number ?? "";
      const numberB = b.identity?.studentNumber ?? b.row.student_number ?? "";
      if (numberA !== numberB) return numberA.localeCompare(numberB);
      return a.row.student_id.localeCompare(b.row.student_id);
    });
  }, [loaded, identities.bySectionAndStudent, cohortView]);

  const rowsForSummary = useMemo(() => loaded, [loaded]);
  const summary = useMemo(() => summarizeLoadedRows(rowsForSummary), [rowsForSummary]);
  const rosterForSummary = useMemo(() => {
    if (identities.rosterRows.length === 0) return [];
    if (filter.sectionId) {
      return identities.rosterRows.filter((row) => row.section_id === filter.sectionId);
    }
    if (courseSectionIds.length > 0) {
      const allowed = new Set(courseSectionIds);
      return identities.rosterRows.filter((row) => allowed.has(row.section_id));
    }
    return identities.rosterRows;
  }, [courseSectionIds, filter.sectionId, identities.rosterRows]);

  const { completedCount, unfinishedCount } = useMemo(() => {
    if (!identities.isError && rosterForSummary.length > 0) {
      const cohort = buildCohort(rosterForSummary, rowsForSummary);
      return {
        completedCount: cohort.counts.graded,
        unfinishedCount: cohort.rows.length - cohort.counts.graded,
      };
    }
    return {
      completedCount: Math.max(0, summary.rowCount - summary.unfinishedCount),
      unfinishedCount: summary.unfinishedCount,
    };
  }, [identities.isError, rosterForSummary, rowsForSummary, summary.rowCount, summary.unfinishedCount]);

  const buckets = useMemo(() => scoreDistribution(rowsForSummary), [rowsForSummary]);
  const summaryComplete =
    complete &&
    !capped &&
    !grades.isError &&
    (!identities.isLoading || identities.isError || sectionIds.length === 0);
  const filtersActive =
    sectionId !== "" ||
    questionVersionId !== "" ||
    fromInput !== "" ||
    toInput !== "" ||
    activeRunId !== null;

  const clearFilters = () => {
    setSectionId("");
    setQuestionVersionId("");
    setFromInput("");
    setToInput("");
    if (activeRunId !== null) {
      setDrilldownRunId(null);
      onRunChange?.(null);
    }
  };

  const openRun = (runId: string) => {
    setDrilldownRunId(runId);
    onRunChange?.(runId);
  };
  const closeRun = () => {
    setDrilldownRunId(null);
    onRunChange?.(null);
  };

  // 学生没有题目筛选目录，但成绩行仍应显示题目名称而不是一串 UUID。
  // 这里按成绩行里出现的版本号补取公开版本（含标题），失败时只影响这一列。
  const gradeVersionIds = useMemo(
    () => [...new Set(loaded.map((row) => row.question_version_id))],
    [loaded],
  );
  const gradeVersionRows = useQuestionVersionRows(gradeVersionIds);
  const gradeQuestionTitles = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of gradeVersionRows) {
      if (row.question?.title) map.set(row.questionVersionId, row.question.title);
    }
    return map;
  }, [gradeVersionRows]);

  const questionLabel = (versionId: string) =>
    questionLabels.get(versionId) ??
    gradeQuestionTitles.get(versionId) ??
    t("grades.questionUnavailable");

  const drilldownRow = displayRows.find((entry) => entry.row.evaluation_run_id === drilldownRunId);

  // 学生视图按题目折叠：同一题目的多次提交只留一条（最高分），其余进"历史记录"。
  const [historyQuestionId, setHistoryQuestionId] = useState<string | null>(null);
  const studentGroups = useMemo(() => {
    const groups = new Map<string, DisplayRow[]>();
    for (const entry of displayRows) {
      const key = entry.row.question_version_id;
      const bucket = groups.get(key);
      if (bucket) {
        bucket.push(entry);
      } else {
        groups.set(key, [entry]);
      }
    }
    return groups;
  }, [displayRows]);

  const bestOf = (entries: DisplayRow[]): DisplayRow => {
    // 最高分优先；未评分（score 为 null）视为最低；同分取最近提交。
    const scoreOf = (entry: DisplayRow) =>
      typeof entry.row.score === "number" ? entry.row.score : Number.NEGATIVE_INFINITY;
    return entries.reduce((best, current) => {
      const diff = scoreOf(current) - scoreOf(best);
      if (diff > 0) return current;
      if (diff === 0 && (current.row.submitted_at ?? "") > (best.row.submitted_at ?? "")) {
        return current;
      }
      return best;
    });
  };

  const studentRows = useMemo<DisplayRow[]>(() => {
    const collapsed: DisplayRow[] = [];
    for (const entries of studentGroups.values()) {
      collapsed.push(bestOf(entries));
    }
    return collapsed.sort((left, right) =>
      (right.row.submitted_at ?? "").localeCompare(left.row.submitted_at ?? ""),
    );
  }, [studentGroups]);

  const historyRows =
    historyQuestionId === null ? [] : (studentGroups.get(historyQuestionId) ?? []);


  const evidenceButton = (row: GradeRow) =>
    row.evaluation_run_id ? (
      <button
        type="button"
        className="button button--ghost"
        data-testid={`drilldown-${row.evaluation_run_id}`}
        onClick={() => openRun(row.evaluation_run_id as string)}
      >
        {t("grades.viewDetail")}
      </button>
    ) : (
      <span className="muted">{t("common.none")}</span>
    );

  const stateCell = (row: GradeRow) => (
    <StatusPill tone={STATE_TONE[row.state]}>{t(`grades.state.${row.state}`)}</StatusPill>
  );
  const scoreCell = (row: GradeRow) =>
    row.grade_status === "awaiting_manual" ? (
      <span className="muted" data-testid="awaiting-grade">
        {t("grades.gradeStatus.awaiting_manual")}
      </span>
    ) : typeof row.score === "number" ? (
      <span data-testid="score">
        {row.score} / {row.max_score ?? t("common.none")}
      </span>
    ) : (
      <span className="muted" data-testid="unfinished-score">
        {t("grades.unscored")}
      </span>
    );
  const gradeActionCell = (row: GradeRow) =>
    canGradeSubmission(session) ? (
      <button
        type="button"
        className="button button--ghost"
        data-testid={`grade-${row.submission_id}`}
        onClick={() => setGradeTarget(row.submission_id)}
      >
        {t("grades.gradeAction")}
      </button>
    ) : (
      <span className="muted">
        {t(`grades.gradeStatus.${row.grade_status ?? "deterministic"}`)}
      </span>
    );
  const resultCell = (row: GradeRow) =>
    row.state === "completed" ? (
      row.is_pass === true ? (
        <StatusPill tone="ok">{t("grades.pass")}</StatusPill>
      ) : (
        <StatusPill tone="bad">{t("grades.fail")}</StatusPill>
      )
    ) : (
      <span className="muted">{t("grades.notScored")}</span>
    );

  const detailTable = (
    <Panel
      title={t("grades.countLoaded", { count: displayRows.length })}
      testId="grades-table-panel"
      actions={
        <LoadMoreActions
          loadedCount={displayRows.length}
          hasNextPage={grades.hasNextPage}
          isFetchingNextPage={grades.isFetchingNextPage}
          onLoadMore={() => {
            void grades.fetchNextPage();
          }}
          testId="grades"
        />
      }
    >
      {effectiveCourseId === "" ? (
        <Banner tone="info">{t("grades.noCourse")}</Banner>
      ) : timeProblem !== null ? (
        <Banner tone="warn" testId="grades-time-problem">
          {t(TIME_PROBLEM_KEY[timeProblem])}
        </Banner>
      ) : grades.isPending ? (
        <Spinner />
      ) : grades.isError ? (
        <ErrorState error={grades.error} />
      ) : displayRows.length === 0 ? (
        <div className="stack">
          <p className="muted" data-testid="grades-empty">
            {filtersActive ? t("grades.noMatch") : t("grades.empty")}
          </p>
          {filtersActive ? (
            <button type="button" className="button" data-testid="clear-grades-filters" onClick={clearFilters}>
              {t("grades.clearFilters")}
            </button>
          ) : null}
        </div>
      ) : (
        <Table<DisplayRow>
          testId="grades-table"
          label={t("grades.countLoaded", { count: displayRows.length })}
          rows={cohortView ? displayRows : studentRows}
          getRowKey={(entry) => entry.row.submission_id}
          columns={
            cohortView
              ? [
                  {
                    key: "student_number",
                    header: t("grades.studentNumber"),
                    sort: "ascending",
                    render: (entry) =>
                      entry.identity?.studentNumber ?? (
                        <span className="muted" data-testid="identity-missing">
                          {t("grades.identityMissing")}
                        </span>
                      ),
                  },
                  {
                    key: "display_name",
                    header: t("grades.displayName"),
                    render: (entry) => entry.identity?.displayName ?? t("grades.identityMissing"),
                  },
                  {
                    key: "question",
                    header: t("grades.question"),
                    render: (entry) => questionLabel(entry.row.question_version_id),
                  },
                  {
                    key: "submitted_at",
                    header: t("grades.submittedAt"),
                    render: (entry) => formatDateTime(entry.row.submitted_at, t("common.none")),
                  },
                  { key: "state", header: t("grades.state"), render: (entry) => stateCell(entry.row) },
                  { key: "score", header: t("grades.scoreSlashMax"), align: "right", render: (entry) => scoreCell(entry.row) },
                  { key: "result", header: t("grades.result"), render: (entry) => resultCell(entry.row) },
                  { key: "grade", header: t("grades.gradeAction"), render: (entry) => gradeActionCell(entry.row) },
                  { key: "evidence", header: t("grades.colDetail"), render: (entry) => evidenceButton(entry.row) },
                ]
              : [
                  {
                    key: "question",
                    header: t("grades.question"),
                    render: (entry) =>
                      questionLabel(entry.row.question_version_id),
                  },
                  {
                    key: "submitted_at",
                    header: t("grades.submittedAt"),
                    render: (entry) => formatDateTime(entry.row.submitted_at, t("common.none")),
                  },
                  { key: "state", header: t("grades.state"), render: (entry) => stateCell(entry.row) },
                  { key: "score", header: t("grades.scoreSlashMax"), align: "right", render: (entry) => scoreCell(entry.row) },
                  { key: "result", header: t("grades.result"), render: (entry) => resultCell(entry.row) },
                  {
                    key: "history",
                    header: t("grades.colHistory"),
                    render: (entry) => {
                      const versionId = entry.row.question_version_id;
                      const attempts = studentGroups.get(versionId)?.length ?? 0;
                      if (attempts < 2) {
                        return <span className="muted">{t("common.none")}</span>;
                      }
                      const open = historyQuestionId === versionId;
                      return (
                        <button
                          type="button"
                          className="button button--ghost"
                          data-testid={`history-${versionId}`}
                          aria-expanded={open}
                          onClick={() =>
                            setHistoryQuestionId(open ? null : versionId)
                          }
                        >
                          {open
                            ? t("grades.historyHide")
                            : t("grades.historyShow", { count: attempts })}
                        </button>
                      );
                    },
                  },
                ]
          }
        />
      )}
      {gradeTarget ? (
        <ManualGradeDrawer submissionId={gradeTarget} onClose={() => setGradeTarget(null)} />
      ) : null}
      {!cohortView && historyQuestionId !== null ? (
        <div className="stack" data-testid="grades-history">
          <h3>
            {t("grades.historyTitle")} · {questionLabel(historyQuestionId)}
          </h3>
          {historyRows.length === 0 ? (
            <p className="muted">{t("grades.historyEmpty")}</p>
          ) : (
            <Table<DisplayRow>
              testId="grades-history-table"
              label={t("grades.historyTitle")}
              rows={historyRows}
              getRowKey={(entry) => entry.row.submission_id}
              columns={[
                {
                  key: "submitted_at",
                  header: t("grades.submittedAt"),
                  render: (entry) => formatDateTime(entry.row.submitted_at, t("common.none")),
                },
                { key: "state", header: t("grades.state"), render: (entry) => stateCell(entry.row) },
                {
                  key: "score",
                  header: t("grades.scoreSlashMax"),
                  align: "right",
                  render: (entry) => scoreCell(entry.row),
                },
                {
                  key: "result",
                  header: t("grades.result"),
                  render: (entry) => resultCell(entry.row),
                },
                {
                  key: "evidence",
                  header: t("grades.colDetail"),
                  render: (entry) => evidenceButton(entry.row),
                },
              ]}
            />
          )}
        </div>
      ) : null}
    </Panel>
  );

  const summaryPanel = cohortView ? (
    <Panel title={t("grades.summaryTitle")} testId="grades-summary">
      {activeRunId !== null ? (
        <Banner tone="info" testId="summary-single-run">
          {t("grades.summarySingleRun")}
        </Banner>
      ) : !summaryComplete ? (
        <Banner tone="warn" testId="summary-incomplete">
          {grades.isError
            ? t("grades.summaryError")
            : capped
              ? t("grades.summaryTruncated")
              : t("grades.summaryLoading")}
        </Banner>
      ) : (
        <>
          <dl className="summary-cards" data-testid="grades-summary-cards">
            <div className="summary-card" data-testid="summary-completed">
              <dt>{t("grades.summaryCompleted")}</dt>
              <dd>{t("grades.summaryPeople", { count: completedCount })}</dd>
            </div>
            <div className="summary-card" data-testid="summary-unfinished">
              <dt>{t("grades.unfinished")}</dt>
              <dd>{t("grades.summaryPeople", { count: unfinishedCount })}</dd>
            </div>
            <div className="summary-card" data-testid="summary-average">
              <dt>{t("grades.summaryAverage")}</dt>
              <dd>
                {summary.averageScore === null ? t("grades.notScored") : summary.averageScore}
              </dd>
            </div>
            <div className="summary-card" data-testid="summary-pass-rate">
              <dt>{t("grades.summaryPassRate")}</dt>
              <dd>
                {summary.passRate === null
                  ? t("grades.notScored")
                  : `${Math.round(summary.passRate * 100)}%`}
              </dd>
            </div>
          </dl>
          {displayRows.length > 0 ? (
            <>
              <GradesChart buckets={buckets} loader={chartLoader} />
              <p className="muted">{t("grades.distribution")}</p>
            </>
          ) : null}
        </>
      )}
      {identities.isError ? (
        <Banner tone="warn" testId="identity-roster-error">
          {t("grades.identityRosterError")}
          <button type="button" className="button button--ghost" onClick={identities.retry}>
            {t("common.retry")}
          </button>
        </Banner>
      ) : identities.isLoading ? (
        <p className="muted" data-testid="identity-loading">
          {t("grades.identityLoading")}
        </p>
      ) : null}
    </Panel>
  ) : null;

  return (
    <div className="stack">
      <PageTitle>{t("nav.grades")}</PageTitle>

      <Panel title={t("grades.filterTitle")}>
        <div className="form-grid">
          <Field label={t("grades.course")} htmlFor="grades-course">
            <Select
              id="grades-course"
              value={effectiveCourseId}
              disabled={!cohortView && courseOptions.length === 0}
              onChange={(event) => {
                // 切换课程会清空课序/题目/时间与旧游标：跨课程共用筛选是数据串班的最短路径。
                setCourseId(event.target.value);
                setSectionId("");
                setQuestionVersionId("");
                setFromInput("");
                setToInput("");
              }}
            >
              {courseOptions.length === 0 && effectiveCourseId === "" ? (
                <option value="">{t("grades.noCourse")}</option>
              ) : null}
              {/* 学生不一定能看到课程目录；此时至少显示会话所在的课程作用域，避免出现空白下拉。 */}
              {courseOptions.length === 0 && effectiveCourseId !== "" ? (
                <option value={effectiveCourseId}>{shortenId(effectiveCourseId)}</option>
              ) : null}
              {courseOptions.map((course) => (
                <option key={course.course_id} value={course.course_id}>
                  {formatCourseLabel(course)}
                </option>
              ))}
            </Select>
          </Field>

          {cohortView ? (
            <>
              <Field label={t("grades.section")} htmlFor="grades-section">
                <Select
                  id="grades-section"
                  value={sectionId}
                  onChange={(event) => setSectionId(event.target.value)}
                  disabled={effectiveCourseId === ""}
                >
                  <option value="">{t("common.any")}</option>
                  {(sections.data ?? []).map((section) => (
                    <option key={section.sectionId} value={section.sectionId}>
                      {section.label}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label={t("grades.question")} htmlFor="grades-question">
                <Select
                  id="grades-question"
                  value={questionVersionId}
                  onChange={(event) => setQuestionVersionId(event.target.value)}
                  disabled={effectiveCourseId === ""}
                >
                  <option value="">{t("common.any")}</option>
                  {questionOptions.options.map((option) => (
                    <option key={option.questionVersionId} value={option.questionVersionId}>
                      {t("grades.questionOption", {
                        title: option.title,
                        version: option.version,
                      })}
                      {option.status === "published" ? "" : ` · ${t(`questions.status.${option.status}`)}`}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field
                label={t("grades.from")}
                htmlFor="grades-from"
                error={timeProblem === "fromInvalid" ? t("grades.timeFromInvalid") : null}
              >
                <div style={{ position: "relative" }}>
                  <input
                    id="grades-from"
                    type="text"
                    placeholder="yyyy/mm/dd"
                    value={fromInput}
                    onChange={(event) => setFromInput(event.target.value)}
                  />
                  <input
                    type="date"
                    aria-label={t("grades.selectDate")}
                    value={fromInput}
                    tabIndex={-1}
                    onChange={(event) => setFromInput(event.target.value)}
                    style={{
                      position: "absolute",
                      right: 0,
                      top: 0,
                      width: 36,
                      height: "100%",
                      opacity: 0,
                      cursor: "pointer",
                    }}
                  />
                  <span
                    aria-hidden="true"
                    style={{
                      position: "absolute",
                      right: 10,
                      top: "50%",
                      transform: "translateY(-50%)",
                      pointerEvents: "none",
                      color: "var(--color-text-faint)",
                    }}
                  >
                    📅
                  </span>
                </div>
              </Field>

              <Field
                label={t("grades.to")}
                htmlFor="grades-to"
                error={
                  timeProblem === "toInvalid"
                    ? t("grades.timeToInvalid")
                    : timeProblem === "order"
                      ? t("grades.timeOrder")
                      : null
                }
              >
                <div style={{ position: "relative" }}>
                  <input
                    id="grades-to"
                    type="text"
                    placeholder="yyyy/mm/dd"
                    value={toInput}
                    onChange={(event) => setToInput(event.target.value)}
                  />
                  <input
                    type="date"
                    aria-label={t("grades.selectDate")}
                    value={toInput}
                    tabIndex={-1}
                    onChange={(event) => setToInput(event.target.value)}
                    style={{
                      position: "absolute",
                      right: 0,
                      top: 0,
                      width: 36,
                      height: "100%",
                      opacity: 0,
                      cursor: "pointer",
                    }}
                  />
                  <span
                    aria-hidden="true"
                    style={{
                      position: "absolute",
                      right: 10,
                      top: "50%",
                      transform: "translateY(-50%)",
                      pointerEvents: "none",
                      color: "var(--color-text-faint)",
                    }}
                  >
                    📅
                  </span>
                </div>
              </Field>
            </>
          ) : null}
        </div>

        {exportAllowed ? (
          <div className="inline">
            <button
              type="button"
              className="button"
              data-testid="open-export"
              disabled={effectiveCourseId === "" || timeProblem !== null}
              onClick={() => {
                setExportError(null);
                setExportMessage(null);
                setExportOpen(true);
              }}
            >
              {t("grades.export")}
            </button>
            <button type="button" className="button button--ghost" onClick={clearFilters}>
              {t("grades.clearFilters")}
            </button>
          </div>
        ) : null}

        {courseOptions.length === 0 && courses.isError ? <ErrorState error={courses.error} /> : null}
        {sections.isError ? <ErrorState error={sections.error} /> : null}
        {questionOptions.isError ? <ErrorState error={questionOptions.error} /> : null}
        {questionOptions.hasNextPage ? (
          <LoadMoreActions
            loadedCount={questionOptions.options.length}
            hasNextPage={questionOptions.hasNextPage}
            isFetchingNextPage={questionOptions.isFetchingNextPage}
            onLoadMore={() => {
              void questionOptions.fetchNextPage();
            }}
            testId="question-options"
          />
        ) : null}

        {activeRunId ? (
          <Banner tone="info" testId="single-run-notice">
            {t("grades.singleRunNotice", { run: shortenId(activeRunId) })}
            <button type="button" className="button button--ghost" onClick={closeRun}>
              {t("grades.showAll")}
            </button>
          </Banner>
        ) : null}

      </Panel>

      {summaryPanel}

      {cohortView ? (
        <Tabs
          label={t("grades.tabs.label")}
          testId="grades-tabs"
          activeId={tab}
          onChange={(id) => setTab(id as GradesTab)}
          tabs={[
            { id: "detail", label: t("grades.tabs.detail"), content: detailTable },
            {
              id: "missing",
              label: t("grades.tabs.missing"),
              content: (
                <SubmissionMatrix
                  filter={{
                    courseId: filter.courseId,
                    sectionId: filter.sectionId,
                    sectionIds: courseSectionIds,
                    questionVersionIds:
                      filter.questionVersionId === null ? null : [filter.questionVersionId],
                  }}
                />
              ),
            },
          ]}
        />
      ) : (
        detailTable
      )}

      {exportOpen ? (
        <Modal title={t("grades.exportTitle")} testId="export-drawer" onClose={() => setExportOpen(false)}>
          <div className="stack">
            <Field label={t("grades.exportFormat")} htmlFor="export-format">
              <Select
                id="export-format"
                value={format}
                onChange={(event) => setFormat(event.target.value as ExportFormat)}
              >
                <option value="xlsx">XLSX</option>
                <option value="csv">CSV</option>
              </Select>
            </Field>

            <div>
              <h3>{t("grades.exportRange")}</h3>
              <p className="muted" data-testid="export-range">
                {fromInput === "" && toInput === ""
                  ? t("grades.exportAllTime")
                  : t("grades.exportRangeSummary", {
                      from: fromInput === "" ? t("grades.scopeUndecided") : fromInput,
                      to: toInput === "" ? t("grades.scopeUndecided") : toInput,
                    })}
              </p>
            </div>

            <div>
              <h3>{t("grades.exportRequired")}</h3>
              <ul className="muted" data-testid="export-required-columns">
                {[
                  "studentNumber",
                  "displayName",
                  "course",
                  "section",
                  "question",
                  "score",
                  "maxScore",
                  "submittedAt",
                  "evaluationRun",
                ].map((key) => (
                  <li key={key}>
                    <label>
                      <input type="checkbox" checked disabled readOnly />{" "}
                      {t(`grades.exportColumn.${key}` as Parameters<typeof t>[0])}
                    </label>
                  </li>
                ))}
              </ul>
            </div>

            <div>
              <h3>{t("grades.exportOptional")}</h3>
              {OPTIONAL_EXPORT_FIELDS.map((field) => (
                <label key={field} className="inline">
                  <input
                    type="checkbox"
                    data-testid={`export-column-${field}`}
                    checked={selectedColumns.includes(field)}
                    onChange={(event) => {
                      setSelectedColumns((previous) =>
                        event.target.checked
                          ? [...previous, field]
                          : previous.filter((item) => item !== field),
                      );
                    }}
                  />
                  {t(optionalExportFieldLabel(field))}
                </label>
              ))}
            </div>

            <div className="inline">
              <button
                type="button"
                className="button"
                data-testid="download-export"
                disabled={exporting || timeProblem !== null}
                onClick={async () => {
                  setExportError(null);
                  setExportMessage(null);
                  setExporting(true);
                  const requestId = newRequestId();
                  try {
                    const result = await downloadGradesExport(
                      filter,
                      format,
                      selectedColumns,
                    );
                    log({
                      event: "grades_export_downloaded",
                      request_id: requestId,
                      format,
                      columns: selectedColumns,
                    });
                    setExportMessage(t("grades.exportDownloaded", { filename: result.filename }));
                    setExportOpen(false);
                  } catch (error) {
                    setExportError(error);
                  } finally {
                    setExporting(false);
                  }
                }}
              >
                {exporting ? t("grades.exporting") : t("grades.exportDownload")}
              </button>
              <button type="button" className="button button--ghost" onClick={() => setExportOpen(false)}>
                {t("common.cancel")}
              </button>
            </div>

            {exportError ? (
              <Banner tone="error" testId="export-error">
                {t("grades.exportFailed", {
                  reason:
                    isApiError(exportError) && exportError.isForbidden
                      ? t("grades.exportForbidden")
                      : isApiError(exportError) && exportError.status === 409
                        ? t("grades.exportIdentityMissing")
                        : isApiError(exportError) && exportError.requestId
                          ? `${describeApiError(exportError)}（request_id ${exportError.requestId}）`
                          : describeApiError(exportError),
                })}
              </Banner>
            ) : null}
            {timeProblem !== null ? (
              <Banner tone="warn">{t(TIME_PROBLEM_KEY[timeProblem])}</Banner>
            ) : null}
          </div>
        </Modal>
      ) : null}

      {exportMessage ? (
        <Banner tone="success" testId="export-success">
          {exportMessage}
        </Banner>
      ) : null}

      {drilldownRunId ? (
        <Modal title={t("grades.drilldownTitle")} testId="drilldown-modal" onClose={closeRun}>
          <GradeDrilldown
            runId={drilldownRunId}
            view={teacherView ? "teacher" : "student"}
            runState={drilldownRow?.row.state}
            questionVersionId={drilldownRow?.row.question_version_id}
          />
        </Modal>
      ) : null}
    </div>
  );
}
