import { useEffect, useState } from "react";
import { t } from "../../i18n";
import type { EvaluationQualitySummary } from "../../api/types";
import { isUuid } from "../../ui/format";
import {
  Banner,
  ErrorState,
  Field,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
  Table,
} from "../../ui/components";
import {
  SMALL_SAMPLE_THRESHOLD,
  isSmallSample,
  toContractTimestamp,
  toLocalInputValue,
  useQualitySummary,
} from "./useQualitySummary";

/**
 * 题目质量热力图（CHG-0015）。教师读它来决定"这道题值不值得留"，不是用来查某个人。
 *
 * 两个字段**必须分开渲染**，因为契约点名它们"很容易被不诚实地渲染"：
 *
 * - `security_not_checked` 与 `security_not_found`：`security_*` 三项各自成一块、各自带值，
 *   页面上不存在任何"合并后的安全检查数"。契约原文：Never merge into `security_not_found`。
 * - `unmeasured_runs` 单独一块：没有它，"没有资源数据"会被静默读成"没有占用"。
 *
 * 另外两处诚实性：`infrastructure_error` 从题目难度里拿出来单列（系统的问题不是难度），
 * 以及 `attempt_count` 很小时先提示样本不足，而不是让读者从噪声里下结论。
 */

/** 单个计数的展示件：值用 `data-testid` 固定，避免"哪一块显示了哪个数"只靠排版判断。 */
function CountCard({
  label,
  value,
  hint,
  testId,
  tone = "idle",
}: {
  label: string;
  value: number;
  hint?: string;
  testId: string;
  tone?: "ok" | "warn" | "bad" | "idle";
}) {
  return (
    <div className="count-card" data-testid={testId}>
      <StatusPill tone={tone}>{label}</StatusPill>
      <p className="count-card__value" data-testid={`${testId}-value`}>
        {value}
      </p>
      {hint ? <p className="muted">{hint}</p> : null}
    </div>
  );
}

function FailureRates({ summary }: { summary: EvaluationQualitySummary }) {
  return (
    <div data-testid="quality-case-rates">
      <h3>{t("quality.caseRates")}</h3>
      <p className="muted">{t("quality.band")}</p>
      {summary.case_failure_rates.length === 0 ? (
        <p className="muted">{t("common.empty")}</p>
      ) : (
        <Table
          testId="quality-case-rates-table"
          rows={summary.case_failure_rates}
          getRowKey={(row) => String(row.ordinal)}
          columns={[
            { key: "ordinal", header: t("quality.caseOrdinal"), render: (row) => row.ordinal },
            { key: "attempts", header: t("quality.attempts"), render: (row) => row.attempts },
            { key: "failures", header: t("quality.failures"), render: (row) => row.failures },
            {
              key: "rate",
              header: t("quality.failureRate"),
              render: (row) =>
                row.attempts === 0 ? (
                  // 0 次尝试时失败率是"没有样例"，不是 0%：后者会读成"这个用例大家都过得去"。
                  <span className="muted">{t("quality.noAttempts")}</span>
                ) : (
                  `${Math.round((row.failures / row.attempts) * 100)}%`
                ),
            },
          ]}
        />
      )}
    </div>
  );
}

function QualitySummaryBody({ summary }: { summary: EvaluationQualitySummary }) {
  const systemCategories = summary.failure_categories.filter(
    (item) => item.category === "infrastructure_error",
  );
  const otherCategories = summary.failure_categories.filter(
    (item) => item.category !== "infrastructure_error",
  );

  return (
    <div className="stack" data-testid="quality-summary">
      <Banner tone="info" testId="quality-counts-only">
        {t("quality.countsOnly")}
      </Banner>

      <div className="count-grid">
        <CountCard
          label={t("quality.sampleSize")}
          value={summary.attempt_count}
          hint={t("quality.attemptScope")}
          testId="quality-attempt-count"
        />
        <CountCard
          label={t("quality.unmeasured")}
          value={summary.unmeasured_runs}
          hint={t("quality.unmeasuredHint")}
          testId="quality-unmeasured-runs"
          tone="warn"
        />
      </div>
      <p className="muted">{t("quality.sampleCaveat")}</p>
      <p className="muted" data-testid="quality-unmeasured-separate">
        {t("quality.unmeasuredSeparate")}
      </p>

      {isSmallSample(summary.attempt_count) ? (
        <Banner tone="warn" testId="quality-small-sample">
          {t("quality.smallSample", {
            attempts: summary.attempt_count,
            threshold: SMALL_SAMPLE_THRESHOLD,
          })}
          {t("quality.smallSampleAid", { threshold: SMALL_SAMPLE_THRESHOLD })}
        </Banner>
      ) : null}

      <div data-testid="quality-security">
        <h3>{t("quality.security")}</h3>
        <p className="muted" data-testid="quality-security-separate-note">
          {t("quality.securitySeparate")}
        </p>
        <div className="count-grid">
          {/* 三项各自独立：页面上不存在任何一个"合并后的安全检查计数"。 */}
          <CountCard
            label={t("quality.securityFound")}
            value={summary.security_found}
            testId="quality-security-found"
            tone={summary.security_found > 0 ? "bad" : "idle"}
          />
          <CountCard
            label={t("quality.securityNotFound")}
            value={summary.security_not_found}
            testId="quality-security-not-found"
            tone="ok"
          />
          <CountCard
            label={t("quality.securityNotChecked")}
            value={summary.security_not_checked}
            testId="quality-security-not-checked"
            tone="warn"
          />
        </div>
      </div>

      <div data-testid="quality-categories">
        <h3>{t("quality.categories")}</h3>
        <div className="count-grid">
          <CountCard
            label={t("quality.categorySystem")}
            value={systemCategories.reduce((total, item) => total + item.count, 0)}
            hint={t("quality.categorySystemHint")}
            testId="quality-categories-system"
            tone="warn"
          />
          <CountCard
            label={t("quality.categoryOther")}
            value={otherCategories.reduce((total, item) => total + item.count, 0)}
            testId="quality-categories-other"
          />
        </div>
        {otherCategories.length === 0 ? (
          <p className="muted">{t("common.empty")}</p>
        ) : (
          <Table
            testId="quality-category-table"
            rows={otherCategories}
            getRowKey={(row) => row.category}
            columns={[
              { key: "category", header: t("quality.categories"), render: (row) => row.category },
              { key: "count", header: t("quality.hits"), render: (row) => row.count },
            ]}
          />
        )}
      </div>

      <FailureRates summary={summary} />

      <div data-testid="quality-rules">
        <h3>{t("quality.rules")}</h3>
        {/* 目录形状先说，理由后说：契约的 `static_rule_hits` 是**冻结目录逐条给出**（未命中即 0），
            小标题与说明必须与渲染出来的数据一致——曾写成"只含命中过的规则"并称"拿不到规则全集"，
            而那 15 行 0 命中记录正是它渲染出来的东西。 */}
        <p className="muted" data-testid="quality-rules-catalog">
          {t("quality.rulesCatalog")}
        </p>
        <p className="muted">{t("quality.hitsHint")}</p>
        {summary.static_rule_hits.length === 0 ? (
          <p className="muted">{t("common.empty")}</p>
        ) : (
          <Table
            testId="quality-rules-table"
            rows={summary.static_rule_hits}
            getRowKey={(row) => row.rule_id}
            columns={[
              { key: "rule_id", header: t("quality.ruleId"), render: (row) => row.rule_id },
              { key: "severity", header: t("quality.severity"), render: (row) => row.severity },
              { key: "count", header: t("quality.hits"), render: (row) => row.count },
            ]}
          />
        )}
      </div>
    </div>
  );
}

export function QualitySummaryPage({
  questionVersionId,
  from,
  to,
  onApply,
}: {
  questionVersionId: string | null;
  from: string | null;
  to: string | null;
  onApply: (next: {
    questionVersionId: string | null;
    from: string | null;
    to: string | null;
  }) => void;
}) {
  const [draft, setDraft] = useState({
    version: questionVersionId ?? "",
    from: toLocalInputValue(from),
    to: toLocalInputValue(to),
  });
  const [issue, setIssue] = useState<string | null>(null);

  // 深链进入或外部导航后，输入框必须与 URL 一致（否则页面显示的筛选条件与查询用的不是同一套）。
  useEffect(() => {
    setDraft({
      version: questionVersionId ?? "",
      from: toLocalInputValue(from),
      to: toLocalInputValue(to),
    });
  }, [questionVersionId, from, to]);

  // 已应用的版本 ID 才是查询依据；格式不符时不发请求，并如实说明原因。
  const appliedInvalid = questionVersionId !== null && !isUuid(questionVersionId);
  const filter = {
    questionVersionId:
      questionVersionId !== null && isUuid(questionVersionId) ? questionVersionId : null,
    from,
    to,
  };
  const summary = useQualitySummary(filter);

  const apply = () => {
    const version = draft.version.trim();
    if (version === "") {
      setIssue(t("quality.noVersion"));
      return;
    }
    if (!isUuid(version)) {
      setIssue(t("quality.invalidVersion"));
      return;
    }
    const fromValue = toContractTimestamp(draft.from);
    const toValue = toContractTimestamp(draft.to);
    if (draft.from.trim() !== "" && fromValue === null) {
      setIssue(t("quality.invalidTime"));
      return;
    }
    if (draft.to.trim() !== "" && toValue === null) {
      setIssue(t("quality.invalidTime"));
      return;
    }
    setIssue(null);
    onApply({ questionVersionId: version, from: fromValue, to: toValue });
  };

  return (
    <div className="stack">
      <PageTitle>{t("quality.title")}</PageTitle>

      <Panel title={t("quality.filter")}>
        <p className="muted">{t("quality.intro")}</p>
        <div className="form-grid">
          <Field label={t("quality.questionVersion")} htmlFor="quality-version" error={issue}>
            <input
              id="quality-version"
              data-testid="quality-version-input"
              value={draft.version}
              onChange={(event) => setDraft({ ...draft, version: event.target.value })}
            />
          </Field>
          <Field label={t("quality.from")} htmlFor="quality-from">
            <input
              id="quality-from"
              type="datetime-local"
              data-testid="quality-from-input"
              value={draft.from}
              onChange={(event) => setDraft({ ...draft, from: event.target.value })}
            />
          </Field>
          <Field label={t("quality.to")} htmlFor="quality-to">
            <input
              id="quality-to"
              type="datetime-local"
              data-testid="quality-to-input"
              value={draft.to}
              onChange={(event) => setDraft({ ...draft, to: event.target.value })}
            />
          </Field>
        </div>
        <p className="muted">{t("quality.questionVersionHint")}</p>
        <p className="muted">{t("quality.timeHint")}</p>
        <div className="inline">
          <button type="button" className="button" data-testid="quality-apply" onClick={apply}>
            {t("quality.apply")}
          </button>
        </div>
      </Panel>

      {appliedInvalid ? (
        <Banner tone="warn" testId="quality-invalid-version">
          {t("quality.invalidVersion")}
        </Banner>
      ) : null}

      {filter.questionVersionId === null ? (
        <Banner tone="info" testId="quality-no-request">
          {t("quality.noVersion")}
        </Banner>
      ) : summary.isPending ? (
        <Spinner />
      ) : summary.isError ? (
        <ErrorState error={summary.error} testId="quality-error" />
      ) : summary.data ? (
        summary.data.attempt_count === 0 ? (
          <Panel title={t("quality.title")} testId="quality-empty-panel">
            <Banner tone="info" testId="quality-empty">
              {t("quality.empty")}
            </Banner>
            <QualitySummaryBody summary={summary.data} />
          </Panel>
        ) : (
          <QualitySummaryBody summary={summary.data} />
        )
      ) : null}
    </div>
  );
}
