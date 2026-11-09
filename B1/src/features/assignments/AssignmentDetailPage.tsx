import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import { useSession } from "../../auth/sessionContext";
import { canViewCohortGrades } from "../../auth/visibility";
import { EmptyState, ErrorState, PageTitle, Spinner } from "../../ui/components";
import { AssignmentWorkspace } from "../submission/AssignmentWorkspace";
import { GradingPage } from "./GradingPage";
import { useAssignment } from "./useAssignments";

/**
 * 作业详情（CHG-0032 §7.2）：`/assignments/{assignment_id}` **按角色分流**。
 *
 * - 学生 → 作业工作区（多题列表 + 每题 `.txt` 提交 + 邮件提交面板），
 *   取代旧的单题作答页 `/submit/{question_version_id}`（该路由与页面已删除）；
 * - 教师/管理员 → 作业详情（概览、汇总计数、题目表、逐人逐题矩阵）。
 *
 * 读路径是 `GET /api/v1/assignments/{assignment_id}`：404（不存在）与 403（不在课程范围）
 * 是不同结论，分别渲染，不合并成"空列表"。
 */

/** 403 与 404 是不同结论：一个说"你没权限"，一个说"它不存在"。 */
function assignmentErrorContent(error: unknown) {
  if (isApiError(error)) {
    if (error.status === 403) {
      return <EmptyState>{t("assignments.detailForbidden")}</EmptyState>;
    }
    if (error.status === 404) {
      return <EmptyState>{t("assignments.detailMissing")}</EmptyState>;
    }
  }
  return <ErrorState error={error} testId="assignment-detail-error" />;
}

export function AssignmentDetailPage({
  assignmentId,
  initialQuestionVersionId = null,
  initialSelectedVersionId = null,
  initialSelectedStudentId = null,
}: {
  assignmentId: string;
  /** 学生从列表「继续作答」进入时带上的题目（属于该作业时才生效）。 */
  initialQuestionVersionId?: string | null;
  /** 教师详情页（批改界面）当前选中的题目，来自 `?version=`。 */
  initialSelectedVersionId?: string | null;
  /** 教师详情页当前选中的学生，来自 `?student=`。 */
  initialSelectedStudentId?: string | null;
}) {
  const query = useAssignment(assignmentId);
  const session = useSession();
  const teacherView = canViewCohortGrades(session);

  // 教师的「作业详情」就是 PDF 的「作业情况（子界面）」：左栏题目 + 已完成/未完成，
  // 右栏大窗口看题干/作答/评测并改分。因此详情路由直接渲染批改界面，而不是再维护
  // 一份矩阵表式的旧页面——两套 UI 并存必然漂移，"点作业进哪一页"也会变成歧义。
  // 学生仍然进入作业工作区（多题列表 + 逐题提交），不变。
  if (teacherView) {
    return (
      <GradingPage
        assignmentId={assignmentId}
        initialVersionId={initialSelectedVersionId ?? initialQuestionVersionId}
        initialStudentId={initialSelectedStudentId}
        embedded
      />
    );
  }

  return (
    <div className="stack">
      <nav className="muted" aria-label={t("assignments.breadcrumb")}>
        <Link to="/assignments" data-testid="assignment-back-link">
          {t("nav.assignments")}
        </Link>
        <span aria-hidden="true"> / </span>
        <span data-testid="assignment-breadcrumb-current">
          {query.data?.title ?? t("assignments.detailTitle")}
        </span>
      </nav>

      <div className="inline page-head">
        <PageTitle>{query.data?.title ?? t("assignments.detailTitle")}</PageTitle>
        <button
          type="button"
          className="button button--ghost"
          data-testid="assignment-back-button"
          onClick={() => {
            if (window.history.length > 1) window.history.back();
            else window.location.assign("/app/assignments");
          }}
        >
          {t("assignments.backPrevious")}
        </button>
      </div>

      {query.isPending ? (
        <Spinner label={t("assignments.detailLoading")} />
      ) : query.isError ? (
        assignmentErrorContent(query.error)
      ) : (
        <AssignmentWorkspace
          assignment={query.data}
          initialQuestionVersionId={initialQuestionVersionId}
        />
      )}
    </div>
  );
}
