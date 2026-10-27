import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { AppShell } from "./AppShell";
import { ForgotPasswordPage } from "../features/auth/ForgotPasswordPage";
import { LoginPage } from "../features/auth/LoginPage";
import { NotFoundPage } from "./NotFoundPage";
import { RegisterPage } from "../features/auth/RegisterPage";
import { safeRedirectTarget } from "../auth/redirect";
import { parseReviewSearch } from "../features/review/search";
import {
  AssignmentDetailRoute,
  AssignmentsRoute,
  AuthoringEditRoute,
  AuthoringNewRoute,
  GradesRoute,
  QuestionDetailRoute,
  QuestionListRoute,
  ReviewRoute,
  RosterRoute,
  SubmitRoute,
  UsersRoute,
} from "./routeComponents";

/**
 * 路由表（**共享文件**：改动前在 `docs/phases/active/` 登记）。
 * 本文件只组装路由；页面与守卫在 `routeComponents.tsx`。
 */

const rootRoute = createRootRoute({ component: AppShell, notFoundComponent: NotFoundPage });

/**
 * `/` 不是页面，只是一个入口：登录后一律进入作业页（P01）。
 * 在 `beforeLoad` 里重定向，避免先渲染一颗"首页卡片"再跳走。
 */
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/assignments", replace: true });
  },
});

/**
 * `/login` 的查询串。**两个键都必须可选**：多数入口是裸的 `/login`，
 * 若把键推成"必填但可为 undefined"，那些 `Link` 会全部报类型错。
 */
type LoginSearch = { redirect?: string; registered?: string; reset?: string };

function validateLoginSearch(search: Record<string, unknown>): LoginSearch {
  const parsed: LoginSearch = {};
  // `redirect` 是用户可控的：只有 `safeRedirectTarget` 认可的站内路径才进入会话。
  const redirectTarget = safeRedirectTarget(search.redirect);
  if (redirectTarget) parsed.redirect = redirectTarget;
  // `registered` 只是一个标记。查询串解析器会把 `?registered=1` 读成数字 1，
  // 因此数字/布尔值同样按"已注册"处理，而不是让成功提示悄悄消失。
  if (typeof search.registered === "string" && search.registered !== "") {
    parsed.registered = search.registered;
  } else if (search.registered === true || typeof search.registered === "number") {
    parsed.registered = "created";
  }
  if (typeof search.reset === "string" && search.reset !== "") {
    parsed.reset = search.reset;
  } else if (search.reset === true || typeof search.reset === "number") {
    parsed.reset = "done";
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

/** 忘记密码：公开页，走 B4 的邮件验证码重置流程（P02 补充）。 */
const forgotPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/forgot-password",
  component: ForgotPasswordPage,
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

/**
 * `/questions/new` 的查询串：`generation` 是 AI 候选 id（AI-FLOW-01A）。
 *
 * 放进 URL 而不是组件状态，是因为刷新后要能用它取回**服务端原始候选**——这正是
 * `generation_id` 存在的理由（教师尚未保存的字段编辑不持久化，恢复的是模型给出的那一份）。
 * 键可选：多数入口是裸的 `/questions/new`。
 */
type AuthoringNewSearch = { generation?: string };

function validateAuthoringNewSearch(search: Record<string, unknown>): AuthoringNewSearch {
  const parsed: AuthoringNewSearch = {};
  if (typeof search.generation === "string" && search.generation !== "") {
    parsed.generation = search.generation;
  }
  return parsed;
}

const questionNewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/questions/new",
  validateSearch: validateAuthoringNewSearch,
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

/**
 * P12 工作区：`view`（名单 / 导入名单）、课程/课序作用域、导入任务 ID 全部住在 query。
 * 刷新、前进后退、复制链接都回到同一处状态；无效值按缺省处理，不渲染第三个页面。
 */
type RosterSearch = {
  view?: "roster" | "import";
  course_id?: string;
  section_id?: string;
  import_id?: string;
};

function validateRosterSearch(search: Record<string, unknown>): RosterSearch {
  const result: RosterSearch = {};
  if (search.view === "import") result.view = "import";
  if (search.view === "roster") result.view = "roster";
  if (typeof search.course_id === "string" && search.course_id !== "") {
    result.course_id = search.course_id;
  }
  if (typeof search.section_id === "string" && search.section_id !== "") {
    result.section_id = search.section_id;
  }
  if (typeof search.import_id === "string" && search.import_id !== "") {
    result.import_id = search.import_id;
  }
  return result;
}

const rosterRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/roster",
  validateSearch: validateRosterSearch,
  component: RosterRoute,
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

/**
 * P13 复核工作区的唯一入口（`/review?tab=mail|quality`）。
 *
 * 旧 `/intake/quarantines` 与 `/quality` 已合并到这里：不注册别名、不留兼容路由。
 * 未知 `tab` 由 `parseReviewSearch` 归一到 `mail`；查询串只承载筛选与对象 ID。
 */
const reviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/review",
  validateSearch: parseReviewSearch,
  component: ReviewRoute,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  registerRoute,
  forgotPasswordRoute,
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
  usersRoute,
  reviewRoute,
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
