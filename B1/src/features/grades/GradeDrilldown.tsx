import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { EvaluationReport, GradeRow } from "../../api/types";
import { Banner, ErrorState, Spinner, StatusPill } from "../../ui/components";
import { CommentarySection } from "../commentary/CommentarySection";
import { useEvaluationReport } from "./useGrades";

/**
 * 评测详情（学生查看评测结果 & 教师查看单题批改详情共用）：
 * 只呈现 AI 评分结果与结构化 AI 评价，移除测试用例、静态检查、安全结论与技术摘要等冗余块。
 */
export function GradeDrilldown({
  runId,
  view,
  runState,
}: {
  runId: string;
  view: "student" | "teacher";
  /** 该行的终态（来自 `GradeRow.state`）。报告缺失时（failed/rejected）用于展示对应说明。 */
  runState?: GradeRow["state"];
  questionVersionId?: string;
  onBackToQuestion?: (questionVersionId: string) => void;
}) {
  const report = useEvaluationReport(runId, view);

  if (report.isPending) return <Spinner />;
  if (report.isError) {
    const missingReport = isApiError(report.error) && report.error.isNotFound;
    const friendly =
      runState === "rejected"
        ? t("grades.reportMissingRejected")
        : runState === "failed"
          ? t("grades.reportMissingFailed")
          : t("grades.reportMissing");
    return (
      <div className="stack" data-testid="grade-drilldown">
        {missingReport ? (
          <Banner tone="info" testId="report-missing">
            {friendly}
          </Banner>
        ) : (
          <ErrorState error={report.error} />
        )}
        <CommentarySection runId={runId} />
      </div>
    );
  }
  if (!report.data) return <Banner tone="warn">{t("grades.reportUnavailable")}</Banner>;

  const data: EvaluationReport = report.data;
  const state = data.state ?? runState ?? "completed";

  return (
    <div className="stack" data-testid="grade-drilldown">
      <div className="stack" data-testid="score-region">
        <div className="inline">
          <StatusPill
            tone={
              data.state === "completed" ? "ok" : data.state === "rejected" ? "warn" : "bad"
            }
          >
            {t(`submit.terminal.${data.state}`)}
          </StatusPill>
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
        </div>
        <CommentarySection runId={runId} />
      </div>
    </div>
  );
}
