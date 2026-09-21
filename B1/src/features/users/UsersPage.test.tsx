import { afterEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  type FetchStub,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  UUID_B,
  coursePage,
  courseSummary,
  sessionCreated,
  userSummary,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { UsersPage } from "./UsersPage";

/**
 * 用户列表的游标分页。与成绩/名单/课程同一缺陷类：只取第一页会让第 200 个之后的账号
 * 在管理页上**静默消失**，而管理员会据此判断"这个账号不存在"。
 */

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function routes(pages: { items: unknown[]; nextCursor: string | null }[]): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: ["admin"],
            scope: [{ course_id: UUID_A, section_id: UUID_B }],
          }),
        ),
    },
    {
      path: "/api/v1/users",
      respond: ({ url }) => {
        const cursor = url.searchParams.get("cursor");
        const index = Math.min(cursor === null ? 0 : Number(cursor), pages.length - 1);
        return jsonResponse({ items: pages[index].items, next_cursor: pages[index].nextCursor });
      },
    },
    {
      path: "/api/v1/courses",
      respond: () => jsonResponse(coursePage([courseSummary()])),
    },
  ];
}

describe("用户与角色（RQ-B-1-20 / RQ-B-4-50）", () => {
  it("账号超过一页时给出「加载更多」，加载后到末页给出明确终点", async () => {
    stub = installFetchStub(
      routes([
        { items: [userSummary({ username: "admin1", display_name: "管理员" })], nextCursor: "1" },
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
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage />);

    const table = await screen.findByTestId("users-table");
    expect(within(table).getAllByRole("row")).toHaveLength(2); // 表头 + 1 行
    expect(screen.getByText(/已加载 1 行/)).toBeInTheDocument();

    await user.click(screen.getByTestId("users-load-more"));

    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
    expect(table).toHaveTextContent("second");
    expect(screen.getByText(/已加载 2 行/)).toBeInTheDocument();
    expect(screen.queryByTestId("users-load-more")).toBeNull();
    expect(screen.getByTestId("users-end")).toBeInTheDocument();

    // 第二页请求必须带游标，第一页不带。
    const requests = stub.requests.filter((request) => request.url.pathname === "/api/v1/users");
    expect(requests[0].url.searchParams.get("cursor")).toBeNull();
    expect(requests.at(-1)?.url.searchParams.get("cursor")).toBe("1");
  });

  it("单页到底时不显示「加载更多」，只显示已到末页", async () => {
    stub = installFetchStub(
      routes([{ items: [userSummary({ username: "admin1" })], nextCursor: null }]),
    );
    await signIn("admin1", "pw");
    renderWithProviders(<UsersPage />);

    await screen.findByTestId("users-table");
    expect(screen.queryByTestId("users-load-more")).toBeNull();
    expect(screen.getByTestId("users-end")).toBeInTheDocument();
  });
});
