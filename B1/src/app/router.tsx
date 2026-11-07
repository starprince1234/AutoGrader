import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { AppShell } from "./AppShell";
import { ForgotPasswordPage } from "../features/auth/ForgotPasswordPage";
import { LandingPage } from "../features/home/LandingPage";
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
  UsersRoute,
} from "./routeComponents";

/**
 * 路由表（**共享文件**：改动前在 `docs/phases/active/` 登记）。
 * 本文件只组装路由；页面与守卫在 `routeComponents.tsx`。
 */

const rootRoute = createRootRoute({ component: AppShell, notFoundComponent: NotFoundPage });

/**
 * `/` 是面向所有访客的导览页：左侧教师端、右侧学生端，各自只列本端功能。
 * 登录与否都在这里先看到两栏；已登录用户直接点入口即可，未登录用户在本页浮窗登录。
 * 查询串承接三类一次性状态：深链回跳、注册成功、重置密码成功。
 */
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  validateSearch: validateAuthSearch,
  component: LandingPage,
});

/**
 * 认证相关查询串。**所有键都必须可选**：多数入口是裸的 `/` 或 `/login`。
 */
type AuthSearch = {
  redirect?: string;
  registered?: string;
  reset?: string;
  login?: boolean;
};

function validateAuthSearch(search: Record<string, unknown>): AuthSearch {
  const parsed: AuthSearch = {};
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
  if (search.login === true || search.login === 1 || search.login === "1") {
    parsed.login = true;
  }
  return parsed;
}

/**
 * `/login` 不再是一张页面，只保留为旧链接/收藏的兼容入口：带着原查询串回到导览页，
 * 由导览页直接打开登录浮窗。这样退出登录、注册成功和找回密码成功都不会再停在一张
 * 独立登录页上。
 */
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: validateAuthSearch,
  beforeLoad: ({ search }) => {
    throw redirect({
      to: "/",
      search: { ...search, login: true },
      replace: true,
    });
  },
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
type AuthoringNewSearch = { generation?: string; ai?: boolean };

function validateAuthoringNewSearch(search: Record<string, unknown>): AuthoringNewSearch {
  const parsed: AuthoringNewSearch = {};
  if (typeof search.generation === "string" && search.generation !== "") {
    parsed.generation = search.generation;
  }
  // `ai=1` 打开「AI 出题」视图：写进 URL 是为了刷新/后退能回到同一处，
  // 且不会像独立路由那样把内存里的表单丢掉。
  if (search.ai === "1" || search.ai === 1 || search.ai === true) {
    parsed.ai = true;
  }
  return parsed;
}

const questionNewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/questions/new",
  validateSearch: validateAuthoringNewSearch,
  component: AuthoringNewRoute,
});

/**
 * `/questions/{id}/edit` 的查询串：`from=detail` 表示这一页是从**题目详情页**点
 * 「编辑草稿 / 另存为新版本」进来的，返回按钮要回到那道题，而不是题库列表（3.0）。
 */
type AuthoringEditSearch = { from?: "detail" };

function validateAuthoringEditSearch(search: Record<string, unknown>): AuthoringEditSearch {
  return search.from === "detail" ? { from: "detail" } : {};
}

const questionEditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/questions/$questionId/edit",
  validateSearch: validateAuthoringEditSearch,
  component: AuthoringEditRoute,
});

const assignmentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/assignments",
  component: AssignmentsRoute,
});

/**
 * 作业详情/工作区：`/assignments/{assignment_id}`（CHG-0032）。
 *
 * 旧的单题作答路由 `/submit/{question_version_id}` 与页面已**完整删除**（不保留别名）。
 * 学生从作业列表「继续作答」进入时用 `?question=<version_id>` 预选题目；该值不是本作业的
 * 题目时页面忽略它并停在第一题（查询串只承载对象 id，不承载答案内容）。
 */
const assignmentDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/assignments/$assignmentId",
  validateSearch: (search: Record<string, unknown>) => {
    const result: { question?: string } = {};
    if (typeof search.question === "string" && search.question !== "") {
      result.question = search.question;
    }
    return result;
  },
  component: AssignmentDetailRoute,
});

const gradesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/grades",
  // P10 的“查看本次结果”用真实 `evaluation_run_id` 深链到同一个 report；URL 不携带学生身份。
  validateSearch: (search: Record<string, unknown>) => {
    const result: { evaluation_run_id?: string } = {};
    if (typeof search.evaluation_run_id === "string" && search.evaluation_run_id !== "") {
      result.evaluation_run_id = search.evaluation_run_id;
    }
    return result;
  },
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
