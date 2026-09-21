import { t, type MessageKey } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { AdvisoryScore, EvaluationCommentary, CommentaryStatus } from "../../api/types";
import { useSession } from "../../auth/sessionContext";
import { canGenerateCommentary } from "../../auth/visibility";
import { Banner, ErrorState, Panel, Spinner, StatusPill } from "../../ui/components";
import { useEvaluationCommentary, useGenerateCommentary } from "./useCommentary";

/**
 * 教学评语轨（RQ-T-20）与 AI 评分参考（RQ-T-30，`ADR-0014`）。
 *
 * 三处不可让步的展示约束，都来自契约原文而不是本模块的偏好：
 *
 * 1. **`disclaimers` 逐字显示**（`EvaluationCommentary.disclaimers` 的 `description` 写明
 *    "normative labels a client must display verbatim"）：渲染成列表，不做改写、不折叠、
 *    不因为 `status` 不是 `ready` 就省略——契约要求它"永远存在、永不为空"。
 * 2. **参考分与权威分并列，且不得显得更权威**：本组件渲染的参考分块放在**权威分之后**，
 *    用 `advisory-score` 这个低权重外观（灰底、虚线边、无强调色、`pill--idle`），
 *    权威分在 `GradeDrilldown` 里用的是 `score-authoritative` + 强调 pill。
 *    两者 testId 不同（`authoritative-score` / `advisory-score`），测试对"视觉标记不同"
 *    有断言。
 * 3. **`advisory_score` 为 `null` 时整块不渲染**（契约允许它为空），**不**用 0 代替。
 *    另外：若服务端返回了参考分却没有返回 `disclaimers`（违反 `minItems: 1`），
 *    参考分也**不渲染**——契约的原话是"渲染参考分却不带这些标注，就是把模型的建议当成成绩"。
 *
 * `advisory_score` 只被读取与显示：不求和、不排序、不比较、不导出，也不参与任何本地计算。
 */

/** 提示"本部署未启用"的状态：契约明确 `unavailable` 是配置而不是失败。 */
const STATUS_BANNER: Record<
  CommentaryStatus,
  { tone: "info" | "warn"; testId: string; message: MessageKey } | null
> = {
  unavailable: {
    tone: "info",
    testId: "commentary-unavailable",
    message: "commentary.statusUnavailable",
  },
  pending: { tone: "info", testId: "commentary-pending", message: "commentary.statusPending" },
  failed: { tone: "warn", testId: "commentary-failed", message: "commentary.statusFailed" },
  ready: null,
};

/**
 * 参考分块。
 *
 * DOM 上也刻意与权威分不同：没有 `pill--ok`、没有 `score-authoritative`、字号更小、
 * 文案里带"不是分数"。任何"把两者渲染成同一类东西"的改动都会让测试失败。
 */
function AdvisoryScoreBlock({ advisory }: { advisory: AdvisoryScore }) {
  return (
    <div className="advisory-score" data-testid="advisory-score">
      <div className="inline">
        <StatusPill tone="idle" testId="advisory-score-label">
          {t("advisory.label")}
        </StatusPill>
        <span className="muted" data-testid="advisory-score-value">
          {t("advisory.score")}：{advisory.score} / {advisory.max_score}
        </span>
        <span className="muted" data-testid="advisory-score-max-note">
          {t("advisory.maxScore")}
        </span>
        <span className="muted" data-testid="advisory-score-confidence">
          {t("advisory.confidence")}
          {t(`advisory.confidence.${advisory.confidence}`)}
        </span>
      </div>
      <p className="muted" data-testid="advisory-score-rationale">
        {t("advisory.rationale")}：{advisory.rationale}
      </p>
      <p className="muted" data-testid="advisory-score-discipline">
        {t("advisory.notAScore")}
        {t("advisory.neverComputed")}
      </p>
    </div>
  );
}

function Disclaimers({ commentary }: { commentary: EvaluationCommentary }) {
  if (commentary.disclaimers.length === 0) {
    // 契约违规（`minItems: 1`）。渲染"没有标注的参考分"比不渲染更糟，因此这里只报告事实。
    return (
      <Banner tone="warn" testId="commentary-disclaimers-missing">
        {t("commentary.disclaimersMissing")}
      </Banner>
    );
  }
  return (
    <div data-testid="commentary-disclaimers">
      <h3>{t("commentary.disclaimers")}</h3>
      <ul>
        {commentary.disclaimers.map((line, index) => (
          <li key={`${index}-${line}`}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

export function CommentarySection({ runId }: { runId: string }) {
  const session = useSession();
  const commentary = useEvaluationCommentary(runId);
  const generate = useGenerateCommentary(runId);

  const canRegenerate = canGenerateCommentary(session);
  const data = commentary.data;
  const forbidden = isApiError(commentary.error) && commentary.error.isForbidden;
  /**
   * 尚未生成评语时的真实行为（**本部署实测**）：`GET` 返回 `404`，detail 明确写着
   * "No commentary has been generated for this evaluation run yet. Generate it with POST on
   * this path. **This is not the same as a missing run.**"
   *
   * 因此它既不是"运行不存在"，也不是失败：把它渲染成红色错误框，就等于把"还没生成"
   * 说成"出错了"，而这正是本轨要与判分轨分开的地方。其他错误码仍走通用错误态。
   */
  const notGenerated = isApiError(commentary.error) && commentary.error.isNotFound
    ? commentary.error
    : null;
  const notEnabled = isApiError(generate.error) && generate.error.isConflict;
  const statusBanner = data ? STATUS_BANNER[data.status] : null;
  const disclaimersPresent = (data?.disclaimers.length ?? 0) > 0;
  const advisory = data?.advisory_score ?? null;

  return (
    <div className="stack" data-testid="commentary-section">
      {/* 参考分只在这里出现，且只在服务端真的给出非空 `advisory_score` 时出现。 */}
      {advisory !== null && disclaimersPresent ? (
        <AdvisoryScoreBlock advisory={advisory} />
      ) : null}

      <Panel title={t("commentary.title")} testId="commentary" className="panel--advisory">
        <p className="muted">{t("commentary.intro")}</p>

        {canRegenerate ? (
          <div className="inline">
            <button
              type="button"
              className="button"
              data-testid="regenerate-commentary"
              disabled={generate.isPending}
              onClick={() => generate.mutate()}
            >
              {generate.isPending ? t("commentary.regenerating") : t("commentary.regenerate")}
            </button>
            <span className="muted">{t("commentary.regenerateHint")}</span>
          </div>
        ) : null}

        {notEnabled ? (
          <Banner tone="info" testId="commentary-not-enabled">
            {t("commentary.notEnabled")}
            {isApiError(generate.error) && generate.error.problem?.detail ? (
              <span className="muted"> {generate.error.problem.detail}</span>
            ) : null}
          </Banner>
        ) : generate.isError ? (
          <ErrorState error={generate.error} testId="commentary-generate-error" />
        ) : null}

        {commentary.isPending ? <Spinner /> : null}

        {forbidden ? (
          <Banner tone="info" testId="commentary-forbidden">
            {t("commentary.forbidden")}
          </Banner>
        ) : notGenerated ? (
          <Banner tone="info" testId="commentary-not-generated">
            {t("commentary.notGenerated")}
            {notGenerated.problem?.detail ? (
              <span className="muted"> {notGenerated.problem.detail}</span>
            ) : null}
          </Banner>
        ) : commentary.isError ? (
          <ErrorState error={commentary.error} testId="commentary-error" />
        ) : null}

        {statusBanner ? (
          <Banner tone={statusBanner.tone} testId={statusBanner.testId}>
            {t(statusBanner.message)}
          </Banner>
        ) : null}

        {data && data.status === "ready" ? (
          <p className="muted" data-testid="commentary-ready-note">
            {t("commentary.statusReady")}
          </p>
        ) : null}

        {/* `ready` 但没有参考分：契约允许（"the model returned none"）。
            这里说明"本次没有产生"，而不是留白让人以为漏渲染。 */}
        {data && data.status === "ready" && advisory === null ? (
          <p className="muted" data-testid="advisory-absent">
            {t("advisory.absent")}
          </p>
        ) : null}

        {/* `text` 只在 `ready` 时非空（契约原文：Null unless status is ready）。 */}
        {data?.text ? <p data-testid="commentary-text">{data.text}</p> : null}

        {data ? <Disclaimers commentary={data} /> : null}

        {data?.provenance ? (
          <div data-testid="commentary-provenance">
            <h3>{t("commentary.provenance")}</h3>
            <p className="muted">{t("commentary.provenanceHint")}</p>
            <ul className="muted">
              <li>
                {t("commentary.provider")}: {data.provenance.provider}
              </li>
              <li>
                {t("commentary.model")}: {data.provenance.model}
              </li>
              <li>
                {t("commentary.promptVersion")}: {data.provenance.prompt_version}
              </li>
            </ul>
            <h4>{t("commentary.inputScope")}</h4>
            <p className="muted">{t("commentary.inputScopeHint")}</p>
            <ul className="muted" data-testid="commentary-input-scope">
              {data.provenance.input_scope.map((field) => (
                <li key={field}>{field}</li>
              ))}
            </ul>
          </div>
        ) : null}

      </Panel>
    </div>
  );
}
