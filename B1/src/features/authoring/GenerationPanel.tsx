import { useEffect, useRef, useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { QuestionDraftGeneration, QuestionDraftProposal } from "../../api/types";
import { formatDateTime } from "../../ui/format";
import { Banner, Field, Modal, Panel, StatusPill } from "../../ui/components";
import { SafeMarkdown } from "../questions/SafeMarkdown";
import { fieldLabel, parseArgv, parseTags, type DraftForm } from "./draft";
import {
  useCreateGeneration,
  useDiscardGeneration,
  useGeneration,
  useGenerationCapabilities,
} from "./useGeneration";

/**
 * 「让 AI 帮你出题」。
 *
 * 两条交互规则，都是为了不再出现"按钮去哪了"：
 *
 * 1. **入口在标题栏，面板由按钮展开**，所以第一屏就能看到，不必滚到表单底部；
 * 2. **表单与「生成草稿」按钮始终渲染**，不可用时只是禁用并说明原因（还没选课程 / AI 未开通 /
 *    当前账号没有出题权限），而不是整块消失。
 *
 * 生成出来的只是草稿：由教师检查、修改，点「保存草稿」才会写进题库；发布前仍要检验。
 * 界面上不出现任何内部字段名、摘要、编号或状态码——给出的是教师能判断的结论。
 */

const STATUS_TONE = { ready: "ok", needs_review: "warn", failed: "bad", unavailable: "idle" } as const;

function statusText(generation: QuestionDraftGeneration): string {
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
  const cases = proposal.cases ?? [];
  const fixtures = proposal.fixtures ?? [];
  return (
    <div className="stack" data-testid="generation-proposal">
      <div className="inline">
        <h3>{t("authoring.generation.previewTitle")}</h3>
        <StatusPill tone="warn">{t("authoring.generation.protectedNotice")}</StatusPill>
      </div>

      {proposal.stem_markdown.trim() === "" ? (
        <p className="muted">{t("authoring.generation.emptyStem")}</p>
      ) : (
        <SafeMarkdown source={proposal.stem_markdown} testId="generation-preview-stem" />
      )}

      <ul className="stack" data-testid="generation-proposal-facts">
        <li>
          {t("authoring.title")}：{proposal.title.trim() === "" ? "—" : proposal.title}
        </li>
        <li>
          {t("authoring.language")}：{t(`questions.language.${proposal.language}`)}
        </li>
        <li>
          {t("authoring.adapterKind")}：{t(`questions.adapter.${proposal.adapter_kind}`)}
        </li>
        <li>
          {t("authoring.tags")}：
          {(proposal.tags ?? []).length === 0 ? "—" : (proposal.tags ?? []).join("、")}
        </li>
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

        <h3>{t("authoring.generation.cases", { count: cases.length })}</h3>
        {cases.length === 0 ? (
          <p className="muted">{t("authoring.generation.noCases")}</p>
        ) : (
          <ol data-testid="generation-cases">
            {cases.map((item) => (
              <li key={item.ordinal}>
                <strong>{item.name.trim() === "" ? "—" : item.name}</strong>
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
                      {t("authoring.generation.caseStdout")}: <code>{item.expected_stdout}</code>
                    </span>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        )}

        {fixtures.length > 0 ? (
          <>
            <h3>{t("authoring.generation.fixtures")}</h3>
            <ul data-testid="generation-fixtures">
              {fixtures.map((item) => (
                <li key={`${item.path}-${item.mount}`}>
                  <code>{item.path}</code> ·{" "}
                  {item.mount === "input"
                    ? t("authoring.fixture.mountInput")
                    : t("authoring.fixture.mountExpected")}
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
        <button type="button" className="button" data-testid="generation-apply" onClick={onApply}>
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
  form: DraftForm;
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
  const [discardedId, setDiscardedId] = useState<string | null>(null);
  const appliedOnce = useRef<string | null>(null);

  const objectiveLimit = capabilities.data?.learning_objective_max_chars ?? 0;
  const constraintsLimit = capabilities.data?.constraints_max_chars ?? 0;
  const caseLimit = capabilities.data?.desired_case_count_max ?? 0;
  const providerAvailable = capabilities.data?.provider_status === "enabled";
  const forbidden =
    capabilities.isError && isApiError(capabilities.error) && capabilities.error.isForbidden;

  const candidate = create.data ?? restored.data ?? null;
  const generation =
    candidate !== null && candidate.generation_id === discardedId ? null : candidate;

  // 恢复：服务端原始候选只填一次，之后不再覆盖教师的编辑。
  useEffect(() => {
    if (!generationId || !restored.data?.proposal) return;
    if (appliedOnce.current === generationId) return;
    appliedOnce.current = generationId;
    setAppliedId(generationId);
    onApply(restored.data.proposal);
  }, [generationId, onApply, restored.data]);

  const overLimit =
    objectiveLimit > 0 && objective.length > objectiveLimit
      ? t("authoring.generation.objectiveTooLong", { max: objectiveLimit })
      : null;
  const ready = capabilityReady({
    courseId,
    pending: capabilities.isPending,
    error: capabilities.isError,
    forbidden: Boolean(forbidden),
    providerAvailable,
  });
  const missing = !courseId
    ? t("authoring.generation.needCourseShort")
    : forbidden
      ? t("authoring.generation.noPermission")
      : capabilities.isError
        ? t("authoring.generation.statusUnknown")
        : !providerAvailable
          ? t("authoring.generation.notEnabled")
          : objective.trim() === ""
            ? t("authoring.generation.objectiveRequired")
            : overLimit;
  const canGenerate = ready && missing === null && !create.isPending;

  const submit = () => {
    if (!courseId || !canGenerate) return;
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
      <p className="muted" data-testid="generation-disclaimer">
        {t("authoring.generation.intro")}
      </p>

      {/* 一句话状态：不可用只影响按钮，不影响你继续手动填写。 */}
      <div className="inline" data-testid="generation-status-line">
        <StatusPill tone={ready ? "ok" : capabilityTone({ courseId, pending: capabilities.isPending })}>
          {ready ? t("authoring.generation.readyPill") : t("authoring.generation.notReadyPill")}
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
            {t("authoring.generation.budget", { remaining: capabilities.data.requests_remaining })}
          </span>
        )}
      </div>

      {!courseId ? (
        <Banner tone="info" testId="generation-no-course">
          {t("authoring.generation.needCourse")}
        </Banner>
      ) : forbidden ? (
        <Banner tone="warn" testId="generation-forbidden">
          {t("authoring.generation.noPermission")}
        </Banner>
      ) : capabilities.isError ? (
        <Banner tone="warn" testId="generation-capabilities-error">
          {t("authoring.generation.statusUnknown")}
        </Banner>
      ) : !capabilities.isPending && !providerAvailable ? (
        <Banner tone="info" testId="generation-unavailable">
          {t("authoring.generation.notEnabledHint")}
        </Banner>
      ) : null}

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
            placeholder={t("authoring.generation.objectivePlaceholder")}
            value={objective}
            maxLength={objectiveLimit > 0 ? objectiveLimit : undefined}
            onChange={(event) => setObjective(event.target.value)}
          />
        </Field>
        {objectiveLimit > 0 ? (
          <p className="muted" data-testid="generation-objective-limit">
            {t("authoring.generation.limitHint", { used: objective.length, max: objectiveLimit })}
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
              placeholder={caseLimit > 0 ? `1–${caseLimit}` : "2"}
              value={caseCount}
              onChange={(event) => setCaseCount(event.target.value)}
            />
          </Field>
        </div>

        <Field label={t("authoring.generation.constraints")} htmlFor="generation-constraints-input">
          <textarea
            id="generation-constraints-input"
            rows={2}
            placeholder={t("authoring.generation.constraintsPlaceholder")}
            value={constraints}
            maxLength={constraintsLimit > 0 ? constraintsLimit : undefined}
            onChange={(event) => setConstraints(event.target.value)}
          />
        </Field>
      </fieldset>

      <div className="inline">
        <button
          type="button"
          className="button"
          data-testid="generation-generate"
          disabled={!canGenerate}
          onClick={submit}
        >
          {create.isPending ? t("authoring.generation.generating") : t("authoring.generation.generate")}
        </button>
        <span className="muted" data-testid="generation-reason">
          {missing ?? t("authoring.generation.manualNote")}
        </span>
      </div>

      {generationError !== null && generationError !== undefined ? (
        isApiError(generationError) && generationError.status === 429 ? (
          <Banner tone="warn" testId="generation-budget-exhausted">
            {t("authoring.generation.budgetExhausted")}
          </Banner>
        ) : isApiError(generationError) && generationError.status === 502 ? (
          <Banner tone="error" testId="generation-provider-failed">
            {t("authoring.generation.providerFailed")}
          </Banner>
        ) : isApiError(generationError) && generationError.isNotFound ? (
          <Banner tone="info" testId="generation-gone">
            {t("authoring.generation.gone")}
          </Banner>
        ) : isApiError(generationError) && generationError.isForbidden ? (
          <Banner tone="warn" testId="generation-forbidden">
            {t("authoring.generation.noPermission")}
          </Banner>
        ) : (
          <Banner tone="error" testId="generation-error">
            {t("authoring.generation.failedHint")}
          </Banner>
        )
      ) : null}

      {generation ? (
        <div className="stack" data-testid="generation-result">
          <div className="inline">
            <StatusPill tone={STATUS_TONE[generation.status]} testId="generation-status">
              {statusText(generation)}
            </StatusPill>
            {generation.created_at ? (
              <span className="muted">
                {t("authoring.generation.createdAt", {
                  time: formatDateTime(generation.created_at, "—"),
                })}
              </span>
            ) : null}
            {generation.expires_at ? (
              <span className="muted" data-testid="generation-expiry">
                {t("authoring.generation.expiresAt", {
                  time: formatDateTime(generation.expires_at, "—"),
                })}
              </span>
            ) : null}
          </div>

          {generation.status !== "ready" ? (
            <Banner
              tone={generation.status === "failed" ? "error" : "info"}
              testId={`generation-status-${generation.status}`}
            >
              {t(`authoring.generation.status.${generation.status}`)}
            </Banner>
          ) : null}

          {generation.warnings.length > 0 ? (
            <div data-testid="generation-warnings">
              <h3>{t("authoring.generation.warnings", { count: generation.warnings.length })}</h3>
              <ul>
                {generation.warnings.map((warning, index) => (
                  <li key={`${warning.field}-${index}`}>
                    <strong>{fieldLabel(warning.field)}</strong>：{warning.message}
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

          {generation.provenance ? (
            <details data-testid="generation-provenance">
              <summary>{t("authoring.generation.provenanceLabel")}</summary>
              <ul className="muted">
                <li>{t("authoring.generation.provenanceModel", { model: generation.provenance.model })}</li>
              </ul>
            </details>
          ) : null}

          <ul className="muted" data-testid="generation-disclaimers">
            {generation.disclaimers.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>

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

          {discard.isError ? (
            <Banner tone="error" testId="generation-discard-error">
              {t("authoring.generation.discardFailed")}
            </Banner>
          ) : null}
        </div>
      ) : null}

      {discard.isSuccess ? (
        <Banner tone="success" testId="generation-discarded">
          {t("authoring.generation.discarded")}
        </Banner>
      ) : null}

      {confirmDiscard && generation?.generation_id ? (
        <Modal
          title={t("authoring.generation.discardConfirmTitle")}
          onClose={() => setConfirmDiscard(false)}
        >
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

/** 能不能出题：只取决于课程、权限、AI 是否开通；与目标是否填好无关（那是 `missing`）。 */
function capabilityReady(input: {
  courseId: string | null;
  pending: boolean;
  error: boolean;
  forbidden: boolean;
  providerAvailable: boolean;
}): boolean {
  if (!input.courseId || input.pending || input.error || input.forbidden) return false;
  return input.providerAvailable;
}

function capabilityTone(input: { courseId: string | null; pending: boolean }): "idle" | "warn" {
  if (!input.courseId) return "idle";
  return input.pending ? "idle" : "warn";
}
