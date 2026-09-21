import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { useSession } from "../../auth/sessionContext";
import { canAuthorQuestions, canSubmitAnswer, canViewQuestionQuality } from "../../auth/visibility";
import { Banner, EmptyState, ErrorState, Field, Panel, Spinner } from "../../ui/components";
import { ReferenceVerificationPanel } from "../authoring/ReferenceVerificationPanel";
import { RevisionSummary } from "./RevisionSummary";
import { SafeMarkdown } from "./SafeMarkdown";
import { useQuestion, useQuestionVersion } from "./useQuestions";

/**
 * 题目详情（RQ-B-1-40）。
 *
 * 版本切换：`QuestionPublic.versions` 给出完整版本历史（新→旧），下拉选择后以
 * `?version=<question_version_id>` 深链取该版本详情，并按 `question_version_id` 进查询缓存。
 * 渲染的是**该版本自己的题干**（`QuestionVersionPublic.stem_markdown`），不是当前题干——
 * 历史版本必须显示当时的内容，否则"看起来对、其实错"。
 */
export function QuestionDetailPage({
  questionId,
  versionId,
  onSelectVersion,
}: {
  questionId: string;
  versionId: string | null;
  onSelectVersion: (next: string | null) => void;
}) {
  const question = useQuestion(questionId);
  const session = useSession();
  const versions = question.data?.versions ?? [];

  // 未显式选择版本时，默认展示最新的已发布版本（没有则最新版本）。
  const preferred =
    versions.find((item) => item.status === "published") ?? versions[0] ?? null;
  const effectiveId = versionId ?? preferred?.question_version_id ?? null;
  // 必须用 effectiveId 取数：传原始 prop 会让"未显式选择"时根本不发请求，
  // 面板因此永远空白（集成时踩到过）。
  const version = useQuestionVersion(effectiveId);
  const selected = versions.find((item) => item.question_version_id === effectiveId) ?? null;
  // 版本的冻结题干与题目当前题干是否不同（决定要不要重复渲染一遍）。
  const versionStemDiffers =
    version.data !== undefined &&
    version.data.stem_markdown.trim() !== (question.data?.stem_markdown ?? "").trim();

  return (
    <div className="stack">
      <Panel title={t("questions.detailTitle")} titleLevel={1}>
        {question.isPending ? (
          <Spinner />
        ) : question.isError ? (
          <ErrorState error={question.error} />
        ) : question.data ? (
          <div className="stack">
            <div className="inline">
              <h3>{question.data.title}</h3>
              <span className="muted">
                {t("questions.language")}：{t(`questions.language.${question.data.language}`)}
              </span>
              <span className="muted">
                {t("grades.state")}：{t(`questions.status.${question.data.status}`)}
              </span>
              {(question.data.tags?.length ?? 0) > 0 ? (
                <span className="inline" data-testid="question-tags">
                  {question.data.tags?.map((tag) => (
                    <span className="pill pill--idle" key={tag}>
                      {tag}
                    </span>
                  ))}
                </span>
              ) : null}
              {/* 出题入口只对出题角色渲染；服务端仍是判定方（B3 对越权写返回 403）。 */}
              {canAuthorQuestions(session) ? (
                <Link
                  className="button button--ghost"
                  to="/questions/$questionId/edit"
                  params={{ questionId: question.data.question_id }}
                  data-testid="edit-question"
                >
                  {t("questions.editDraft")}
                </Link>
              ) : null}
              {/* 题目质量视图（CHG-0015）的入口：带上**当前选中的版本**，因为契约要求
                  `question_version_id`。它与成绩证据视图同一角色集合，因此用同一个可见性判定。 */}
              {effectiveId && canViewQuestionQuality(session) ? (
                <Link
                  className="button button--ghost"
                  to="/quality"
                  search={{ question_version_id: effectiveId, from: undefined, to: undefined }}
                  data-testid="question-quality-link"
                >
                  {t("nav.quality")}
                </Link>
              ) : null}
            </div>

            <SafeMarkdown source={question.data.stem_markdown} />

            {(question.data.stem_assets?.length ?? 0) > 0 ? (
              <p className="muted" data-testid="asset-present">
                {t("questions.assetCount", { count: question.data.stem_assets?.length ?? 0 })}
              </p>
            ) : null}

            <Banner tone="info">{t("questions.publicDtoGuard")}</Banner>
          </div>
        ) : null}
      </Panel>

      <Panel title={t("questions.versionPicker")}>
        {versions.length === 0 ? (
          <EmptyState>{t("questions.noVersions")}</EmptyState>
        ) : (
          <div className="form-grid">
            <Field label={t("questions.versionSelect")} htmlFor="version-select">
              <select
                id="version-select"
                data-testid="version-select"
                value={effectiveId ?? ""}
                onChange={(event) => onSelectVersion(event.target.value || null)}
              >
                {versions.map((item) => (
                  <option key={item.question_version_id} value={item.question_version_id}>
                    {t("questions.version", { version: item.version })} ·{" "}
                    {t(`questions.status.${item.status}`)}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        )}

        {!effectiveId ? null : version.isPending ? (
          <Spinner />
        ) : version.isError ? (
          <ErrorState error={version.error} />
        ) : version.data ? (
          <div className="stack" data-testid="version-panel">
            <div className="inline">
              <span>{t("questions.version", { version: version.data.version })}</span>
              <span className="muted">
                {t("questions.adapterKind")}：{t(`questions.adapter.${version.data.adapter_kind}`)}
              </span>
              <span className="muted">
                {t("questions.language")}：{t(`questions.language.${version.data.language}`)}
              </span>
              <span className="muted">
                {t("grades.state")}：{t(`questions.status.${version.data.status}`)}
              </span>
            </div>

            {selected && selected.question_version_id !== preferred?.question_version_id ? (
              <Banner tone="info" testId="historical-version-notice">
                {t("questions.historicalVersion", { version: version.data.version })}
              </Banner>
            ) : null}

            {/* 该版本自己的题干（可能为空，例如早期草稿）。
                与当前题干逐字相同时不再重复渲染：两段一模一样的富文本一上一下，读起来
                像是渲染了两遍，而不是"题目"与"版本"两个不同对象。内容一旦不同（历史版本、
                或题干已改而版本冻结）就必须完整渲染——那正是"看起来对、其实错"的防线。 */}
            {version.data.stem_markdown === "" ? null : versionStemDiffers ? (
              <SafeMarkdown source={version.data.stem_markdown} />
            ) : (
              <p className="muted" data-testid="version-stem-same">
                {t("questions.versionStemSame")}
              </p>
            )}

            {version.data.status !== "published" ? (
              <Banner tone="warn" testId="version-not-published">
                {t("questions.notPublished")}
              </Banner>
            ) : canSubmitAnswer(session) ? (
              <div className="inline">
                <Link
                  className="button"
                  to="/submit/$questionVersionId"
                  params={{ questionVersionId: version.data.question_version_id }}
                >
                  {t("questions.submit")}
                </Link>
              </div>
            ) : (
              // 教师/助教看得到题干，但作答入口对学生开放（服务端同样只接受学生提交）。
              // 之前这里对所有人渲染按钮，点进去立刻是 403 页面——一个自己指向死路的 CTA。
              <p className="muted" data-testid="submit-student-only">
                {t("questions.submitStudentOnly")}
              </p>
            )}

            {/* 版本说明与结构差异（CHG-0013）。仅在服务端真的给出时渲染内容。 */}
            <RevisionSummary
              revisionNote={version.data.revision_note}
              revisionDiff={version.data.revision_diff}
              isFirstVersion={version.data.version === 1}
            />
          </div>
        ) : null}
      </Panel>

      {/* 未发布版本读到这里时，读者会去找"发布"动作。`ADR-0015` 之后这条路径是通的：
          入口在 `ReferenceVerificationPanel`（请求平台验证 → 全通过且存证已记录才可发布）。
          **只对出题角色渲染**：存证的存在与否不是学生需要的信息。 */}
      {version.data && version.data.status !== "published" && canAuthorQuestions(session) ? (
        <ReferenceVerificationPanel version={version.data} questionId={questionId} />
      ) : null}
    </div>
  );
}
