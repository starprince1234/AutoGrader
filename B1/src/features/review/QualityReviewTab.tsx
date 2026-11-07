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
import { DEMO_COURSE, DEMO_VERSION_OPTIONS, demoQualitySummary } from "./demoData";
import { DemoHint } from "./DemoHint";
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

function systemFailureCount(summary: EvaluationQualitySummary): number {
  const infrastructure =
    summary.failure_categories.find((item) => item.category === "infrastructure_error")?.count ??
    0;
  return infrastructure + summary.unmeasured_runs;
}

/** 后端与前端滚动发布时旧响应可能还没有该字段：退回提交总次数，避免卡片空白。 */
function answererCount(summary: EvaluationQualitySummary): number {
  return summary.answerer_count ?? summary.attempt_count;
}

function CountCard({
  label,
  value,
  foot,
  testId,
  tone = "default",
  marked = false,
}: {
  label: string;
  value: number;
  foot: string;
  testId: string;
  tone?: "default" | "warn" | "bad";
  marked?: boolean;
}) {
  const background =
    tone === "bad"
      ? "var(--color-bad-soft)"
      : tone === "warn" || marked
        ? "var(--color-warn-soft)"
        : "var(--color-surface-2)";
  const border =
    tone === "bad"
      ? "var(--color-bad)"
      : tone === "warn" || marked
        ? "var(--color-warn)"
        : "var(--color-border)";
  return (
    <div
      className="count-card"
      data-testid={testId}
      style={{ background, borderColor: border }}
    >
      <span className="muted">{label}</span>
      <p className="count-card__value" data-testid={`${testId}-value`}>
        {value}
      </p>
      <span className="muted">{foot}</span>
    </div>
  );
}

function CaseTable({ summary }: { summary: EvaluationQualitySummary }) {
  const rows = summary.case_failure_rates
    .filter((row) => row.attempts > 0)
    .sort((left, right) => {
      const byRate = right.failures / right.attempts - left.failures / left.attempts;
      return byRate !== 0 ? byRate : left.ordinal - right.ordinal;
    });
  if (rows.length === 0) {
    return (
      <p className="muted" data-testid="review-quality-no-case-failures">
        {t("review.quality.noCaseRecords")}
      </p>
    );
  }
  return (
    <div data-testid="review-quality-cases">
      <h3>{t("review.quality.caseRates")}</h3>
      <Table
        testId="review-quality-cases-table"
        label={t("review.quality.caseRates")}
        rows={rows}
        getRowKey={(row) => String(row.ordinal)}
        columns={[
          {
            key: "name",
            header: t("review.quality.caseName"),
            render: (row) => t("review.quality.caseNameValue", { ordinal: row.ordinal + 1 }),
          },
          {
            key: "rate",
            header: t("review.quality.failureRate"),
            render: (row) => `${Math.round((row.failures / row.attempts) * 100)}%`,
          },
          {
            key: "passFail",
            header: t("review.quality.passFailHeader"),
            render: (row) => `${row.attempts - row.failures} / ${row.failures}`,
          },
        ]}
      />
    </div>
  );
}

function AbnormalSummary({ summary }: { summary: EvaluationQualitySummary }) {
  const parts = summary.failure_categories
    .filter((item) => item.count > 0)
    .map((item) => `${categoryLabel(item.category)}：${item.count}次`);
  if (summary.security_found > 0) {
    parts.push(`安全告警：${summary.security_found}次`);
  }
  if (summary.unmeasured_runs > 0) {
    parts.push(`资源丢失：${summary.unmeasured_runs}次`);
  }
  return (
    <div data-testid="review-quality-abnormal">
      <h3>{t("review.quality.abnormal")}</h3>
      {parts.length > 0 ? (
        <p data-testid="review-quality-abnormal-text">{parts.join(" ｜ ")}</p>
      ) : (
        <p className="muted" data-testid="review-quality-abnormal-none">
          {t("review.quality.abnormalNone")}
        </p>
      )}
    </div>
  );
}

function QualityBody({ summary }: { summary: EvaluationQualitySummary }) {
  const systemFailures = systemFailureCount(summary);
  return (
    <div className="stack" data-testid="review-quality-summary">
      <div
        className="count-grid"
        data-testid="review-quality-counts"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}
      >
        <CountCard
          label={t("review.quality.answerers")}
          value={answererCount(summary)}
          foot={t("review.quality.answerersFoot")}
          testId="review-quality-answerers"
          marked={answererCount(summary) < 5}
        />
        <CountCard
          label={t("review.quality.attemptsTotal")}
          value={summary.attempt_count}
          foot={t("review.quality.attemptsFoot")}
          testId="review-quality-attempts"
        />
        <CountCard
          label={t("review.quality.systemFailures")}
          value={systemFailures}
          foot={t("review.quality.systemFailuresFoot")}
          testId="review-quality-system"
          tone={systemFailures > 0 ? "warn" : "default"}
        />
        <CountCard
          label={t("review.quality.securityRisk")}
          value={summary.security_found}
          foot={t("review.quality.securityRiskFoot")}
          testId="review-quality-security"
          tone={summary.security_found > 0 ? "bad" : "default"}
        />
        <CountCard
          label={t("review.quality.unchecked")}
          value={summary.security_not_checked}
          foot={t("review.quality.uncheckedFoot")}
          testId="review-quality-unchecked"
        />
      </div>

      <CaseTable summary={summary} />
      <AbnormalSummary summary={summary} />
    </div>
  );
}

/**
 * 评测汇总面板：真实数据与示例数据走同一段渲染。
 *
 * 示例模式的说明只出现在页头那一条横幅里：同一句话在面板里再写一遍，读者会以为自己看漏了什么。
 */
function SummaryPanel({ summary }: { summary: EvaluationQualitySummary }) {
  return (
    <Panel title={t("review.quality.result")} testId="review-quality-panel">
      {summary.attempt_count === 0 ? (
        <Banner tone="info" testId="review-quality-empty">
          {t("review.quality.empty")}
        </Banner>
      ) : (
        <>
          {systemFailureCount(summary) > 0 || summary.security_found > 0 ? (
            <Banner tone="warn" testId="review-quality-hint">
              {t("review.quality.anomalyHint")}
            </Banner>
          ) : summary.answerer_count !== undefined && answererCount(summary) < 5 ? (
            <Banner tone="warn" testId="review-quality-hint">
              {t("review.quality.smallSampleHint")}
            </Banner>
          ) : null}
          <QualityBody summary={summary} />
        </>
      )}
    </Panel>
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
  const demo = search.demo === "1";
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

  // 示例模式不读课程目录：样例自带课程名与版本，没登录、没后端也能完整浏览。
  const courses = useCourses({ enabled: !demo });
  const isAdmin = session.rolesKnown && session.roles.includes("admin");
  // 与邮件标签同一口径：下拉只展示令牌作用域覆盖的课程；服务端仍会拒绝越权版本。
  const scopeCourseIds = new Set(session.scope.map((entry) => entry.course_id));
  const courseOptions = demo
    ? [DEMO_COURSE]
    : loadedCourses(courses.data?.pages).filter(
        (course) => isAdmin || scopeCourseIds.has(course.course_id),
      );
  const versionOptions = useVersionOptions(demo ? null : filter.courseId);
  const versionChoices = demo ? DEMO_VERSION_OPTIONS : versionOptions.options;

  /** 示例模式只给一门课、两个版本：下拉与"查询"要有值可用，不能停在"请选择"。 */
  const effectiveCourseId = demo
    ? (filter.courseId === DEMO_COURSE.course_id ? filter.courseId : DEMO_COURSE.course_id)
    : filter.courseId;
  // 从题目详情点进示例时，URL 里是**真实**版本 id：拿它算示例聚合只会得到"暂无评测记录"。
  // 示例模式只认示例自己的版本，其余一律回落到第一个示例版本。
  const demoVersionIds = new Set(
    versionChoices.map((option) => option.questionVersionId),
  );
  const effectiveVersionId = demo
    ? filter.questionVersionId !== null && demoVersionIds.has(filter.questionVersionId)
      ? filter.questionVersionId
      : (versionChoices[0]?.questionVersionId ?? null)
    : filter.questionVersionId;

  const invalidVersion = filter.questionVersionId !== null && !isUuid(filter.questionVersionId);
  const context = useVersionContext(
    demo || invalidVersion || filter.questionVersionId === null
      ? null
      : filter.questionVersionId,
  );
  // 示例模式不发 B2 聚合请求：数字由 `demoData` 里的记录算出，时间范围真的会过滤。
  const summaryEnabled =
    !demo && filter.questionVersionId !== null && !invalidVersion && context.version.isSuccess;
  const summary = useQualitySummary({
    identity,
    filter,
    enabled: summaryEnabled,
  });
  const demoSummary =
    demo && effectiveVersionId !== null
      ? demoQualitySummary(effectiveVersionId, filter.from, filter.to)
      : null;

  const apply = () => {
    if (effectiveVersionId === null) return;
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
        courseId: effectiveCourseId,
        questionVersionId: effectiveVersionId,
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
        courseId: effectiveCourseId,
        questionVersionId: effectiveVersionId,
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

      {demo ? (
        <Banner tone="info" testId="review-demo-banner">
          <p>{t("review.demoBanner")}</p>
          <p className="muted">{t("review.demoQualityNote")}</p>
        </Banner>
      ) : null}

      <Panel title={t("review.quality.filter")}>
        <div className="stack">
          <div className="form-grid">
            <Field label={t("review.quality.course")} htmlFor="review-quality-course">
              <Select
                id="review-quality-course"
                data-testid="review-quality-course"
                value={effectiveCourseId ?? ""}
                onChange={(event) => {
                  const nextCourse = event.target.value === "" ? null : event.target.value;
                  onChange(
                    withQualityFilter(search, {
                      courseId: nextCourse,
                      // 换课程后旧版本必然不属于新课程；清空而不是留下一个会 403 的版本。
                      questionVersionId:
                        nextCourse === effectiveCourseId ? filter.questionVersionId : null,
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
                value={effectiveVersionId ?? ""}
                disabled={!demo && filter.courseId === null}
                onChange={(event) => {
                  const next = event.target.value === "" ? null : event.target.value;
                  onChange(
                    withQualityFilter(search, {
                      courseId: effectiveCourseId,
                      questionVersionId: next,
                      from: filter.from,
                      to: filter.to,
                    }),
                  );
                }}
              >
                <option value="">{t("common.selectPrompt")}</option>
                {filter.questionVersionId !== null &&
                !invalidVersion &&
                !versionChoices.some(
                  (option) => option.questionVersionId === filter.questionVersionId,
                ) ? (
                  // 深链带来的版本可能不在列表里（未发布/超出当前页）：仍然要显示出来，
                  // 否则选择器会退成"请选择"，而下面的汇总却已经有数字。
                  <option value={filter.questionVersionId}>
                    {context.label ?? filter.questionVersionId}
                  </option>
                ) : null}
                {versionChoices.map((option) => (
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
            {!demo && versionOptions.query.hasNextPage ? (
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

      {demo ? (
        demoSummary ? <SummaryPanel summary={demoSummary} /> : null
      ) : filter.questionVersionId === null ? (
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
        <SummaryPanel summary={summary.data} />
      ) : null}

      {/* 没有版本 / 没有评测记录是最常见的首次访问：空态要给一条"可以看示例"的路。 */}
      {!demo && (filter.questionVersionId === null || summary.data?.attempt_count === 0) ? (
        <DemoHint
          testId="review-quality-empty-demo"
          onShow={() => onChange({ ...search, demo: "1" })}
        />
      ) : null}
    </div>
  );
}
