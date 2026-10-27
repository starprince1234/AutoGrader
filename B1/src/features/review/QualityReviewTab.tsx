import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { EvaluationQualitySummary } from "../../api/types";
import { loadedCourses, useCourses } from "../../api/directory";
import { useSession } from "../../auth/sessionContext";
import {
  Banner,
  EmptyState,
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
  SMALL_SAMPLE_THRESHOLD,
  isSmallSample,
  toContractTimestamp,
  toLocalInputValue,
  useQualitySummary,
} from "./useReviewQuality";
import { readQualityFilter, withQualityFilter, type ReviewSearch } from "./search";

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

function CountCard({
  label,
  value,
  testId,
  tone = "idle",
}: {
  label: string;
  value: number;
  testId: string;
  tone?: "ok" | "warn" | "bad" | "idle";
}) {
  return (
    <div className="count-card" data-testid={testId}>
      <StatusPill tone={tone}>{label}</StatusPill>
      <p className="count-card__value" data-testid={`${testId}-value`}>
        {value}
      </p>
    </div>
  );
}

function CaseTable({ summary }: { summary: EvaluationQualitySummary }) {
  return (
    <div data-testid="review-quality-cases">
      <h3>{t("review.quality.caseRates")}</h3>
      {summary.case_failure_rates.length === 0 ? (
        <EmptyState>{t("review.quality.noCases")}</EmptyState>
      ) : (
        <Table
          testId="review-quality-cases-table"
          label={t("review.quality.caseRates")}
          rows={summary.case_failure_rates}
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
      )}
    </div>
  );
}

function CategoryTables({ summary }: { summary: EvaluationQualitySummary }) {
  const system = summary.failure_categories.filter(
    (item) => item.category === "infrastructure_error",
  );
  const others = summary.failure_categories.filter(
    (item) => item.category !== "infrastructure_error",
  );
  return (
    <div data-testid="review-quality-categories">
      <h3>{t("review.quality.categories")}</h3>
      <div className="count-grid">
        <CountCard
          label={t("review.quality.categorySystem")}
          value={system.reduce((total, item) => total + item.count, 0)}
          testId="review-quality-category-system"
          tone="warn"
        />
      </div>
      <p className="muted">{t("review.quality.categorySystemHint")}</p>
      {others.length === 0 ? (
        <EmptyState>{t("review.quality.noOtherCategories")}</EmptyState>
      ) : (
        <Table
          testId="review-quality-category-table"
          label={t("review.quality.categories")}
          rows={others}
          getRowKey={(row) => row.category}
          columns={[
            { key: "category", header: t("review.quality.category"), render: (row) => row.category },
            { key: "count", header: t("review.quality.hits"), render: (row) => row.count },
          ]}
        />
      )}
    </div>
  );
}

function QualityBody({ summary }: { summary: EvaluationQualitySummary }) {
  return (
    <div className="stack" data-testid="review-quality-summary">
      <div className="count-grid" data-testid="review-quality-counts">
        <CountCard
          label={t("review.quality.sampleSize")}
          value={summary.attempt_count}
          testId="review-quality-attempts"
        />
        <CountCard
          label={t("review.quality.unmeasured")}
          value={summary.unmeasured_runs}
          testId="review-quality-unmeasured"
          tone="warn"
        />
        <CountCard
          label={t("review.quality.securityFound")}
          value={summary.security_found}
          testId="review-quality-security-found"
          tone={summary.security_found > 0 ? "bad" : "idle"}
        />
        <CountCard
          label={t("review.quality.securityNotFound")}
          value={summary.security_not_found}
          testId="review-quality-security-not-found"
          tone="ok"
        />
        <CountCard
          label={t("review.quality.securityNotChecked")}
          value={summary.security_not_checked}
          testId="review-quality-security-not-checked"
          tone="warn"
        />
      </div>
      <p className="muted">{t("review.quality.attemptScope")}</p>
      <p className="muted" data-testid="review-quality-counts-only">
        {t("review.quality.countsOnly")}
      </p>

      {/* 0 次运行已经有"没有评测记录"的说明：再叠一句"样本较少"会把两件事混成一件。 */}
      {summary.attempt_count > 0 && isSmallSample(summary.attempt_count) ? (
        <Banner tone="warn" testId="review-quality-small-sample">
          {t("review.quality.smallSample")}
          {t("review.quality.smallSampleAid", { threshold: SMALL_SAMPLE_THRESHOLD })}
        </Banner>
      ) : null}

      <CaseTable summary={summary} />

      <div data-testid="review-quality-security">
        <h3>{t("review.quality.security")}</h3>
        <div className="count-grid">
          <p className="muted">{t("review.quality.securityFoundMeaning")}</p>
          <p className="muted">{t("review.quality.securityNotFoundMeaning")}</p>
          <p className="muted">{t("review.quality.securityNotCheckedMeaning")}</p>
        </div>
        <p className="muted">{t("review.quality.securityCountsAtTop")}</p>
      </div>

      <div data-testid="review-quality-resources">
        <h3>{t("review.quality.resources")}</h3>
        <p className="muted">{t("review.quality.resourceMeaning")}</p>
        <p className="muted">{t("review.quality.unmeasuredCountsAtTop")}</p>
      </div>

      <CategoryTables summary={summary} />

      <div data-testid="review-quality-rules">
        <h3>{t("review.quality.rules")}</h3>
        <p className="muted">{t("review.quality.rulesNote")}</p>
        {summary.static_rule_hits.length === 0 ? (
          <EmptyState>{t("review.quality.noRules")}</EmptyState>
        ) : (
          <Table
            testId="review-quality-rules-table"
            label={t("review.quality.rules")}
            rows={summary.static_rule_hits}
            getRowKey={(row) => row.rule_id}
            columns={[
              { key: "rule_id", header: t("review.quality.ruleId"), render: (row) => row.rule_id },
              {
                key: "severity",
                header: t("review.quality.severity"),
                render: (row) => row.severity,
              },
              { key: "count", header: t("review.quality.hits"), render: (row) => row.count },
            ]}
          />
        )}
      </div>
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
  const [courseId, setCourseId] = useState("");
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
  const courseOptions = loadedCourses(courses.data?.pages);
  const versionOptions = useVersionOptions(courseId === "" ? null : courseId);

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
        <div className="form-grid">
          <Field label={t("review.quality.course")} htmlFor="review-quality-course">
            <select
              id="review-quality-course"
              data-testid="review-quality-course"
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
            >
              <option value="">{t("common.selectPrompt")}</option>
              {courseOptions.map((course) => (
                <option key={course.course_id} value={course.course_id}>
                  {course.course_code} · {course.title}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label={t("review.quality.version")}
            htmlFor="review-quality-version"
            hint={t("review.quality.versionHint")}
          >
            <select
              id="review-quality-version"
              data-testid="review-quality-version"
              value={filter.questionVersionId ?? ""}
              disabled={courseId === ""}
              onChange={(event) => {
                const next = event.target.value === "" ? null : event.target.value;
                onChange(
                  withQualityFilter(search, {
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
            </select>
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

        <div className="inline">
          <button type="button" className="button" data-testid="review-quality-apply" onClick={apply}>
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
          {context.questionId ? (
            <p>
              <Link
                to="/questions/$questionId"
                params={{ questionId: context.questionId }}
                search={{ version: filter.questionVersionId ?? undefined }}
                data-testid="review-quality-back"
              >
                {t("review.quality.back")}
              </Link>
            </p>
          ) : null}
        </Panel>
      ) : null}
    </div>
  );
}
