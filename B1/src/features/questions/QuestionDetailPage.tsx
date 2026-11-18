import { useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import { useSession } from "../../auth/sessionContext";
import { canAuthorQuestions, canSubmitAnswer } from "../../auth/visibility";
import {
  Banner,
  EmptyState,
  ErrorState,
  Modal,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
} from "../../ui/components";
import { useDraftContent, usePublishQuestionVersion } from "../authoring/useAuthoring";
import { SafeMarkdown } from "./SafeMarkdown";
import { IoSpecPanel, IoTypeBadge } from "./IoTypeBadge";
import { useDeleteQuestionVersion, useQuestion, useQuestionVersion } from "./useQuestions";

/**
 * 题目详情（P07，RQ-B-1-40）。
 *
 * 页面只有**一个** h1：题目的真实标题。语言、状态、标签以轻量文本/胶囊跟在标题旁边，
 * 不再把"标题卡"与"版本卡"叠成两层同一件事。题干是主要内容，图片走受控资源路径。
 *
 * 版本：
 * - `?version=<question_version_id>` 仍决定**用哪个版本取数**（质量入口、教师参考解法/直接发布、学生入口），
 *   刷新或复制链接都回到同一版本；
 * - **版本卡（选择版本 / 版本详情 / 相关信息 / 此版本题干）已删除**：页面上不再有切换版本的入口，
 *   也不再渲染历史版本的冻结题干。
 *
 * 授权：学生只在**已发布**版本上看到入口（作业范围内提交，因此指向「我的作业」）；
 * 教师看到编辑入口（草稿 → 编辑草稿；已发布 → 另存为新版本）与题目质量深链，后者携带**当前选中的
 * `question_version_id`**，页面不恢复独立的质量路由。
 */
export function QuestionDetailPage({
  questionId,
  versionId,
}: {
  questionId: string;
  /** `?version=` 深链：决定用哪个版本取数（质量入口、验证并发布面板、学生入口），不再有切换 UI。 */
  versionId: string | null;
}) {
  const question = useQuestion(questionId);
  const session = useSession();
  const navigate = useNavigate();
  const removeVersion = useDeleteQuestionVersion();
  const publishVersion = usePublishQuestionVersion();
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
  // 版本卡删除后这个查询仍有用途：教师的多档参考解法与直接发布面板需要 `version.data`，
  // 学生入口也依赖它的状态。
  const version = useQuestionVersion(effectiveId);
  // 教师可见的多档参考解法 / 评分提示词要按当前版本取内容（学生不请求）。
  const draftContent = useDraftContent(
    canAuthorQuestions(session) &&
      version.data &&
      (version.data.status === "draft" || version.data.status === "published")
      ? effectiveId
      : null,
  );

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
        {shown.io_type ? (
          <IoTypeBadge ioType={shown.io_type} testId="question-io-type-badge" />
        ) : null}
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
        {/* 老师要求删掉「查看题目质量」入口：复核页只保留邮件隔离，题库不再把人引过去。 */}
      </div>

      <Panel title={t("questions.stemTitle")} testId="question-stem">
        <SafeMarkdown source={shown.stem_markdown} testId="question-stem-body" />
      </Panel>

      {(version.data?.io_type ?? shown.io_type) ? (
        <IoSpecPanel
          ioType={version.data?.io_type ?? shown.io_type}
          language={version.data?.language ?? shown.language}
          questionVersionId={version.data?.question_version_id ?? effectiveId}
          ioSpec={version.data?.io_spec}
          visibleFixtures={version.data?.visible_fixtures}
        />
      ) : null}

      {/* 「提交答案」原本长在版本卡里；版本卡删除后仍保留这个入口。作答现在是「作业范围内提交」，
          所以只把学生带到「我的作业」，不再直接链到 /submit。 */}
      {version.data && version.data.status === "published" && canSubmitAnswer(session) ? (
        <div className="inline">
          <Link className="button" to="/assignments" data-testid="go-to-assignments">
            {t("questions.goToAssignments")}
          </Link>
        </div>
      ) : null}

      {/* 仅教师可见：多档参考解法与 AI 评分提示词/细则，学生视图不获取也不渲染。 */}
      {canAuthorQuestions(session) && draftContent.data ? (
        <Panel title={t("questions.teacherReferenceTitle")} testId="teacher-grading-context">
          <div className="stack">
            {(draftContent.data.reference_answers && draftContent.data.reference_answers.length > 0
              ? draftContent.data.reference_answers
              : draftContent.data.reference_answer
                ? [{ label: t("authoring.referenceDefaultTier"), body: draftContent.data.reference_answer }]
                : []
            ).map((tier, index) => (
              <div className="stack" key={`${index}-${tier.label}`} data-testid={`teacher-tier-${index}`}>
                <strong>{tier.label}</strong>
                <pre>
                  <code>{tier.body}</code>
                </pre>
              </div>
            ))}
            <div className="stack" data-testid="teacher-grading-prompt">
              <strong>{t("authoring.gradingPrompt")}</strong>
              {draftContent.data.grading_prompt ? (
                <pre>{draftContent.data.grading_prompt}</pre>
              ) : (
                <p className="muted">{t("questions.teacherGradingPromptEmpty")}</p>
              )}
            </div>
          </div>
        </Panel>
      ) : null}

      {/* 草稿版本支持教师确认后直接发布，不再需要先运行测试用例沙箱验证。 */}
      {version.data && version.data.status !== "published" && canAuthorQuestions(session) ? (
        <Panel title={t("questions.directPublishTitle")} testId="direct-publish-panel">
          <div className="stack">
            <p className="muted">{t("questions.directPublishHint")}</p>
            {publishVersion.isError ? <ErrorState error={publishVersion.error} /> : null}
            <div className="inline">
              <button
                type="button"
                className="button"
                data-testid="reference-publish"
                disabled={publishVersion.isPending}
                onClick={() => {
                  publishVersion.mutate({
                    questionId,
                    questionVersionId: version.data.question_version_id,
                  });
                }}
              >
                {publishVersion.isPending
                  ? t("questions.publishing")
                  : t("questions.directPublishAction")}
              </button>
            </div>
          </div>
        </Panel>
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
