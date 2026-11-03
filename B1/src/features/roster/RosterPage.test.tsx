import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, renderWithRouter } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
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

/**
 * 课程与名单：一张课程表 + 新建/重命名弹窗；点课程名展开该课程的班级，班级行与
 * 「已注册名单」同一套 UI（班级名 / 人数 / 时间 + 展开后的学生网格）。
 *
 * 断言重点是"点击之后会发生什么"：课程名 → 班级行；班级行 → 学生；学生右侧垃圾桶 → DELETE。
 */

const SECTION_ID = "00000000-0000-4000-8000-0000000000b1";
const ENROLLMENT_ID = "00000000-0000-4000-8000-0000000000f1";

const COURSE = courseSummary({
  course_id: UUID_A,
  title: "软件工程",
  course_code: "91",
  section_count: 1,
});

function routes(role: "teacher" | "student" = "teacher"): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(sessionCreated({ roles: [role], access_token: "t" })),
    },
    { method: "GET", path: "/api/v1/courses", respond: () => jsonResponse(coursePage([COURSE])) },
    {
      method: "GET",
      path: `/api/v1/courses/${UUID_A}/sections`,
      respond: () =>
        jsonResponse({
          items: [
            {
              section_id: SECTION_ID,
              course_id: UUID_A,
              section_code: "01",
              term: null,
              created_at: "2026-11-02T07:32:50.000Z",
              enrollment_count: 1,
            },
          ],
        }),
    },
    {
      method: "GET",
      path: `/api/v1/sections/${SECTION_ID}/enrollments`,
      respond: () =>
        jsonResponse(
          enrollmentPage([
            enrollmentPublic({
              enrollment_id: ENROLLMENT_ID,
              student_number: "2024141460192",
              display_name: "黄俊杰",
            }),
          ]),
        ),
    },
    {
      method: "POST",
      path: "/api/v1/courses",
      respond: () =>
        jsonResponse(
          {
            course_id: UUID_B,
            course_code: "92",
            title: "新课程",
            updated_at: "2026-11-02T08:00:00.000Z",
            section_count: 0,
          },
          201,
        ),
    },
    {
      method: "PATCH",
      path: `/api/v1/courses/${UUID_A}`,
      respond: () =>
        jsonResponse({
          course_id: UUID_A,
          course_code: "91",
          title: "软件工程（改）",
          updated_at: "2026-11-02T08:10:00.000Z",
          section_count: 1,
        }),
    },
    {
      method: "DELETE",
      path: `/api/v1/enrollments/${ENROLLMENT_ID}`,
      respond: () => new Response(null, { status: 204 }),
    },
  ];
}

async function openPage(role: "teacher" | "student" = "teacher") {
  const stub = installFetchStub(routes(role));
  const user = userEvent.setup();
  await signIn(`${role}1`, "pw");
  renderWithRouter(<RosterPage />, { path: "/roster", linkTargets: [] });
  return { stub, user };
}

describe("课程表", () => {
  it("按契约列渲染课程：名称、序号、已注册班级数，且不再有下拉查询区", async () => {
    const { stub } = await openPage();
    const table = await screen.findByTestId("courses-table");
    expect(within(table).getByText("软件工程")).toBeInTheDocument();
    expect(within(table).getByText("91")).toBeInTheDocument();
    expect(screen.getByTestId(`course-classes-${UUID_A}`)).toHaveTextContent("1");
    expect(screen.queryByLabelText("课程")).toBeNull();
    stub.restore();
  });

  it("学生角色看不到课程表（可见性守卫拦截）", async () => {
    const stub = installFetchStub(routes("student"));
    await signIn("student1", "pw");
    renderWithProviders(
      <RequirePermission allow={canViewRoster}>
        <RosterPage />
      </RequirePermission>,
    );
    expect(await screen.findByTestId("guard-forbidden")).toBeInTheDocument();
    stub.restore();
  });
});

describe("课程 → 班级 → 学生", () => {
  it("点课程名后按行显示班级：人数与时间", async () => {
    const { stub, user } = await openPage();
    await user.click(await screen.findByTestId(`course-open-${UUID_A}`));

    const row = await screen.findByTestId(`class-row-${SECTION_ID}`);
    expect(within(row).getByText(/01/)).toBeInTheDocument();
    expect(screen.getByTestId(`class-count-${SECTION_ID}`)).toHaveTextContent("1 人");
    expect(within(row).getByText(/2026-11-02 07:32:50/)).toBeInTheDocument();
    stub.restore();
  });

  it("展开班级后逐个显示学生姓名与学号，右侧垃圾桶删除该名学生", async () => {
    const { stub, user } = await openPage();
    await user.click(await screen.findByTestId(`course-open-${UUID_A}`));
    await user.click(await screen.findByTestId(`class-expand-${SECTION_ID}`));

    const students = await screen.findByTestId(`class-students-${SECTION_ID}`);
    expect(within(students).getByText("黄俊杰")).toBeInTheDocument();
    expect(within(students).getByText("2024141460192")).toBeInTheDocument();

    await user.click(screen.getByTestId(`class-student-delete-${ENROLLMENT_ID}`));
    await waitFor(() => {
      const deleted = stub.requests.some(
        (request) =>
          request.method === "DELETE" &&
          request.url.pathname === `/api/v1/enrollments/${ENROLLMENT_ID}`,
      );
      expect(deleted).toBe(true);
    });
    stub.restore();
  });
});

describe("弹窗", () => {
  it("新建课程提交名称与序号；缺一个字段时不可提交", async () => {
    const { stub, user } = await openPage();
    await user.click(await screen.findByTestId("create-course-entry"));
    const modal = await screen.findByTestId("create-course");

    expect(within(modal).getByTestId("create-course-save")).toBeDisabled();
    await user.type(within(modal).getByTestId("create-course-title"), "新课程");
    await user.type(within(modal).getByTestId("create-course-code"), "92");
    await user.click(within(modal).getByTestId("create-course-save"));

    await waitFor(() => {
      const request = stub.requests.find(
        (candidate) => candidate.method === "POST" && candidate.url.pathname === "/api/v1/courses",
      );
      expect(request?.body).toEqual({ title: "新课程", course_code: "92" });
    });
    stub.restore();
  });

  it("重命名预填名称与序号，保存时发 PATCH 并带乐观锁", async () => {
    const { stub, user } = await openPage();
    await user.click(await screen.findByTestId(`course-rename-${UUID_A}`));
    const modal = await screen.findByTestId("rename-course");

    const title = within(modal).getByTestId("rename-course-title") as HTMLInputElement;
    expect(title.value).toBe("软件工程");
    const code = within(modal).getByTestId("rename-course-code") as HTMLInputElement;
    expect(code.value).toBe("91");

    await user.clear(title);
    await user.type(title, "软件工程（改）");
    await user.click(within(modal).getByTestId("rename-course-save"));

    await waitFor(() => {
      const request = stub.requests.find(
        (candidate) =>
          candidate.method === "PATCH" && candidate.url.pathname === `/api/v1/courses/${UUID_A}`,
      );
      expect(request?.body).toMatchObject({
        title: "软件工程（改）",
        course_code: "91",
      });
      expect((request?.body as { expected_updated_at?: string }).expected_updated_at).toBeTruthy();
    });
    stub.restore();
  });
});
