import { useMemo, useState } from "react";
import { t } from "../../i18n";
import type { GradeRow } from "../../api/types";
import { isApiError } from "../../api/problem";
import { loadedCourses, useCourses, useSections } from "../../api/directory";
import { useSession } from "../../auth/sessionContext";
import { courseLabel as formatCourseLabel } from "../../api/directory";
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
import { sortRowsForDisplay, scoreDistribution, summarizeLoadedRows } from "./aggregate";
import { GradeDrilldown } from "./GradeDrilldown";
import { GradesChart } from "./GradesChart";
import type { EChartsLoader } from "./echartsLoader";
import { SubmissionMatrix } from "./SubmissionMatrix";
import { downloadGradesExport, loadedGrades, useGrades, type ExportFormat } from "./useGrades";
import { ManualGradeDrawer } from "./ManualGradeDrawer";

const STATE_TONE: Record<GradeRow["state"], "ok" | "warn" | "bad" | "idle"> = {
  received: "idle",
  validated: "idle",
  queued: "warn",
  running: "warn",
  completed: "ok",
  rejected: "warn",
  failed: "bad",
};

/** 两个视图共用同一组筛选条件：成绩明细与「谁还没交」必须看同一个作用域。 */
type GradesTab = "detail" | "missing";

export function GradesPage({ chartLoader }: { chartLoader?: EChartsLoader } = {}) {
  const session = useSession();
  const courses = useCourses();
  const courseOptions = loadedCourses(courses.data?.pages);
  const [courseId, setCourseId] = useState("");
  const [sectionId, setSectionId] = useState("");
  const [questionVersionId, setQuestionVersionId] = useState("");
  const [format, setFormat] = useState<ExportFormat>("xlsx");
  const [drilldownRunId, setDrilldownRunId] = useState<string | null>(null);
  const [exportError, setExportError] = useState<unknown>(null);
  const [exportMessage, setExportMessage] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [tab, setTab] = useState<GradesTab>("detail");
  // 打分抽屉只记哪一次提交，其余状态由抽屉自己读（含乐观锁需要的 updated_at）。
  const [gradeTarget, setGradeTarget] = useState<string | null>(null);

  // 默认选中第一个可见课程：成绩列表需要一个课程作用域，而多数用户只被授权一门课。
  // 留空会让页面首屏显示"已加载 0 行"，看起来像没有成绩，而不是"还没选课程"。
  const effectiveCourseId = courseId === "" ? (courseOptions[0]?.course_id ?? "") : courseId;

  const sections = useSections(effectiveCourseId === "" ? null : effectiveCourseId);
  const filter = useMemo(
    () => ({
      courseId: effectiveCourseId === "" ? null : effectiveCourseId,
      sectionId: sectionId === "" ? null : sectionId,
      questionVersionId: questionVersionId === "" ? null : questionVersionId,
    }),
    [effectiveCourseId, sectionId, questionVersionId],
  );
  const grades = useGrades(filter);
  const loaded = useMemo(() => loadedGrades(grades.data?.pages), [grades.data]);
  const rows = useMemo(() => sortRowsForDisplay(loaded), [loaded]);
  const summary = useMemo(() => summarizeLoadedRows(rows), [rows]);
  const buckets = useMemo(() => scoreDistribution(rows), [rows]);

  const teacherView = canViewTeacherEvidence(session);
  const cohortView = canViewCohortGrades(session);

  const detailView = (
    <>
      <Panel
        title={t("grades.summary", { count: summary.rowCount })}
        actions={
          canExportGrades(session) ? (
            <div className="inline">
              <Field label={t("grades.exportFormat")} htmlFor="export-format">
                <select
                  id="export-format"
                  value={format}
                  onChange={(event) => setFormat(event.target.value as ExportFormat)}
                >
                  <option value="xlsx">XLSX</option>
                  <option value="csv">CSV</option>
                </select>
              </Field>
              <button
                type="button"
                className="button"
                data-testid="export-grades"
                disabled={effectiveCourseId === "" || exporting}
                onClick={async () => {
                  setExportError(null);
                  setExportMessage(null);
                  setExporting(true);
                  const requestId = newRequestId();
                  try {
                    const result = await downloadGradesExport(filter, format);
                    log({
                      event: "grades_export_triggered",
                      request_id: requestId,
                      format,
                      filename: result.filename,
                    });
                    setExportMessage(
                      `${t("grades.exportTriggered")} ${result.filename}（${result.bytes} B）`,
                    );
                  } catch (error) {
                    setExportError(error);
                  } finally {
                    setExporting(false);
                  }
                }}
              >
                {exporting ? t("grades.exporting") : t("grades.export")}
              </button>
            </div>
          ) : null
        }
      >
        <ul>
          <li>
            {t("grades.summarySubmissions")}：{summary.rowCount}
          </li>
          <li>
            {t("grades.summaryAverage")}：
            {summary.averageScore === null ? t("grades.notScored") : summary.averageScore}
          </li>
          <li>
            {t("grades.summaryPassRate")}：
            {summary.passRate === null ? t("grades.notScored") : `${Math.round(summary.passRate * 100)}%`}
          </li>
          <li>
            {t("grades.unfinished")}：{summary.unfinishedCount}
          </li>
        </ul>
        <Banner tone="warn" testId="summary-scope-notice">
          {t("grades.summaryScopeNotice")}
        </Banner>
        <GradesChart buckets={buckets} loader={chartLoader} />
        <p className="muted">{t("grades.distribution")}</p>
        {exportMessage ? <Banner tone="success">{exportMessage}</Banner> : null}
        {exportError ? (
          <Banner tone="error" testId="export-error">
            {t("grades.exportFailed", {
              reason: isApiError(exportError) && exportError.isForbidden
                ? t("grades.exportForbidden")
                : String(exportError),
            })}
          </Banner>
        ) : null}
        <p className="muted">{t("grades.exportNoRebuild")}</p>
      </Panel>

      <Panel
        title={t("grades.countLoaded", { count: rows.length })}
        actions={
          <LoadMoreActions
            loadedCount={rows.length}
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
          <p className="muted">{t("grades.course")}</p>
        ) : grades.isPending ? (
          <Spinner />
        ) : grades.isError ? (
          <ErrorState error={grades.error} />
        ) : (
          <Table<GradeRow>
            testId="grades-table"
            label={t("grades.countLoaded", { count: rows.length })}
            rows={rows}
            getRowKey={(row, index) => `${row.submission_id}-${index}`}
            columns={[
              {
                key: "student_number",
                // B2 的 `student_number` / `display_name` **恒为 null**（B2 只拥有 `b2`，
                // 姓名与学号属 B4；见 B2/grades/query.py 的说明）。因此这一列的表头是
                // "学生"而不是"学号"：回退值 `student_id` 是平台用户 ID，把它显示在
                // "学号"表头下就是在给数据贴错标签。
                header: t("grades.student"),
                // 行序由 `sortRowsForDisplay` 决定（学号为主键、`student_id` 为次键）：
                // 声明出来，读屏用户才知道这个顺序是有意义的。
                sort: "ascending",
                render: (row) =>
                  row.student_number ?? (
                    <span className="mono-id" title={`学生 ID：${row.student_id}`}>
                      {shortenId(row.student_id)}
                    </span>
                  ),
              },
              {
                key: "display_name",
                header: t("grades.displayName"),
                render: (row) => row.display_name ?? t("common.none"),
              },
              {
                key: "state",
                header: t("grades.state"),
                render: (row) => (
                  <StatusPill tone={STATE_TONE[row.state]}>
                    {t(`grades.state.${row.state}`)}
                  </StatusPill>
                ),
              },
              {
                key: "score",
                header: t("grades.score"),
                align: "right",
                // 未完成的行 score 为 null：显示状态，不显示 0 分。
                render: (row) => {
                  // awaiting_manual 既不是 0 分也不是“未完成”：它表示“等老师打分”。
                  if (row.grade_status === "awaiting_manual") {
                    return (
                      <span className="muted" data-testid="awaiting-grade">
                        {t("grades.gradeStatus.awaiting_manual")}
                      </span>
                    );
                  }
                  return typeof row.score === "number" ? (
                    row.score
                  ) : (
                    <span className="muted" data-testid="unfinished-score">
                      {t("grades.unfinished")}
                    </span>
                  );
                },
              },
              {
                key: "max_score",
                header: t("grades.maxScore"),
                align: "right",
                render: (row) => row.max_score ?? t("common.none"),
              },
              {
                key: "submitted_at",
                header: t("grades.submittedAt"),
                render: (row) => formatDateTime(row.submitted_at, t("common.none")),
              },
              {
                key: "grade_action",
                header: t("grades.gradeAction"),
                render: (row) =>
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
                  ),
              },
              {
                key: "evidence",
                header: t("grades.evidence"),
                render: (row) =>
                  row.evaluation_run_id ? (
                    <button
                      type="button"
                      className="button button--ghost"
                      data-testid={`drilldown-${row.evaluation_run_id}`}
                      onClick={() => setDrilldownRunId(row.evaluation_run_id ?? null)}
                    >
                      {t("grades.evidence")}
                    </button>
                  ) : (
                    <span className="muted">{t("common.none")}</span>
                  ),
              },
            ]}
          />
        )}
        {gradeTarget ? (
          <ManualGradeDrawer submissionId={gradeTarget} onClose={() => setGradeTarget(null)} />
        ) : null}
      </Panel>
    </>
  );

  return (
    <div className="stack">
      <PageTitle>{t("nav.grades")}</PageTitle>

      <Panel title={t("grades.filter")}>
        <div className="form-grid">
          <Field label={t("grades.course")} htmlFor="grades-course">
            <select
              id="grades-course"
              value={effectiveCourseId}
              onChange={(event) => {
                setCourseId(event.target.value);
                setSectionId("");
              }}
            >
              {/* 课程是成绩查询的必需作用域，且默认选中第一门可见课程；留一个空选项
                  只会得到一个永远选不中的条目。 */}
              {courseOptions.map((course) => (
                <option key={course.course_id} value={course.course_id}>
                  {formatCourseLabel(course)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("grades.section")} htmlFor="grades-section">
            <select
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
            </select>
          </Field>
          <Field label={t("grades.questionVersion")} htmlFor="grades-version">
            <input
              id="grades-version"
              value={questionVersionId}
              onChange={(event) => setQuestionVersionId(event.target.value)}
            />
          </Field>
        </div>
        <div className="inline">
          <span className="muted" data-testid="grades-courses-count">
            {t("common.coursesLoaded", { count: courseOptions.length })}
          </span>
          <LoadMoreActions
            loadedCount={courseOptions.length}
            hasNextPage={courses.hasNextPage}
            isFetchingNextPage={courses.isFetchingNextPage}
            onLoadMore={() => {
              void courses.fetchNextPage();
            }}
            testId="courses"
          />
        </div>

        <Banner tone="info" testId="unfinished-notice">
          {t("grades.stateNotice")}
        </Banner>
        {courses.isError ? <ErrorState error={courses.error} /> : null}
        {sections.isError ? <ErrorState error={sections.error} /> : null}
      </Panel>

      {cohortView ? (
        <Tabs
          label={t("grades.tabs.label")}
          testId="grades-tabs"
          activeId={tab}
          onChange={(id) => setTab(id as GradesTab)}
          tabs={[
            { id: "detail", label: t("grades.tabs.detail"), content: detailView },
            {
              id: "missing",
              label: t("grades.tabs.missing"),
              content: (
                <SubmissionMatrix
                  filter={{
                    courseId: filter.courseId,
                    sectionId: filter.sectionId,
                    // 契约的 `listGrades` 只接受单个 `question_version_id`；
                    // 空筛选 = 该课程/课序下的全部提交。
                    questionVersionIds:
                      filter.questionVersionId === null ? null : [filter.questionVersionId],
                  }}
                />
              ),
            },
          ]}
        />
      ) : (
        detailView
      )}

      {drilldownRunId ? (
        <Modal
          title={t("grades.drilldownTitle")}
          testId="drilldown-modal"
          onClose={() => setDrilldownRunId(null)}
        >
          <GradeDrilldown runId={drilldownRunId} view={teacherView ? "teacher" : "student"} />
        </Modal>
      ) : null}
    </div>
  );
}
