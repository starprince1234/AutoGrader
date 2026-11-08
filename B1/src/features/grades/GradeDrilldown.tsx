import { t } from "../../i18n";
import type { EvaluationReport, GradeRow } from "../../api/types";
import { Banner, Panel, Spinner, StatusPill, Table } from "../../ui/components";
import { ErrorState } from "../../ui/components";
import { AppealPackPanel } from "../appeals/AppealPackPanel";
import { CommentarySection } from "../commentary/CommentarySection";
import { LearningHintsSection } from "../hints/LearningHintsSection";
import { CaseEvidenceCard } from "./CaseEvidence";
import { useEvaluationReport } from "./useGrades";

/**
 * 逐项证据下钻（RQ-B-1-30 验收 3/4）。
 *
 * 渲染规则直接来自 `evaluation-report.schema.json`：
 * - `state` 是终态（`completed`/`rejected`/`failed`），**不得**当作低分显示；
 * - `security_conclusion.status` 是三态，`not_checked` 与 `not_found` 必须区分；
 * - `case_evidence[]` 每个用例渲染成一张卡片（`CaseEvidence.tsx`）；
 * - `expected_output_excerpt` 与 `artifacts.expected_sha256` 是受保护字段：**只有教师视图**
 *   才渲染，学生视图一律不显示、也不由前端推导。
 *
 * 三条轨道在这里分开呈现，读者不该需要猜哪一条是成绩：
 * 1. **权威分与逐项证据**（确定性，B2 决定）；
 * 2. **教学评语 / AI 参考分**（教师轨，`CommentarySection`；学生不显示参考分）；
 * 3. **学生分层学习提示**（`LearningHintsSection`，仅 run owner 学生，**不是分数**）。
 *
 * 技术摘要（五个绑定摘要、`result_digest`）默认收进 `<details>`：它们用于复核，不是主阅读内容；
 * 申诉证据包仍按需加载并下载服务端原始字节。
 */

const SECURITY_LABEL = {
  found: "grades.security.found",
  not_found: "grades.security.not_found",
  not_checked: "grades.security.not_checked",
  // CHG-0031: email_delivery 不执行代码，安全检查不适用；它必须与"未检查"分开展示，
  // 否则读者会把"该模式没有安全判定"误读成"有检查没跑"。
  not_applicable: "grades.security.not_applicable",
} as const;

export function GradeDrilldown({
  runId,
  view,
  runState,
  questionVersionId,
  onBackToQuestion,
}: {
  runId: string;
  view: "student" | "teacher";
  /** 该行的终态（来自 `GradeRow.state`）。报告缺失时（failed/rejected）仍需要它来决定提示入口。 */
  runState?: GradeRow["state"];
  /** 用于学习提示卡尾部的"返回作业"（P10）。深链且尚未加载行时可为空。 */
  questionVersionId?: string;
  onBackToQuestion?: (questionVersionId: string) => void;
}) {
  const report = useEvaluationReport(runId, view);
  const studentView = view === "student";

  if (report.isPending) return <Spinner />;
  if (report.isError) {
    /**
     * 报告不可得（例如 `failed` 运行根本没有报告：本部署实测 `GET .../report` 返回 404）。
     *
     * **两条非确定性轨不能因此消失**：契约把两轨分开，正是因为它们各自独立——报告缺失不等于
     * 评语或学习提示不存在。把整页替换成一个错误框，会让"B2 已实现、界面却没有出口"的缺陷以
     * 另一种形式继续存在。证据包（内嵌报告）在这个状态下不可得，因此不渲染。
     */
    return (
      <div className="stack" data-testid="grade-drilldown">
        <ErrorState error={report.error} />
        <CommentarySection runId={runId} />
        {studentView ? (
          <LearningHintsSection
            runId={runId}
            runState={runState ?? "failed"}
            questionVersionId={questionVersionId ?? ""}
            onBackToQuestion={questionVersionId ? onBackToQuestion : undefined}
          />
        ) : null}
      </div>
    );
  }
  if (!report.data) return <Banner tone="warn">{t("grades.reportUnavailable")}</Banner>;

  const data: EvaluationReport = report.data;
  const security = data.security_conclusion.status;
  const state = data.state ?? runState ?? "completed";
  const passedCount = data.case_evidence.filter((evidence) => evidence.passed).length;

  return (
    <div className="stack" data-testid="grade-drilldown">
      {/* 权威分与模型参考分在同一个区域内并列：权威分先出现、用强调外观
          （`score-authoritative` + 强调 pill），参考分紧随其后、用低权重外观
          （`advisory-score`，灰底虚线、`pill--idle`、无强调色）。 */}
      <div className="stack" data-testid="score-region">
        <div className="inline">
          <StatusPill
            tone={
              data.state === "completed" ? "ok" : data.state === "rejected" ? "warn" : "bad"
            }
          >
            {t(`submit.terminal.${data.state}`)}
          </StatusPill>
          {/* 未完成 / 被拒绝 / 系统失败时没有权威分：显示状态，不显示 0 分。 */}
          {state === "completed" ? (
            <strong className="score-authoritative" data-testid="authoritative-score">
              {t("grades.overall")}：{data.overall.score} / {data.overall.max_score} ·{" "}
              {data.overall.is_pass ? t("grades.pass") : t("grades.fail")}
            </strong>
          ) : (
            <strong className="score-authoritative" data-testid="authoritative-score">
              {t("grades.notScored")}
            </strong>
          )}
          {false ? (
            <details className="muted">
              <summary>{t("grades.technicalInfo")}</summary>
              <span>
                {t("grades.totalCheck")}：
                {data.total_check.matches_total ? t("grades.totalCheckOk") : t("common.no")}
                （{data.total_check.sum_of_awards}）
              </span>
            </details>
          ) : null}
        </div>
        {/* 评语轨由服务端按角色收窄：学生即使调用也只会得到课程策略允许的内容；
            参考分只在教师/助教/管理员视图渲染（`isStaff`）。 */}
        <CommentarySection runId={runId} showAdvisory={!studentView} />
      </div>

      {studentView ? (
        <LearningHintsSection
          runId={runId}
          runState={state}
          questionVersionId={questionVersionId ?? data.question_version_id}
          onBackToQuestion={onBackToQuestion}
        />
      ) : null}

      <p data-testid="overall-summary">
        {t("grades.friendlySummary", {
          cases: data.case_evidence.length,
          passed: passedCount,
          score: data.overall.score,
          max: data.overall.max_score,
        })}
      </p>

      {false ? (
        <>
      <Panel title={t("grades.staticFindings")}>
        {data.static_findings.length === 0 ? (
          <p className="muted">{t("grades.noFindings")}</p>
        ) : (
          <Table
            testId="static-findings"
            rows={data.static_findings}
            getRowKey={(row, index) => `${row.rule_id}-${index}`}
            columns={[
              { key: "rule_id", header: t("grades.ruleId"), render: (row) => row.rule_id },
              { key: "severity", header: t("grades.severity"), render: (row) => row.severity },
              {
                key: "location",
                header: t("grades.location"),
                render: (row) =>
                  `${row.file}${row.line ? `:${row.line}` : ""}${row.column ? `:${row.column}` : ""}`,
              },
              { key: "message", header: t("grades.message"), render: (row) => row.message },
              {
                key: "blocking",
                header: t("grades.blocking"),
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
              { key: "category", header: t("grades.securityCategory"), render: (row) => row.category },
              { key: "severity", header: t("grades.severity"), render: (row) => row.severity },
              {
                key: "detected_in",
                header: t("grades.detectedIn"),
                render: (row) => row.detected_in ?? t("common.none"),
              },
              {
                key: "message",
                header: t("grades.message"),
                render: (row) => row.message ?? t("common.none"),
              },
            ]}
          />
        ) : null}
      </Panel>
        </>
      ) : null}

      <Panel
        title={studentView ? t("grades.casesStudent") : t("grades.cases")}
        testId="case-evidence"
      >
        {!studentView ? <p className="muted">{t("grades.evidenceRedaction")}</p> : null}
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

      {/* 技术摘要默认收起：教师需要它来复核，但它不是学生的主阅读内容。 */}
      {false ? (
        <>
          <details data-testid="binding-details">
            <summary>{t("grades.technicalDetails")}</summary>
            <Panel title={t("grades.binding")}>
              <ul className="muted">
                <li>{t("grades.bindingSubmission")}：{data.binding.submission_digest}</li>
                <li>{t("grades.bindingPackage")}：{data.binding.package_digest}</li>
                <li>{t("grades.bindingPolicy")}：{data.binding.runner_policy_digest}</li>
                <li>{t("grades.bindingEnvironment")}：{data.binding.environment_digest}</li>
                <li>{t("grades.resultDigest")}：{data.result_digest}</li>
              </ul>
            </Panel>
          </details>

          <AppealPackPanel runId={runId} />
        </>
      ) : null}
    </div>
  );
}
