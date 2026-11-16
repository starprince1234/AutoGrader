import { useMemo, useState } from "react";
import { t } from "../../i18n";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import { formatDateTime } from "../../ui/format";
import {
  Banner,
  EmptyState,
  ErrorState,
  Field,
  Panel,
  Spinner,
  StatusPill,
  Table,
} from "../../ui/components";
import { cohortStatusLabel, cohortStatusTone, type CohortStatus } from "./cohort";
import { useCohort, type CohortFilter } from "./useCohort";
import { Select } from "../../ui/Select";

/**
 * 「谁还没交」（RQ-B-1-30 验收 4 的操作化形态）。
 *
 * 教师的真实人工工作是**催交**，而这需要把两个已冻结端点**连接**起来：
 * - 名单：`GET /sections/{section_id}/enrollments`（B4，`EnrollmentPublic`）——"这门课序应该有谁"；
 * - 成绩：`GET /grades?course_id&section_id`（B2，`GradeRow`）——"谁真的提交了"。
 *
 * 连接逻辑与"取完全部页再判定"的纪律都在 `cohort.ts` / `useCohort.ts` 里；作业中心的
 * 「提交情况」复用同一套，不重复实现连接。
 *
 * 连接键是 `student_id`：成绩行的 `student_id` 是平台用户 ID（B2 只拥有 `b2`，其
 * `student_number`/`display_name` 恒为 null，因此**不能**用学号连接）；名单行的
 * `student_id` 可空（`b4.enrollments.user_id` 可空），未绑定的行如实显示"未绑定账号"，
 * 既不算成"未提交"，也不拿学号或 `enrollment_id` 冒充用户 ID。
 */

const INCOMPLETE_STATUS_ORDER: readonly CohortStatus[] = [
  "not_submitted",
  "unbound",
  "running",
  "failed",
  "rejected",
];

function filterLabel(status: CohortStatus | "all" | "incomplete", count: number): string {
  if (status === "incomplete") return t("grades.missing.filterIncomplete", { count });
  if (status === "all") return t("grades.missing.filterAll", { count });
  if (status === "not_submitted") return t("grades.missing.filterNotSubmitted", { count });
  if (status === "unbound") return t("grades.missing.filterUnbound", { count });
  if (status === "running") return t("grades.missing.filterRunning", { count });
  if (status === "failed") return t("grades.missing.filterFailed", { count });
  if (status === "rejected") return t("grades.missing.filterRejected", { count });
  return t("grades.missing.filterGraded", { count });
}

export function SubmissionMatrix({ filter }: { filter: CohortFilter }) {
  const cohort = useCohort(filter);
  const [statusFilter, setStatusFilter] = useState<CohortStatus | "all" | "incomplete">(
    "incomplete",
  );

  const visible = useMemo(() => {
    if (statusFilter === "all") return cohort.rows;
    if (statusFilter === "incomplete") {
      return cohort.rows.filter((row) => row.status !== "graded");
    }
    return cohort.rows.filter((row) => row.status === statusFilter);
  }, [cohort.rows, statusFilter]);

  if (filter.sectionId === null && (!filter.sectionIds || filter.sectionIds.length === 0)) {
    return (
      <Panel title={t("grades.missing.title")}>
        <EmptyState>{t("grades.missing.needsSection")}</EmptyState>
      </Panel>
    );
  }

  return (
    <Panel
      title={t("grades.missing.title")}
      actions={
        !cohort.incomplete && cohort.rows.length > 0 ? (
          <span className="muted" data-testid="cohort-end">
            {t("common.endOfList")}
          </span>
        ) : undefined
      }
    >
      {cohort.error ? <ErrorState error={cohort.error} /> : null}

      {cohort.isPending ? (
        <>
          <Spinner />
          <Banner tone="info" testId="cohort-loading-all">
            {t("grades.missing.loadingAll")}
          </Banner>
        </>
      ) : cohort.truncated ? (
        // 到自动翻页上限是**永久状态**：这里绝不能渲染 spinner，
        // 否则用户看到的是一个永远不会结束的"加载中"，而真相是分页已经停了。
        <Banner tone="warn" testId="cohort-truncated">
          {t("common.paginationTruncated", { pages: MAX_AUTO_PAGES })}{" "}
          {t("grades.missing.truncated")}
        </Banner>
      ) : cohort.incomplete ? (
        <>
          <Spinner />
          <Banner tone="info" testId="cohort-loading-all">
            {t("grades.missing.loadingAll")}
          </Banner>
        </>
      ) : (
        (() => {
          const incompleteCount = cohort.rows.length - cohort.counts.graded;
          const detail = INCOMPLETE_STATUS_ORDER.filter((status) => cohort.counts[status] > 0)
            .map((status) => `${cohortStatusLabel(status)} ${cohort.counts[status]}`)
            .join("，");
          const summaryText = detail
            ? t("grades.missing.summaryWithDetail", {
                total: cohort.rows.length,
                graded: cohort.counts.graded,
                incomplete: incompleteCount,
                detail,
              })
            : t("grades.missing.summarySimple", {
                total: cohort.rows.length,
                graded: cohort.counts.graded,
                incomplete: incompleteCount,
              });

          return (
            <>
              <Banner tone="info" testId="cohort-summary">
                {summaryText}
              </Banner>
              {cohort.outsideRoster > 0 ? (
                <Banner tone="warn" testId="cohort-outside-roster">
                  {t("grades.missing.outsideRoster", { count: cohort.outsideRoster })}
                </Banner>
              ) : null}

              <Field label={t("grades.missing.filterLabel")} htmlFor="cohort-status">
                <Select
                  id="cohort-status"
                  value={statusFilter}
                  onChange={(event) =>
                    setStatusFilter(event.target.value as CohortStatus | "all" | "incomplete")
                  }
                >
                  <option value="incomplete">
                    {filterLabel("incomplete", incompleteCount)}
                  </option>
                  <option value="graded">{filterLabel("graded", cohort.counts.graded)}</option>
                  <option value="all">{filterLabel("all", cohort.rows.length)}</option>
                  {INCOMPLETE_STATUS_ORDER.filter(
                    (status) => cohort.counts[status] > 0 || status === statusFilter,
                  ).map((status) => (
                    <option key={status} value={status}>
                      {filterLabel(status, cohort.counts[status])}
                    </option>
                  ))}
                </Select>
              </Field>

          {visible.length === 0 ? (
            <EmptyState />
          ) : (
            <Table
              testId="cohort-table"
              label={t("grades.missing.title")}
              rows={visible}
              getRowKey={(row) => row.enrollmentId}
              columns={[
                {
                  key: "student_number",
                  header: t("roster.studentNumber"),
                  render: (row) => row.studentNumber,
                },
                {
                  key: "display_name",
                  header: t("roster.displayName"),
                  render: (row) => row.displayName,
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
                  key: "score",
                  header: t("grades.score"),
                  align: "right",
                  // 与成绩页同一立场：未完成/未提交都不是 0 分，得分列不出现数字。
                  render: (row) =>
                    typeof row.grade?.score === "number" ? (
                      row.grade.score
                    ) : (
                      <span className="muted" data-testid="cohort-unscored">
                        —
                      </span>
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
          )}
            </>
          );
        })()
      )}
    </Panel>
  );
}
