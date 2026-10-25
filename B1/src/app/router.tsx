import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { AppShell } from "./AppShell";
import { LoginPage } from "../features/auth/LoginPage";
import { RegisterPage } from "../features/auth/RegisterPage";
import {
  AssignmentDetailRoute,
  AssignmentsRoute,
  AuthoringEditRoute,
  AuthoringNewRoute,
  GradesRoute,
  HomeRoute,
  QuarantineRoute,
  QualityRoute,
  QuestionDetailRoute,
  QuestionListRoute,
  RosterImportRoute,
  RosterRoute,
  SubmitRoute,
  UsersRoute,
} from "./routeComponents";

/**
 * 路由表（**共享文件**：改动前在 `docs/phases/active/` 登记）。
 * 本文件只组装路由；页面与守卫在 `routeComponents.tsx`。
 */

const rootRoute = createRootRoute({ component: AppShell });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: HomeRoute,
});

/**
 * `/login` 的查询串。**两个键都必须可选**：多数入口是裸的 `/login`（首页提示、注册页链接），
 * 若把键推成"必填但可为 undefined"，那些 `Link` 会全部报类型错。
 */
type LoginSearch = { redirect?: string; registered?: string };

function validateLoginSearch(search: Record<string, unknown>): LoginSearch {
  const parsed: LoginSearch = {};
  // `redirect` 只接受站内相对路径；`//host` 会被浏览器当成外站，必须排除（开放重定向）。
  if (
    typeof search.redirect === "string" &&
    search.redirect.startsWith("/") &&
    !search.redirect.startsWith("//")
  ) {
    parsed.redirect = search.redirect;
  }
  if (typeof search.registered === "string" && search.registered !== "") {
    parsed.registered = search.registered;
  }
  return parsed;
}

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: validateLoginSearch,
  component: LoginPage,
});

const registerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/register",
  component: RegisterPage,
});

const questionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/questions",
  component: QuestionListRoute,
});

const questionDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/questions/$questionId",
  validateSearch: (search: Record<string, unknown>) => ({
    version:
      typeof search.version === "string" && search.version !== "" ? search.version : undefined,
  }),
  component: QuestionDetailRoute,
});

const questionNewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/questions/new",
  component: AuthoringNewRoute,
});

const questionEditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/questions/$questionId/edit",
  component: AuthoringEditRoute,
});

const submitRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/submit/$questionVersionId",
  component: SubmitRoute,
});

const assignmentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/assignments",
  component: AssignmentsRoute,
});

const assignmentDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/assignments/$assignmentId",
  component: AssignmentDetailRoute,
});

const gradesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/grades",
  component: GradesRoute,
});

const rosterRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/roster",
  component: RosterRoute,
});

const rosterImportRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/roster/import",
  component: RosterImportRoute,
});

/**
 * P14 的标签走查询串（`?tab=users|allowlist`）：刷新、前进、后退都回到同一个标签，
 * 而切换标签本身不触发任何写操作。
 *
 * 未知值（例如旧的 `/admin/users?tab=platform`）被**丢弃**而不是渲染成第三条页面：
 * 归一后就是默认的 `users`，服务端授权检查照常执行。
 */
type AdminUsersSearch = { tab?: "users" | "allowlist" };

function validateAdminUsersSearch(search: Record<string, unknown>): AdminUsersSearch {
  if (search.tab === "allowlist") return { tab: "allowlist" };
  if (search.tab === "users") return { tab: "users" };
  return {};
}

const usersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/users",
  validateSearch: validateAdminUsersSearch,
  component: UsersRoute,
});

/** 隔离区复核台（CHG-0014）。 */
const quarantineRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/intake/quarantines",
  component: QuarantineRoute,
});

/**
 * 题目质量视图（CHG-0015）。筛选条件走查询串，使"某版本的教师质量视图"成为可分享的深链。
 * 三个参数都可选：`question_version_id` 虽是契约的必需参数，但页面在它为空时**不发请求**并
 * 说明原因，而不是渲染一个空结果——因此路由层不强制要求它。
 */
const qualityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/quality",
  validateSearch: (search: Record<string, unknown>) => ({
    question_version_id:
      typeof search.question_version_id === "string" && search.question_version_id !== ""
        ? search.question_version_id
        : undefined,
    from: typeof search.from === "string" && search.from !== "" ? search.from : undefined,
    to: typeof search.to === "string" && search.to !== "" ? search.to : undefined,
  }),
  component: QualityRoute,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  registerRoute,
  questionsRoute,
  // 静态段 `/questions/new` 必须与动态段 `/questions/$questionId` 同时注册：
  // 路由器按段排序，静态段优先，因此 `new` 不会被当成一个 question_id。
  questionNewRoute,
  questionDetailRoute,
  questionEditRoute,
  submitRoute,
  assignmentsRoute,
  assignmentDetailRoute,
  gradesRoute,
  rosterRoute,
  rosterImportRoute,
  usersRoute,
  quarantineRoute,
  qualityRoute,
]);

export const router = createRouter({
  routeTree,
  // Must match `base` in vite.config.ts: Vercel serves the SPA under `/app`, so
  // routes and every generated link have to live under the same prefix.
  basepath: "/app",
  defaultPreload: false,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
