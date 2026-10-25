import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type FetchStub,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  UUID_B,
  coursePage,
  courseSummary,
  sessionCreated,
  userPage,
  userSummary,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { peekBuffer, resetLoggerForTests } from "../../logging/logger";
import { AdminUsersPage } from "./AdminUsersPage";
import { normalizeAdminTab, type AdminTab } from "./tab";

/**
 * P14 工作区：一个管理员页面、两个标签，默认 `users`。
 *
 * 断言的重点是"切换标签只切视图"：切到注册名单才读名单，切回用户不再写任何东西；
 * 创建用户只发一次 `POST`，成功后才关闭 Drawer 并提示。
 */

let stub: FetchStub | null = null;

beforeEach(() => {
  resetLoggerForTests();
});

afterEach(() => {
  stub?.restore();
  stub = null;
  vi.restoreAllMocks();
});

function withCommonRoutes(routes: StubbedRoute[]): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({ roles: ["admin"], scope: [{ course_id: UUID_A, section_id: UUID_B }] }),
        ),
    },
    { path: "/api/v1/courses", respond: () => jsonResponse(coursePage([courseSummary()])) },
    ...routes,
  ];
}

/** 与 `UsersRoute` 同一形态的受控外壳：标签状态由外层持有（生产里是 URL）。 */
function Harness({ initial = "users" as AdminTab }: { initial?: AdminTab }) {
  const [tab, setTab] = useState<AdminTab>(initial);
  return <AdminUsersPage tab={tab} onTabChange={setTab} />;
}

const USER_ID = "00000000-0000-4000-8000-0000000000a1";

describe("P14 工作区：标签与查询", () => {
  it("默认打开用户标签，只读用户列表；未知 tab 归一到 users", async () => {
    expect(normalizeAdminTab(undefined)).toBe("users");
    expect(normalizeAdminTab("platform")).toBe("users");
    expect(normalizeAdminTab("allowlist")).toBe("allowlist");

    stub = installFetchStub(
      withCommonRoutes([
        { method: "GET", path: "/api/v1/users", respond: () => jsonResponse(userPage([userSummary()])) },
        {
          method: "GET",
          path: "/api/v1/registration-allowlists",
          respond: () => jsonResponse({ batch_id: null, filename: null, row_count: 0, uploaded_at: null }),
        },
      ]),
    );
    await signIn("admin1", "pw");
    renderWithProviders(<Harness />);

    expect(await screen.findByTestId("users-table")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("用户管理");
    expect(screen.getByRole("tab", { name: "用户" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "注册名单" })).toHaveAttribute("aria-selected", "false");

    // 用户标签不读名单：两个标签各自请求各自的对象。
    expect(
      stub.requests.filter((request) => request.url.pathname === "/api/v1/registration-allowlists"),
    ).toHaveLength(0);
  });

  it("切到注册名单才读批次，切回用户不产生任何写请求", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        { method: "GET", path: "/api/v1/users", respond: () => jsonResponse(userPage([userSummary()])) },
        {
          method: "GET",
          path: "/api/v1/registration-allowlists",
          respond: () => jsonResponse({ batch_id: null, filename: null, row_count: 0, uploaded_at: null }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<Harness />);

    await screen.findByTestId("users-table");
    await user.click(screen.getByRole("tab", { name: "注册名单" }));

    expect(await screen.findByTestId("allowlist-empty")).toHaveTextContent("尚未导入注册名单。");
    expect(
      stub.requests.filter((request) => request.url.pathname === "/api/v1/registration-allowlists"),
    ).toHaveLength(1);

    await user.click(screen.getByRole("tab", { name: "用户" }));
    await screen.findByTestId("users-table");

    const writes = stub.requests.filter((request) => request.method !== "GET");
    // 唯一的非 GET 是登录本身。
    expect(writes.map((request) => request.url.pathname)).toEqual(["/api/v1/auth/session"]);
  });

  it("注册名单标签不显示「创建用户」主操作", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        { method: "GET", path: "/api/v1/users", respond: () => jsonResponse(userPage([])) },
        {
          method: "GET",
          path: "/api/v1/registration-allowlists",
          respond: () => jsonResponse({ batch_id: null, filename: null, row_count: 0, uploaded_at: null }),
        },
      ]),
    );
    await signIn("admin1", "pw");
    renderWithProviders(<Harness initial="allowlist" />);

    await screen.findByTestId("allowlist-empty");
    expect(screen.queryByTestId("create-user")).toBeNull();
  });
});

describe("P14 用户标签：创建用户", () => {
  it("字段顺序为姓名、用户名、学号、角色、初始密码；空表单不发 POST 且焦点落到姓名", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        { method: "GET", path: "/api/v1/users", respond: () => jsonResponse(userPage([])) },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<Harness />);

    await screen.findByTestId("users-empty");
    await user.click(screen.getByTestId("create-user"));

    const form = await screen.findByTestId("create-user-form");
    const labels = within(form)
      .getAllByRole("textbox")
      .map((input) => input.id);
    expect(labels).toEqual([
      "create-user-display-name",
      "create-user-username",
      "create-user-student-number",
    ]);
    const password = within(form).getByLabelText("初始密码");
    expect(password).toHaveAttribute("type", "password");

    await user.click(within(form).getByTestId("create-user-submit"));

    expect(within(form).getByText("请填写姓名。")).toBeInTheDocument();
    expect(within(form).getByText("请填写用户名。")).toBeInTheDocument();
    expect(within(form).getByText("请填写初始密码。")).toBeInTheDocument();
    expect(document.activeElement?.id).toBe("create-user-display-name");
    expect(stub.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/v1/users")).toHaveLength(0);
  });

  it("创建成功只发一次 POST，关闭 Drawer、刷新列表并提示已创建；日志不含密码", async () => {
    let created = false;
    const pending: { resolve: ((response: Response) => void) | null } = { resolve: null };
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () =>
            jsonResponse(
              userPage(
                created
                  ? [userSummary({ user_id: USER_ID, username: "newbie", display_name: "新同学" })]
                  : [],
              ),
            ),
        },
        {
          method: "POST",
          path: "/api/v1/users",
          respond: () =>
            new Promise<Response>((resolve) => {
              created = true;
              pending.resolve = resolve;
            }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<Harness />);

    await screen.findByTestId("users-empty");
    await user.click(screen.getByTestId("create-user"));
    const form = await screen.findByTestId("create-user-form");

    await user.type(within(form).getByLabelText("姓名"), "新同学");
    await user.type(within(form).getByLabelText("用户名"), "newbie");
    await user.type(within(form).getByLabelText("学号（可选）"), "20260001");
    await user.selectOptions(within(form).getByLabelText("角色"), "student");
    await user.type(within(form).getByLabelText("初始密码"), "s3cret-pass");

    await user.click(within(form).getByTestId("create-user-submit"));
    // 请求 pending 期间的第二次点击不得再造一个账号。
    await user.click(within(form).getByTestId("create-user-submit"));
    expect(
      stub.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/v1/users"),
    ).toHaveLength(1);

    const createRequests = stub.requests.filter(
      (request) => request.method === "POST" && request.url.pathname === "/api/v1/users",
    );
    expect(createRequests[0].body).toEqual({
      display_name: "新同学",
      username: "newbie",
      student_number: "20260001",
      roles: ["student"],
      password: "s3cret-pass",
    });

    pending.resolve?.(jsonResponse({ user_id: USER_ID }, 201));

    expect(await screen.findByTestId("users-created")).toHaveTextContent("用户已创建");
    await waitFor(() => expect(screen.queryByTestId("create-user-modal")).toBeNull());
    await waitFor(() => expect(screen.getByTestId("users-table")).toHaveTextContent("newbie"));

    // 前端日志只留事件与非秘密标识：密码与学号内容都不出现。
    const logged = JSON.stringify(peekBuffer());
    expect(logged).toContain("user_created");
    expect(logged).not.toContain("s3cret-pass");
  });

  it("用户名冲突（409）就地提示该字段，保留其他输入并清空密码", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        { method: "GET", path: "/api/v1/users", respond: () => jsonResponse(userPage([])) },
        {
          method: "POST",
          path: "/api/v1/users",
          respond: () =>
            problemResponse({
              status: 409,
              title: "Conflict",
              detail: "A user with that username already exists.",
            }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<Harness />);

    await screen.findByTestId("users-empty");
    await user.click(screen.getByTestId("create-user"));
    const form = await screen.findByTestId("create-user-form");

    await user.type(within(form).getByLabelText("姓名"), "张三");
    await user.type(within(form).getByLabelText("用户名"), "dup");
    await user.type(within(form).getByLabelText("初始密码"), "s3cret-pass");
    await user.click(within(form).getByTestId("create-user-submit"));

    expect(await within(form).findByText("该用户名已被使用。")).toBeInTheDocument();
    // 非秘密字段保留，密码清空（重新输入密码比重新填学号便宜）。
    expect(within(form).getByLabelText("姓名")).toHaveValue("张三");
    expect(within(form).getByLabelText("用户名")).toHaveValue("dup");
    expect(within(form).getByLabelText("初始密码")).toHaveValue("");
    expect(screen.getByTestId("create-user-modal")).toBeInTheDocument();
  });

  it("创建 403 显示权限问题，不显示成功", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        { method: "GET", path: "/api/v1/users", respond: () => jsonResponse(userPage([])) },
        {
          method: "POST",
          path: "/api/v1/users",
          respond: () =>
            problemResponse({
              status: 403,
              title: "Forbidden",
              detail: "The role or course scope does not permit this operation.",
            }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<Harness />);

    await screen.findByTestId("users-empty");
    await user.click(screen.getByTestId("create-user"));
    const form = await screen.findByTestId("create-user-form");
    await user.type(within(form).getByLabelText("姓名"), "张三");
    await user.type(within(form).getByLabelText("用户名"), "ghost");
    await user.type(within(form).getByLabelText("初始密码"), "s3cret-pass");
    await user.click(within(form).getByTestId("create-user-submit"));

    expect(await screen.findByTestId("create-user-forbidden")).toBeInTheDocument();
    expect(screen.queryByTestId("users-created")).toBeNull();
  });
});
