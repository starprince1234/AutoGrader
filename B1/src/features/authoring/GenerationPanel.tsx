import { useEffect, useRef, useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type {
  QuestionDraftGeneration,
  QuestionDraftProposal,
} from "../../api/types";
import { formatDateTime } from "../../ui/format";
import {
  Banner,
  ErrorState,
  Field,
  Modal,
  Panel,
  Spinner,
  StatusPill,
} from "../../ui/components";
import { SafeMarkdown } from "../questions/SafeMarkdown";
import { parseArgv, parseTags, type DraftForm } from "./draft";
import {
  useCreateGeneration,
  useDiscardGeneration,
  useGeneration,
  useGenerationCapabilities,
} from "./useGeneration";

/**
 * 「按教学目标生成题目」（AI-FLOW-01A）。
 *
 * 这个面板把四件事分开呈现，它们的区别是产品要求，不是排版偏好：
 *
 * 1. **状态**——`ready` / `needs_review` / `failed` / `unavailable` 是服务端给出的结论位；
 *    `ready` 只表示"结构可读、可以进入编辑器"，`needs_review` 表示"读得出来但有字段要你补齐"，
 *    两者都**不是**"题目正确、公平或可发布"的证明。
 * 2. **字段问题**——`warnings[].field` 指向具体控件，因此每条警告都带着它属于哪个字段，
 *    教师能直接回到表单去改，而不是读一段泛泛的说明。
 * 3. **来源**——provider / model / prompt_version 与过期时间一起显示；不显示 API key、provider URL
 *    或他人的用量。免责声明原样展示，且与候选同屏。
 * 4. **两轨分离**——生成（本面板）只产出候选；「检查这道题」（`AuthoringAssistancePanel`）只给建议；
 *    确定性诊断（`ComparisonDiagnosticsPanel`）只报规则命中。三者不混成一段聊天记录。
 *
 * 刷新语义：`generation_id` 只用于取回**服务端原始候选**。教师尚未保存的字段编辑留在内存里，
 * 离开页面即丢失——所以本页在离开前会提示，而不是假装恢复过它们。
 */

const STATUS_TONE = {
  ready: "ok",
  needs_review: "warn",
  failed: "bad",
  unavailable: "idle",
} as const;

function statusLabel(generation: QuestionDraftGeneration): string {
  return t(`authoring.generation.status.${generation.status}`);
}

function ProposalPreview({
  proposal,
  onApply,
  applied,
}: {
  proposal: QuestionDraftProposal;
  onApply: () => void;
  applied: boolean;
}) {
  return (
    <div className="stack" data-testid="generation-proposal">
      <div className="inline">
        <h3>{t("authoring.generation.previewTitle")}</h3>
        <StatusPill tone="warn">{t("authoring.generation.protectedNotice")}</StatusPill>
      </div>

      {proposal.stem_markdown.trim() === "" ? (
        <p className="muted">{t("authoring.generation.emptyStem")}</p>
      ) : (
        <div data-testid="generation-proposal-stem">
          <SafeMarkdown source={proposal.stem_markdown} testId="generation-preview-stem" />
        </div>
      )}

      <ul className="stack" data-testid="generation-proposal-facts">
        <li>
          {t("authoring.title")}：{proposal.title.trim() === "" ? t("common.none") : proposal.title}
        </li>
        <li>
          {t("authoring.language")}：{t(`questions.language.${proposal.language}`)}
        </li>
        <li>
          {t("authoring.adapterKind")}：{t(`questions.adapter.${proposal.adapter_kind}`)}
        </li>
        <li>
          {t("authoring.tags")}：
          {(proposal.tags ?? []).length === 0 ? t("common.none") : (proposal.tags ?? []).join(", ")}
        </li>
        {proposal.difficulty ? (
          <li>
            {t("authoring.generation.difficulty")}：{proposal.difficulty}
          </li>
        ) : null}
        {proposal.adapter?.args?.length ? (
          <li>
            {t("authoring.adapterArgs")}：
            <span className="mono-id">{parseArgv(proposal.adapter.args.join(" ")).join(" ")}</span>
          </li>
        ) : null}
      </ul>

      <details data-testid="generation-protected">
        <summary>{t("authoring.generation.protectedSummary")}</summary>
        <p className="muted">{t("authoring.generation.protectedHint")}</p>
        <h3>{t("authoring.referenceAnswer")}</h3>
        {proposal.reference_answer ? (
          <pre data-testid="generation-reference">{proposal.reference_answer}</pre>
        ) : (
          <p className="muted">{t("authoring.generation.noReference")}</p>
        )}

        <h3>{t("authoring.generation.cases", { count: proposal.cases?.length ?? 0 })}</h3>
        {(proposal.cases ?? []).length === 0 ? (
          <p className="muted">{t("authoring.generation.noCases")}</p>
        ) : (
          <ol data-testid="generation-cases">
            {(proposal.cases ?? []).map((item) => (
              <li key={item.ordinal}>
                <strong>{item.name.trim() === "" ? t("common.none") : item.name}</strong>
                {" · "}
                {t("authoring.caseWeight")}{" "}
                {item.weight === null || item.weight === undefined
                  ? t("authoring.generation.noWeight")
                  : item.weight}
                <div className="muted">
                  {item.stdin ? (
                    <span>
                      {t("authoring.generation.caseStdin")}: <code>{item.stdin}</code>
                    </span>
                  ) : null}
                  {item.expected_stdout ? (
                    <span>
                      {t("authoring.generation.caseStdout")}:{" "}
                      <code>{item.expected_stdout}</code>
                    </span>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        )}

        {(proposal.fixtures ?? []).length > 0 ? (
          <>
            <h3>{t("authoring.generation.fixtures")}</h3>
            <ul data-testid="generation-fixtures">
              {(proposal.fixtures ?? []).map((item) => (
                <li key={`${item.path}-${item.mount}`}>
                  <code>{item.path}</code> · {item.mount}
                  <div className="muted">
                    {t("authoring.generation.fixtureTextHint", { chars: item.content_text.length })}
                  </div>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </details>

      {proposal.notes ? <p className="muted">{proposal.notes}</p> : null}

      <div className="inline">
        <button
          type="button"
          className="button"
          data-testid="generation-apply"
          onClick={onApply}
        >
          {applied ? t("authoring.generation.reapply") : t("authoring.generation.apply")}
        </button>
        <span className="muted">{t("authoring.generation.applyHint")}</span>
      </div>
    </div>
  );
}

export function GenerationPanel({
  courseId,
  form,
  generationId,
  onGenerationId,
  onApply,
}: {
  courseId: string | null;
  /** 读取教师已选的语言与 adapter：它们是候选必须满足的**约束**，不是模型的建议。 */
  form: DraftForm;
  /** 当前 URL 里的候选 id（刷新恢复用）。 */
  generationId: string | null;
  onGenerationId: (next: string | null) => void;
  onApply: (proposal: QuestionDraftProposal) => void;
}) {
  const capabilities = useGenerationCapabilities(courseId);
  const restored = useGeneration(generationId);
  const create = useCreateGeneration();
  const discard = useDiscardGeneration();

  const [objective, setObjective] = useState("");
  const [difficulty, setDifficulty] = useState("");
  const [caseCount, setCaseCount] = useState("");
  const [constraints, setConstraints] = useState("");
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [appliedId, setAppliedId] = useState<string | null>(null);
  const appliedOnce = useRef<string | null>(null);
  /**
   * 被丢弃的候选 id。丢弃后 `create.data` 仍在缓存里，若不记住这个 id，面板会继续显示
   * 一份服务端已经删掉正文的候选——那正是"页面说还有、服务端说没有"的典型缺陷。
   */
  const [discardedId, setDiscardedId] = useState<string | null>(null);

  const objectiveLimit = capabilities.data?.learning_objective_max_chars ?? 0;
  const constraintsLimit = capabilities.data?.constraints_max_chars ?? 0;
  const caseLimit = capabilities.data?.desired_case_count_max ?? 0;
  const providerAvailable = capabilities.data?.provider_status === "enabled";

  const candidate = create.data ?? restored.data ?? null;
  const generation =
    candidate !== null && candidate.generation_id === discardedId ? null : candidate;

  // 恢复：服务端原始候选只填一次，之后不再覆盖教师的编辑（否则每次重取都会把改动冲掉）。
  useEffect(() => {
    if (!generationId || !restored.data?.proposal) return;
    if (appliedOnce.current === generationId) return;
    appliedOnce.current = generationId;
    setAppliedId(generationId);
    onApply(restored.data.proposal);
  }, [generationId, onApply, restored.data]);

  // 目标为空是"还没填"，不是"填错了"：只在越界时把 Field 标成错误，
  // 空值以按钮旁的提示出现，避免一进页面就是一个红色字段。
  const objectiveHint =
    objective.trim() === "" ? t("authoring.generation.objectiveRequired") : null;
  const overLimit =
    objectiveLimit > 0 && objective.length > objectiveLimit
      ? t("authoring.generation.objectiveTooLong", { max: objectiveLimit })
      : null;
  const canGenerate =
    Boolean(courseId) &&
    providerAvailable &&
    objective.trim() !== "" &&
    overLimit === null &&
    !create.isPending;

  const submit = () => {
    if (!courseId || !canGenerate) return;
    // 只发送能解析成正整数的目标用例数：把 `parseInt` 的 NaN 序列化成 `null` 是在拿
    // JSON 的宽松语义掩盖一个输入错误。
    const desiredCases = /^\d+$/.test(caseCount.trim())
      ? Math.max(1, Number.parseInt(caseCount.trim(), 10))
      : null;
    create.mutate(
      {
        courseId,
        request: {
          learning_objective: objective,
          language: form.language,
          adapter_kind: form.adapterKind,
          difficulty: difficulty.trim() === "" ? null : difficulty.trim(),
          tags: parseTags(form.tags).tags,
          desired_case_count: desiredCases,
          constraints: constraints.trim() === "" ? null : constraints,
        },
      },
      {
        onSuccess: (result) => {
          setDiscardedId(null);
          if (result.generation_id) {
            onGenerationId(result.generation_id);
            if (result.proposal) {
              appliedOnce.current = result.generation_id;
              setAppliedId(result.generation_id);
              onApply(result.proposal);
            }
          }
        },
      },
    );
  };

  const generationError = create.error ?? restored.error;

  return (
    <Panel title={t("authoring.generation.title")} testId="generation-panel">
      <Banner tone="warn" testId="generation-disclaimer">
        {t("authoring.generation.intro")}
      </Banner>

      {courseId === null ? (
        <Banner tone="info" testId="generation-no-course">
          {t("authoring.generation.needCourse")}
        </Banner>
      ) : capabilities.isPending ? (
        <Spinner />
      ) : capabilities.isError ? (
        <ErrorState error={capabilities.error} testId="generation-capabilities-error" />
      ) : (
        <>
          <div className="inline" data-testid="generation-constraints">
            <StatusPill tone={providerAvailable ? "ok" : "idle"}>
              {providerAvailable
                ? t("authoring.generation.providerReady", {
                    provider: capabilities.data?.provider ?? t("common.none"),
                    model: capabilities.data?.model ?? t("common.none"),
                  })
                : t("authoring.generation.providerOff")}
            </StatusPill>
            <span className="muted">
              {t("authoring.generation.constraintNote", {
                language: t(`questions.language.${form.language}`),
                adapter: t(`questions.adapter.${form.adapterKind}`),
              })}
            </span>
            {capabilities.data?.requests_remaining === null ||
            capabilities.data?.requests_remaining === undefined ? null : (
              <span className="muted" data-testid="generation-budget">
                {t("authoring.generation.budget", {
                  remaining: capabilities.data.requests_remaining,
                })}
              </span>
            )}
          </div>

          {providerAvailable ? null : (
            <Banner tone="info" testId="generation-unavailable">
              {t("authoring.generation.unavailableHint")}
            </Banner>
          )}

          <fieldset className="case-card">
            <legend className="case-card__legend">{t("authoring.generation.objective")}</legend>
            <Field
              label={t("authoring.generation.objectiveLabel")}
              htmlFor="generation-objective"
              error={overLimit}
            >
              <textarea
                id="generation-objective"
                rows={3}
                value={objective}
                maxLength={objectiveLimit > 0 ? objectiveLimit : undefined}
                onChange={(event) => setObjective(event.target.value)}
              />
            </Field>
            {objectiveLimit > 0 ? (
              <p className="muted" data-testid="generation-objective-limit">
                {t("authoring.generation.limitHint", {
                  used: objective.length,
                  max: objectiveLimit,
                })}
              </p>
            ) : null}

            <div className="form-grid">
              <Field label={t("authoring.generation.difficulty")} htmlFor="generation-difficulty">
                <input
                  id="generation-difficulty"
                  value={difficulty}
                  onChange={(event) => setDifficulty(event.target.value)}
                />
              </Field>
              <Field label={t("authoring.generation.caseCount")} htmlFor="generation-case-count">
                <input
                  id="generation-case-count"
                  inputMode="numeric"
                  value={caseCount}
                  onChange={(event) => setCaseCount(event.target.value)}
                />
              </Field>
            </div>
            {caseLimit > 0 ? (
              <p className="muted">
                {t("authoring.generation.caseCountHint", { max: caseLimit })}
              </p>
            ) : null}

            <Field
              label={t("authoring.generation.constraints")}
              htmlFor="generation-constraints-input"
            >
              <textarea
                id="generation-constraints-input"
                rows={2}
                value={constraints}
                maxLength={constraintsLimit > 0 ? constraintsLimit : undefined}
                onChange={(event) => setConstraints(event.target.value)}
              />
            </Field>
            {constraintsLimit > 0 ? (
              <p className="muted">
                {t("authoring.generation.constraintsHint", { max: constraintsLimit })}
              </p>
            ) : null}
          </fieldset>

          <div className="inline">
            <button
              type="button"
              className="button"
              data-testid="generation-generate"
              disabled={!canGenerate}
              onClick={submit}
            >
              {create.isPending
                ? t("authoring.generation.generating")
                : t("authoring.generation.generate")}
            </button>
            {objectiveHint ? <span className="muted">{objectiveHint}</span> : null}
          </div>
          <p className="muted">{t("authoring.generation.manualNote")}</p>
        </>
      )}

      {generationError !== null && generationError !== undefined ? (
        isApiError(generationError) && generationError.status === 429 ? (
          <Banner tone="warn" testId="generation-budget-exhausted">
            <p>{t("authoring.generation.budgetExhausted")}</p>
            <p className="muted">
              {generationError.problem?.detail ?? t("common.none")}
            </p>
          </Banner>
        ) : isApiError(generationError) && generationError.status === 502 ? (
          <Banner tone="error" testId="generation-provider-failed">
            <p>{t("authoring.generation.providerFailed")}</p>
            <p className="muted">
              {generationError.problem?.detail ?? t("common.none")}
            </p>
          </Banner>
        ) : isApiError(generationError) && generationError.isNotFound ? (
          <Banner tone="info" testId="generation-gone">
            {t("authoring.generation.gone")}
          </Banner>
        ) : (
          <ErrorState error={generationError} testId="generation-error" />
        )
      ) : null}

      {generation ? (
        <div className="stack" data-testid="generation-result">
          <div className="inline">
            <StatusPill tone={STATUS_TONE[generation.status]} testId="generation-status">
              {statusLabel(generation)}
            </StatusPill>
            {generation.provenance ? (
              <span className="muted" data-testid="generation-provenance">
                {t("authoring.generation.provenance", {
                  provider: generation.provenance.provider,
                  model: generation.provenance.model,
                  promptVersion: generation.provenance.prompt_version,
                })}
              </span>
            ) : null}
            {generation.created_at ? (
              <span className="muted">
                {t("authoring.generation.createdAt", {
                  time: formatDateTime(generation.created_at, t("common.none")),
                })}
              </span>
            ) : null}
            {generation.expires_at ? (
              <span className="muted" data-testid="generation-expiry">
                {t("authoring.generation.expiresAt", {
                  time: formatDateTime(generation.expires_at, t("common.none")),
                })}
              </span>
            ) : null}
          </div>

          {generation.status === "unavailable" ? (
            <Banner tone="info" testId="generation-status-unavailable">
              {t("authoring.generation.status.unavailable")}
            </Banner>
          ) : null}
          {generation.status === "failed" ? (
            <Banner tone="error" testId="generation-status-failed">
              {t("authoring.generation.status.failed")}
            </Banner>
          ) : null}
          {generation.status === "needs_review" ? (
            <Banner tone="warn" testId="generation-status-needs-review">
              {t("authoring.generation.status.needs_review")}
            </Banner>
          ) : null}
          {generation.status === "ready" ? (
            <Banner tone="info" testId="generation-status-ready">
              {t("authoring.generation.status.ready")}
            </Banner>
          ) : null}

          {generation.warnings.length > 0 ? (
            <div data-testid="generation-warnings">
              <h3>{t("authoring.generation.warnings", { count: generation.warnings.length })}</h3>
              <ul>
                {generation.warnings.map((warning, index) => (
                  <li key={`${warning.field}-${index}`}>
                    <code className="mono-id">{warning.field}</code>{" "}
                    <span className="muted">{warning.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {generation.proposal ? (
            <ProposalPreview
              proposal={generation.proposal}
              onApply={() => onApply(generation.proposal as QuestionDraftProposal)}
              applied={appliedId === generation.generation_id}
            />
          ) : null}

          {/* 免责声明与候选同屏、逐条原样展示。 */}
          <div data-testid="generation-disclaimers">
            <p className="muted">{t("authoring.generation.disclaimers")}</p>
            <ul className="muted">
              {generation.disclaimers.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
          </div>

          <div className="inline">
            <button
              type="button"
              className="button button--danger"
              data-testid="generation-discard"
              disabled={discard.isPending || !generation.generation_id}
              onClick={() => setConfirmDiscard(true)}
            >
              {discard.isPending
                ? t("authoring.generation.discarding")
                : t("authoring.generation.discard")}
            </button>
            <span className="muted">{t("authoring.generation.discardHint")}</span>
          </div>

          {discard.isError ? <ErrorState error={discard.error} testId="generation-discard-error" /> : null}
        </div>
      ) : null}

      {/* 丢弃成功的提示留在候选区块之外：丢弃后候选本身会立刻消失（服务端已删正文），
          如果提示也放在里面，教师就看不到"到底成没成"。 */}
      {discard.isSuccess ? (
        <Banner tone="success" testId="generation-discarded">
          {t("authoring.generation.discarded")}
        </Banner>
      ) : null}

      {confirmDiscard && generation?.generation_id ? (
        <Modal title={t("authoring.generation.discardConfirmTitle")} onClose={() => setConfirmDiscard(false)}>
          <p>{t("authoring.generation.discardConfirm")}</p>
          <div className="inline">
            <button
              type="button"
              className="button button--danger"
              data-testid="generation-discard-confirm"
              disabled={discard.isPending}
              onClick={() => {
                if (!courseId) return;
                discard.mutate(
                  { courseId, generationId: generation.generation_id as string },
                  {
                    onSuccess: () => {
                      setConfirmDiscard(false);
                      setAppliedId(null);
                      appliedOnce.current = null;
                      // 记住这个 id，好让缓存里那份候选立刻从面板上消失（服务端已删正文）。
                      setDiscardedId(generation.generation_id ?? null);
                      onGenerationId(null);
                    },
                  },
                );
              }}
            >
              {t("authoring.generation.discardConfirmAction")}
            </button>
            <button
              type="button"
              className="button button--ghost"
              onClick={() => setConfirmDiscard(false)}
            >
              {t("common.cancel")}
            </button>
          </div>
        </Modal>
      ) : null}
    </Panel>
  );
}
