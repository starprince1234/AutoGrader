import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { AppShell } from "./AppShell";
import { LoginPage } from "../features/auth/LoginPage";
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

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
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

const usersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/users",
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
