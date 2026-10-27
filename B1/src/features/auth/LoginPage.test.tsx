import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { installSessionAuth, resetSessionForTests } from "../../auth/sessionStore";
import { installFetchStub, jsonResponse, problemResponse, type FetchStub } from "../../testing/httpBoundary";
import { sessionCreated } from "../../testing/contractFixtures";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { LoginPage } from "./LoginPage";

/**
 * 登录页的集成测试：真实的表单、真实的 `signIn`、真实的 HTTP 边界桩。
 * 覆盖"空字段不发请求 / 401 统一文案 / 深链回跳 / 站外 redirect 被丢弃 / 只发一次请求"。
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

function renderLogin(initialEntry: string) {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/login",
    validateSearch: (search: Record<string, unknown>) => ({
      redirect: typeof search.redirect === "string" ? search.redirect : undefined,
      registered: typeof search.registered === "string" ? search.registered : undefined,
    }),
    component: LoginPage,
  });
  const registerRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/register",
    component: () => <p>注册页占位</p>,
  });
  const assignmentsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments",
    component: () => <p>作业页占位</p>,
  });
  const gradesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/grades",
    validateSearch: (search: Record<string, unknown>) => ({
      course_id: typeof search.course_id === "string" ? search.course_id : undefined,
    }),
    component: () => <p>成绩页占位</p>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      loginRoute,
      registerRoute,
      assignmentsRoute,
      gradesRoute,
    ]),
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
  });
  renderWithProviders(<RouterProvider router={router} />);
  return router;
}

function stubSession(respond: () => Response | Promise<Response>) {
  stub = installFetchStub([
    { method: "POST", path: "/api/v1/auth/session", respond },
  ]);
}

describe("P02 登录页", () => {
  it("空用户名或密码：就地显示字段错误并且不发请求", async () => {
    const user = userEvent.setup();
    const router = renderLogin("/login");
    stub = installFetchStub([]);

    await screen.findByTestId("login-panel");
    await user.click(screen.getByTestId("login-submit"));

    expect(await screen.findByText("请输入用户名")).toBeInTheDocument();
    expect(screen.getByText("请输入密码")).toBeInTheDocument();
    expect(stub.requests).toHaveLength(0);
    expect(screen.getByLabelText("用户名")).toHaveFocus();
    expect(router.state.location.pathname).toBe("/login");
  });

  it("凭据错误显示统一提示，不区分账号是否存在", async () => {
    const user = userEvent.setup();
    const router = renderLogin("/login");
    stubSession(() =>
      problemResponse({ status: 401, title: "Unauthorized", detail: "invalid credentials" }),
    );

    await screen.findByTestId("login-panel");
    await user.type(screen.getByLabelText("用户名"), "student1");
    await user.type(screen.getByLabelText("密码"), "wrong-password");
    await user.click(screen.getByTestId("login-submit"));

    expect(await screen.findByTestId("login-error")).toHaveTextContent("用户名或密码不正确");
    expect(router.state.location.pathname).toBe("/login");
    // 失败后凭据仍可继续编辑，不需要重打用户名。
    expect(screen.getByLabelText("用户名")).toHaveValue("student1");
  });

  it("登录成功且没有 redirect 时进入作业页（不再有独立首页）", async () => {
    const user = userEvent.setup();
    const router = renderLogin("/login");
    stubSession(() => jsonResponse(sessionCreated({ roles: ["student"] })));

    await screen.findByTestId("login-panel");
    await user.type(screen.getByLabelText("用户名"), "student1");
    await user.type(screen.getByLabelText("密码"), "correct-password");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/assignments"));
  });

  it("登录成功回到原深链（含筛选参数）", async () => {
    const user = userEvent.setup();
    const router = renderLogin("/login?redirect=%2Fgrades%3Fcourse_id%3Dabc");
    stubSession(() => jsonResponse(sessionCreated({ roles: ["teacher"] })));

    await screen.findByTestId("login-panel");
    await user.type(screen.getByLabelText("用户名"), "teacher1");
    await user.type(screen.getByLabelText("密码"), "correct-password");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/grades"));
    expect((router.state.location.search as { course_id?: string }).course_id).toBe("abc");
  });

  it("站外 redirect 被丢弃，登录后仍停在站内作业页", async () => {
    const user = userEvent.setup();
    const router = renderLogin("/login?redirect=%2F%2Fevil.example%2Fpath");
    stubSession(() => jsonResponse(sessionCreated({ roles: ["student"] })));

    await screen.findByTestId("login-panel");
    await user.type(screen.getByLabelText("用户名"), "student1");
    await user.type(screen.getByLabelText("密码"), "correct-password");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/assignments"));
  });

  it("提交中按钮显示「正在登录…」并只发一次请求", async () => {
    const user = userEvent.setup();
    renderLogin("/login");
    stubSession(
      () =>
        new Promise<Response>((resolve) => {
          setTimeout(() => resolve(jsonResponse(sessionCreated({ roles: ["student"] }))), 40);
        }),
    );

    await screen.findByTestId("login-panel");
    await user.type(screen.getByLabelText("用户名"), "student1");
    await user.type(screen.getByLabelText("密码"), "correct-password");

    const submit = screen.getByTestId("login-submit");
    await user.click(submit);
    expect(submit).toHaveTextContent("正在登录…");
    expect(submit).toBeDisabled();
    await user.click(submit);
    await user.click(submit);

    await waitFor(() => expect(stub?.requests).toHaveLength(1));
  });

  it("注册成功后回到登录页显示最少量成功提示", async () => {
    renderLogin("/login?registered=created");
    stub = installFetchStub([]);
    expect(await screen.findByTestId("login-registered")).toHaveTextContent(
      "账号已创建，请登录。",
    );
  });
});
