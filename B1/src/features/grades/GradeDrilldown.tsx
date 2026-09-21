import { t } from "../../i18n";
import type { EvaluationReport } from "../../api/types";
import { Banner, Panel, Spinner, StatusPill, Table } from "../../ui/components";
import { ErrorState } from "../../ui/components";
import { AppealPackPanel } from "../appeals/AppealPackPanel";
import { CommentarySection } from "../commentary/CommentarySection";
import { CaseEvidenceCard } from "./CaseEvidence";
import { useEvaluationReport } from "./useGrades";

/**
 * 逐项证据下钻（RQ-B-1-30 验收 3/4）。
 *
 * 渲染规则直接来自 `evaluation-report.schema.json`：
 * - `state` 是终态（`completed`/`rejected`/`failed`），**不得**当作低分显示；
 * - `security_conclusion.status` 是三态，`not_checked` 与 `not_found` 必须区分；
 * - `case_evidence[]` 每个用例渲染成一张卡片（`CaseEvidence.tsx`）：资源峰值、文件改动与
 *   确定性说明都是 CHG-0012 新增的证据，压进表格单元格只会变成另一种 JSON；
 * - `expected_output_excerpt` 与 `artifacts.expected_sha256` 是受保护字段：**只有教师视图**
 *   才渲染，学生视图一律不显示、也不由前端推导。
 *
 * 这里同时是两个"契约有、界面曾经没有出口"的能力的落点：
 * - **教学评语轨与 AI 参考分**（RQ-T-20 / RQ-T-30，`CommentarySection`）：参考分与上面的权威分
 *   并列，但外观权重更低，且永远带着契约要求的标注；
 * - **申诉证据包**（CHG-0014，`AppealPackPanel`）：按需加载并下载，`redactions` 原文列出。
 */

const SECURITY_LABEL = {
  found: "grades.security.found",
  not_found: "grades.security.not_found",
  not_checked: "grades.security.not_checked",
} as const;

export function GradeDrilldown({
  runId,
  view,
}: {
  runId: string;
  view: "student" | "teacher";
}) {
  const report = useEvaluationReport(runId, view);

  if (report.isPending) return <Spinner />;
  if (report.isError) {
    /**
     * 报告不可得（例如 `failed` 运行根本没有报告：本部署实测 `GET .../report` 返回 404，
     * detail 为 "A report exists only after a successful run."）。
     *
     * **评语轨不能因此消失**：契约把两轨分开，正是因为它们各自独立——报告缺失不等于评语
     * 不存在（本部署实测：`failed` 运行同样能生成并读到评语）。把整页替换成一个错误框，
     * 会让"B2 已实现、界面却没有出口"的缺陷以另一种形式继续存在。
     * 证据包（内嵌报告）在这个状态下不可得，因此不渲染。
     */
    return (
      <div className="stack" data-testid="grade-drilldown">
        <ErrorState error={report.error} />
        <CommentarySection runId={runId} />
      </div>
    );
  }
  if (!report.data) return <Banner tone="warn">{t("grades.reportUnavailable")}</Banner>;

  const data: EvaluationReport = report.data;
  const security = data.security_conclusion.status;

  return (
    <div className="stack" data-testid="grade-drilldown">
      {/* 权威分与模型参考分在同一个区域内并列：权威分先出现、用强调外观
          （`score-authoritative` + 强调 pill），参考分紧随其后、用低权重外观
          （`advisory-score`，灰底虚线、`pill--idle`、无强调色）。
          `ADR-0014` 决策 3 要的不是"把参考分藏起来"，而是"别让参考分看起来像成绩"。 */}
      <div className="stack" data-testid="score-region">
        <div className="inline">
          <StatusPill
            tone={
              data.state === "completed" ? "ok" : data.state === "rejected" ? "warn" : "bad"
            }
          >
            {t(`submit.terminal.${data.state}`)}
          </StatusPill>
          <strong className="score-authoritative" data-testid="authoritative-score">
            {t("grades.overall")}：{data.overall.score} / {data.overall.max_score} ·{" "}
            {data.overall.is_pass ? t("grades.pass") : t("grades.fail")}
          </strong>
          <span className="muted">
            {t("grades.totalCheck")}：
            {data.total_check.matches_total ? t("grades.totalCheckOk") : t("common.no")}
            （{data.total_check.sum_of_awards}）
          </span>
        </div>
        {/* 评语轨（RQ-T-20）与 AI 参考分（RQ-T-30）都只在这里出现。 */}
        <CommentarySection runId={runId} />
      </div>

      {data.overall.summary ? <p>{data.overall.summary}</p> : null}

      <Panel title={t("grades.staticFindings")}>
        {data.static_findings.length === 0 ? (
          <p className="muted">{t("grades.noFindings")}</p>
        ) : (
          <Table
            testId="static-findings"
            rows={data.static_findings}
            getRowKey={(row, index) => `${row.rule_id}-${index}`}
            columns={[
              { key: "rule_id", header: "rule_id", render: (row) => row.rule_id },
              { key: "severity", header: "severity", render: (row) => row.severity },
              {
                key: "location",
                header: "location",
                render: (row) =>
                  `${row.file}${row.line ? `:${row.line}` : ""}${row.column ? `:${row.column}` : ""}`,
              },
              { key: "message", header: "message", render: (row) => row.message },
              {
                key: "blocking",
                header: "blocking",
                render: (row) => (row.blocking ? t("common.yes") : t("common.no")),
              },
            ]}
          />
        )}
      </Panel>

      <Panel title={t("grades.security")}>
        <StatusPill tone={security === "found" ? "bad" : security === "not_found" ? "ok" : "warn"}>
          {t(SECURITY_LABEL[security])}
        </StatusPill>
        {data.security_conclusion.findings.length > 0 ? (
          <Table
            testId="security-findings"
            rows={data.security_conclusion.findings}
            getRowKey={(row) => row.finding_id}
            columns={[
              { key: "category", header: "category", render: (row) => row.category },
              { key: "severity", header: "severity", render: (row) => row.severity },
              {
                key: "detected_in",
                header: "detected_in",
                render: (row) => row.detected_in ?? t("common.none"),
              },
              {
                key: "message",
                header: "message",
                render: (row) => row.message ?? t("common.none"),
              },
            ]}
          />
        ) : null}
      </Panel>

      <Panel title={t("grades.cases")} testId="case-evidence">
        <p className="muted">{t("grades.evidenceRedaction")}</p>
        {data.case_evidence.length === 0 ? (
          <p className="muted" data-testid="case-evidence-empty">
            {t("grades.noFindings")}
          </p>
        ) : (
          <div className="stack">
            {data.case_evidence.map((evidence) => (
              <CaseEvidenceCard
                key={evidence.case_id}
                evidence={evidence}
                teacherView={view === "teacher"}
              />
            ))}
          </div>
        )}
      </Panel>

      <Panel title={t("grades.binding")}>
        <ul className="muted">
          <li>submission_digest: {data.binding.submission_digest}</li>
          <li>package_digest: {data.binding.package_digest}</li>
          <li>runner_policy_digest: {data.binding.runner_policy_digest}</li>
          <li>environment_digest: {data.binding.environment_digest}</li>
          <li>{t("grades.resultDigest")}: {data.result_digest}</li>
        </ul>
      </Panel>

      {/* 证据包（CHG-0014）：按需加载。它是"可移动的"文件，复核者没登录本部署也要能凭它判断。 */}
      <AppealPackPanel runId={runId} />
    </div>
  );
}
