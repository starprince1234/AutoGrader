import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { HomePage } from "../features/home/HomePage";
import { AssignmentDetailPage } from "../features/assignments/AssignmentDetailPage";
import { AssignmentsPage } from "../features/assignments/AssignmentsPage";
import { AuthoringPage } from "../features/authoring/AuthoringPage";
import { GradesPage } from "../features/grades/GradesPage";
import { QuarantinePage } from "../features/intake/QuarantinePage";
import { QualitySummaryPage } from "../features/quality/QualitySummaryPage";
import { QuestionDetailPage } from "../features/questions/QuestionDetailPage";
import { QuestionListPage } from "../features/questions/QuestionListPage";
import { RosterImportPage } from "../features/roster-import/RosterImportPage";
import { RosterPage } from "../features/roster/RosterPage";
import { SubmitPage } from "../features/submission/SubmitPage";
import { UsersPage } from "../features/users/UsersPage";
import { RegistrationAllowlistPage } from "../features/admin/RegistrationAllowlistPage";
import { RequireAuth, RequirePermission } from "../auth/guards";
import {
  canAuthorQuestions,
  canImportRoster,
  canManageUsers,
  canReviewQuarantine,
  canSubmitAnswer,
  canViewQuestionQuality,
  canViewRoster,
} from "../auth/visibility";

/**
 * 路由组件（只导出组件，供 `router.tsx` 组装；见 eslint
 * `react-refresh/only-export-components` 的分工）。
 *
 * 守卫分两层：
 * - `RequireAuth`：未认证时给出登录提示（不跳转，避免丢失深链）；
 * - `RequirePermission`：按角色隐藏页面主体。**这仍然不是安全边界**：
 *   每个被隐藏的入口都对应一个服务端 403，页面会把 403 原样展示出来。
 */

export function HomeRoute() {
  return <HomePage />;
}

export function QuestionListRoute() {
  return (
    <RequireAuth>
      <QuestionListPage />
    </RequireAuth>
  );
}

export function QuestionDetailRoute() {
  // 通过已注册的路由类型读取参数，避免与 `router.tsx` 形成循环依赖。
  const { questionId } = useParams({ from: "/questions/$questionId" });
  const { version } = useSearch({ from: "/questions/$questionId" });
  const navigate = useNavigate();
  return (
    <RequireAuth>
      <QuestionDetailPage
        questionId={questionId}
        versionId={version ?? null}
        onSelectVersion={(next) => {
          void navigate({
            to: "/questions/$questionId",
            params: { questionId },
            search: { version: next ?? undefined },
          });
        }}
      />
    </RequireAuth>
  );
}

export function AuthoringNewRoute() {
  return (
    <RequireAuth>
      <RequirePermission
        allow={canAuthorQuestions}
        allowedRoles={["teacher", "assistant", "admin"]}
      >
        <AuthoringPage questionId={null} />
      </RequirePermission>
    </RequireAuth>
  );
}

export function AuthoringEditRoute() {
  const { questionId } = useParams({ from: "/questions/$questionId/edit" });
  return (
    <RequireAuth>
      <RequirePermission
        allow={canAuthorQuestions}
        allowedRoles={["teacher", "assistant", "admin"]}
      >
        <AuthoringPage questionId={questionId} />
      </RequirePermission>
    </RequireAuth>
  );
}

export function SubmitRoute() {
  const { questionVersionId } = useParams({ from: "/submit/$questionVersionId" });
  return (
    <RequireAuth>
      <RequirePermission allow={canSubmitAnswer} allowedRoles={["student"]}>
        <SubmitPage questionVersionId={questionVersionId} />
      </RequirePermission>
    </RequireAuth>
  );
}

export function GradesRoute() {
  return (
    <RequireAuth>
      <GradesPage />
    </RequireAuth>
  );
}

export function AssignmentsRoute() {
  return (
    <RequireAuth>
      <AssignmentsPage />
    </RequireAuth>
  );
}

export function AssignmentDetailRoute() {
  const { assignmentId } = useParams({ from: "/assignments/$assignmentId" });
  return (
    <RequireAuth>
      <AssignmentDetailPage assignmentId={assignmentId} />
    </RequireAuth>
  );
}

export function RosterRoute() {
  return (
    <RequireAuth>
      <RequirePermission allow={canViewRoster} allowedRoles={["teacher", "assistant", "admin"]}>
        <RosterPage />
      </RequirePermission>
    </RequireAuth>
  );
}

export function RosterImportRoute() {
  return (
    <RequireAuth>
      <RequirePermission allow={canImportRoster} allowedRoles={["teacher", "admin"]}>
        <RosterImportPage />
      </RequirePermission>
    </RequireAuth>
  );
}

export function UsersRoute() {
  return (
    <RequireAuth>
      <RequirePermission allow={canManageUsers} allowedRoles={["admin"]}>
        <UsersPage />
      </RequirePermission>
    </RequireAuth>
  );
}

/**
 * 隔离区复核台（CHG-0014）。契约把授权写成 "teacher/assistant/admin within the course scope"，
 * 可见性与之一致；服务端仍是判定方（403 原样展示）。
 */
export function QuarantineRoute() {
  return (
    <RequireAuth>
      <RequirePermission
        allow={canReviewQuarantine}
        allowedRoles={["teacher", "assistant", "admin"]}
      >
        <QuarantinePage />
      </RequirePermission>
    </RequireAuth>
  );
}

/**
 * 题目质量视图（CHG-0015）。筛选条件住在 URL 的查询串里（与题目详情页的 `?version=` 同一做法）：
 * 这样"某版本的教师质量视图"是一条可以贴给同事的深链，而不是一个只有当前会话才存在的页面内状态。
 */
export function QualityRoute() {
  const { question_version_id, from, to } = useSearch({ from: "/quality" });
  const navigate = useNavigate();
  return (
    <RequireAuth>
      <RequirePermission
        allow={canViewQuestionQuality}
        allowedRoles={["teacher", "assistant", "admin"]}
      >
        <QualitySummaryPage
          questionVersionId={question_version_id ?? null}
          from={from ?? null}
          to={to ?? null}
          onApply={(next) => {
            void navigate({
              to: "/quality",
              search: {
                question_version_id: next.questionVersionId ?? undefined,
                from: next.from ?? undefined,
                to: next.to ?? undefined,
              },
            });
          }}
        />
      </RequirePermission>
    </RequireAuth>
  );
}

/**
 * 注册白名单维护（仅管理员）。名单是自助注册的身份锚点，所以它的写入口与用户管理同权限；
 * 服务端仍是判定方，前端可见性只是体验。
 */
export function RegistrationAllowlistRoute() {
  return (
    <RequireAuth>
      <RequirePermission allow={canManageUsers} allowedRoles={["admin"]}>
        <RegistrationAllowlistPage />
      </RequirePermission>
    </RequireAuth>
  );
}
