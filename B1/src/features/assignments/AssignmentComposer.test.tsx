import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithRouter } from "../../testing/renderWithProviders";
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
  UUID_C,
  assignmentPublic,
  coursePage,
  courseSummary,
  enrollmentPage,
  enrollmentPublic,
  questionPublic,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { AssignmentComposer } from "./AssignmentComposer";

/**
 * 新建作业（CHG-0032）。
 *
 * 断言的是四条容易做错的规则：
 * 1. 打开表单不写库；标题为空/未选课序时不发请求；
 * 2. **同一 course 内可多选课序**：请求体带全部 `section_ids`；
 * 3. **不同 course 即使同名也不合并**：课程选择器是独立选项，课序列表只来自选中的 course；
 * 4. 可选指定学生：勾选后请求体带 `student_ids`；不勾选表示整个课序（不带该字段）。
 */

const COURSE_1 = "00000000-0000-4000-8000-0000000000c1";
const COURSE_2 = "00000000-0000-4000-8000-0000000000c2";
const SECTION_1 = "00000000-0000-4000-8000-0000000000b1";
const SECTION_2 = "00000000-0000-4000-8000-0000000000b2";
const SECTION_3 = "00000000-0000-4000-8000-0000000000b3";
const STUDENT_1 = "00000000-0000-4000-8000-0000000000d1";
const STUDENT_2 = "00000000-0000-4000-8000-0000000000d2";

const PUBLISHED = "00000000-0000-4000-8000-000000000401";
const DRAFT = "00000000-0000-4000-8000-000000000402";
const QUESTION = "00000000-0000-4000-8000-000000000403";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  localStorage.clear();
});

function jwtFor(subject: string): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "EdDSA" })}.${encode({ sub: subject })}.signature`;
}

function baseRoutes(overrides: { onPost?: () => Response } = {}): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: ["teacher"],
            scope: [{ course_id: COURSE_1, section_id: SECTION_1 }],
            access_token: jwtFor(UUID_B),
          }),
        ),
    },
    {
      path: "/api/v1/courses",
      respond: () =>
        jsonResponse(
          coursePage([
            // 同名课程、不同 course_id：必须是两个独立选项，不能合并。
            courseSummary({ course_id: COURSE_1, course_code: "OS-2026A", title: "操作系统" }),
            courseSummary({ course_id: COURSE_2, course_code: "OS-2026B", title: "操作系统" }),
          ]),
        ),
    },
    {
      path: `/api/v1/courses/${COURSE_1}/sections`,
      respond: () =>
        jsonResponse({
          items: [
            { section_id: SECTION_1, course_id: COURSE_1, section_code: "01", term: "2026F" },
            { section_id: SECTION_2, course_id: COURSE_1, section_code: "02", term: "2026F" },
          ],
        }),
    },
    {
      path: `/api/v1/courses/${COURSE_2}/sections`,
      respond: () =>
        jsonResponse({
          items: [
            { section_id: SECTION_3, course_id: COURSE_2, section_code: "01", term: "2026F" },
          ],
        }),
    },
    {
      path: `/api/v1/sections/${SECTION_1}/enrollments`,
      respond: () =>
        jsonResponse(
          enrollmentPage([
            enrollmentPublic({
              enrollment_id: UUID_A,
              section_id: SECTION_1,
              student_id: STUDENT_1,
              student_number: "20260001",
              display_name: "张三",
            }),
          ]),
        ),
    },
    {
      path: `/api/v1/sections/${SECTION_2}/enrollments`,
      respond: () =>
        jsonResponse(
          enrollmentPage([
            enrollmentPublic({
              enrollment_id: UUID_C,
              section_id: SECTION_2,
              student_id: STUDENT_2,
              student_number: "20260002",
              display_name: "李四",
            }),
          ]),
        ),
    },
    {
      path: `/api/v1/sections/${SECTION_3}/enrollments`,
      respond: () => jsonResponse(enrollmentPage([])),
    },
    {
      path: "/api/v1/questions",
      respond: () =>
        jsonResponse({
          items: [
            questionPublic({
              question_id: QUESTION,
              title: "进程与线程",
              versions: [
                {
                  question_version_id: PUBLISHED,
                  version: 1,
                  status: "published",
                  published_at: "2026-10-20T00:00:00Z",
                },
                {
                  question_version_id: DRAFT,
                  version: 2,
                  status: "draft",
                  published_at: null,
                },
              ],
            }),
          ],
          next_cursor: null,
        }),
    },
    {
      method: "POST",
      path: "/api/v1/assignments",
      respond:
        overrides.onPost ??
        (() =>
          jsonResponse(
            assignmentPublic({
              assignment_id: UUID_B,
              title: "第一次作业",
              section_ids: [SECTION_1],
              student_ids: [],
              question_version_ids: [PUBLISHED],
            }),
            201,
          )),
    },
  ];
}

function renderComposer() {
  const onCreated = vi.fn();
  const onClose = vi.fn();
  renderWithRouter(
    <AssignmentComposer onClose={onClose} onCreated={onCreated} />,
    {
      path: "/assignments",
      linkTargets: ["/assignments/$assignmentId"],
    },
  );
  return { onCreated, onClose };
}

function postRequests(target: FetchStub | null) {
  return (target?.requests ?? []).filter(
    (request) => request.method === "POST" && request.url.pathname === "/api/v1/assignments",
  );
}

async function fillBase(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByTestId("compose-version-list");
  await user.type(screen.getByLabelText("作业名称"), "第一次作业");
  await user.click(screen.getByRole("checkbox", { name: /进程与线程/ }));
}

/** 等课序列表加载完成后再点选：列表是异步取回的。 */
async function selectSection(
  user: ReturnType<typeof userEvent.setup>,
  sectionId: string,
) {
  await user.click(await screen.findByTestId(`compose-section-${sectionId}`));
}

describe("新建作业（CHG-0032）", () => {
  it("打开表单不写库，且只列出已发布版本", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderComposer();

    const list = await screen.findByTestId("compose-version-list");
    await waitFor(() => expect(within(list).getAllByRole("checkbox")).toHaveLength(1));
    // 草稿版本不出现：引用它必然 409，让用户选一个注定失败的选项是设计缺陷。
    expect(within(list).queryByText(/v2/)).toBeNull();
    expect(postRequests(stub)).toHaveLength(0);
  });

  it("同一课程内可多选课序；不指定学生时请求体不带 student_ids（=整个课序）", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderComposer();
    const user = userEvent.setup();

    await fillBase(user);
    await selectSection(user, SECTION_1);
    await selectSection(user, SECTION_2);
    // 两个课序的名单都被并行拉取（指定学生只能来自所选课序）。
    await screen.findByTestId("compose-student-list");

    await user.click(screen.getByTestId("assignment-compose-preview"));
    const confirm = await screen.findByTestId("assignment-compose-confirm");
    expect(within(confirm).getByTestId("compose-preview-sections")).toHaveTextContent(
      "01 · 2026F",
    );
    expect(within(confirm).getByTestId("compose-preview-sections")).toHaveTextContent(
      "02 · 2026F",
    );
    expect(postRequests(stub)).toHaveLength(0);

    await user.click(screen.getByTestId("assignment-compose-confirm-button"));
    await waitFor(() => expect(postRequests(stub)).toHaveLength(1));

    const body = postRequests(stub)[0].body as Record<string, unknown>;
    expect(body.course_id).toBe(COURSE_1);
    expect(body.section_ids).toEqual([SECTION_1, SECTION_2]);
    expect(body).not.toHaveProperty("student_ids");
    expect(body.question_version_ids).toEqual([PUBLISHED]);
  });

  it("可选指定学生：勾选后请求体带 student_ids，且预览写出人数", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderComposer();
    const user = userEvent.setup();

    await fillBase(user);
    await selectSection(user, SECTION_1);
    const studentList = await screen.findByTestId("compose-student-list");
    expect(studentList).toHaveTextContent("张三 · 20260001");
    await user.click(screen.getByTestId(`compose-student-${STUDENT_1}`));

    await user.click(screen.getByTestId("assignment-compose-preview"));
    const confirm = await screen.findByTestId("assignment-compose-confirm");
    expect(within(confirm).getByTestId("compose-preview-students")).toHaveTextContent(
      "已指定 1 名学生",
    );
    expect(within(confirm).getByTestId("compose-preview-students")).toHaveTextContent("张三");

    await user.click(screen.getByTestId("assignment-compose-confirm-button"));
    await waitFor(() => expect(postRequests(stub)).toHaveLength(1));
    const body = postRequests(stub)[0].body as Record<string, unknown>;
    expect(body.student_ids).toEqual([STUDENT_1]);
  });

  it("不同 course 即使同名也不合并：课序只来自选中的那门课程", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderComposer();
    const user = userEvent.setup();

    const courseSelect = await screen.findByLabelText("课程");
    // 课程目录是异步加载的：等两个选项都进入原生 select 再断言（否则读到的是空列表）。
    await waitFor(() =>
      expect(within(courseSelect).getAllByRole("option")).toHaveLength(2),
    );
    const optionLabels = within(courseSelect)
      .getAllByRole("option")
      .map((option) => option.textContent ?? "");
    // 两门同名课程是两个独立选项，用课程编号区分，而不是被合并成一项。
    expect(optionLabels).toHaveLength(2);
    expect(optionLabels[0]).toContain("OS-2026A");
    expect(optionLabels[1]).toContain("OS-2026B");

    await user.selectOptions(courseSelect, COURSE_2);
    await fillBase(user);
    // 只出现第二门课程的课序，第一门课程的课序不会混入。
    await screen.findByTestId(`compose-section-${SECTION_3}`);
    expect(screen.queryByTestId(`compose-section-${SECTION_1}`)).toBeNull();
    await user.click(screen.getByTestId(`compose-section-${SECTION_3}`));

    await user.click(screen.getByTestId("assignment-compose-preview"));
    await user.click(await screen.findByTestId("assignment-compose-confirm-button"));
    await waitFor(() => expect(postRequests(stub)).toHaveLength(1));
    const body = postRequests(stub)[0].body as Record<string, unknown>;
    expect(body.course_id).toBe(COURSE_2);
    expect(body.section_ids).toEqual([SECTION_3]);
  });

  it("标题为空或未选课序时不发请求，并在表单上给出错误", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderComposer();
    const user = userEvent.setup();

    await screen.findByTestId("compose-version-list");
    await user.click(screen.getByRole("checkbox", { name: /进程与线程/ }));
    await user.click(screen.getByTestId("assignment-compose-preview"));
    expect(await screen.findByTestId("assignment-compose-error")).toHaveTextContent(
      "请输入作业名称。",
    );
    expect(postRequests(stub)).toHaveLength(0);

    await user.type(screen.getByLabelText("作业名称"), "第一次作业");
    await user.click(screen.getByTestId("assignment-compose-preview"));
    expect(await screen.findByTestId("assignment-compose-error")).toHaveTextContent(
      "请至少选择一个课序。",
    );
    expect(postRequests(stub)).toHaveLength(0);
  });

  it("服务端 409 时保留表单并提示重新选择，不重复提交", async () => {
    stub = installFetchStub(
      baseRoutes({
        onPost: () =>
          problemResponse({
            status: 409,
            title: "Conflict",
            detail: "an assignment may only reference published versions",
            instance: "/api/v1/assignments",
          }),
      }),
    );
    await signIn("teacher1", "pw");
    const { onCreated } = renderComposer();
    const user = userEvent.setup();

    await fillBase(user);
    await selectSection(user, SECTION_1);
    await user.click(screen.getByTestId("assignment-compose-preview"));
    await user.click(await screen.findByTestId("assignment-compose-confirm-button"));

    expect(await screen.findByTestId("assignment-compose-error")).toHaveTextContent(
      "所选题目版本已变化",
    );
    expect(postRequests(stub)).toHaveLength(1);
    expect(onCreated).not.toHaveBeenCalled();
    // 表单仍在：标题与所选版本都保留，用户不需要重新填。
    expect(screen.getByLabelText("作业名称")).toHaveValue("第一次作业");
    expect(screen.getByRole("checkbox", { name: /进程与线程/ })).toBeChecked();
  });

  it("服务端 422（无效目标）给出明确原因，而不是通用失败", async () => {
    stub = installFetchStub(
      baseRoutes({
        onPost: () =>
          problemResponse({
            status: 422,
            title: "Unprocessable Entity",
            detail: "one or more assignment targets are not valid for the course",
            instance: "/api/v1/assignments",
          }),
      }),
    );
    await signIn("teacher1", "pw");
    renderComposer();
    const user = userEvent.setup();

    await fillBase(user);
    await selectSection(user, SECTION_1);
    await user.click(screen.getByTestId("assignment-compose-preview"));
    await user.click(await screen.findByTestId("assignment-compose-confirm-button"));

    expect(await screen.findByTestId("assignment-compose-error")).toHaveTextContent(
      "所选课序或学生不在该课程的有效名单中",
    );
  });
});
