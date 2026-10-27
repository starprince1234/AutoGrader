import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { AppShell } from "./AppShell";
import { NotFoundPage } from "./NotFoundPage";
import {
  getSessionState,
  installSessionAuth,
  resetSessionForTests,
  restoreSession,
} from "../auth/sessionStore";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type FetchStub,
} from "../testing/httpBoundary";
import { UUID_A, UUID_B } from "../testing/contractFixtures";
import { renderWithProviders } from "../testing/renderWithProviders";

/**
 * 应用外壳的集成测试（P01）：会话恢复门、根入口、角色导航、账户菜单与退出。
 * 这里用真实的 `restoreSession()` 走真实的状态机，只把 HTTP 边界换成桩。
 */

let stub: FetchStub | null = null;

beforeEach(() => {
  installSessionAuth();
});

afterEach(() => {
  stub?.restore();
  stub = null;
  resetSessionForTests();
});

function tokenFor(roles: string[]): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "EdDSA" })}.${encode({
    sub: UUID_A,
    role: roles,
    scope: [{ course_id: UUID_A, section_id: UUID_B }],
  })}.signature`;
}

async function establishSession(role: "anonymous" | "student" | "teacher" | "admin") {
  stub = installFetchStub([
    {
      method: "POST",
      path: "/api/v1/auth/session/refresh",
      respond: () =>
        role === "anonymous"
          ? problemResponse({ status: 401, title: "Unauthorized", detail: "no refresh cookie" })
          : jsonResponse({
              access_token: tokenFor([role]),
              token_type: "Bearer",
              expires_in: 900,
              username: `${role}1`,
              roles: [role],
              scope: [],
            }),
    },
    {
      method: "DELETE",
      path: "/api/v1/auth/session/logout",
      respond: () => new Response(null, { status: 204 }),
    },
  ]);
  await restoreSession();
}

function Placeholder({ label }: { label: string }) {
  return <p>{label}</p>;
}

function renderApp(initialEntry: string) {
  const rootRoute = createRootRoute({
    component: AppShell,
    notFoundComponent: NotFoundPage,
  });
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    beforeLoad: () => {
      throw redirect({ to: "/assignments", replace: true });
    },
  });
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/login",
    validateSearch: (search: Record<string, unknown>) => ({
      redirect: typeof search.redirect === "string" ? search.redirect : undefined,
      registered: typeof search.registered === "string" ? search.registered : undefined,
    }),
    component: () => <Placeholder label="登录页" />,
  });
  const registerRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/register",
    component: () => <Placeholder label="注册页" />,
  });
  const forgotRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/forgot-password",
    component: () => <Placeholder label="忘记密码页" />,
  });
  const assignmentsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments",
    component: () => <Placeholder label="作业页" />,
  });
  const gradesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/grades",
    component: () => <Placeholder label="成绩页" />,
  });
  const questionsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions",
    component: () => <Placeholder label="题库页" />,
  });
  const rosterRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/roster",
    component: () => <Placeholder label="课程页" />,
  });
  const usersRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/admin/users",
    component: () => <Placeholder label="用户管理页" />,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      indexRoute,
      loginRoute,
      registerRoute,
      forgotRoute,
      assignmentsRoute,
      gradesRoute,
      questionsRoute,
      rosterRoute,
      usersRoute,
    ]),
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
  });
  renderWithProviders(<RouterProvider router={router} />);
  return router;
}

describe("P01 根入口与会话恢复", () => {
  it("匿名打开受保护深链：先骨架，再到登录页并保留原站内路径", async () => {
    await establishSession("anonymous");
    const router = renderApp("/grades?course_id=abc");

    // 受保护内容一帧都不渲染
    expect(screen.queryByText("成绩页")).not.toBeInTheDocument();

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect((router.state.location.search as { redirect?: string }).redirect).toBe(
      "/grades?course_id=abc",
    );
  });

  it("匿名打开根路径：进入作业页的登录回跳，而不是欢迎页", async () => {
    await establishSession("anonymous");
    const router = renderApp("/");

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect((router.state.location.search as { redirect?: string }).redirect).toBe("/assignments");
    expect(screen.queryByText(/欢迎/)).not.toBeInTheDocument();
  });

  it("匿名打开忘记密码页：保留公开页，不被送回登录页", async () => {
    await establishSession("anonymous");
    const router = renderApp("/forgot-password");

    expect(await screen.findByText("忘记密码页")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/forgot-password");
  });

  it("已登录打开根路径直接进入作业页", async () => {
    await establishSession("student");
    const router = renderApp("/");

    await waitFor(() => expect(router.state.location.pathname).toBe("/assignments"));
    expect(await screen.findByText("作业页")).toBeInTheDocument();
  });

  it("已登录用户打开登录页会被送回作业页，不出现第二套表单", async () => {
    await establishSession("student");
    const router = renderApp("/login");

    await waitFor(() => expect(router.state.location.pathname).toBe("/assignments"));
    expect(screen.queryByText("登录页")).not.toBeInTheDocument();
  });
});

describe("角色导航与账户菜单", () => {
  it("学生侧栏只有作业与成绩，页头不显示契约版本或欢迎语", async () => {
    await establishSession("student");
    renderApp("/assignments");

    expect(await screen.findByTestId("nav-assignments")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByTestId("nav-grades")).toBeInTheDocument();
    expect(screen.queryByTestId("nav-questions")).toBeNull();
    expect(screen.queryByTestId("nav-roster")).toBeNull();
    expect(screen.queryByText(/契约/)).toBeNull();
    expect(screen.queryByText(/欢迎/)).toBeNull();
    expect(screen.queryByText(UUID_A)).toBeNull();
    expect(screen.getByTestId("session-identity")).toHaveTextContent("student1");
  });

  it("教师/管理员侧栏为作业、题库、成绩、课程", async () => {
    await establishSession("teacher");
    renderApp("/assignments");

    for (const testId of ["nav-assignments", "nav-questions", "nav-grades", "nav-roster"]) {
      expect(await screen.findByTestId(testId)).toBeInTheDocument();
    }
  });

  it("用户管理只在管理员账户菜单里", async () => {
    const user = userEvent.setup();
    await establishSession("admin");
    const router = renderApp("/assignments");

    await screen.findByTestId("account-menu-button");
    expect(screen.queryByTestId("nav-admin-users")).toBeNull();
    await user.click(screen.getByTestId("account-menu-button"));
    await user.click(screen.getByTestId("account-users-link"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/users"));
    expect(await screen.findByText("用户管理页")).toBeInTheDocument();
  });

  it("学生账户菜单没有用户管理", async () => {
    const user = userEvent.setup();
    await establishSession("student");
    renderApp("/assignments");

    await user.click(await screen.findByTestId("account-menu-button"));
    expect(screen.queryByTestId("account-users-link")).toBeNull();
    expect(screen.getByTestId("account-logout")).toBeInTheDocument();
  });

  it("退出登录清空会话并回到干净的登录页（不保留 redirect）", async () => {
    const user = userEvent.setup();
    await establishSession("student");
    const router = renderApp("/grades");

    await screen.findByTestId("account-menu-button");
    await user.click(screen.getByTestId("account-menu-button"));
    await user.click(screen.getByTestId("account-logout"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect((router.state.location.search as { redirect?: string }).redirect).toBeUndefined();
    expect(getSessionState().status).toBe("anonymous");
    const logoutCalls = stub?.requests.filter(
      (request) => request.method === "DELETE" && request.url.pathname === "/api/v1/auth/session/logout",
    );
    expect(logoutCalls).toHaveLength(1);
  });

  it("小屏抽屉打开后可从抽屉选页，并在选页后收起", async () => {
    const user = userEvent.setup();
    await establishSession("teacher");
    const router = renderApp("/grades");

    await user.click(await screen.findByTestId("nav-drawer-open"));
    const drawer = await screen.findByTestId("nav-drawer");
    expect(drawer).toHaveAttribute("aria-modal", "true");
    expect(within(drawer).getByRole("link", { name: "作业" })).toBeInTheDocument();

    await user.click(screen.getByTestId("drawer-nav-assignments"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/assignments"));
    await waitFor(() => expect(screen.queryByTestId("nav-drawer")).toBeNull());
  });
});
