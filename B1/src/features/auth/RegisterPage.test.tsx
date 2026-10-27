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
import { UUID_A } from "../../testing/contractFixtures";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { RegisterPage } from "./RegisterPage";

/**
 * 注册页的集成测试：真实表单与真实 API 调用。
 * 覆盖"格式错误不发请求 / 发码成功进入冷却 / 409 定位到字段并保留身份信息 / 成功后去登录"。
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

function renderRegister() {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const registerRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/register",
    component: RegisterPage,
  });
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/login",
    validateSearch: (search: Record<string, unknown>) => ({
      redirect: typeof search.redirect === "string" ? search.redirect : undefined,
      registered: typeof search.registered === "string" ? search.registered : undefined,
    }),
    component: () => <p>登录页占位</p>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([registerRoute, loginRoute]),
    history: createMemoryHistory({ initialEntries: ["/register"] }),
  });
  renderWithProviders(<RouterProvider router={router} />);
  return router;
}

function routes(overrides: StubbedRoute[] = []): StubbedRoute[] {
  return [
    ...overrides,
    {
      method: "POST",
      path: "/api/v1/auth/registrations/code",
      respond: () =>
        jsonResponse({ expires_in_seconds: 600, resend_after_seconds: 60 }, 202),
    },
    {
      method: "POST",
      path: "/api/v1/auth/registrations",
      respond: () => jsonResponse({ user_id: UUID_A, username: "zhangsan" }, 201),
    },
  ];
}

async function fillIdentity(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("姓名"), "张三");
  await user.type(screen.getByLabelText("学号"), "2023123456789");
  await user.type(screen.getByLabelText("用户名"), "zhangsan");
  await user.type(screen.getByLabelText("邮箱"), "zhangsan@example.com");
}

async function fillAll(user: ReturnType<typeof userEvent.setup>) {
  await fillIdentity(user);
  await user.type(screen.getByLabelText("验证码"), "123456");
  await user.type(screen.getByLabelText("密码"), "password1");
  await user.type(screen.getByLabelText("确认密码"), "password1");
}

describe("P03 注册页", () => {
  it("按「身份信息 / 设置密码」分组，只保留一条常驻帮助文字", async () => {
    renderRegister();
    stub = installFetchStub([]);

    expect(await screen.findByRole("heading", { level: 1, name: "创建学生账号" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "身份信息" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "设置密码" })).toBeInTheDocument();
    expect(screen.getByText("学号需与课程名单一致。")).toBeInTheDocument();
    // 用户名规则只在出错时出现，不作为常驻说明。
    expect(screen.queryByText(/以字母开头/)).not.toBeInTheDocument();
    expect(stub.requests).toHaveLength(0);
  });

  it("学号不是 13 位：错误贴近字段，且不请求发码", async () => {
    const user = userEvent.setup();
    renderRegister();
    stub = installFetchStub(routes());

    await screen.findByTestId("register-panel");
    await user.type(screen.getByLabelText("姓名"), "张三");
    await user.type(screen.getByLabelText("学号"), "123456789012");
    await user.type(screen.getByLabelText("用户名"), "zhangsan");
    await user.type(screen.getByLabelText("邮箱"), "zhangsan@example.com");
    await user.click(screen.getByTestId("register-send-code"));

    expect(await screen.findByText("请输入 13 位数字学号。")).toBeInTheDocument();
    expect(stub.requests).toHaveLength(0);
    expect(screen.getByLabelText("学号")).toHaveFocus();
  });

  it("发送验证码成功后进入冷却，只发一次请求", async () => {
    const user = userEvent.setup();
    renderRegister();
    stub = installFetchStub(routes());

    await screen.findByTestId("register-panel");
    await fillIdentity(user);
    await user.click(screen.getByTestId("register-send-code"));

    expect(await screen.findByTestId("register-notice")).toHaveTextContent("验证码已发送");
    expect(stub.requests).toHaveLength(1);
    expect(stub.lastRequest().body).toEqual({
      username: "zhangsan",
      email: "zhangsan@example.com",
      student_number: "2023123456789",
      display_name: "张三",
    });
    const send = screen.getByTestId("register-send-code");
    expect(send).toBeDisabled();
    expect(send).toHaveTextContent("60 秒后重发");
  });

  it("名单外身份：显示服务端原因且不创建账号", async () => {
    const user = userEvent.setup();
    renderRegister();
    stub = installFetchStub(
      routes([
        {
          method: "POST",
          path: "/api/v1/auth/registrations/code",
          respond: () =>
            problemResponse({
              status: 422,
              title: "Unprocessable Entity",
              detail: "姓名或学号不在注册名单中。若名单刚更新或信息有误,请联系课程管理员。",
            }),
        },
      ]),
    );

    await screen.findByTestId("register-panel");
    await fillIdentity(user);
    await user.click(screen.getByTestId("register-send-code"));

    expect(await screen.findByTestId("register-error")).toHaveTextContent("不在注册名单中");
    expect(stub.requests).toHaveLength(1);
    expect(screen.queryByText("验证码已发送")).not.toBeInTheDocument();
  });

  it("两次密码不一致：报在确认密码字段，不提交", async () => {
    const user = userEvent.setup();
    renderRegister();
    stub = installFetchStub(routes());

    await screen.findByTestId("register-panel");
    await fillAll(user);
    await user.clear(screen.getByLabelText("确认密码"));
    await user.type(screen.getByLabelText("确认密码"), "password2");
    await user.click(screen.getByTestId("register-submit"));

    expect(await screen.findByText("输入的密码不一致。")).toBeInTheDocument();
    expect(stub.requests).toHaveLength(0);
  });

  it("注册成功：清空敏感输入并去登录页显示成功提示", async () => {
    const user = userEvent.setup();
    const router = renderRegister();
    stub = installFetchStub(routes());

    await screen.findByTestId("register-panel");
    await fillAll(user);
    await user.click(screen.getByTestId("register-submit"));

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect((router.state.location.search as { registered?: string }).registered).toBe("created");
    expect(stub.lastRequest().body).toEqual({
      email: "zhangsan@example.com",
      code: "123456",
      password: "password1",
    });
  });

  it("用户名冲突：定位到用户名字段，保留身份字段，清空密码与验证码", async () => {
    const user = userEvent.setup();
    renderRegister();
    stub = installFetchStub(
      routes([
        {
          method: "POST",
          path: "/api/v1/auth/registrations",
          respond: () =>
            problemResponse({
              status: 409,
              title: "Conflict",
              detail: "该用户名已被使用。",
            }),
        },
      ]),
    );

    await screen.findByTestId("register-panel");
    await fillAll(user);
    await user.click(screen.getByTestId("register-submit"));

    expect(await screen.findByText("该用户名已被使用。")).toBeInTheDocument();
    expect(screen.getByLabelText("姓名")).toHaveValue("张三");
    expect(screen.getByLabelText("学号")).toHaveValue("2023123456789");
    expect(screen.getByLabelText("用户名")).toHaveValue("zhangsan");
    expect(screen.getByLabelText("邮箱")).toHaveValue("zhangsan@example.com");
    // 验证码可能已被消费，密码不应留在表单里：重新验证后再提交。
    expect(screen.getByLabelText("验证码")).toHaveValue("");
    expect(screen.getByLabelText("密码")).toHaveValue("");
    expect(screen.getByLabelText("确认密码")).toHaveValue("");
    expect(screen.getByLabelText("用户名")).toHaveFocus();
    expect(stub.requests.filter((request) => request.method === "POST")).toHaveLength(1);
  });

  it("创建中按钮禁用，重复点击只创建一次账号", async () => {
    const user = userEvent.setup();
    renderRegister();
    stub = installFetchStub(
      routes([
        {
          method: "POST",
          path: "/api/v1/auth/registrations",
          respond: () =>
            new Promise<Response>((resolve) => {
              setTimeout(
                () => resolve(jsonResponse({ user_id: UUID_A, username: "zhangsan" }, 201)),
                40,
              );
            }),
        },
      ]),
    );

    await screen.findByTestId("register-panel");
    await fillAll(user);

    const submit = screen.getByTestId("register-submit");
    await user.click(submit);
    expect(submit).toBeDisabled();
    expect(submit).toHaveTextContent("正在创建…");
    await user.click(submit);
    await user.click(submit);

    await waitFor(() => expect(stub?.requests).toHaveLength(1));
  });
});
