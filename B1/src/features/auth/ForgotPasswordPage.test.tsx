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
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type FetchStub,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { ForgotPasswordPage } from "./ForgotPasswordPage";

/**
 * 忘记密码页的集成测试：真实表单、真实 API 调用与字段错误映射。
 * 发码接口对"邮箱是否存在"返回同一提示，这里同时固定通用文案与请求体形状。
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

function renderForgot() {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const forgotRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/forgot-password",
    component: ForgotPasswordPage,
  });
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/login",
    validateSearch: (search: Record<string, unknown>) => ({
      redirect: typeof search.redirect === "string" ? search.redirect : undefined,
      reset: typeof search.reset === "string" ? search.reset : undefined,
    }),
    component: () => <p>登录页占位</p>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([forgotRoute, loginRoute]),
    history: createMemoryHistory({ initialEntries: ["/forgot-password"] }),
  });
  renderWithProviders(<RouterProvider router={router} />);
  return router;
}

function routes(overrides: StubbedRoute[] = []): StubbedRoute[] {
  return [
    ...overrides,
    {
      method: "POST",
      path: "/api/v1/auth/password-reset/code",
      respond: () => jsonResponse({ expires_in_seconds: 600, resend_after_seconds: 60 }, 202),
    },
    {
      method: "POST",
      path: "/api/v1/auth/password-reset",
      respond: () => new Response(null, { status: 204 }),
    },
  ];
}

describe("P02 忘记密码", () => {
  it("邮箱格式不合法时不发请求，错误落在邮箱字段", async () => {
    const user = userEvent.setup();
    renderForgot();
    stub = installFetchStub(routes());

    await screen.findByTestId("forgot-panel");
    await user.type(screen.getByLabelText("注册邮箱"), "not-an-email");
    await user.click(screen.getByTestId("forgot-send-code"));

    expect(await screen.findByText("请输入有效的邮箱地址。")).toBeInTheDocument();
    expect(stub.requests).toHaveLength(0);
    expect(screen.getByLabelText("注册邮箱")).toHaveFocus();
  });

  it("发送验证码使用通用提示并进入冷却", async () => {
    const user = userEvent.setup();
    renderForgot();
    stub = installFetchStub(routes());

    await screen.findByTestId("forgot-panel");
    await user.type(screen.getByLabelText("注册邮箱"), "Student@Example.com");
    await user.click(screen.getByTestId("forgot-send-code"));

    expect(await screen.findByTestId("forgot-notice")).toHaveTextContent(
      "如果该邮箱已注册，验证码已发送。",
    );
    expect(stub.requests).toHaveLength(1);
    expect(stub.lastRequest().body).toEqual({ email: "student@example.com" });
    const send = screen.getByTestId("forgot-send-code");
    expect(send).toBeDisabled();
    expect(send).toHaveTextContent("60 秒后重发");
  });

  it("邮件通道不可用时说明原因，不假装已发送", async () => {
    const user = userEvent.setup();
    renderForgot();
    stub = installFetchStub(
      routes([
        {
          method: "POST",
          path: "/api/v1/auth/password-reset/code",
          respond: () =>
            problemResponse({
              status: 503,
              title: "Service Unavailable",
              detail: "the password reset mail channel is unavailable",
            }),
        },
      ]),
    );

    await screen.findByTestId("forgot-panel");
    await user.type(screen.getByLabelText("注册邮箱"), "student@example.com");
    await user.click(screen.getByTestId("forgot-send-code"));

    expect(await screen.findByTestId("forgot-error")).toHaveTextContent("邮件服务当前不可用");
    expect(screen.queryByTestId("forgot-notice")).toBeNull();
  });

  it("新密码过短或两次不一致时不提交", async () => {
    const user = userEvent.setup();
    renderForgot();
    stub = installFetchStub(routes());

    await screen.findByTestId("forgot-panel");
    await user.type(screen.getByLabelText("注册邮箱"), "student@example.com");
    await user.type(screen.getByLabelText("验证码"), "123456");
    await user.type(screen.getByLabelText("新密码"), "short");
    await user.type(screen.getByLabelText("确认新密码"), "different");
    await user.click(screen.getByTestId("forgot-submit"));

    expect(await screen.findByText("密码至少 8 位。")).toBeInTheDocument();
    expect(screen.getByText("输入的密码不一致。")).toBeInTheDocument();
    expect(stub.requests).toHaveLength(0);
  });

  it("验证码错误显示在验证码字段", async () => {
    const user = userEvent.setup();
    renderForgot();
    stub = installFetchStub(
      routes([
        {
          method: "POST",
          path: "/api/v1/auth/password-reset",
          respond: () =>
            problemResponse({
              status: 422,
              title: "Unprocessable Entity",
              detail: "验证码不正确或已过期,请重新获取。",
            }),
        },
      ]),
    );

    await screen.findByTestId("forgot-panel");
    await user.type(screen.getByLabelText("注册邮箱"), "student@example.com");
    await user.type(screen.getByLabelText("验证码"), "000000");
    await user.type(screen.getByLabelText("新密码"), "new-password1");
    await user.type(screen.getByLabelText("确认新密码"), "new-password1");
    await user.click(screen.getByTestId("forgot-submit"));

    expect(
      await screen.findByText("验证码不正确或已过期，请重新获取。"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("forgot-error")).toBeNull();
  });

  it("提交成功后回到登录页并带上重置成功标记", async () => {
    const user = userEvent.setup();
    const router = renderForgot();
    stub = installFetchStub(routes());

    await screen.findByTestId("forgot-panel");
    await user.type(screen.getByLabelText("注册邮箱"), "student@example.com");
    await user.type(screen.getByLabelText("验证码"), "123456");
    await user.type(screen.getByLabelText("新密码"), "new-password1");
    await user.type(screen.getByLabelText("确认新密码"), "new-password1");
    await user.click(screen.getByTestId("forgot-submit"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect((router.state.location.search as { reset?: string }).reset).toBe("done");
    expect(stub.lastRequest().body).toEqual({
      email: "student@example.com",
      code: "123456",
      new_password: "new-password1",
    });
  });
});
