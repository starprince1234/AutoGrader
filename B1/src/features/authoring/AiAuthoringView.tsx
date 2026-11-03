import { t } from "../../i18n";
import { PageTitle } from "../../ui/components";
import type { QuestionDraftProposal } from "../../api/types";
import { GenerationPanel } from "./GenerationPanel";
import type { DraftForm } from "./draft";

/**
 * 「AI 出题」页：由题干页标题右侧的按钮进入，整块替换向导内容（URL 上是 `/questions/new?ai=1`）。
 *
 * 为什么是同一页面的视图而不是新路由：生成面板要读写**当前这一份**表单（目标、语言、题型，以及
 * 应用候选后的字段），换路由会把内存里的草稿与候选上下文一起丢掉；这里换视图，状态不丢，
 * 刷新时又靠 URL 上的 `ai` 与 `generation` 回到同一处。
 *
 * 文案只留必要的一句状态与一句行动，不再重复解释"草稿不是成品"。
 */
export function AiAuthoringView({
  courseId,
  courses,
  onCourseChange,
  form,
  generationId,
  onGenerationId,
  onApply,
  onApplied,
  onBack,
}: {
  courseId: string | null;
  courses: Array<{ course_id: string; course_code: string | null; title: string }>;
  onCourseChange: (next: string) => void;
  form: DraftForm;
  generationId: string | null;
  onGenerationId: (next: string | null) => void;
  onApply: (proposal: QuestionDraftProposal) => void;
  onApplied: () => void;
  onBack: () => void;
}) {
  return (
    <div className="stack" data-testid="ai-authoring-view">
      <div className="inline">
        <PageTitle>{t("authoring.ai.pageTitle")}</PageTitle>
        <button type="button" className="button button--ghost" data-testid="ai-back" onClick={onBack}>
          {t("authoring.ai.back")}
        </button>
      </div>
      <GenerationPanel
        courseId={courseId}
        courses={courses}
        onCourseChange={onCourseChange}
        form={form}
        generationId={generationId}
        onGenerationId={onGenerationId}
        onApply={onApply}
        onApplied={onApplied}
      />
    </div>
  );
}
