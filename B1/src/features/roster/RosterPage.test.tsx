import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  UUID_B,
  coursePage,
  courseSummary,
  enrollmentPage,
  enrollmentPublic,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { RequirePermission } from "../../auth/guards";
import { canViewRoster } from "../../auth/visibility";
import { RosterPage } from "./RosterPage";

const ENROLLMENT_ID = "00000000-0000-4000-8000-0000000000f1";
const STUDENT_ID = "00000000-0000-4000-8000-0000000000f2";

const ROW = enrollmentPublic({
  enrollment_id: ENROLLMENT_ID,
  student_id: STUDENT_ID,
  student_number: "20260001",
  display_name: "张三",
});

function rosterRoutes(options: {
  role: "teacher" | "student";
  rows?: Record<string, unknown>[];
  pages?: { items: unknown[]; nextCursor: string | null }[];
  patch?: () => Response;
}): StubbedRoute[] {
  const pages =
    options.pages ??
    [enrollmentPage((options.rows ?? [ROW]) as never[], null)].map((page) => ({
      items: page.items,
      nextCursor: page.next_cursor,
    }));
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: [options.role],
            scope: [{ course_id: UUID_A, section_id: UUID_B }],
          }),
        ),
    },
    {
      path: "/api/v1/courses",
      respond: () => jsonResponse(coursePage([courseSummary()])),
    },
    {
      path: `/api/v1/courses/${UUID_A}/sections`,
      respond: () => jsonResponse({ items: [{ section_id: UUID_B, section_code: "01" }] }),
    },
    {
      path: `/api/v1/sections/${UUID_B}/enrollments`,
      respond: ({ url }) => {
        // 第一页不带 cursor；后续页用页序当作游标。
        const cursor = url.searchParams.get("cursor");
        const index = Math.min(cursor === null ? 0 : Number(cursor), pages.length - 1);
        return jsonResponse({ items: pages[index].items, next_cursor: pages[index].nextCursor });
      },
    },
    {
      method: "PATCH",
      path: `/api/v1/enrollments/${ENROLLMENT_ID}`,
      respond:
        options.patch ??
        (() => jsonResponse({}, 200)),
    },
  ];
}

async function openRoster(role: "teacher" | "student" = "teacher") {
  const user = userEvent.setup();
  await signIn(`${role}1`, "pw");
  renderWithProviders(<RosterPage />);
  const courseSelect = await screen.findByLabelText("课程");
  await waitFor(() => expect(within(courseSelect).getAllByRole("option")).toHaveLength(2));
  await user.selectOptions(courseSelect, UUID_A);

  const sectionSelect = screen.getByLabelText("课序");
  await waitFor(() => expect(within(sectionSelect).getAllByRole("option")).toHaveLength(2));
  await user.selectOptions(sectionSelect, UUID_B);
  return user;
}

describe("名单读取与编辑（RQ-B-1-20）", () => {
  it("按契约声明的列渲染名单行；契约未声明的字段（例如邮箱）不出现", async () => {
    const stub = installFetchStub(
      rosterRoutes({
        role: "teacher",
        rows: [{ ...ROW, email: "zhangsan@example.edu" }],
      }),
    );
    await openRoster();

    const table = await screen.findByTestId("enrollments-table");
    expect(table).toHaveTextContent("20260001");
    expect(table).toHaveTextContent("张三");
    // 单页到底：面板上给出明确终点，而不是让读者猜"还有没有下一页"。
    expect(screen.getByTestId("enrollments-end")).toHaveTextContent("已到末页");
    // 契约没有声明 email：页面只渲染声明的列，多余键不会被带出来。
    expect(table).not.toHaveTextContent("zhangsan@example.edu");
    stub.restore();
  });

  it("名单超过一页时给出「加载更多」，加载后到末页给出明确终点（不静默截断）", async () => {
    const second = enrollmentPublic({
      enrollment_id: "00000000-0000-4000-8000-0000000000f3",
      student_id: "00000000-0000-4000-8000-0000000000f4",
      student_number: "20260002",
      display_name: "李四",
    });
    const stub = installFetchStub(
      rosterRoutes({
        role: "teacher",
        pages: [
          { items: [ROW], nextCursor: "1" },
          { items: [second], nextCursor: null },
        ],
      }),
    );
    const user = await openRoster();

    const table = await screen.findByTestId("enrollments-table");
    expect(within(table).getAllByRole("row")).toHaveLength(2); // 表头 + 1 行
    expect(screen.getByTestId("enrollments-load-more")).toBeInTheDocument();

    await user.click(screen.getByTestId("enrollments-load-more"));

    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
    expect(table).toHaveTextContent("20260002");
    expect(screen.getByText(/已加载 2 行/)).toBeInTheDocument();
    expect(screen.queryByTestId("enrollments-load-more")).toBeNull();
    expect(screen.getByTestId("enrollments-end")).toBeInTheDocument();
    stub.restore();
  });

  it("未绑定账号的名单行如实显示，不拿学号当用户 ID", async () => {
    const stub = installFetchStub(
      rosterRoutes({ role: "teacher", rows: [{ ...ROW, student_id: null }] }),
    );
    await openRoster();

    expect(await screen.findByTestId("student-unbound")).toHaveTextContent("未绑定账号");
    stub.restore();
  });

  it("并发冲突（409）给出明确提示，不静默覆盖", async () => {
    const stub = installFetchStub(
      rosterRoutes({
        role: "teacher",
        patch: () =>
          problemResponse({
            status: 409,
            title: "Conflict",
            detail: "expected_updated_at mismatch",
          }),
      }),
    );
    const user = await openRoster();

    await user.click(await screen.findByRole("button", { name: "编辑" }));
    await user.click(screen.getByTestId("save-enrollment"));

    expect(await screen.findByTestId("conflict-banner")).toHaveTextContent("并发冲突");
    const patch = stub.requests.find((request) => request.method === "PATCH");
    expect(patch?.body).toMatchObject({
      expected_updated_at: ROW.updated_at,
      display_name: "张三",
    });
    stub.restore();
  });

  it("服务端未返回 updated_at 时禁止保存（避免无并发保护的覆盖）", async () => {
    const stub = installFetchStub(
      rosterRoutes({ role: "teacher", rows: [{ ...ROW, updated_at: undefined }] }),
    );
    await openRoster();

    expect(await screen.findByTestId("missing-updated-at")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑" })).toBeDisabled();
    stub.restore();
  });

  it("学生角色的名单页面被可见性守卫拦截（服务端仍会独立校验）", async () => {
    const stub = installFetchStub(rosterRoutes({ role: "student" }));
    await signIn("student1", "pw");
    // 与路由表一致的组合方式：真实守卫 + 页面
    renderWithProviders(
      <RequirePermission allow={canViewRoster}>
        <RosterPage />
      </RequirePermission>,
    );

    expect(await screen.findByTestId("guard-forbidden")).toBeInTheDocument();
    // 页面被拦截时不发出名单读取请求
    expect(
      stub.requests.some((request) => request.url.pathname.includes("/enrollments")),
    ).toBe(false);
    stub.restore();
  });
});
