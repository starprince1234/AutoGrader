import { useEffect, useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { EvaluationQualitySummary } from "../../api/types";
import { courseLabel as formatCourseLabel, loadedCourses, useCourses } from "../../api/directory";
import { useSession } from "../../auth/sessionContext";
import {
  Banner,
  ErrorState,
  Field,
  LoadMoreActions,
  Panel,
  Spinner,
  StatusPill,
  Table,
} from "../../ui/components";
import { isUuid } from "../../ui/format";
import { useVersionContext, useVersionOptions } from "./useQualityVersions";
import {
  toContractTimestamp,
  toLocalInputValue,
  useQualitySummary,
} from "./useReviewQuality";
import { readQualityFilter, withQualityFilter, type ReviewSearch } from "./search";
import { Select } from "../../ui/Select";

/**
 * P13-B：题目质量标签（去标识化聚合）。
 *
 * 页面边界（任务书点名）：
 *
 * - 只读 B2 `GET /evaluation-runs/quality-summary` 的计数；**不**显示学生、提交、源码、
 *   期望输出，也不提供任何个人下钻。
 * - 版本来自 P07 的 `question_version_id`（或课程 → 题目版本选择），不要求教师手填 UUID；
 *   深链时先经 B3 `GET /question-versions/{id}` 取标题/版本（B3 对无权版本 403），
 *   **B3 拒绝就不向 B2 发聚合请求**。
 * - 三态安全结论、`unmeasured_runs`、`infrastructure_error` 各自独立；`null` 不转 0；
 *   `attempt_count = 0` 只说明"没有评测记录"，不给健康/排名结论。
 * - 顶部固定五元计数；正文的"安全检查/资源测量"是**口径说明**，不再重复同一计数
 *   （同一数字画两遍会被读成两个指标）。
 */

/** 契约分类是机器词；老师看到的是"为什么错"，不是英文枚举。 */
const CATEGORY_LABELS: Record<string, string> = {
  timeout: "运行超时",
  crash: "程序崩溃",
  output_mismatch: "输出结果不一致",
  resource_limit: "超出资源限制",
  missing_output: "没有产生预期文件",
  import_error: "依赖或导入错误",
  security_blocked: "触发安全检查",
  infrastructure_error: "系统/运行环境问题",
  internal_error: "系统内部错误",
};

function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? "其他失败";
}

function severityLabel(severity: string): string {
  if (severity === "warning") return "警告";
  if (severity === "error") return "错误";
  return "提示";
}

function CaseTable({ summary }: { summary: EvaluationQualitySummary }) {
  const failed = summary.case_failure_rates.filter((row) => row.failures > 0);
  if (failed.length === 0) {
    return (
      <p className="muted" data-testid="review-quality-no-case-failures">
        {t("review.quality.noCaseFailures")}
      </p>
    );
  }
  return (
    <div data-testid="review-quality-cases">
      <h3>{t("review.quality.caseRates")}</h3>
      <Table
        testId="review-quality-cases-table"
        label={t("review.quality.caseRates")}
        rows={failed}
        getRowKey={(row) => String(row.ordinal)}
        columns={[
          { key: "ordinal", header: t("review.quality.caseOrdinal"), render: (row) => row.ordinal },
          { key: "attempts", header: t("review.quality.attempts"), render: (row) => row.attempts },
          { key: "failures", header: t("review.quality.failures"), render: (row) => row.failures },
          {
            key: "rate",
            header: t("review.quality.failureRate"),
            render: (row) =>
              row.attempts === 0
                ? t("review.quality.noAttempts")
                : `${Math.round((row.failures / row.attempts) * 100)}%（${row.failures}/${row.attempts}）`,
          },
        ]}
      />
    </div>
  );
}

function OtherCauses({ summary }: { summary: EvaluationQualitySummary }) {
  const system = summary.failure_categories.filter(
    (item) => item.category === "infrastructure_error",
  );
  const others = summary.failure_categories.filter(
    (item) => item.category !== "infrastructure_error",
  );
  const systemCount = system.reduce((total, item) => total + item.count, 0);
  if (systemCount === 0 && others.length === 0) return null;
  return (
    <div data-testid="review-quality-categories">
      <h3>{t("review.quality.categories")}</h3>
      {systemCount > 0 ? (
        <p className="muted" data-testid="review-quality-category-system">
          {t("review.quality.systemCauses", { count: systemCount })}
        </p>
      ) : null}
      {others.length > 0 ? (
        <Table
          testId="review-quality-category-table"
          label={t("review.quality.categories")}
          rows={others}
          getRowKey={(row) => row.category}
          columns={[
            { key: "category", header: t("review.quality.category"), render: (row) => categoryLabel(row.category) },
            { key: "count", header: t("review.quality.hits"), render: (row) => row.count },
          ]}
        />
      ) : null}
    </div>
  );
}

function QualityBody({ summary }: { summary: EvaluationQualitySummary }) {
  const ruleHits = summary.static_rule_hits.filter((row) => row.count > 0);
  return (
    <div className="stack" data-testid="review-quality-summary">
      <div className="inline" data-testid="review-quality-counts">
        <StatusPill tone="idle">
          {t("review.quality.attemptCount", { count: summary.attempt_count })}
        </StatusPill>
        {summary.unmeasured_runs > 0 ? (
          <StatusPill tone="warn" data-testid="review-quality-unmeasured">
            {t("review.quality.unmeasuredCount", { count: summary.unmeasured_runs })}
          </StatusPill>
        ) : null}
        {summary.security_found > 0 ? (
          <StatusPill tone="bad" data-testid="review-quality-security-found">
            {t("review.quality.securityFoundCount", { count: summary.security_found })}
          </StatusPill>
        ) : null}
      </div>

      <CaseTable summary={summary} />
      <OtherCauses summary={summary} />

      {ruleHits.length > 0 ? (
        <div data-testid="review-quality-rules">
          <h3>{t("review.quality.rules")}</h3>
          <Table
            testId="review-quality-rules-table"
            label={t("review.quality.rules")}
            rows={ruleHits}
            getRowKey={(row) => row.rule_id}
            columns={[
              { key: "rule_id", header: t("review.quality.ruleId"), render: (row) => row.rule_id },
              {
                key: "severity",
                header: t("review.quality.severity"),
                render: (row) => severityLabel(row.severity),
              },
              { key: "count", header: t("review.quality.hits"), render: (row) => row.count },
            ]}
          />
        </div>
      ) : null}
    </div>
  );
}

export function QualityReviewTab({
  search,
  onChange,
}: {
  search: ReviewSearch;
  onChange: (next: ReviewSearch) => void;
}) {
  const session = useSession();
  const identity = session.subject ?? session.username ?? "anonymous";
  const filter = readQualityFilter(search);
  const [fromDraft, setFromDraft] = useState(toLocalInputValue(filter.from));
  const [toDraft, setToDraft] = useState(toLocalInputValue(filter.to));
  // 时间校验的错误位置：结束时间早于开始时间要指到结束时间字段。
  const [issue, setIssue] = useState<"invalidTime" | "endBeforeStart" | null>(null);

  // 深链/外部导航后，输入框必须与 URL 一致（否则显示的筛选与查询用的不是同一套）。
  useEffect(() => {
    setFromDraft(toLocalInputValue(filter.from));
    setToDraft(toLocalInputValue(filter.to));
    setIssue(null);
  }, [filter.from, filter.to]);

  const courses = useCourses();
  const isAdmin = session.rolesKnown && session.roles.includes("admin");
  // 与邮件标签同一口径：下拉只展示令牌作用域覆盖的课程；服务端仍会拒绝越权版本。
  const scopeCourseIds = new Set(session.scope.map((entry) => entry.course_id));
  const courseOptions = loadedCourses(courses.data?.pages).filter(
    (course) => isAdmin || scopeCourseIds.has(course.course_id),
  );
  const versionOptions = useVersionOptions(filter.courseId);

  const invalidVersion = filter.questionVersionId !== null && !isUuid(filter.questionVersionId);
  const context = useVersionContext(
    filter.questionVersionId !== null && !invalidVersion ? filter.questionVersionId : null,
  );
  const summaryEnabled =
    filter.questionVersionId !== null && !invalidVersion && context.version.isSuccess;
  const summary = useQualitySummary({
    identity,
    filter,
    enabled: summaryEnabled,
  });

  const apply = () => {
    if (filter.questionVersionId === null) return;
    const fromValue = toContractTimestamp(fromDraft);
    if (fromDraft.trim() !== "" && fromValue === null) {
      setIssue("invalidTime");
      return;
    }
    const toValue = toContractTimestamp(toDraft);
    if (toDraft.trim() !== "" && toValue === null) {
      setIssue("invalidTime");
      return;
    }
    if (
      fromValue !== null &&
      toValue !== null &&
      new Date(fromValue).getTime() >= new Date(toValue).getTime()
    ) {
      setIssue("endBeforeStart");
      return;
    }
    setIssue(null);
    onChange(
      withQualityFilter(search, {
        courseId: filter.courseId,
        questionVersionId: filter.questionVersionId,
        from: fromValue,
        to: toValue,
      }),
    );
  };

  const clearTime = () => {
    setIssue(null);
    setFromDraft("");
    setToDraft("");
    onChange(
      withQualityFilter(search, {
        courseId: filter.courseId,
        questionVersionId: filter.questionVersionId,
        from: null,
        to: null,
      }),
    );
  };

  const versionError = context.version.error;
  const versionForbidden = isApiError(versionError) && versionError.isForbidden;
  const versionNotFound = isApiError(versionError) && versionError.isNotFound;
  const summaryForbidden = isApiError(summary.error) && summary.error.isForbidden;

  return (
    <div className="stack">
      <h2>{t("review.quality.title")}</h2>

      <Panel title={t("review.quality.filter")}>
        <div className="stack">
          <div className="form-grid">
            <Field label={t("review.quality.course")} htmlFor="review-quality-course">
              <Select
                id="review-quality-course"
                data-testid="review-quality-course"
                value={filter.courseId ?? ""}
                onChange={(event) => {
                  const nextCourse = event.target.value === "" ? null : event.target.value;
                  onChange(
                    withQualityFilter(search, {
                      courseId: nextCourse,
                      // 换课程后旧版本必然不属于新课程；清空而不是留下一个会 403 的版本。
                      questionVersionId: null,
                      from: filter.from,
                      to: filter.to,
                    }),
                  );
                }}
              >
                <option value="">{t("common.selectPrompt")}</option>
                {courseOptions.map((course) => (
                  <option key={course.course_id} value={course.course_id}>
                    {formatCourseLabel(course)}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label={t("review.quality.version")} htmlFor="review-quality-version">
              <Select
                id="review-quality-version"
                data-testid="review-quality-version"
                value={filter.questionVersionId ?? ""}
                disabled={filter.courseId === null}
                onChange={(event) => {
                  const next = event.target.value === "" ? null : event.target.value;
                  onChange(
                    withQualityFilter(search, {
                      courseId: filter.courseId,
                      questionVersionId: next,
                      from: filter.from,
                      to: filter.to,
                    }),
                  );
                }}
              >
                <option value="">{t("common.selectPrompt")}</option>
                {filter.questionVersionId !== null && !invalidVersion ? (
                  <option value={filter.questionVersionId}>
                    {context.label ?? filter.questionVersionId}
                  </option>
                ) : null}
                {versionOptions.options
                  .filter((option) => option.questionVersionId !== filter.questionVersionId)
                  .map((option) => (
                    <option key={option.questionVersionId} value={option.questionVersionId}>
                      {option.label}
                    </option>
                  ))}
              </Select>
            </Field>

            <Field
              label={t("review.quality.from")}
              htmlFor="review-quality-from"
              error={issue === "invalidTime" ? t("review.quality.invalidTime") : null}
            >
              <input
                id="review-quality-from"
                data-testid="review-quality-from"
                type="datetime-local"
                value={fromDraft}
                onChange={(event) => setFromDraft(event.target.value)}
              />
            </Field>

            <Field
              label={t("review.quality.to")}
              htmlFor="review-quality-to"
              error={issue === "endBeforeStart" ? t("review.quality.endBeforeStart") : null}
            >
              <input
                id="review-quality-to"
                data-testid="review-quality-to"
                type="datetime-local"
                value={toDraft}
                onChange={(event) => setToDraft(event.target.value)}
              />
            </Field>
          </div>

          <div className="row-actions">
            <button
              type="button"
              className="button"
              data-testid="review-quality-apply"
              onClick={apply}
            >
              {t("review.quality.apply")}
            </button>
            {filter.from !== null || filter.to !== null ? (
              <button
                type="button"
                className="button button--ghost"
                data-testid="review-quality-clear-time"
                onClick={clearTime}
              >
                {t("review.quality.clearTime")}
              </button>
            ) : null}
            {versionOptions.query.hasNextPage ? (
              <LoadMoreActions
                loadedCount={versionOptions.options.length}
                hasNextPage={versionOptions.query.hasNextPage}
                isFetchingNextPage={versionOptions.query.isFetchingNextPage}
                onLoadMore={() => {
                  void versionOptions.query.fetchNextPage();
                }}
                testId="review-quality-versions"
              />
            ) : null}
          </div>
          {courses.isError ? <ErrorState error={courses.error} /> : null}
          {versionOptions.query.isError ? <ErrorState error={versionOptions.query.error} /> : null}
          <p className="muted">{t("review.quality.rangeNote")}</p>
        </div>
      </Panel>

      {invalidVersion ? (
        <Banner tone="warn" testId="review-quality-invalid-version">
          {t("review.quality.invalidVersion")}
        </Banner>
      ) : null}

      {filter.questionVersionId === null ? (
        <Banner tone="info" testId="review-quality-no-version">
          {t("review.quality.noVersion")}
        </Banner>
      ) : versionForbidden ? (
        <Banner tone="error" testId="review-quality-forbidden">
          {t("review.quality.forbidden")}
        </Banner>
      ) : versionNotFound ? (
        <Banner tone="warn" testId="review-quality-version-not-found">
          {t("review.quality.versionNotFound")}
        </Banner>
      ) : context.version.isError ? (
        <ErrorState error={context.version.error} testId="review-quality-version-error" />
      ) : context.version.isPending && !invalidVersion ? (
        <Spinner />
      ) : summaryForbidden ? (
        <Banner tone="error" testId="review-quality-summary-forbidden">
          {t("review.quality.forbidden")}
        </Banner>
      ) : summary.isError ? (
        <>
          <Banner tone="error" testId="review-quality-unavailable">
            {t("review.quality.unavailable")}
          </Banner>
          <ErrorState error={summary.error} testId="review-quality-error" />
        </>
      ) : summary.isPending && summaryEnabled ? (
        <Spinner />
      ) : summary.data ? (
        <Panel title={t("review.quality.result")} testId="review-quality-panel">
          {summary.data.attempt_count === 0 ? (
            <Banner tone="info" testId="review-quality-empty">
              {t("review.quality.empty")}
            </Banner>
          ) : null}
          <QualityBody summary={summary.data} />
        </Panel>
      ) : null}
    </div>
  );
}
