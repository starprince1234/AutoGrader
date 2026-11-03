import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import {
  getSessionState,
  installSessionAuth,
  resetSessionForTests,
  signIn,
} from "../../auth/sessionStore";
import { readPortalIntent } from "../../auth/portal";
import {
  installFetchStub,
  jsonResponse,
  type FetchStub,
} from "../../testing/httpBoundary";
import { sessionCreated } from "../../testing/contractFixtures";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { LandingPage } from "./LandingPage";

/** 导览页：两栏功能不混、匿名点入口记住意图、已登录角色与入口端匹配。 */

let stub: FetchStub | null = null;

beforeEach(() => {
  installSessionAuth();
  sessionStorage.clear();
});

afterEach(() => {
  stub?.restore();
  stub = null;
  resetSessionForTests();
  sessionStorage.clear();
});

function renderLanding(initialEntry = "/") {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const landingRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    validateSearch: (search: Record<string, unknown>) => ({
      redirect: typeof search.redirect === "string" ? search.redirect : undefined,
      registered: typeof search.registered === "string" ? search.registered : undefined,
      reset: typeof search.reset === "string" ? search.reset : undefined,
      login: search.login === true,
    }),
    component: LandingPage,
  });
  const placeholders = [
    ["/login", "登录页"],
    ["/assignments", "作业页"],
    ["/questions", "题库页"],
    ["/grades", "成绩页"],
    ["/roster", "课程页"],
    ["/review", "复核页"],
  ].map(([path, label]) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path,
      component: () => <p>{label}</p>,
    }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren([landingRoute, ...placeholders]),
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
  });
  renderWithProviders(<RouterProvider router={router} />);
  return router;
}

async function signedInAs(role: "student" | "teacher" | "admin") {
  stub = installFetchStub([
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () => jsonResponse(sessionCreated({ roles: [role] })),
    },
    {
      method: "DELETE",
      path: "/api/v1/auth/session/logout",
      respond: () => new Response(null, { status: 204 }),
    },
  ]);
  await signIn(`${role}1`, "pw");
}

describe("P01 导览页", () => {
  it("匿名访客第一眼看到教师端与学生端两栏，功能各自独立", async () => {
    renderLanding();

    expect(await screen.findByTestId("landing-page")).toBeInTheDocument();
    const teacher = screen.getByTestId("landing-teacher");
    const student = screen.getByTestId("landing-student");

    expect(within(teacher).getByText("作业管理")).toBeInTheDocument();
    expect(within(teacher).getByText("题库")).toBeInTheDocument();
    expect(within(teacher).getByText("班级成绩")).toBeInTheDocument();
    expect(within(teacher).getByText("课程与名单")).toBeInTheDocument();
    expect(within(teacher).getByText("复核工作区")).toBeInTheDocument();

    // 学生端只有自己的两种功能；不能出现教师端的题库/课程/复核。
    expect(within(student).getByText("我的作业")).toBeInTheDocument();
    expect(within(student).getByText("我的成绩")).toBeInTheDocument();
    expect(within(student).queryByText("题库")).not.toBeInTheDocument();
    expect(within(student).queryByText("课程与名单")).not.toBeInTheDocument();
    expect(within(student).queryByText("复核工作区")).not.toBeInTheDocument();
    expect(within(teacher).queryByText("我的作业")).not.toBeInTheDocument();
  });

  it("注册成功回到导览页时自动打开浮窗并显示成功提示", async () => {
    renderLanding("/?registered=created");

    const dialog = await screen.findByTestId("login-dialog");
    expect(within(dialog).getByTestId("login-registered")).toHaveTextContent(
      "账号已创建，请登录。",
    );
    expect(screen.getByTestId("landing-page")).toBeInTheDocument();
  });

  it("匿名点击教师端入口：当前页打开登录浮窗，不跳转新页面", async () => {
    const user = userEvent.setup();
    const router = renderLanding();

    await screen.findByTestId("landing-page");
    await user.click(screen.getByTestId("landing-teacher-assignments"));

    const dialog = await screen.findByTestId("login-dialog");
    expect(within(dialog).getByText("教师端登录")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("用户名")).toHaveFocus();
    expect(router.state.location.pathname).toBe("/");
    expect(readPortalIntent()).toEqual({ portal: "teacher", path: "/assignments" });
  });

  it("在浮窗里登录为学生会直接进入所点功能，意图仍是学生端", async () => {
    const user = userEvent.setup();
    const router = renderLanding();
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["student"] })),
      },
    ]);

    await screen.findByTestId("landing-page");
    await user.click(screen.getByTestId("landing-student-grades"));

    const dialog = await screen.findByTestId("login-dialog");
    expect(within(dialog).getByText("学生端登录")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("用户名"), "student1");
    await user.type(within(dialog).getByLabelText("密码"), "correct-password");
    await user.click(within(dialog).getByTestId("login-submit"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/grades"));
    expect(readPortalIntent()).toEqual({ portal: "student", path: "/grades" });
  });

  it("教师登录记忆：教师端入口可直接点，学生端入口禁用", async () => {
    await signedInAs("teacher");
    renderLanding();

    expect(await screen.findByTestId("landing-identity")).toHaveTextContent("已登录：teacher1");
    const teacher = screen.getByTestId("landing-teacher");
    const student = screen.getByTestId("landing-student");
    expect(within(teacher).getByRole("link", { name: /作业管理/ })).toBeInTheDocument();
    expect(screen.getByTestId("landing-student-assignments")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(within(student).queryByRole("link", { name: /我的作业/ })).toBeNull();
    expect(within(student).getByText(/当前账号没有学生端权限/)).toBeInTheDocument();
    expect(screen.getByTestId("landing-logout")).toBeInTheDocument();
  });

  it("学生登录记忆：学生端入口可直接点，教师端入口禁用", async () => {
    await signedInAs("student");
    renderLanding();

    await screen.findByTestId("landing-page");
    const teacher = screen.getByTestId("landing-teacher");
    const student = screen.getByTestId("landing-student");
    expect(within(student).getByRole("link", { name: /我的作业/ })).toBeInTheDocument();
    expect(screen.getByTestId("landing-teacher-questions")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(within(teacher).queryByRole("link", { name: /题库/ })).toBeNull();
    expect(within(teacher).getByText(/当前账号没有教师端权限/)).toBeInTheDocument();
  });

  it("退出登录后回到匿名导览页，可切换身份", async () => {
    const user = userEvent.setup();
    await signedInAs("teacher");
    renderLanding();

    await screen.findByTestId("landing-identity");
    await user.click(screen.getByTestId("landing-logout"));

    await waitFor(() => expect(getSessionState().status).toBe("anonymous"));
    expect(await screen.findByTestId("landing-login")).toBeInTheDocument();
  });
});
