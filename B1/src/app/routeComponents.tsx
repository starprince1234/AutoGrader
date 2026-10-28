import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { AssignmentDetailPage } from "../features/assignments/AssignmentDetailPage";
import { AssignmentsPage } from "../features/assignments/AssignmentsPage";
import { AuthoringPage } from "../features/authoring/AuthoringPage";
import { GradesPage } from "../features/grades/GradesPage";
import { ReviewPage } from "../features/review/ReviewPage";
import { QuestionDetailPage } from "../features/questions/QuestionDetailPage";
import { QuestionListPage } from "../features/questions/QuestionListPage";
import { RosterPage } from "../features/roster/RosterPage";
import { SubmitPage } from "../features/submission/SubmitPage";
import { AdminUsersPage } from "../features/admin/AdminUsersPage";
import { normalizeAdminTab } from "../features/admin/tab";
import { RequireAuth, RequirePermission } from "../auth/guards";
import {
  canAuthorQuestions,
  canManageUsers,
  canReviewQuarantine,
  canSubmitAnswer,
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
  const { generation, ai } = useSearch({ from: "/questions/new" });
  const navigate = useNavigate();
  return (
    <RequireAuth>
      <RequirePermission
        allow={canAuthorQuestions}
        allowedRoles={["teacher", "assistant", "admin"]}
      >
        <AuthoringPage
          questionId={null}
          generationId={generation ?? null}
          aiView={Boolean(ai)}
          onGenerationId={(next) => {
            // 候选 id 写回 URL：刷新后能恢复同一份原始候选，分享出去的链接也不会丢上下文。
            // 用函数式 search 更新，避免把同一条 URL 上的 `ai` 键一起抹掉。
            void navigate({
              to: "/questions/new",
              search: (previous) => ({ ...previous, generation: next ?? undefined }),
            });
          }}
          onAiViewChange={(next) => {
            void navigate({
              to: "/questions/new",
              search: (previous) => ({ ...previous, ai: next ? true : undefined }),
            });
          }}
        />
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


/**
 * P14 管理员账户工作区（`/admin/users?tab=users|allowlist`，仅管理员）。
 *
 * 标签状态住在 URL 里：刷新、前进、后退都回到同一个标签，且切换标签只改视图。
 * 可见性仍不是授权——B4 对用户与名单的每个端点独立判定角色，非管理员直接调用得到 403。
 */
export function UsersRoute() {
  const { tab } = useSearch({ from: "/admin/users" });
  const navigate = useNavigate();
  return (
    <RequireAuth>
      <RequirePermission allow={canManageUsers} allowedRoles={["admin"]}>
        <AdminUsersPage
          tab={normalizeAdminTab(tab)}
          onTabChange={(next) => {
            void navigate({ to: "/admin/users", search: { tab: next } });
          }}
        />
      </RequirePermission>
    </RequireAuth>
  );
}

/**
 * P13 复核工作区（`/review?tab=mail|quality`）。
 *
 * 唯一路由、两个标签；标签与筛选住在 URL 里（刷新/复制链接/前进后退都恢复）。
 * 可见性与 B2 的角色集合一致；服务端仍是判定方：学生或越权调用得到 403，页面原样展示。
 */
export function ReviewRoute() {
  const search = useSearch({ from: "/review" });
  const navigate = useNavigate();
  return (
    <RequireAuth>
      <RequirePermission
        allow={canReviewQuarantine}
        allowedRoles={["teacher", "assistant", "admin"]}
      >
        <ReviewPage
          search={search}
          onChange={(next) => {
            void navigate({ to: "/review", search: next });
          }}
        />
      </RequirePermission>
    </RequireAuth>
  );
}
