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
import { formatDateTime, isUuid } from "../../ui/format";
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

/** 示例模式只给一门课、两个版本：下拉与"查询"要有值可用，不能停在"请选择"。 */
const DEMO_COURSE_ID_VALUE = DEMO_COURSE.course_id;
const DEMO_FIRST_VERSION_ID = DEMO_VERSION_OPTIONS[0]?.questionVersionId ?? "";
const DEMO_VERSION_IDS = new Set(
  DEMO_VERSION_OPTIONS.map((option) => option.questionVersionId),
);

function draftCourse(demo: boolean, urlCourseId: string | null): string {
  return demo ? DEMO_COURSE_ID_VALUE : (urlCourseId ?? "");
}

/**
 * 草稿版本：URL 里的真实版本不属于示例，示例模式一律回落到示例版本——
 * 否则"从题目详情点进示例"会拿着真实版本去算示例数字，结果还是空的。
 */
function draftVersion(demo: boolean, urlVersionId: string | null): string {
  if (!demo) return urlVersionId ?? "";
  return urlVersionId !== null && DEMO_VERSION_IDS.has(urlVersionId)
    ? urlVersionId
    : DEMO_FIRST_VERSION_ID;
}

/*
 * P13-B：题目质量标签（去标识化聚合）。
 *
 * 这个页面回答**一个**问题：这道题值不值得改。因此交互只有一条主线：
 *
 * - 总览入口（`/review?tab=quality`）：选课程 → 选版本 → 点「查询」才出数字；
 *   只换下拉不查询时，旧汇总会收起来，避免把上一次的结果当成新条件的结果。
 * - 题目详情入口（`origin=question`）：直接显示这道题的汇总，并且**没有**课程/版本下拉，
 *   只能看这道题（时间范围仍可调）。
 * - 只读 B2 `GET /evaluation-runs/quality-summary` 的计数；不显示学生、提交、源码、期望输出，
 *   也不提供任何个人下钻。`attempt_count = 0` 只说明"没有评测记录"，不给健康/排名结论。
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

/**
 * 异常汇总：一行一类，竖着排。
 *
 * 用「｜」把五类异常挤成一行时，老师要逐段数着读，最严重的那个反而不显眼；竖排之后
 * "哪种问题占了多少次"一眼就能对上。
 */
function AbnormalSummary({ summary }: { summary: EvaluationQualitySummary }) {
  const parts = summary.failure_categories
    .filter((item) => item.count > 0)
    .map((item) => ({ key: item.category, text: `${categoryLabel(item.category)}：${item.count} 次` }));
  if (summary.security_found > 0) {
    parts.push({ key: "security", text: `安全告警：${summary.security_found} 次` });
  }
  if (summary.unmeasured_runs > 0) {
    parts.push({ key: "unmeasured", text: `资源用量丢失：${summary.unmeasured_runs} 次` });
  }
  return (
    <div data-testid="review-quality-abnormal">
      <h3>{t("review.quality.abnormal")}</h3>
      {parts.length > 0 ? (
        <ul
          data-testid="review-quality-abnormal-text"
          style={{ margin: 0, paddingLeft: 20, lineHeight: 1.9 }}
        >
          {parts.map((part) => (
            <li key={part.key}>{part.text}</li>
          ))}
        </ul>
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
 * 第一行永远是"统计范围"：同一道题换个时间范围数字就会变，不写清范围，老师只会觉得
 * "这两个数怎么对不上"。
 */
function SummaryPanel({
  summary,
  from,
  to,
}: {
  summary: EvaluationQualitySummary;
  from: string | null;
  to: string | null;
}) {
  const fromLabel = from === null ? null : formatDateTime(from, t("common.none"));
  const toLabel = to === null ? null : formatDateTime(to, t("common.none"));
  const scope =
    fromLabel === null && toLabel === null
      ? t("review.quality.scopeAll")
      : t("review.quality.scopeRange", {
          from: fromLabel ?? t("review.quality.scopeOpenStart"),
          to: toLabel ?? t("review.quality.scopeOpenEnd"),
        });
  return (
    <Panel title={t("review.quality.result")} testId="review-quality-panel">
      <p className="muted" data-testid="review-quality-scope">
        {scope}
      </p>
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

/** 复核工作区的第二个标签：见文件头部的交互说明（查询后才出数字 / 深链只看这道题）。 */
export function QualityReviewTab({
  search,
  onChange,
}: {
  search: ReviewSearch;
  onChange: (next: ReviewSearch) => void;
}) {
  const session = useSession();
  const demo = search.demo === "1";
  /**
   * 从题目详情点进来（`origin=question`）：直接显示这道题的汇总，并且**只**能看这道题——
   * 课程/版本下拉不再出现，避免老师以为可以在这里换成别的题。
   *
   * 示例模式例外：示例里的数字是编的，不能顶着某个真实题目说"正在看这道题"，因此示例
   * 一律走总览视图（可换示例版本），并且同样要点「查询」。
   */
  const fromQuestion = search.origin === "question" && !demo;
  const identity = session.subject ?? session.username ?? "anonymous";
  const filter = readQualityFilter(search);

  // 总览页的三项条件先落草稿：点「查询」之前不写 URL、也不发聚合请求。
  const [courseDraft, setCourseDraft] = useState(() => draftCourse(demo, filter.courseId));
  const [versionDraft, setVersionDraft] = useState(() =>
    draftVersion(demo, filter.questionVersionId),
  );
  const [fromDraft, setFromDraft] = useState(toLocalInputValue(filter.from));
  const [toDraft, setToDraft] = useState(toLocalInputValue(filter.to));
  // 时间校验的错误位置：结束时间早于开始时间要指到结束时间字段。
  const [issue, setIssue] = useState<"invalidTime" | "endBeforeStart" | null>(null);
  // 「清除全部」之后不再自动回填示例课程/版本：老师要的是回到空白页，不是回到预填页。
  const [cleared, setCleared] = useState(false);

  // 深链 / 刷新 / 前进后退：草稿跟着 URL 走——URL 里那一份才是"已经查过"的条件。
  useEffect(() => {
    setCourseDraft(cleared ? (filter.courseId ?? "") : draftCourse(demo, filter.courseId));
    setVersionDraft(
      cleared ? (filter.questionVersionId ?? "") : draftVersion(demo, filter.questionVersionId),
    );
    setFromDraft(toLocalInputValue(filter.from));
    setToDraft(toLocalInputValue(filter.to));
    setIssue(null);
  }, [cleared, demo, filter.courseId, filter.from, filter.questionVersionId, filter.to]);

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
  // 版本列表跟着**草稿**里的课程走：先选课再选题，点「查询」之后才取数字。
  const versionOptions = useVersionOptions(demo || courseDraft === "" ? null : courseDraft);
  const versionChoices = demo ? DEMO_VERSION_OPTIONS : versionOptions.options;

  /** 已经查询过的那一份（URL 是唯一真源）；页面上的数字只属于它。 */
  const appliedVersionId = filter.questionVersionId;
  const invalidVersion = appliedVersionId !== null && !isUuid(appliedVersionId);
  /**
   * 只在两种情况下算"查过了"：地址栏明确带着一次查询（`q=1`，刷新/分享仍有效），
   * 或者是从题目详情点进来的那一次（`origin=question`，契约要求的"直接看到"）。
   * 只是在下拉里选好条件不算——那是这一页之前的问题：选一下就把数字放出来了。
   *
   * 示例模式额外要求版本必须是示例自带的：从题目详情带着真实版本点「查看示例」时，
   * 不能再拿真实版本去算示例数字（只会得到空汇总），而是退回"没查过"并让人占查询。
   */
  const queried =
    appliedVersionId !== null &&
    (fromQuestion || search.q === "1") &&
    (!demo || DEMO_VERSION_IDS.has(appliedVersionId));
  const targetVersionId = fromQuestion
    ? appliedVersionId
    : versionDraft === ""
      ? null
      : versionDraft;
  const targetCourseId = fromQuestion
    ? filter.courseId
    : courseDraft === ""
      ? null
      : courseDraft;
  // 选了别的版本却还没点「查询」：把上一次的汇总收起来，避免把旧数字当成新结果读。
  const selectionChanged =
    !fromQuestion &&
    (courseDraft !== (filter.courseId ?? "") ||
      versionDraft !== (filter.questionVersionId ?? ""));

  const context = useVersionContext(
    demo || invalidVersion || appliedVersionId === null ? null : appliedVersionId,
  );
  // 示例模式不发 B2 聚合请求：数字由 `demoData` 里的记录算出，时间范围真的会过滤。
  const summaryEnabled =
    !demo && queried && !invalidVersion && context.version.isSuccess;
  const summary = useQualitySummary({ identity, filter, enabled: summaryEnabled });
  const demoSummary =
    demo && queried ? demoQualitySummary(appliedVersionId as string, filter.from, filter.to) : null;

  const apply = () => {
    if (targetVersionId === null) return;
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
    onChange({
      ...withQualityFilter(search, {
        courseId: targetCourseId,
        questionVersionId: targetVersionId,
        from: fromValue,
        to: toValue,
      }),
      q: "1",
    });
  };

  const clearTime = () => {
    setIssue(null);
    setFromDraft("");
    setToDraft("");
    onChange({
      ...withQualityFilter(search, {
        courseId: targetCourseId,
        questionVersionId: targetVersionId,
        from: null,
        to: null,
      }),
      q: "1",
    });
  };

  /** 回到"还没查过"的空白页：课程、版本、时间一起清掉，地址栏里的查询也不再保留。 */
  const clearAll = () => {
    setCleared(true);
    setCourseDraft("");
    setVersionDraft("");
    setFromDraft("");
    setToDraft("");
    setIssue(null);
    onChange({
      ...withQualityFilter(search, {
        courseId: null,
        questionVersionId: null,
        from: null,
        to: null,
      }),
      q: undefined,
      // 从题目详情进来时，"全部清掉"也意味着不再锁定那道题。
      origin: undefined,
    });
  };

  const hasAnySelection =
    fromQuestion ||
    courseDraft !== "" ||
    versionDraft !== "" ||
    fromDraft !== "" ||
    toDraft !== "" ||
    filter.courseId !== null ||
    filter.questionVersionId !== null ||
    filter.from !== null ||
    filter.to !== null;

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
          {fromQuestion ? (
            <p className="muted" data-testid="review-quality-locked">
              {context.label
                ? t("review.quality.lockedWithTitle", { title: context.label })
                : t("review.quality.lockedNoTitle")}
            </p>
          ) : null}

          <div className="form-grid">
            {!fromQuestion ? (
              <>
                <Field label={t("review.quality.course")} htmlFor="review-quality-course">
                  <Select
                    id="review-quality-course"
                    data-testid="review-quality-course"
                    value={courseDraft}
                    onChange={(event) => {
                      // 换课程后旧版本必然不属于新课程：草稿里的版本一起清掉，等老师重选。
                      setCourseDraft(event.target.value);
                      setVersionDraft("");
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
                    value={versionDraft}
                    disabled={courseDraft === ""}
                    onChange={(event) => setVersionDraft(event.target.value)}
                  >
                    <option value="">{t("common.selectPrompt")}</option>
                    {versionDraft !== "" &&
                    !versionChoices.some(
                      (option) => option.questionVersionId === versionDraft,
                    ) ? (
                      // 深链带来的版本可能不在列表里（未发布/超出当前页）：仍然要显示出来。
                      <option value={versionDraft}>{context.label ?? versionDraft}</option>
                    ) : null}
                    {versionChoices.map((option) => (
                      <option key={option.questionVersionId} value={option.questionVersionId}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                </Field>
              </>
            ) : null}

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
              disabled={targetVersionId === null}
            >
              {t("review.quality.apply")}
            </button>
            {filter.from !== null || filter.to !== null ? (
              <button
                type="button"
                className="button button--ghost"
                data-testid="review-quality-clear-time"
                onClick={clearTime}
                disabled={targetVersionId === null}
              >
                {t("review.quality.clearTime")}
              </button>
            ) : null}
            {hasAnySelection ? (
              <button
                type="button"
                className="button button--ghost"
                data-testid="review-quality-clear-all"
                onClick={clearAll}
              >
                {t("review.quality.clearAll")}
              </button>
            ) : null}
            {!demo && !fromQuestion && versionOptions.query.hasNextPage ? (
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

      {!queried ? (
        <Banner tone="info" testId="review-quality-no-version">
          {fromQuestion
            ? t("review.quality.deepLinkMissing")
            : t("review.quality.queryPrompt")}
        </Banner>
      ) : selectionChanged ? (
        <Banner tone="info" testId="review-quality-selection-changed">
          {t("review.quality.queryAgain")}
        </Banner>
      ) : demo ? (
        demoSummary ? (
          <SummaryPanel summary={demoSummary} from={filter.from} to={filter.to} />
        ) : null
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
        <SummaryPanel summary={summary.data} from={filter.from} to={filter.to} />
      ) : null}

      {/* 还没查过 / 查出来是空的：给一条"可以先看示例"的路（从题目详情进来时不需要）。 */}
      {!demo && !fromQuestion && (!queried || summary.data?.attempt_count === 0) ? (
        <DemoHint
          testId="review-quality-empty-demo"
          onShow={() => onChange({ ...search, demo: "1" })}
        />
      ) : null}
    </div>
  );
}
