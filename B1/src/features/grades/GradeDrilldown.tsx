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
        {/* 双轨分解：最终分是用例轨与 AI 轨按题目权重合成的，学生/教师必须能看出这两半。 */}
        {state === "completed" && data.overall.dual_track ? (
          <div className="stack" data-testid="dual-track-breakdown">
            <p className="muted">
              {t("grades.dualTrackCase", {
                ratio: data.overall.dual_track.case_ratio,
                score: data.overall.dual_track.case_score,
                max: data.overall.dual_track.case_max,
                percent: data.overall.dual_track.case_percent,
              })}
            </p>
            <p className="muted">
              {data.overall.dual_track.ai_available
                ? t("grades.dualTrackAi", {
                    score: data.overall.dual_track.ai_score ?? 0,
                    percent: data.overall.dual_track.ai_percent,
                  })
                : t("grades.dualTrackAiUnavailable", {
                    percent: data.overall.dual_track.ai_percent,
                  })}
            </p>
          </div>
        ) : null}
        <CommentarySection runId={runId} />
      </div>
    </div>
  );
}
