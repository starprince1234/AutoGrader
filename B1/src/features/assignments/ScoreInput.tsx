import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { t } from "../../i18n";
import { describeApiError, isApiError } from "../../api/problem";
import { queryKeys } from "../../app/queryClient";
import { useManualGrade, useSetManualGrade, type ManualGradeView } from "../grades/useManualGrade";

/**
 * 一个输入框完成"看分 / 改分"。
 *
 * 规则（与契约一致，不在此处发明）：
 * - 框里默认是**AI 分数**（`GradeRow.score`）。不动它 = 不提交任何写请求，成绩仍是 AI 分。
 * - 直接改数字才表示"老师给这个人改分"：失焦或回车即保存，`score_source` 由服务端写成 `manual`。
 * - **已经人工改过一次之后再改**必须有理由：契约在缺理由时返回 409，所以这里先拦一道，
 *   并把理由输入框放在同一行，而不是再弹一个抽屉。
 * - 数值格式按百分制：0–100，最多一位小数；超范围或格式不对只显示错误，不发请求。
 */

const MAX_SCORE = 100;
/** 0–100，最多一位小数（`80` / `80.5` / `100` / `100.0`）。 */
const SCORE_PATTERN = /^(100(\.0)?|\d{1,2}(\.\d)?)$/;

function formatScore(score: number | null): string {
  if (score === null || !Number.isFinite(score)) return "";
  return String(Math.round(score * 10) / 10);
}

type Validation = { ok: true; value: number } | { ok: false; message: string };

function validate(raw: string): Validation {
  const text = raw.trim();
  if (text === "") return { ok: false, message: t("grading.scoreRequired") };
  if (!SCORE_PATTERN.test(text)) return { ok: false, message: t("grading.scoreFormat") };
  const value = Number(text);
  if (!Number.isFinite(value) || value < 0 || value > MAX_SCORE) {
    return { ok: false, message: t("grading.scoreRange") };
  }
  return { ok: true, value };
}

export function ScoreInput({
  submissionId,
  aiScore,
}: {
  submissionId: string;
  /** AI 分数（契约 `GradeRow.score`；评测未出分时为 null）。 */
  aiScore: number | null;
}) {
  const manualQuery = useManualGrade(submissionId);
  const setGrade = useSetManualGrade();
  const queryClient = useQueryClient();
  // 保存成功后先用响应更新界面，避免等整页重取。
  const [saved, setSaved] = useState<ManualGradeView | null>(null);
  const [draft, setDraft] = useState<string>(formatScore(aiScore));
  const [dirty, setDirty] = useState(false);
  const [reason, setReason] = useState("");
  const [feedback, setFeedback] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const current = saved ?? manualQuery.data ?? null;
  const hasManual = current !== null;
  const displayScore = current?.score ?? aiScore;

  // 外部值到位（AI 分或已有人工分）后同步输入框；用户正在编辑时不覆盖。
  useEffect(() => {
    if (!dirty) setDraft(formatScore(displayScore));
  }, [displayScore, dirty]);

  const pending = setGrade.isPending;

  const commit = async () => {
    setFeedback(null);
    if (manualQuery.isPending) return;
    const parsed = validate(draft);
    if (!parsed.ok) {
      setFeedback({ tone: "error", text: parsed.message });
      return;
    }
    // 没改数字就不写：默认跟随 AI 分数，不是一个"人工分"。
    if (displayScore !== null && parsed.value === displayScore) {
      setDirty(false);
      return;
    }
    if (hasManual && reason.trim() === "") {
      setFeedback({ tone: "error", text: t("grading.scoreReasonRequired") });
      return;
    }
    try {
      const next = await setGrade.mutateAsync({
        submissionId,
        score: parsed.value,
        reason: hasManual ? reason.trim() : undefined,
        // 乐观锁：把读到的那一版带回去；没读过人工分时用当前时间（契约对首评的约定）。
        expectedUpdatedAt: current?.updated_at ?? new Date().toISOString(),
      });
      setSaved(next);
      setDirty(false);
      setReason("");
      setFeedback({ tone: "ok", text: t("grading.scoreSaved", { score: next.score }) });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.intakeMessage(`grade:${submissionId}`),
      });
    } catch (error) {
      setFeedback({
        tone: "error",
        text: isApiError(error) ? describeApiError(error) : t("common.requestFailed"),
      });
    }
  };

  return (
    <div className="grading-score-box" data-testid="grading-score">
      <label className="grading-score-box__field" htmlFor={`score-${submissionId}`}>
        <span className="muted">{t("grading.scoreLabel")}</span>
        <input
          id={`score-${submissionId}`}
          className="input grading-score-box__input"
          type="number"
          inputMode="decimal"
          min={0}
          max={MAX_SCORE}
          step="0.1"
          value={draft}
          placeholder={t("grading.scorePlaceholder")}
          disabled={pending || manualQuery.isPending}
          data-testid="grading-score-input"
          onChange={(event) => {
            setDraft(event.target.value);
            setDirty(true);
            setFeedback(null);
          }}
          onBlur={() => {
            if (dirty) void commit();
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void commit();
            }
            if (event.key === "Escape") {
              setDraft(formatScore(displayScore));
              setDirty(false);
              setFeedback(null);
            }
          }}
        />
      </label>

      <p className="muted grading-score-box__hint" data-testid="grading-score-hint">
        {displayScore === null
          ? t("grading.scoreNoAi")
          : t("grading.scoreAiHint", { score: formatScore(displayScore) })}
        {pending ? ` ${t("grading.scoreSaving")}` : ""}
      </p>

      {/* 二次改分才需要理由：首评（把 AI 分改成教师分）走同一个框，不额外打扰。 */}
      {hasManual && dirty ? (
        <label className="grading-score-box__field" htmlFor={`reason-${submissionId}`}>
          <span className="muted">{t("grading.scoreReasonLabel")}</span>
          <input
            id={`reason-${submissionId}`}
            className="input"
            value={reason}
            maxLength={500}
            placeholder={t("grading.scoreReasonPlaceholder")}
            data-testid="grading-score-reason"
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
      ) : null}

      {feedback ? (
        <p
          className={feedback.tone === "ok" ? "muted" : "form-error"}
          data-testid="grading-score-feedback"
        >
          {feedback.text}
        </p>
      ) : null}
    </div>
  );
}
