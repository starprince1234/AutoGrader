import { useEffect, useMemo, useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { GradeRow, LearningHintStep } from "../../api/types";
import { Banner, ErrorState, Panel, Spinner, StatusPill } from "../../ui/components";
import { useGenerateLearningHints, useLearningHints } from "./useLearningHints";

/**
 * 学生本次评测的**分层学习提示**（AI-FLOW-01B）。
 *
 * 与其他区域的边界（视觉与语义都要分得开）：
 * - 权威分/逐项证据在 `GradeDrilldown`；教学评语与 AI 参考分在 `CommentarySection`（教师轨）；
 *   本组件只呈现**学生自己的**提示，用独立面板与不同标题，学生看不到 `advisory_score`。
 * - 提示**不参与评分**：面板内不出现"得分/满分/通过"等字样，也不因为提示存在而改变任何数字。
 *
 * 逐层展开（知识点 → 排查方向 → 自检思路）只是**学习节奏**控制，不是安全边界：服务端已经把
 * 完整答案与隐藏值挡在生成阶段。隐藏/展开只影响本地显隐，不重新请求、不改变任何记录。
 *
 * 状态映射（契约 `LearningHintsResponse.status`）：
 * `not_requested` 显示生成按钮；`pending` 显示"正在整理提示…"；`ready` 逐层展开；
 * `failed` 显示暂时不可用并提供显式重试；`unavailable` 说明本部署未启用；
 * `not_applicable` 说明这次评测没有可讲的失败证据（不虚构原因）。
 */
export function LearningHintsSection({
  runId,
  runState,
  questionVersionId,
  onBackToQuestion,
}: {
  runId: string;
  /** 这一行的终态（来自 `GradeRow.state`）；只有 `completed` 才能生成提示。 */
  runState: GradeRow["state"];
  /** 用于"返回作业"（P10）并保留本地未提交草稿。 */
  questionVersionId: string;
  onBackToQuestion?: (questionVersionId: string) => void;
}) {
  const completed = runState === "completed";
  const hints = useLearningHints(runId, completed);
  const generate = useGenerateLearningHints(runId);
  const [revealed, setRevealed] = useState(1);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const data = hints.data;
  const steps = useMemo<LearningHintStep[]>(() => data?.hint_steps ?? [], [data]);
  const readyKey = data?.generation_id ?? `${runId}:${data?.status ?? ""}`;

  // 换 run / 换结果时回到"只显示第一条"，避免把上一次的展开状态套到新提示上。
  useEffect(() => {
    setRevealed(1);
    setHidden(new Set());
  }, [readyKey]);

  const forbidden = isApiError(hints.error) && hints.error.isForbidden;
  const rateLimited = isApiError(generate.error) && generate.error.status === 429;
  const notCompleted = !completed;
  const generateDisabled =
    generate.isPending || hints.isPending || data?.status === "pending" || notCompleted;

  const toggle = (key: string) => {
    setHidden((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <Panel
      title={t("hints.title")}
      testId="learning-hints"
      className="panel--advisory"
      actions={<StatusPill tone="idle">{t("hints.aidLabel")}</StatusPill>}
    >
      <p className="muted">{t("hints.intro")}</p>

      {notCompleted ? (
        <Banner tone="info" testId="hints-not-completed">
          {t("hints.notCompleted")}
        </Banner>
      ) : null}

      {forbidden ? (
        <Banner tone="info" testId="hints-forbidden">
          {t("hints.forbidden")}
        </Banner>
      ) : hints.isError ? (
        <ErrorState error={hints.error} testId="hints-error" />
      ) : null}

      {hints.isPending && completed ? <Spinner label={t("hints.loading")} /> : null}

      {data ? (
        <>
          {data.status === "unavailable" ? (
            <Banner tone="info" testId="hints-unavailable">
              {t("hints.unavailable")}
            </Banner>
          ) : null}

          {data.status === "not_requested" ? (
            <p className="muted" data-testid="hints-not-requested">
              {t("hints.notRequested")}
            </p>
          ) : null}

          {data.status === "not_applicable" ? (
            <Banner tone="info" testId="hints-not-applicable">
              {t("hints.notApplicable")}
            </Banner>
          ) : null}

          {data.status === "pending" ? (
            <Banner tone="info" testId="hints-pending">
              {t("hints.pending")}
            </Banner>
          ) : null}

          {data.status === "failed" ? (
            <Banner tone="warn" testId="hints-failed">
              {t("hints.failed")}
              {data.failure_category ? (
                <span className="muted"> {t("hints.failureCategory", { category: data.failure_category })}</span>
              ) : null}
            </Banner>
          ) : null}

          {data.status === "ready" ? (
            <div className="stack" data-testid="hints-ready">
              {steps.map((step, index) => {
                const key = `${step.level}-${index}`;
                const isHidden = hidden.has(key);
                const isRevealed = index < revealed;
                if (!isRevealed) return null;
                return (
                  <div key={key} className="stack" data-testid={`hint-step-${step.level}`}>
                    <div className="inline">
                      <StatusPill tone="ok">
                        {t(`hints.level.${step.level}`)}
                      </StatusPill>
                      <strong>{step.title}</strong>
                      <button
                        type="button"
                        className="button button--ghost"
                        data-testid={`hints-${isHidden ? "show" : "hide"}-${step.level}`}
                        aria-expanded={!isHidden}
                        onClick={() => toggle(key)}
                      >
                        {isHidden ? t("hints.show") : t("hints.hide")}
                      </button>
                    </div>
                    {isHidden ? null : <p data-testid={`hint-text-${step.level}`}>{step.text}</p>}
                  </div>
                );
              })}
              {revealed < steps.length ? (
                <button
                  type="button"
                  className="button"
                  data-testid="hints-reveal-next"
                  onClick={() => setRevealed((current) => Math.min(current + 1, steps.length))}
                >
                  {t("hints.revealNext", { remaining: steps.length - revealed })}
                </button>
              ) : null}
            </div>
          ) : null}

          {data.disclaimers.length > 0 ? (
            <ul className="muted" data-testid="hints-disclaimers">
              {data.disclaimers.map((line, index) => (
                <li key={`${index}-${line}`}>{line}</li>
              ))}
            </ul>
          ) : (
            <Banner tone="warn" testId="hints-disclaimers-missing">
              {t("hints.disclaimersMissing")}
            </Banner>
          )}

          {data.provider ? (
            <details data-testid="hints-provenance">
              <summary>{t("hints.provenance")}</summary>
              <p className="muted">{t("hints.provenanceHint")}</p>
              <ul className="muted">
                <li>
                  {t("commentary.provider")}: {data.provider}
                </li>
                <li>
                  {t("commentary.model")}: {data.model ?? t("common.none")}
                </li>
                <li>
                  {t("commentary.promptVersion")}: {data.prompt_version ?? t("common.none")}
                </li>
              </ul>
              <h4>{t("commentary.inputScope")}</h4>
              <ul className="muted" data-testid="hints-context-scope">
                {(data.context_scope ?? []).map((field) => (
                  <li key={field}>{field}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      ) : null}

      {rateLimited ? (
        <Banner tone="warn" testId="hints-rate-limited">
          {t("hints.rateLimited")}
        </Banner>
      ) : null}

      {generate.isError && !rateLimited ? (
        <ErrorState error={generate.error} testId="hints-generate-error" />
      ) : null}

      <div className="inline">
        {completed && (data?.status === "not_requested" || data?.status === "failed" || data === undefined) ? (
          <button
            type="button"
            className="button"
            data-testid="hints-generate"
            disabled={generateDisabled}
            onClick={() => generate.mutate()}
          >
            {generate.isPending ? t("hints.generating") : t("hints.generate")}
          </button>
        ) : null}
        {onBackToQuestion ? (
          <button
            type="button"
            className="button button--ghost"
            data-testid="hints-back"
            onClick={() => onBackToQuestion(questionVersionId)}
          >
            {t("hints.backToQuestion")}
          </button>
        ) : null}
      </div>
      <p className="muted">{t("hints.generateHint")}</p>
    </Panel>
  );
}
