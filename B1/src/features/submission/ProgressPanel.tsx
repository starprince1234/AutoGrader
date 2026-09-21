import { t } from "../../i18n";
import { Banner, StatusPill } from "../../ui/components";
import { ErrorState } from "../../ui/components";
import type { ProgressState } from "./progress";
import type { StreamStatus } from "./useEvaluationStream";

const STATUS_TONE: Record<StreamStatus, "ok" | "warn" | "bad" | "idle"> = {
  idle: "idle",
  connecting: "idle",
  open: "ok",
  reconnecting: "warn",
  closed: "idle",
  error: "bad",
};

/** 评测进度面板：未完成时**只显示状态，不显示 0 分**（RQ-B-1-30 验收 4 / RQ-B-1-50 验收 3）。 */
export function ProgressPanel({
  progress,
  status,
  error,
}: {
  progress: ProgressState;
  status: StreamStatus;
  error: unknown;
}) {
  const latest = progress.frames.at(-1) ?? null;
  const total = latest?.cases_total ?? null;
  const done = latest?.cases_done ?? null;
  const percent = total && done !== null ? Math.min(100, Math.round((done / total) * 100)) : null;

  return (
    <div className="stack" data-testid="progress-panel">
      <div className="inline">
        <StatusPill tone={STATUS_TONE[status]}>{t(`submit.streamState.${status}`)}</StatusPill>
        {latest ? (
          <span className="muted" data-testid="progress-stage">
            {t("submit.progress", {
              stage: latest.stage,
              done: done ?? 0,
              total: total ?? 0,
            })}
          </span>
        ) : (
          <span className="muted">{t("submit.noTerminalYet")}</span>
        )}
        {latest ? <span className="muted">sequence {latest.sequence}</span> : null}
      </div>

      {percent !== null ? (
        <div className="progress" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
          <div className="progress__bar" style={{ width: `${percent}%` }} />
        </div>
      ) : null}

      <p className="muted">{t("submit.dedupNotice")}</p>

      {progress.terminal === "completed" ? (
        <Banner tone="success" testId="terminal-completed">
          <strong>{t("submit.terminal.completed")}</strong>
          <p>
            {t("grades.score")}：{latest?.score ?? "—"} /{" "}
            {latest?.is_pass === true ? t("grades.pass") : t("grades.fail")}
          </p>
        </Banner>
      ) : null}

      {progress.terminal === "rejected" ? (
        <Banner tone="warn" testId="terminal-rejected">
          <strong>{t("submit.terminal.rejected")}</strong>
          <p>
            {latest?.terminal_reason
              ? t(`submit.terminalReason.${latest.terminal_reason}`)
              : t("common.unknownError")}
          </p>
        </Banner>
      ) : null}

      {progress.terminal === "failed" ? (
        <Banner tone="error" testId="terminal-failed">
          <strong>{t("submit.terminal.failed")}</strong>
          <p>
            {latest?.terminal_reason
              ? t(`submit.terminalReason.${latest.terminal_reason}`)
              : t("common.unknownError")}
          </p>
        </Banner>
      ) : null}

      {status === "error" ? <ErrorState error={error} testId="stream-error" /> : null}
    </div>
  );
}
