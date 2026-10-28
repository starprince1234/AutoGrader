import { useState } from "react";
import { Drawer } from "../../ui/components";
import { t } from "../../i18n";
import { describeApiError, isApiError } from "../../api/problem";
import {
  useIntakeMessage,
  useManualGrade,
  useReleaseManualGrade,
  useSetManualGrade,
} from "./useManualGrade";

/**
 * 老师给一次提交打分（P11 "待评分" 的落地入口）。
 *
 * 三件事必须同时可见，否则分数无法被解释：
 * 1. **学生交了什么**（来信主题/正文摘录/附件下载）；
 * 2. **这次是首评还是改分**——改分必须写理由，服务端缺理由会 409，这里先拦一道；
 * 3. **乐观锁**：把读到的 `updated_at` 原样带回，别人先改过就会得到 409 而不是覆盖。
 */
export function ManualGradeDrawer({
  submissionId,
  onClose,
}: {
  submissionId: string;
  onClose: () => void;
}) {
  const existing = useManualGrade(submissionId);
  const hasManualGrade = existing.data != null;
  const [score, setScore] = useState("");
  const [comment, setComment] = useState("");
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  // 改分时把当前分数填进输入框：老师要先看到"现在是多少"再决定改成多少。
  // 用 effect 而不是渲染期赋值——渲染期 setState 会让用户刚输入的值被预填覆盖，
  // 这正是"明明输入了 92、提交却是 70"的那类 bug。
  // 不预填输入框：预填会被任何一次重渲染覆盖掉老师刚敲的数字（实测会静默提交旧分）。
  // 当前分数改为在输入框上方只读展示，老师据此决定改成多少。

  const setGrade = useSetManualGrade();
  const release = useReleaseManualGrade();
  const intake = useIntakeMessage(submissionId);

  const parsed = Number(score);
  const scoreValid = score.trim() !== "" && Number.isFinite(parsed) && parsed >= 0 && parsed <= 100;
  const reasonRequired = hasManualGrade;
  const canSave = scoreValid && (!reasonRequired || reason.trim().length > 0) && !setGrade.isPending;

  async function save() {
    setError(null);
    setMessage(null);
    try {
      await setGrade.mutateAsync({
        submissionId,
        score: parsed,
        comment,
        reason,
        expectedUpdatedAt: existing.data?.updated_at ?? new Date().toISOString(),
      });
      setMessage(t("manualGrade.saved"));
    } catch (caught) {
      setError(caught);
    }
  }

  async function withdraw() {
    setError(null);
    setMessage(null);
    try {
      await release.mutateAsync(submissionId);
      setMessage(t("manualGrade.released"));
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <Drawer title={t("manualGrade.title")} onClose={onClose} testId="manual-grade-drawer">
      <div className="stack">
        <section>
          <h3>{t("manualGrade.intake")}</h3>
          {intake.isPending ? <p className="muted">{t("common.loading")}</p> : null}
          {intake.data === null ? (
            <p className="muted" data-testid="intake-none">
              {t("manualGrade.noMessage")}
            </p>
          ) : null}
          {intake.data ? (
            <div data-testid="intake-message">
              <p className="muted">
                {intake.data.sender} · {intake.data.subject ?? t("common.none")}
              </p>
              <pre className="intake-body" data-testid="intake-body">
                {intake.data.body_excerpt}
              </pre>
              {intake.data.truncated ? (
                <p className="muted" data-testid="intake-truncated">
                  {t("manualGrade.truncated")}
                </p>
              ) : null}
              {(intake.data.attachment_names ?? []).length > 0 ? (
                <ul data-testid="intake-attachments">
                  {(intake.data.attachment_names ?? []).map((name) => (
                    <li key={name}>
                      <a
                        href={`/api/v1/submissions/${submissionId}/files?path=${encodeURIComponent(name)}`}
                        download
                        data-testid={`download-${name}`}
                      >
                        {t("manualGrade.download", { name })}
                      </a>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
          {intake.isError ? <p className="form-error">{describeApiError(intake.error)}</p> : null}
        </section>

        <section>
          <h3>{t("manualGrade.grade")}</h3>
          {existing.data ? (
            <p className="muted" data-testid="manual-current">
              {t("manualGrade.current", { score: existing.data.score })}
            </p>
          ) : null}
          <label className="field">
            <span>{t("manualGrade.score")}</span>
            <input
              className="input"
              type="number"
              min={0}
              max={100}
              step={0.1}
              value={score}
              data-testid="manual-score"
              onChange={(event) => setScore(event.target.value)}
            />
          </label>
          <label className="field">
            <span>{t("manualGrade.comment")}</span>
            <textarea
              className="input"
              rows={3}
              value={comment}
              data-testid="manual-comment"
              onChange={(event) => setComment(event.target.value)}
            />
          </label>
          {reasonRequired ? (
            <label className="field">
              <span>{t("manualGrade.reason")}</span>
              <textarea
                className="input"
                rows={2}
                value={reason}
                data-testid="manual-reason"
                onChange={(event) => setReason(event.target.value)}
              />
              <span className="muted">{t("manualGrade.reasonHint")}</span>
            </label>
          ) : null}
        </section>

        {message ? <p data-testid="manual-grade-message">{message}</p> : null}
        {error ? (
          <p className="form-error" data-testid="manual-grade-error">
            {describeApiError(error)}
            {isApiError(error) && error.status === 409 ? ` ${t("manualGrade.conflictHint")}` : null}
          </p>
        ) : null}

        <div className="row">
          <button
            type="button"
            className="button button--primary"
            disabled={!canSave}
            data-testid="manual-save"
            onClick={() => void save()}
          >
            {t("manualGrade.save")}
          </button>
          {hasManualGrade ? (
            <button
              type="button"
              className="button button--ghost"
              disabled={release.isPending}
              data-testid="manual-release"
              onClick={() => void withdraw()}
            >
              {t("manualGrade.release")}
            </button>
          ) : null}
        </div>
      </div>
    </Drawer>
  );
}
