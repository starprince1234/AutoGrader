import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { useSession } from "../../auth/sessionContext";
import { canAuthorQuestions, canSubmitAnswer, canViewQuestionQuality } from "../../auth/visibility";
import { Banner, EmptyState, ErrorState, Field, PageTitle, Panel, Spinner, StatusPill } from "../../ui/components";
import { formatDateTime } from "../../ui/format";
import { ReferenceVerificationPanel } from "../authoring/ReferenceVerificationPanel";
import { RevisionSummary } from "./RevisionSummary";
import { SafeMarkdown } from "./SafeMarkdown";
import { useQuestion, useQuestionVersion } from "./useQuestions";

/**
 * 题目详情（P07，RQ-B-1-40）。
 *
 * 页面只有**一个** h1：题目的真实标题。语言、状态、标签以轻量文本/胶囊跟在标题旁边，
 * 不再把"标题卡"与"版本卡"叠成两层同一件事。题干是主要内容，图片走受控资源路径。
 *
 * 版本：
 * - `?version=<question_version_id>` 是唯一状态来源，切换后写回 URL，刷新或复制链接都回到同一版本；
 * - 渲染的是**该版本自己的冻结题干**（`QuestionVersionPublic.stem_markdown`），不是题目当前题干；
 * - 与当前题干逐字相同时只显示一份（避免两段一模一样的富文本看起来像渲染了两次），不同则在上方
 *   标注"此版本题干"，历史版本还会给出"正在查看历史版本 v{n}"的提示。
 *
 * 授权：学生只在**已发布**版本上看到作答入口；未发布版本对学生显示"这个版本尚未发布，暂不能作答。"。
 * 教师看到编辑入口（草稿 → 编辑草稿；已发布 → 另存为新版本）与题目质量深链，后者携带**当前选中的
 * `question_version_id`**，页面不恢复独立的质量路由。
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
  const versionStemDiffers =
    version.data !== undefined &&
    version.data.stem_markdown.trim() !== (question.data?.stem_markdown ?? "").trim();
  const isHistorical =
    version.data !== undefined &&
    preferred !== null &&
    version.data.question_version_id !== preferred.question_version_id;

  if (question.isPending) return <Spinner />;
  if (question.isError) return <ErrorState error={question.error} />;
  if (!question.data) return <EmptyState />;

  const shown = question.data;

  return (
    <div className="stack">
      <div className="inline">
        <Link className="button button--ghost" to="/questions" data-testid="back-to-list">
          {t("questions.backToList")}
        </Link>
      </div>

      <PageTitle>{shown.title}</PageTitle>

      <div className="inline" data-testid="question-facts">
        <span className="muted">
          {t("questions.language")}：{t(`questions.language.${shown.language}`)}
        </span>
        <StatusPill
          tone={
            shown.status === "published" ? "ok" : shown.status === "draft" ? "warn" : "idle"
          }
        >
          {t(`questions.status.${shown.status}`)}
        </StatusPill>
        {(shown.tags?.length ?? 0) > 0 ? (
          <span className="inline" data-testid="question-tags">
            {shown.tags?.map((tag) => (
              <span className="pill pill--idle" key={tag}>
                {tag}
              </span>
            ))}
          </span>
        ) : (
          <span className="muted" data-testid="question-tags-none">
            {t("questions.tagsNone")}
          </span>
        )}
        {/* 出题入口只对出题角色渲染；服务端仍是判定方（B3 对越权写返回 403）。 */}
        {canAuthorQuestions(session) ? (
          <Link
            className="button button--ghost"
            to="/questions/$questionId/edit"
            params={{ questionId: shown.question_id }}
            data-testid="edit-question"
          >
            {shown.status === "published" ? t("questions.newVersion") : t("questions.editDraft")}
          </Link>
        ) : null}
        {/* 题目质量入口（P13）：打开复核工作区的 quality 标签，并带上**当前选中的版本**。
            P13 是唯一入口（旧 `/quality` 已合并），版本 id 由这里带过去，教师不必手填 UUID。 */}
        {effectiveId && canViewQuestionQuality(session) ? (
          <Link
            className="button button--ghost"
            to="/review"
            search={{ tab: "quality", question_version_id: effectiveId }}
            data-testid="question-quality-link"
          >
            {t("questions.qualityLink")}
          </Link>
        ) : null}
      </div>

      <Panel title={t("questions.stemTitle")} testId="question-stem">
        <SafeMarkdown source={shown.stem_markdown} testId="question-stem-body" />
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
                    {item.published_at
                      ? ` · ${formatDateTime(item.published_at, t("common.none"))}`
                      : ""}
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
              <StatusPill
                tone={
                  version.data.status === "published"
                    ? "ok"
                    : version.data.status === "draft"
                      ? "warn"
                      : "idle"
                }
              >
                {t(`questions.status.${version.data.status}`)}
              </StatusPill>
            </div>

            {isHistorical ? (
              <Banner tone="info" testId="historical-version-notice">
                {t("questions.historicalVersion", { version: version.data.version })}
              </Banner>
            ) : null}

            {/* 该版本自己的题干。与当前题干逐字相同时不再重复渲染；不同时，历史版本要标出
                "此版本题干"，否则读者会把冻结内容当成当前内容。 */}
            {version.data.stem_markdown === "" ? null : versionStemDiffers ? (
              <>
                <h3 data-testid="version-stem-label">{t("questions.versionStemLabel")}</h3>
                <SafeMarkdown source={version.data.stem_markdown} testId="version-stem-body" />
              </>
            ) : (
              <p className="muted" data-testid="version-stem-same">
                {t("questions.versionStemSame")}
              </p>
            )}

            {version.data.status !== "published" ? (
              canSubmitAnswer(session) ? (
                <Banner tone="warn" testId="version-not-published">
                  {t("questions.notPublished")}
                </Banner>
              ) : (
                <Banner tone="info" testId="version-not-published">
                  {t("questions.notPublishedStaff")}
                </Banner>
              )
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
              // 教师/助教看得到题干，但作答入口只对学生开放（服务端同样只接受学生提交）。
              <p className="muted" data-testid="submit-student-only">
                {t("questions.submitStudentOnly")}
              </p>
            )}

            <RevisionSummary
              revisionNote={version.data.revision_note}
              revisionDiff={version.data.revision_diff}
              isFirstVersion={version.data.version === 1}
            />
          </div>
        ) : null}
      </Panel>

      {/* 未发布版本看到这里时，读者会去找"发布"动作。入口在 `ReferenceVerificationPanel`
          （请求平台验证 → 全通过且存证已记录才可发布），**只对出题角色渲染**：
          存证的存在与否不是学生需要的信息。 */}
      {version.data && version.data.status !== "published" && canAuthorQuestions(session) ? (
        <ReferenceVerificationPanel version={version.data} questionId={questionId} />
      ) : null}
    </div>
  );
}
