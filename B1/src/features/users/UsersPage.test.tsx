import { afterEach, describe, expect, it, vi } from "vitest";
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
import type { Role } from "../../api/types";
import { UsersPage } from "./UsersPage";

/**
 * 用户标签（P14 的一半）。
 *
 * 这里断言的都是"页面不能替服务端说话"的地方：状态来自 `is_active`、改角色必须确认且带
 * `expected_updated_at`、停用只发一次 `DELETE`、403 不是空列表。
 */

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  vi.restoreAllMocks();
});

const noop = () => {};

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

const USER_ID = "00000000-0000-4000-8000-0000000000a1";

describe("用户标签：列表与状态（P14 / RQ-B-4-50）", () => {
  it("账号超过一页时给出「加载更多」，加载后到末页给出明确终点", async () => {
    const pages = [
      { items: [userSummary({ user_id: UUID_A, username: "admin1", display_name: "管理员" })], nextCursor: "1" },
      {
        items: [
          userSummary({
            user_id: "00000000-0000-4000-8000-0000000000d9",
            username: "second",
            display_name: "第二页用户",
          }),
        ],
        nextCursor: null,
      },
    ];
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: ({ url }) => {
            const cursor = url.searchParams.get("cursor");
            const index = Math.min(cursor === null ? 0 : Number(cursor), pages.length - 1);
            return jsonResponse(userPage(pages[index].items, pages[index].nextCursor));
          },
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    const table = await screen.findByTestId("users-table");
    expect(within(table).getAllByRole("row")).toHaveLength(2); // 表头 + 1 行
    expect(screen.getByText(/已加载 1 位/)).toBeInTheDocument();

    await user.click(screen.getByTestId("users-load-more"));

    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
    expect(table).toHaveTextContent("second");
    expect(screen.getByText(/已加载 2 位/)).toBeInTheDocument();
    expect(screen.queryByTestId("users-load-more")).toBeNull();
    expect(screen.getByTestId("users-end")).toBeInTheDocument();

    // 第二页请求必须带游标，第一页不带。
    const requests = stub.requests.filter(
      (request) => request.method === "GET" && request.url.pathname === "/api/v1/users",
    );
    expect(requests[0].url.searchParams.get("cursor")).toBeNull();
    expect(requests.at(-1)?.url.searchParams.get("cursor")).toBe("1");
  });

  it("单页到底时不显示「加载更多」，只显示已到末页", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () => jsonResponse(userPage([userSummary({ username: "admin1" })])),
        },
      ]),
    );
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    expect(screen.queryByTestId("users-load-more")).toBeNull();
    expect(screen.getByTestId("users-end")).toBeInTheDocument();
  });

  it("状态列来自 is_active：已停用的行不再提供「停用账号」；空学号显示「未绑定」", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () =>
            jsonResponse(
              userPage([
                userSummary({ user_id: UUID_A, username: "alice", student_number: null }),
                userSummary({
                  user_id: UUID_B,
                  username: "bob",
                  display_name: "鲍勃",
                  is_active: false,
                  roles: ["teacher"],
                }),
              ]),
            ),
        },
      ]),
    );
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    expect(screen.getByText("未绑定")).toBeInTheDocument();
    expect(screen.getByTestId(`user-status-${UUID_B}`)).toHaveTextContent("已停用");
    expect(screen.queryByTestId(`deactivate-${UUID_B}`)).toBeNull();
    // 已停用的行给"启用账号"（反向软操作），启用的行不给。
    expect(screen.getByTestId(`activate-${UUID_B}`)).toHaveTextContent("启用账号");
    expect(screen.queryByTestId(`activate-${UUID_A}`)).toBeNull();
    // 停用的行仍然可以被读出与查阅（软停用，不是删除）。
    expect(screen.getByTestId(`detail-${UUID_B}`)).toBeInTheDocument();
  });

  it("接口没返回 is_active / updated_at 时按「未知」处理：不冒充已停用，也不许改角色", async () => {
    // 契约要求这两个字段存在；缺失说明后端与契约版本不一致（例如前后端分批部署）。
    // 此时页面不得把"未知"渲染成"已停用"，更不得发出没有并发保护的 PATCH。
    const broken = {
      user_id: UUID_A,
      username: "alice",
      display_name: "爱丽丝",
      student_number: null,
      roles: ["student"],
      created_at: "2026-09-15T07:00:00.000Z",
    } as unknown as ReturnType<typeof userSummary>;
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () => jsonResponse(userPage([broken])),
        },
      ]),
    );
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    expect(screen.getByTestId(`user-status-${UUID_A}`)).toHaveTextContent("状态未知");
    expect(screen.getByTestId(`user-status-${UUID_A}`)).not.toHaveTextContent("已停用");
    // 未知不是"可以停用"，也不是"已经停用"：两个动作都不提供。
    expect(screen.queryByTestId(`deactivate-${UUID_A}`)).toBeNull();
    expect(screen.queryByTestId(`activate-${UUID_A}`)).toBeNull();
    expect(screen.queryByTestId(`change-role-${UUID_A}`)).toBeNull();
    expect(screen.getByTestId(`role-blocked-${UUID_A}`)).toHaveTextContent("缺少并发字段");
    expect(screen.getByTestId(`state-unknown-${UUID_A}`)).toHaveTextContent("状态未知");
    // 只读的详情仍然可用。
    expect(screen.getByTestId(`detail-${UUID_A}`)).toBeInTheDocument();
  });

  it("空集合显示「还没有用户」与创建入口；筛选无结果显示另一句", async () => {
    let items: ReturnType<typeof userSummary>[] = [];
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: ({ url }) =>
            jsonResponse(userPage(url.searchParams.get("role") === null ? items : [])),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    const created = vi.fn();
    renderWithProviders(<UsersPage onCreate={created} />);

    expect(await screen.findByTestId("users-empty")).toHaveTextContent("还没有用户。");
    await user.click(within(screen.getByTestId("users-empty")).getByRole("button"));
    expect(created).toHaveBeenCalledTimes(1);

    items = [userSummary({ roles: ["student"] })];
    await user.selectOptions(screen.getByTestId("user-role-filter"), "assistant");
    expect(await screen.findByTestId("users-no-results")).toHaveTextContent(
      "没有符合条件的用户。",
    );
  });

  it("列表 403 显示权限拒绝，不是空表格", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
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
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    expect(await screen.findByTestId("users-forbidden")).toBeInTheDocument();
    expect(screen.queryByTestId("users-empty")).toBeNull();
    expect(screen.queryByTestId("users-table")).toBeNull();
  });
});

describe("用户标签：角色修改（并发保护）", () => {
  it("确认框显示旧/新角色，确认后才 PATCH，并带上该行的 updated_at", async () => {
    let roles: Role[] = ["student"];
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () => jsonResponse(userPage([userSummary({ user_id: USER_ID, username: "alice", roles })])),
        },
        {
          method: "PATCH",
          path: `/api/v1/users/${USER_ID}`,
          respond: ({ request }) => {
            roles = (request.body as { roles: Role[] }).roles;
            return jsonResponse({ user_id: USER_ID });
          },
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    await user.click(screen.getByTestId(`change-role-${USER_ID}`));

    const modal = await screen.findByTestId("role-change-modal");
    expect(within(modal).getByTestId("role-change-target")).toHaveTextContent("alice");
    expect(within(modal).getByTestId("role-change-pair")).toHaveTextContent("旧角色 学生 → 新角色 学生");
    // 打开确认框不发写请求。
    expect(stub.requests.filter((request) => request.method === "PATCH")).toHaveLength(0);

    await user.selectOptions(within(modal).getByTestId("role-change-select"), "teacher");
    expect(within(modal).getByTestId("role-change-pair")).toHaveTextContent(
      "旧角色 学生 → 新角色 教师",
    );
    await user.click(within(modal).getByTestId("role-change-confirm"));

    expect(await screen.findByText(/角色已更新/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("role-change-modal")).toBeNull());

    const patches = stub.requests.filter((request) => request.method === "PATCH");
    expect(patches).toHaveLength(1);
    expect(patches[0].body).toEqual({
      roles: ["teacher"],
      expected_updated_at: "2026-09-15T07:00:00.000Z",
    });
    // 成功后刷新读回真实角色。
    await waitFor(() => expect(screen.getByTestId("users-table")).toHaveTextContent("教师"));
  });

  it("取消不发 PATCH", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () => jsonResponse(userPage([userSummary({ user_id: USER_ID, username: "alice" })])),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    await user.click(screen.getByTestId(`change-role-${USER_ID}`));
    await user.selectOptions(screen.getByTestId("role-change-select"), "admin");
    await user.click(screen.getByTestId("role-change-cancel"));

    expect(screen.queryByTestId("role-change-modal")).toBeNull();
    expect(stub.requests.filter((request) => request.method === "PATCH")).toHaveLength(0);
    expect(screen.getByTestId("users-table")).toHaveTextContent("学生");
  });

  it("409 不静默覆盖：保留操作意图，重新加载后以服务端最新值再确认", async () => {
    let roles: Role[] = ["student"];
    let conflict = true;
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () =>
            jsonResponse(
              userPage([
                userSummary({
                  user_id: USER_ID,
                  username: "alice",
                  roles,
                  // 另一位管理员改过之后，这一行的 `updated_at` 也随之前进。
                  updated_at: roles.includes("teacher")
                    ? "2026-09-15T08:30:00.000Z"
                    : "2026-09-15T07:00:00.000Z",
                }),
              ]),
            ),
        },
        {
          method: "PATCH",
          path: `/api/v1/users/${USER_ID}`,
          respond: () => {
            if (conflict) {
              // 另一位管理员先改了：服务端返回 409，并且真实值已经变成教师。
              conflict = false;
              roles = ["teacher"];
              return problemResponse({
                status: 409,
                title: "Conflict",
                detail: "The user was modified since it was read; re-read and retry.",
              });
            }
            return jsonResponse({ user_id: USER_ID });
          },
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    await user.click(screen.getByTestId(`change-role-${USER_ID}`));
    await user.selectOptions(screen.getByTestId("role-change-select"), "admin");
    await user.click(screen.getByTestId("role-change-confirm"));

    expect(await screen.findByTestId("role-change-conflict")).toBeInTheDocument();
    expect(screen.queryByText(/角色已更新/)).toBeNull();

    await user.click(screen.getByTestId("role-change-reload"));
    // 重新读取后，弹窗里的"旧角色"就是服务端的最新值，而不是用户记忆里的旧值。
    await waitFor(() =>
      expect(screen.getByTestId("role-change-pair")).toHaveTextContent("旧角色 教师"),
    );

    await user.click(screen.getByTestId("role-change-confirm"));
    await waitFor(() => expect(screen.queryByTestId("role-change-modal")).toBeNull());
    const patches = stub.requests.filter((request) => request.method === "PATCH");
    expect(patches).toHaveLength(2);
    // 第二次提交带的是刷新后的更新时间，而不是第一次的旧值。
    expect((patches[1].body as { expected_updated_at: string }).expected_updated_at).not.toBe(
      (patches[0].body as { expected_updated_at: string }).expected_updated_at,
    );
  });
});

describe("用户标签：停用账号", () => {
  it("确认框写「停用账号」并显示用户名；确认后只发一次 DELETE，成功刷新为已停用", async () => {
    let active = true;
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () =>
            jsonResponse(
              userPage([userSummary({ user_id: USER_ID, username: "alice", is_active: active })]),
            ),
        },
        {
          method: "DELETE",
          path: `/api/v1/users/${USER_ID}`,
          respond: () => {
            active = false;
            return new Response(null, { status: 204 });
          },
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    await user.click(screen.getByTestId(`deactivate-${USER_ID}`));

    const modal = await screen.findByTestId("deactivate-modal");
    expect(within(modal).getByText("停用账号", { selector: "h3" })).toBeInTheDocument();
    expect(within(modal).getByTestId("deactivate-target")).toHaveTextContent("alice");
    expect(stub.requests.filter((request) => request.method === "DELETE")).toHaveLength(0);

    await user.click(within(modal).getByTestId("deactivate-confirm"));

    expect(await screen.findByText(/账号已停用/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId(`user-status-${USER_ID}`)).toHaveTextContent("已停用"));

    const deletes = stub.requests.filter((request) => request.method === "DELETE");
    expect(deletes).toHaveLength(1);
    expect(deletes[0].url.pathname).toBe(`/api/v1/users/${USER_ID}`);
    expect(deletes[0].body).toBeNull();
  });

  it("取消停用没有任何写请求", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () => jsonResponse(userPage([userSummary({ user_id: USER_ID, username: "alice" })])),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    await user.click(screen.getByTestId(`deactivate-${USER_ID}`));
    await user.click(screen.getByTestId("deactivate-cancel"));

    expect(screen.queryByTestId("deactivate-modal")).toBeNull();
    expect(stub.requests.filter((request) => request.method === "DELETE")).toHaveLength(0);
  });

  it("409（例如停用自己）显示服务端拒绝，不显示成功", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () => jsonResponse(userPage([userSummary({ user_id: USER_ID, username: "root" })])),
        },
        {
          method: "DELETE",
          path: `/api/v1/users/${USER_ID}`,
          respond: () =>
            problemResponse({
              status: 409,
              title: "Conflict",
              detail: "An administrator cannot deactivate their own account.",
            }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    await user.click(screen.getByTestId(`deactivate-${USER_ID}`));
    await user.click(screen.getByTestId("deactivate-confirm"));

    const conflict = await screen.findByTestId("deactivate-conflict");
    expect(conflict).toHaveTextContent("管理员不能停用自己的账号");
    expect(screen.queryByText(/账号已停用/)).toBeNull();
    // 行仍然在那里，并且状态没有被页面提前改成"已停用"。
    expect(screen.getByTestId(`user-status-${USER_ID}`)).toHaveTextContent("启用");
  });
});

describe("用户标签：启用账号", () => {
  it("确认弹窗写明对象与「软启用」；确认后只发一次 POST，状态刷新为启用", async () => {
    let active = false;
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () =>
            jsonResponse(
              userPage([
                userSummary({
                  user_id: USER_ID,
                  username: "alice",
                  display_name: "爱丽丝",
                  is_active: active,
                }),
              ]),
            ),
        },
        {
          method: "POST",
          path: `/api/v1/users/${USER_ID}/activate`,
          respond: () => {
            active = true;
            return new Response(null, { status: 204 });
          },
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    await user.click(screen.getByTestId(`activate-${USER_ID}`));

    const modal = await screen.findByTestId("activate-modal");
    expect(within(modal).getByText("启用账号", { selector: "h3" })).toBeInTheDocument();
    expect(within(modal).getByTestId("activate-target")).toHaveTextContent(
      "确定启用账号 “alice”（爱丽丝）？启用后该账号恢复登录权限。",
    );
    expect(within(modal).getByText("这是软启用，不会改动账号历史成绩与记录。")).toBeInTheDocument();
    // 打开确认框不是启用。
    expect(activateRequests()).toHaveLength(0);

    await user.click(within(modal).getByTestId("activate-confirm"));

    expect(await screen.findByText(/账号已启用/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId(`user-status-${USER_ID}`)).toHaveTextContent("启用"),
    );
    expect(screen.queryByTestId(`activate-${USER_ID}`)).toBeNull();
    expect(screen.getByTestId(`deactivate-${USER_ID}`)).toBeInTheDocument();

    const requests = activateRequests();
    expect(requests).toHaveLength(1);
    expect(requests[0].url.pathname).toBe(`/api/v1/users/${USER_ID}/activate`);
    expect(requests[0].body).toBeNull();
  });

  it("取消启用没有任何写请求", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () =>
            jsonResponse(
              userPage([
                userSummary({ user_id: USER_ID, username: "alice", is_active: false }),
              ]),
            ),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    await user.click(screen.getByTestId(`activate-${USER_ID}`));
    await user.click(screen.getByTestId("activate-cancel"));

    expect(screen.queryByTestId("activate-modal")).toBeNull();
    expect(activateRequests()).toHaveLength(0);
  });

  it("409（例如启用自己）显示服务端拒绝，不显示成功", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () =>
            jsonResponse(
              userPage([
                userSummary({ user_id: USER_ID, username: "root", is_active: false }),
              ]),
            ),
        },
        {
          method: "POST",
          path: `/api/v1/users/${USER_ID}/activate`,
          respond: () =>
            problemResponse({
              status: 409,
              title: "Conflict",
              detail: "An administrator cannot activate their own account.",
            }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    await user.click(screen.getByTestId(`activate-${USER_ID}`));
    await user.click(screen.getByTestId("activate-confirm"));

    const conflict = await screen.findByTestId("activate-conflict");
    expect(conflict).toHaveTextContent("管理员不能启用或停用自己的账号");
    expect(screen.queryByText(/账号已启用/)).toBeNull();
    // 行仍然在那里，并且状态没有被页面提前改成"启用"。
    expect(screen.getByTestId(`user-status-${USER_ID}`)).toHaveTextContent("已停用");
  });
});

function activateRequests() {
  return (stub?.requests ?? []).filter(
    (request) => request.method === "POST" && request.url.pathname.endsWith("/activate"),
  );
}

describe("用户标签：详情与复制 ID", () => {
  it("详情显示短 ID，复制按钮把完整 ID 写入剪贴板并给出反馈", async () => {
    stub = installFetchStub(
      withCommonRoutes([
        {
          method: "GET",
          path: "/api/v1/users",
          respond: () => jsonResponse(userPage([userSummary({ user_id: USER_ID, username: "alice" })])),
        },
      ]),
    );
    const user = userEvent.setup();
    // `userEvent.setup()` 自己会替换 `navigator.clipboard`，所以桩要在它之后装。
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(globalThis.navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage onCreate={noop} />);

    await screen.findByTestId("users-table");
    // 列表里不出现完整 UUID（否则会把表格撑宽）。
    expect(screen.queryByText(USER_ID)).toBeNull();

    await user.click(screen.getByTestId(`detail-${USER_ID}`));
    const modal = await screen.findByTestId("user-detail-modal");
    expect(modal).toHaveTextContent("00000000…");

    await user.click(within(modal).getByTestId("copy-user-id"));
    expect(await screen.findByTestId("user-id-copied")).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(USER_ID);
  });
});
