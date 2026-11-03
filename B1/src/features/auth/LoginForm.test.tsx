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
  useNavigate,
  useSearch,
} from "@tanstack/react-router";
import { installSessionAuth, resetSessionForTests } from "../../auth/sessionStore";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type FetchStub,
} from "../../testing/httpBoundary";
import { sessionCreated } from "../../testing/contractFixtures";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { LoginForm } from "./LoginForm";

/**
 * 登录表单的集成测试：真实的表单、真实的 `signIn`、真实的 HTTP 边界桩。
 * 独立登录页已删除，表单只作为导览页浮窗与回跳入口的内容存在；
 * 这里用最小路由验证它的输入校验、失败文案、回跳和密码可见性。
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

function FormHarness() {
  const navigate = useNavigate();
  const search = useSearch({ from: "/" });
  return (
    <LoginForm
      redirect={search.redirect}
      registered={Boolean(search.registered)}
      reset={Boolean(search.reset)}
      onSuccess={(target) => navigate({ href: target, replace: true })}
    />
  );
}

async function renderForm(initialEntry: string) {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const formRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    validateSearch: (search: Record<string, unknown>) => ({
      redirect: typeof search.redirect === "string" ? search.redirect : undefined,
      registered: typeof search.registered === "string" ? search.registered : undefined,
      reset: typeof search.reset === "string" ? search.reset : undefined,
    }),
    component: FormHarness,
  });
  const placeholders = [
    ["/assignments", "作业页占位"],
    ["/grades", "成绩页占位"],
    ["/register", "注册页占位"],
    ["/forgot-password", "忘记密码页占位"],
  ].map(([path, label]) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path,
      component: () => <p>{label}</p>,
    }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren([formRoute, ...placeholders]),
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
  });
  renderWithProviders(<RouterProvider router={router} />);
  await screen.findByTestId("login-submit");
  return router;
}

function stubSession(respond: () => Response | Promise<Response>) {
  stub = installFetchStub([
    { method: "POST", path: "/api/v1/auth/session", respond },
  ]);
}

describe("登录表单（导览页浮窗内容）", () => {
  it("空用户名或密码：就地显示字段错误并且不发请求", async () => {
    const user = userEvent.setup();
    const router = await renderForm("/");
    stub = installFetchStub([]);

    await user.click(screen.getByTestId("login-submit"));

    expect(await screen.findByText("请输入用户名")).toBeInTheDocument();
    expect(screen.getByText("请输入密码")).toBeInTheDocument();
    expect(stub.requests).toHaveLength(0);
    expect(screen.getByLabelText("用户名")).toHaveFocus();
    expect(router.state.location.pathname).toBe("/");
  });

  it("凭据错误显示统一提示，不区分账号是否存在", async () => {
    const user = userEvent.setup();
    const router = await renderForm("/");
    stubSession(() =>
      problemResponse({ status: 401, title: "Unauthorized", detail: "invalid credentials" }),
    );

    await user.type(screen.getByLabelText("用户名"), "student1");
    await user.type(screen.getByLabelText("密码"), "wrong-password");
    await user.click(screen.getByTestId("login-submit"));

    expect(await screen.findByTestId("login-error")).toHaveTextContent("用户名或密码不正确");
    expect(router.state.location.pathname).toBe("/");
    expect(screen.getByLabelText("用户名")).toHaveValue("student1");
  });

  it("登录成功且没有 redirect 时进入作业页", async () => {
    const user = userEvent.setup();
    const router = await renderForm("/");
    stubSession(() => jsonResponse(sessionCreated({ roles: ["student"] })));

    await user.type(screen.getByLabelText("用户名"), "student1");
    await user.type(screen.getByLabelText("密码"), "correct-password");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/assignments"));
  });

  it("登录成功回到原深链（含筛选参数）", async () => {
    const user = userEvent.setup();
    const router = await renderForm("/?redirect=%2Fgrades%3Fcourse_id%3Dabc");
    stubSession(() => jsonResponse(sessionCreated({ roles: ["teacher"] })));

    await user.type(screen.getByLabelText("用户名"), "teacher1");
    await user.type(screen.getByLabelText("密码"), "correct-password");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/grades"));
    expect((router.state.location.search as { course_id?: string }).course_id).toBe("abc");
  });

  it("站外 redirect 被丢弃，登录后仍停在站内作业页", async () => {
    const user = userEvent.setup();
    const router = await renderForm("/?redirect=%2F%2Fevil.example%2Fpath");
    stubSession(() => jsonResponse(sessionCreated({ roles: ["student"] })));

    await user.type(screen.getByLabelText("用户名"), "student1");
    await user.type(screen.getByLabelText("密码"), "correct-password");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/assignments"));
  });

  it("提交中按钮显示「正在登录…」并只发一次请求", async () => {
    const user = userEvent.setup();
    await renderForm("/");
    stubSession(
      () =>
        new Promise<Response>((resolve) => {
          setTimeout(() => resolve(jsonResponse(sessionCreated({ roles: ["student"] }))), 40);
        }),
    );

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

  it("注册成功后显示最少量成功提示", async () => {
    await renderForm("/?registered=created");
    stub = installFetchStub([]);
    expect(await screen.findByTestId("login-registered")).toHaveTextContent(
      "账号已创建，请登录。",
    );
  });

  it("密码显示按钮在登录失败后仍然保留，可继续查看/隐藏密码", async () => {
    const user = userEvent.setup();
    await renderForm("/");
    stubSession(() =>
      problemResponse({ status: 401, title: "Unauthorized", detail: "invalid credentials" }),
    );

    const passwordInput = screen.getByLabelText("密码");
    const toggle = screen.getByTestId("login-password-toggle");

    await user.type(screen.getByLabelText("用户名"), "student1");
    await user.type(passwordInput, "wrong-password");
    expect(passwordInput).toHaveAttribute("type", "password");

    await user.click(toggle);
    expect(passwordInput).toHaveAttribute("type", "text");
    expect(toggle).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByTestId("login-submit"));
    expect(await screen.findByTestId("login-error")).toBeInTheDocument();

    // 修复的核心：提交失败后输入框仍在，且显示/隐藏状态没有被重置。
    expect(passwordInput).toHaveAttribute("type", "text");
    expect(screen.getByTestId("login-password-toggle")).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(screen.getByTestId("login-password-toggle"));
    expect(passwordInput).toHaveAttribute("type", "password");
  });

  it("提供可用的忘记密码入口", async () => {
    const user = userEvent.setup();
    const router = await renderForm("/");

    await user.click(screen.getByTestId("login-forgot-password"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/forgot-password"));
  });

  it("密码重置成功后显示明确提示", async () => {
    await renderForm("/?reset=done");
    stub = installFetchStub([]);
    expect(await screen.findByTestId("login-reset")).toHaveTextContent(
      "密码已重置，请使用新密码登录。",
    );
  });
});
