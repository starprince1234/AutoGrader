import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, renderWithRouter } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  classPublic,
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
 * 课程与名单（v0.32.0）：课程 → 课序 → 班级 → 学生。
 *
 * 断言重点是新的领域层级与操作落点：
 * - 课程行显示课序数；点课程展开课序，课序行显示班级数/人数；
 * - 展开课序显示班级，展开班级显示学生；
 * - 新建课序 / 在指定课序下新建班级 / 把已注册班级导入指定课序 / 重命名与删除班级；
 * - 409 冲突把服务端 detail（含冲突计数）原样显示。
 */

const SECTION_ID = "00000000-0000-4000-8000-0000000000b1";
const SECTION_2_ID = "00000000-0000-4000-8000-0000000000b2";
const CLASS_ID = "00000000-0000-4000-8000-0000000000c1";
const CLASS_2_ID = "00000000-0000-4000-8000-0000000000c2";
const ENROLLMENT_ID = "00000000-0000-4000-8000-0000000000f1";
const BATCH_ID = "00000000-0000-4000-8000-0000000000a1";

const COURSE = courseSummary({
  course_id: UUID_A,
  title: "软件工程",
  course_code: "91",
  section_count: 2,
});

const SECTIONS = {
  items: [
    {
      section_id: SECTION_ID,
      course_id: UUID_A,
      section_code: "01",
      term: "2026秋",
      class_count: 2,
      enrollment_count: 3,
    },
    {
      section_id: SECTION_2_ID,
      course_id: UUID_A,
      section_code: "02",
      term: null,
      class_count: 0,
      enrollment_count: 0,
    },
  ],
};

const CLASS = classPublic({
  class_id: CLASS_ID,
  section_id: SECTION_ID,
  class_name: "软件工程1班",
  source: "roster_import",
  enrollment_count: 2,
  bound_count: 1,
});
const CLASS_2 = classPublic({
  class_id: CLASS_2_ID,
  section_id: SECTION_ID,
  class_name: "软件工程2班",
  source: "registered_class",
  allowlist_batch_id: BATCH_ID,
  enrollment_count: 1,
  bound_count: 1,
});

const BATCH = {
  batch_id: BATCH_ID,
  class_name: "已注册1班",
  filename: "已注册1班.csv",
  row_count: 3,
  uploaded_at: "2026-11-01T00:00:00.000Z",
};

function routes(role: "teacher" | "student" = "teacher"): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () => jsonResponse(sessionCreated({ roles: [role], access_token: "t" })),
    },
    { method: "GET", path: "/api/v1/courses", respond: () => jsonResponse(coursePage([COURSE])) },
    {
      method: "GET",
      path: `/api/v1/courses/${UUID_A}/sections`,
      respond: () => jsonResponse(SECTIONS),
    },
    {
      method: "GET",
      path: `/api/v1/sections/${SECTION_ID}/classes`,
      respond: () => jsonResponse({ items: [CLASS, CLASS_2] }),
    },
    {
      method: "GET",
      path: `/api/v1/sections/${SECTION_2_ID}/classes`,
      respond: () => jsonResponse({ items: [] }),
    },
    {
      method: "GET",
      path: `/api/v1/classes/${CLASS_ID}/enrollments`,
      respond: () =>
        jsonResponse(
          enrollmentPage([
            enrollmentPublic({
              enrollment_id: ENROLLMENT_ID,
              course_id: UUID_A,
              section_id: SECTION_ID,
              class_id: CLASS_ID,
              class_name: CLASS.class_name,
              student_number: "2024141460192",
              display_name: "黄俊杰",
            }),
            enrollmentPublic({
              enrollment_id: "00000000-0000-4000-8000-0000000000f2",
              course_id: UUID_A,
              section_id: SECTION_ID,
              class_id: CLASS_ID,
              class_name: CLASS.class_name,
              student_id: null,
              student_number: "2024141460193",
              display_name: "未绑定学生",
            }),
          ]),
        ),
    },
    {
      method: "GET",
      path: "/api/v1/registration-allowlists/batches",
      respond: () =>
        jsonResponse({
          items: [BATCH],
        }),
    },
    {
      method: "POST",
      path: `/api/v1/courses/${UUID_A}/sections`,
      respond: () =>
        jsonResponse(
          {
            section_id: "00000000-0000-4000-8000-0000000000b9",
            course_id: UUID_A,
            section_code: "03",
            term: "2027春",
            class_count: 0,
            enrollment_count: 0,
          },
          201,
        ),
    },
    {
      method: "POST",
      path: `/api/v1/sections/${SECTION_ID}/classes`,
      respond: () =>
        jsonResponse(
          classPublic({
            class_id: "00000000-0000-4000-8000-0000000000c9",
            section_id: SECTION_ID,
            class_name: "手输班级",
          }),
          201,
        ),
    },
    {
      method: "PATCH",
      path: `/api/v1/classes/${CLASS_ID}`,
      respond: () => jsonResponse({ ...CLASS, class_name: "软件工程1班（改）" }),
    },
    {
      method: "DELETE",
      path: `/api/v1/classes/${CLASS_ID}`,
      respond: () => new Response(null, { status: 204 }),
    },
    {
      method: "DELETE",
      path: `/api/v1/courses/${UUID_A}/sections/${SECTION_ID}`,
      respond: () => new Response(null, { status: 204 }),
    },
    {
      method: "DELETE",
      path: `/api/v1/enrollments/${ENROLLMENT_ID}`,
      respond: () => new Response(null, { status: 204 }),
    },
  ];
}

async function openPage() {
  const stub = installFetchStub(routes());
  const user = userEvent.setup();
  await signIn("teacher1", "pw");
  renderWithRouter(<RosterPage />, { path: "/roster", linkTargets: [] });
  return { stub, user };
}

describe("课程表", () => {
  it("课程行显示名称、序号与课序数；点击课程展开课序列表", async () => {
    const { stub, user } = await openPage();
    const table = await screen.findByTestId("courses-table");
    expect(within(table).getByText("软件工程")).toBeInTheDocument();
    expect(within(table).getByText("91")).toBeInTheDocument();
    expect(screen.getByTestId(`course-sections-${UUID_A}`)).toHaveTextContent("2");

    await user.click(screen.getByTestId(`course-open-${UUID_A}`));
    const list = await screen.findByTestId("section-list");
    const row = await screen.findByTestId(`section-row-${SECTION_ID}`);
    expect(list).toBeInTheDocument();
    expect(within(row).getByText(/01 · 2026秋/)).toBeInTheDocument();
    expect(screen.getByTestId(`section-class-count-${SECTION_ID}`)).toHaveTextContent("2 个班级");
    expect(screen.getByTestId(`section-enrollment-count-${SECTION_ID}`)).toHaveTextContent("3 人");
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

describe("课序 → 班级 → 学生", () => {
  it("展开课序显示班级（来源/人数/已绑定）；展开班级显示学生并标注未绑定", async () => {
    const { stub, user } = await openPage();
    await user.click(await screen.findByTestId(`course-open-${UUID_A}`));
    await user.click(await screen.findByTestId(`section-expand-${SECTION_ID}`));

    const classes = await screen.findByTestId(`section-classes-${SECTION_ID}`);
    expect(within(classes).getByText("软件工程1班")).toBeInTheDocument();
    expect(screen.getByTestId(`class-count-${CLASS_ID}`)).toHaveTextContent("2 人");
    expect(screen.getByTestId(`class-bound-${CLASS_ID}`)).toHaveTextContent("已绑定 1/2");
    expect(screen.getByTestId(`class-source-${CLASS_2_ID}`)).toHaveTextContent("来自注册名单");

    await user.click(screen.getByTestId(`class-expand-${CLASS_ID}`));
    const students = await screen.findByTestId(`class-students-${CLASS_ID}`);
    expect(within(students).getByText("黄俊杰")).toBeInTheDocument();
    expect(within(students).getByText("2024141460192")).toBeInTheDocument();
    expect(screen.getByTestId("class-student-unbound-00000000-0000-4000-8000-0000000000f2")).toHaveTextContent(
      "未绑定账号",
    );

    await user.click(screen.getByTestId(`class-student-delete-${ENROLLMENT_ID}`));
    await waitFor(() => {
      expect(
        stub.requests.some(
          (request) =>
            request.method === "DELETE" &&
            request.url.pathname === `/api/v1/enrollments/${ENROLLMENT_ID}`,
        ),
      ).toBe(true);
    });
    stub.restore();
  });
});

describe("课序/班级操作", () => {
  it("新建课序提交课序号与学期", async () => {
    const stub = installFetchStub(routes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderWithRouter(<RosterPage />, { path: "/roster", linkTargets: [] });
    await user.click(await screen.findByTestId(`course-open-${UUID_A}`));
    await user.click(await screen.findByTestId("section-create-entry"));

    const modal = await screen.findByTestId("create-section");
    await user.type(within(modal).getByTestId("create-section-code"), "03");
    await user.type(within(modal).getByTestId("create-section-term"), "2027春");
    await user.click(within(modal).getByTestId("create-section-save"));

    await waitFor(() => {
      const request = stub.requests.find(
        (candidate) =>
          candidate.method === "POST" &&
          candidate.url.pathname === `/api/v1/courses/${UUID_A}/sections`,
      );
      expect(request?.body).toEqual({ section_code: "03", term: "2027春" });
    });
    stub.restore();
  });

  it("在指定课序下新建班级：POST /sections/{id}/classes 带手输班名", async () => {
    const { stub, user } = await openPage();
    await user.click(await screen.findByTestId(`course-open-${UUID_A}`));
    await user.click(await screen.findByTestId(`section-create-class-${SECTION_ID}`));

    const modal = await screen.findByTestId("create-class");
    await user.type(within(modal).getByTestId("create-class-name"), "手输班级");
    await user.click(within(modal).getByTestId("create-class-save"));

    await waitFor(() => {
      const request = stub.requests.find(
        (candidate) =>
          candidate.method === "POST" &&
          candidate.url.pathname === `/api/v1/sections/${SECTION_ID}/classes`,
      );
      expect(request?.body).toEqual({ class_name: "手输班级" });
      // 旧端点（一个班=一个课序）必须已经被删除。
      expect(
        stub.requests.some(
          (candidate) => candidate.url.pathname === `/api/v1/courses/${UUID_A}/classes`,
        ),
      ).toBe(false);
    });
    stub.restore();
  });

  it("把已注册名单的班级导入指定课序：请求体带 class_name 与 batch_id", async () => {
    const { stub, user } = await openPage();
    await user.click(await screen.findByTestId(`course-open-${UUID_A}`));
    await user.click(await screen.findByTestId(`section-attach-${SECTION_ID}`));

    const modal = await screen.findByTestId("attach-registered-class");
    await user.selectOptions(within(modal).getByTestId("attach-batch"), BATCH_ID);
    // 班名默认取注册班级名，仍可修改。
    expect((within(modal).getByTestId("attach-class-name") as HTMLInputElement).value).toBe(
      "已注册1班",
    );
    await user.click(within(modal).getByTestId("attach-class-save"));

    await waitFor(() => {
      const request = stub.requests.find(
        (candidate) =>
          candidate.method === "POST" &&
          candidate.url.pathname === `/api/v1/sections/${SECTION_ID}/classes`,
      );
      expect(request?.body).toEqual({ class_name: "已注册1班", batch_id: BATCH_ID });
    });
    stub.restore();
  });

  it("导入冲突 409 时显示服务端 detail（含冲突计数），不显示成通用失败", async () => {
    const stub = installFetchStub(
      routes().map((route) =>
        route.method === "POST" && route.path === `/api/v1/sections/${SECTION_ID}/classes`
          ? {
              method: "POST",
              path: route.path,
              respond: () =>
                problemResponse({
                  status: 409,
                  title: "Conflict",
                  detail: "3 名学生已在本课程的其他课序/班级有效在册：02/软件工程2班",
                  instance: `/api/v1/sections/${SECTION_ID}/classes`,
                }),
            }
          : route,
      ),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderWithRouter(<RosterPage />, { path: "/roster", linkTargets: [] });
    await user.click(await screen.findByTestId(`course-open-${UUID_A}`));
    await user.click(await screen.findByTestId(`section-attach-${SECTION_ID}`));
    const modal = await screen.findByTestId("attach-registered-class");
    await user.selectOptions(within(modal).getByTestId("attach-batch"), BATCH_ID);
    await user.click(within(modal).getByTestId("attach-class-save"));

    const error = await screen.findByTestId("attach-class-error");
    expect(error).toHaveTextContent("409");
    expect(error).toHaveTextContent("3 名学生");
    stub.restore();
  });

  it("重命名班级：PATCH /classes/{id} 带新班名", async () => {
    const { stub, user } = await openPage();
    await user.click(await screen.findByTestId(`course-open-${UUID_A}`));
    await user.click(await screen.findByTestId(`section-expand-${SECTION_ID}`));
    await user.click(await screen.findByTestId(`class-rename-${CLASS_ID}`));

    const modal = await screen.findByTestId("rename-class");
    const name = within(modal).getByTestId("rename-class-name") as HTMLInputElement;
    expect(name.value).toBe("软件工程1班");
    await user.clear(name);
    await user.type(name, "软件工程1班（改）");
    await user.click(within(modal).getByTestId("rename-class-save"));

    await waitFor(() => {
      const request = stub.requests.find(
        (candidate) =>
          candidate.method === "PATCH" &&
          candidate.url.pathname === `/api/v1/classes/${CLASS_ID}`,
      );
      expect(request?.body).toEqual({ class_name: "软件工程1班（改）" });
    });
    stub.restore();
  });

  it("删除班级先弹确认框，确认后才发 DELETE", async () => {
    const { stub, user } = await openPage();
    await user.click(await screen.findByTestId(`course-open-${UUID_A}`));
    await user.click(await screen.findByTestId(`section-expand-${SECTION_ID}`));
    await user.click(await screen.findByTestId(`class-delete-${CLASS_ID}`));

    const dialog = await screen.findByTestId("confirm-delete");
    expect(within(dialog).getByTestId("confirm-delete-message")).toHaveTextContent(
      "软件工程1班",
    );
    expect(
      stub.requests.some(
        (request) =>
          request.method === "DELETE" &&
          request.url.pathname === `/api/v1/classes/${CLASS_ID}`,
      ),
    ).toBe(false);

    await user.click(within(dialog).getByTestId("confirm-delete-accept"));
    await waitFor(() => {
      expect(
        stub.requests.some(
          (request) =>
            request.method === "DELETE" &&
            request.url.pathname === `/api/v1/classes/${CLASS_ID}`,
        ),
      ).toBe(true);
    });
    stub.restore();
  });

  it("删除课序后清理 section_id 查询参数，且确认前不发请求", async () => {
    const { stub, user } = await openPage();
    await user.click(await screen.findByTestId(`course-open-${UUID_A}`));
    await user.click(await screen.findByTestId(`section-delete-${SECTION_ID}`));

    const dialog = await screen.findByTestId("confirm-delete");
    expect(
      stub.requests.some(
        (request) =>
          request.method === "DELETE" &&
          request.url.pathname === `/api/v1/courses/${UUID_A}/sections/${SECTION_ID}`,
      ),
    ).toBe(false);
    await user.click(within(dialog).getByTestId("confirm-delete-accept"));
    await waitFor(() => {
      expect(
        stub.requests.some(
          (request) =>
            request.method === "DELETE" &&
            request.url.pathname === `/api/v1/courses/${UUID_A}/sections/${SECTION_ID}`,
        ),
      ).toBe(true);
    });
    stub.restore();
  });
});

describe("名单导入标签", () => {
  it("切到导入标签后先要求选择 课程 → 课序 → 班级", async () => {
    const stub = installFetchStub(routes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderWithRouter(<RosterPage />, { path: "/roster", linkTargets: [] });

    await user.click(await screen.findByTestId("roster-tab-import"));
    const scope = await screen.findByTestId("import-scope");
    expect(within(scope).getByLabelText("课程")).toBeInTheDocument();
    expect(within(scope).getByLabelText("课序")).toBeInTheDocument();
    expect(within(scope).getByLabelText("班级")).toBeInTheDocument();
    expect(screen.getByTestId("import-scope-incomplete")).toHaveTextContent(
      "选择课程、课序和班级后才能上传名单",
    );
    stub.restore();
  });
});
