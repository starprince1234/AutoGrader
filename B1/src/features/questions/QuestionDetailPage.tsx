import { useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import { useSession } from "../../auth/sessionContext";
import { canAuthorQuestions, canSubmitAnswer, canViewQuestionQuality } from "../../auth/visibility";
import {
  Banner,
  EmptyState,
  ErrorState,
  Field,
  Modal,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
} from "../../ui/components";
import { formatDateTime } from "../../ui/format";
import { readExecutionMode } from "../authoring/execution";
import { ReferenceVerificationPanel } from "../authoring/ReferenceVerificationPanel";
import { SafeMarkdown } from "./SafeMarkdown";
import { useDeleteQuestionVersion, useQuestion, useQuestionVersion } from "./useQuestions";
import { Select } from "../../ui/Select";

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
  const navigate = useNavigate();
  const removeVersion = useDeleteQuestionVersion();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const versions = question.data?.versions ?? [];

  // 未显式选择版本时，默认展示最新的已发布版本（没有则最新版本）。
  const preferred =
    versions.find((item) => item.status === "published") ?? versions[0] ?? null;
  // 「删除草稿」作用在题目**当前那条草稿版本**上：已发布的版本只能归档，不能删除。
  const draftVersion = versions.find((item) => item.status === "draft") ?? null;
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
      {/* 返回按钮统一放页面右上角（3.0 第 3 条）。 */}
      <div className="inline page-head">
        <PageTitle>{shown.title}</PageTitle>
        <Link
          className="button button--ghost page-head__end"
          to="/questions"
          data-testid="back-to-list"
        >
          {t("questions.backToList")}
        </Link>
      </div>

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
            // 从详情页进去：编辑页的右上角是「返回题目」（3.0 第 1 条）。
            search={{ from: "detail" }}
            data-testid="edit-question"
          >
            {shown.status === "published" ? t("questions.newVersion") : t("questions.editDraft")}
          </Link>
        ) : null}
        {/* 「编辑草稿」后面：删除这条草稿（题目页与题库列表都能删）。 */}
        {canAuthorQuestions(session) && draftVersion !== null ? (
          <button
            type="button"
            className="button button--danger"
            data-testid="delete-draft"
            onClick={() => {
              setDeleteError(null);
              setConfirmDelete(true);
            }}
          >
            {t("questions.deleteDraft")}
          </button>
        ) : null}
        {/* 题目质量入口（P13）：打开复核工作区的 quality 标签，并带上**当前选中的版本**。
            P13 是唯一入口（旧 `/quality` 已合并），版本 id 由这里带过去，教师不必手填 UUID。 */}
        {effectiveId && canViewQuestionQuality(session) ? (
          <Link
            className="button button--ghost"
            to="/review"
            search={{
              tab: "quality",
              origin: "question",
              // 复核页的「返回该题」要靠它回到这一道题，而不是让老师回题库自己再找一遍。
              question_id: questionId,
              course_id: shown.course_id,
              question_version_id: effectiveId,
            }}
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
              <Select
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
              </Select>
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
              {/* 公开面只有模式本身；分段标记、邮件尝试次数与 SSH 配置都不在这里出现。 */}
              <span className="muted" data-testid="version-execution-mode">
                {t("questions.executionMode")}：
                {t(`questions.executionMode.${readExecutionMode(version.data)}`)}
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
            ) : null}

            {version.data.status === "published" && canSubmitAnswer(session) ? (
              <div className="inline">
                {/* 作答入口只存在于作业工作区（一次提交 = 一道题，必须知道 assignment 与题号）。
                    题目页没有作业上下文，因此这里只把学生带到「我的作业」。 */}
                <Link className="button" to="/assignments" data-testid="go-to-assignments">
                  {t("questions.goToAssignments")}
                </Link>
              </div>
            ) : null}

            {/* 3.0：版本框只留「选择版本 / 题型 / 状态 / 相关信息」。
                有改动说明就原样展示，没有就固定写「无」。 */}
            <div className="stack" data-testid="version-related">
              <h3>{t("questions.versionRelated")}</h3>
              <p data-testid="version-related-note">
                {version.data.revision_note && version.data.revision_note.trim() !== ""
                  ? version.data.revision_note
                  : t("common.none")}
              </p>
            </div>
          </div>
        ) : null}
      </Panel>

      {/* 未发布版本看到这里时，读者会去找"发布"动作。入口在 `ReferenceVerificationPanel`
          （请求平台验证 → 全通过且存证已记录才可发布），**只对出题角色渲染**：
          存证的存在与否不是学生需要的信息。 */}
      {version.data && version.data.status !== "published" && canAuthorQuestions(session) ? (
        <ReferenceVerificationPanel version={version.data} questionId={questionId} />
      ) : null}

      {deleteError !== null ? (
        <Banner tone="error" testId="delete-draft-error">
          {deleteError}
        </Banner>
      ) : null}

      {confirmDelete && draftVersion !== null ? (
        <Modal
          title={t("questions.deleteTitle")}
          testId="delete-draft-modal"
          initialFocus='[data-testid="delete-draft-confirm"]'
          onClose={() => setConfirmDelete(false)}
        >
          <p>{t("questions.deleteBody")}</p>
          <div className="inline">
            <button
              type="button"
              className="button button--danger"
              data-testid="delete-draft-confirm"
              disabled={removeVersion.isPending}
              onClick={() => {
                const target = draftVersion.question_version_id;
                setConfirmDelete(false);
                removeVersion
                  .mutateAsync(target)
                  .then(() => {
                    void navigate({ to: "/questions" });
                  })
                  .catch((error: unknown) => {
                    setDeleteError(
                      isApiError(error) && error.status === 409
                        ? t("questions.deleteConflict")
                        : isApiError(error) && error.isForbidden
                          ? t("questions.deleteForbidden")
                          : isApiError(error) && error.isNotFound
                            ? t("questions.deleteNotFound")
                            : t("questions.deleteFailed"),
                    );
                  });
              }}
            >
              {removeVersion.isPending ? t("questions.deleting") : t("questions.deleteConfirm")}
            </button>
            <button
              type="button"
              className="button button--ghost"
              data-testid="delete-draft-cancel"
              onClick={() => setConfirmDelete(false)}
            >
              {t("common.cancel")}
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
