import { useEffect, useRef, useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { Language, QuestionDraftGeneration, QuestionDraftProposal } from "../../api/types";
import {
  Banner,
  Field,
  Modal,
  Panel,
  StatusPill,
} from "../../ui/components";
import { SafeMarkdown } from "../questions/SafeMarkdown";
import { fieldLabel, parseTags, type DraftForm } from "./draft";
import { courseLabel as formatCourseLabel } from "../../api/directory";
import {
  useCreateGeneration,
  useDiscardGeneration,
  useGeneration,
  useGenerationCapabilities,
} from "./useGeneration";
import { Select } from "../../ui/Select";

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

/** 难度四档：简单 / 中等 / 较难 / 困难。顺序即下拉顺序，默认取「中等」。 */
const DIFFICULTY_LEVELS = ["easy", "medium", "advanced", "hard"] as const;
const LANGUAGES: readonly Language[] = ["python", "bash"];
const REFERENCE_TIER_COUNTS = [1, 2, 3] as const;
const OBJECTIVE_PRESETS = [
  {
    labelKey: "authoring.generation.examplePreset1Label",
    textKey: "authoring.generation.examplePreset1Text",
  },
  {
    labelKey: "authoring.generation.examplePreset2Label",
    textKey: "authoring.generation.examplePreset2Text",
  },
  {
    labelKey: "authoring.generation.examplePreset3Label",
    textKey: "authoring.generation.examplePreset3Text",
  },
] as const;

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
  const referenceTiers =
    (proposal.reference_answers ?? []).length > 0
      ? (proposal.reference_answers ?? [])
      : proposal.reference_answer
        ? [{ label: t("authoring.referenceDefaultTier"), body: proposal.reference_answer }]
        : [];
  return (
    <div className="stack" data-testid="generation-proposal">
      <div className="generation-preview__head">
        <h3>{t("authoring.generation.previewTitle")}</h3>
      </div>

      <section className="case-card stack" data-testid="generation-card-stem">
        <h4>{t("authoring.generation.cardStemTitle")}</h4>
        <div className="generation-facts" data-testid="generation-proposal-facts">
          <span>
            {t("authoring.title")}：{proposal.title.trim() === "" ? "—" : proposal.title}
          </span>
          <span>
            {t("authoring.language")}：{t(`questions.language.${proposal.language}`)}
          </span>
          <span>
            {t("authoring.tags")}：
            {(proposal.tags ?? []).length === 0 ? "—" : (proposal.tags ?? []).join("、")}
          </span>
          <span data-testid="generation-proposal-tier-count">
            {t("authoring.referenceTiersTitle")}：{referenceTiers.length}
          </span>
        </div>
        {proposal.stem_markdown.trim() === "" ? (
          <p className="muted">{t("authoring.generation.emptyStem")}</p>
        ) : (
          <SafeMarkdown source={proposal.stem_markdown} testId="generation-preview-stem" />
        )}
      </section>

      <section className="case-card stack" data-testid="generation-card-references">
        <h4>{t("authoring.generation.cardReferencesTitle", { count: referenceTiers.length })}</h4>
        {referenceTiers.length > 0 ? (
          <div className="stack" data-testid="generation-reference">
            {referenceTiers.map((tier, index) => (
              <div className="stack" key={`${index}-${tier.label}`}>
                <strong>{tier.label}</strong>
                <pre>{tier.body}</pre>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">{t("authoring.generation.noReference")}</p>
        )}
      </section>

      <section className="case-card stack" data-testid="generation-card-rubric">
        <h4>{t("authoring.generation.cardRubricTitle")}</h4>
        {proposal.grading_prompt ? (
          <pre data-testid="generation-grading-prompt">{proposal.grading_prompt}</pre>
        ) : (
          <p className="muted">{t("authoring.generation.noGradingPrompt")}</p>
        )}
      </section>

      {proposal.notes ? <p className="muted">{proposal.notes}</p> : null}

      <div className="inline">
        <button type="button" className="button" data-testid="generation-apply" onClick={onApply}>
          {applied ? t("authoring.generation.reapply") : t("authoring.generation.apply")}
        </button>
      </div>
    </div>
  );
}

export function GenerationPanel({
  courseId,
  courses,
  onCourseChange,
  form,
  generationId,
  onGenerationId,
  onApply,
  onApplied,
}: {
  courseId: string | null;
  /** 可选课程：面板自己带一个下拉，教师不必滚到下面的「基本信息」再回来。 */
  courses: Array<{ course_id: string; course_code: string | null; title: string }>;
  onCourseChange: (next: string) => void;
  form: DraftForm;
  generationId: string | null;
  onGenerationId: (next: string | null) => void;
  onApply: (proposal: QuestionDraftProposal) => void;
  /** 教师**主动**点「填进编辑器」之后回调；刷新恢复候选时不触发。 */
  onApplied?: () => void;
}) {
  const capabilities = useGenerationCapabilities(courseId);
  const restored = useGeneration(generationId);
  const create = useCreateGeneration();
  const discard = useDiscardGeneration();

  const [objective, setObjective] = useState("");
  const [language, setLanguage] = useState<Language>(form.language);
  // 难度是四档下拉，默认「中等」；选中的中文等级原样作为 `difficulty` 传给 AI，用来控制出题复杂度。
  const [difficulty, setDifficulty] = useState(() => t("authoring.generation.difficulty.medium"));
  const [referenceTiersCount, setReferenceTiersCount] = useState<number>(2);
  const [constraints, setConstraints] = useState("");
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [appliedId, setAppliedId] = useState<string | null>(null);
  const [discardedId, setDiscardedId] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const appliedOnce = useRef<string | null>(null);

  useEffect(() => {
    if (!create.isPending) {
      setElapsedSeconds(0);
      return;
    }
    setElapsedSeconds(0);
    const timer = window.setInterval(() => {
      setElapsedSeconds((prev) => prev + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [create.isPending]);

  const objectiveLimit = capabilities.data?.learning_objective_max_chars ?? 0;
  const constraintsLimit = capabilities.data?.constraints_max_chars ?? 0;
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
    ? t("authoring.courseRequired")
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

  /**
   * 状态标签只说**这一屏能确定的事**：
   * 还没选课程时不能说"AI 不可用"（那会把"缺一步操作"说成"功能坏了"），
   * 选了课程但还没问到结果时也不能下结论。
   */
  const pill = !courseId
    ? { tone: "idle" as const, label: t("authoring.generation.pickCoursePill") }
    : capabilities.isPending
      ? { tone: "idle" as const, label: t("authoring.generation.checkingPill") }
      : ready
        ? { tone: "ok" as const, label: t("authoring.generation.readyPill") }
        : { tone: "warn" as const, label: t("authoring.generation.notReadyPill") };

  const submit = () => {
    if (!courseId || !canGenerate) return;
    create.mutate(
      {
        courseId,
        request: {
          learning_objective: objective,
          language,
          adapter_kind: form.adapterKind,
          difficulty,
          tags: parseTags(form.tags).tags,
          desired_reference_tiers: referenceTiersCount,
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
              onApplied?.();
            }
          }
        },
      },
    );
  };

  const generationError = create.error ?? restored.error;

  return (
    <Panel title={t("authoring.generation.title")} testId="generation-panel">
      {!ready || (capabilities.data?.requests_remaining !== null && capabilities.data?.requests_remaining !== undefined) ? (
        <div className="inline" data-testid="generation-status-line">
          {!ready ? (
            <StatusPill tone={pill.tone} testId="generation-pill">
              {pill.label}
            </StatusPill>
          ) : null}
          {capabilities.data?.requests_remaining === null ||
          capabilities.data?.requests_remaining === undefined ? null : (
            <span className="muted" data-testid="generation-budget">
              {t("authoring.generation.budget", { remaining: capabilities.data.requests_remaining })}
            </span>
          )}
        </div>
      ) : null}

      {!courseId ? (
        <div data-testid="generation-no-course">
          <Field label={t("authoring.generation.courseField")} htmlFor="generation-course">
            <Select
              id="generation-course"
              value=""
              onChange={(event) => onCourseChange(event.target.value)}
            >
              <option value="">{t("common.selectPrompt")}</option>
              {courses.map((course) => (
                <option key={course.course_id} value={course.course_id}>
                  {formatCourseLabel(course)}
                </option>
              ))}
            </Select>
          </Field>
        </div>
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

      <fieldset className="case-card" aria-label={t("authoring.generation.objective")}>
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

        <div className="inline" data-testid="generation-presets">
          <span className="muted">{t("authoring.generation.examplePresetsLabel")}</span>
          {OBJECTIVE_PRESETS.map((preset) => (
            <button
              key={preset.labelKey}
              type="button"
              className="button button--ghost"
              onClick={() => setObjective(t(preset.textKey))}
            >
              {t(preset.labelKey)}
            </button>
          ))}
        </div>

        <div className="form-grid">
          <Field label={t("authoring.generation.language")} htmlFor="generation-language">
            <Select
              id="generation-language"
              value={language}
              onChange={(event) => setLanguage(event.target.value as Language)}
            >
              {LANGUAGES.map((lang) => (
                <option key={lang} value={lang}>
                  {t(`questions.language.${lang}`)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("authoring.generation.difficulty")} htmlFor="generation-difficulty">
            <Select
              id="generation-difficulty"
              value={difficulty}
              onChange={(event) => setDifficulty(event.target.value)}
            >
              {DIFFICULTY_LEVELS.map((level) => {
                const label = t(`authoring.generation.difficulty.${level}`);
                return (
                  <option key={level} value={label}>
                    {label}
                  </option>
                );
              })}
            </Select>
          </Field>
          <Field
            label={t("authoring.generation.referenceTiers")}
            htmlFor="generation-reference-tiers"
          >
            <Select
              id="generation-reference-tiers"
              value={String(referenceTiersCount)}
              onChange={(event) => setReferenceTiersCount(Number.parseInt(event.target.value, 10))}
            >
              {REFERENCE_TIER_COUNTS.map((count) => (
                <option key={count} value={String(count)}>
                  {t("authoring.generation.referenceTiersOption", { count })}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {/* 补充要求折叠起来：绝大多数题用不到，展开后才占版面。默认收起。 */}
        <details className="generation-advanced" data-testid="generation-advanced">
          <summary>{t("authoring.generation.advancedConstraints")}</summary>
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
        </details>
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
            : generation?.proposal
              ? t("authoring.generation.regenerate")
              : t("authoring.generation.generate")}
        </button>
        {missing === null ? null : (
          <span className="muted" data-testid="generation-reason">
            {missing}
          </span>
        )}
      </div>

      {create.isPending ? (
        <div className="case-card stack" data-testid="generation-progress" aria-live="polite">
          <div className="inline page-head">
            <strong>{t("authoring.generation.generating")}</strong>
            <span className="muted page-head__end">
              {t("authoring.generation.progressElapsed", { seconds: elapsedSeconds })}
            </span>
          </div>
          <ol className="stack">
            {[
              { min: 0, key: "authoring.generation.progressStep1" as const },
              { min: 1, key: "authoring.generation.progressStep2" as const },
              { min: 4, key: "authoring.generation.progressStep3" as const },
              { min: 10, key: "authoring.generation.progressStep4" as const },
            ].map((stage, idx, all) => {
              const nextMin = all[idx + 1]?.min ?? Number.POSITIVE_INFINITY;
              const isDone = elapsedSeconds >= nextMin;
              const isActive = elapsedSeconds >= stage.min && elapsedSeconds < nextMin;
              return (
                <li key={stage.key} className={isActive ? "" : "muted"}>
                  {isDone ? "✓ " : isActive ? "● " : "○ "}
                  {t(stage.key)}
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}

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
          {generation.status === "ready" ? null : (
            <div className="inline">
              <StatusPill tone={STATUS_TONE[generation.status]} testId="generation-status">
                {statusText(generation)}
              </StatusPill>
            </div>
          )}

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
              onApply={() => {
                onApply(generation.proposal as QuestionDraftProposal);
                onApplied?.();
              }}
              applied={appliedId === generation.generation_id}
            />
          ) : null}

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
