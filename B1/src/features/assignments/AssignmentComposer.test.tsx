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
  UUID_B,
  assignmentPublic,
  coursePage,
  courseSummary,
  questionPublic,
  rosterSummary,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { AssignmentComposer } from "./AssignmentComposer";

/**
 * 新建作业（v0.32.0：课程级）。
 *
 * 断言的是新契约下的四条规则：
 * 1. 打开表单不写库；标题为空 / 未选题目时不发请求；
 * 2. 请求体只有 `course_id/title/question_version_ids/due_at`，**不再有** section_ids/student_ids；
 * 3. 预览的人数来自服务端 `roster-summary`（课序数/班级数/学生总数/已绑定/未绑定）；
 * 4. 不同 course 即使同名也不合并：课程选择器是独立选项。
 */

const COURSE_1 = "00000000-0000-4000-8000-0000000000c1";
const COURSE_2 = "00000000-0000-4000-8000-0000000000c2";

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
            scope: [{ course_id: COURSE_1 }],
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
      path: `/api/v1/courses/${COURSE_1}/roster-summary`,
      respond: () =>
        jsonResponse(
          rosterSummary({
            course_id: COURSE_1,
            section_count: 2,
            class_count: 3,
            student_total: 10,
            bound_count: 7,
            unbound_count: 3,
          }),
        ),
    },
    {
      path: `/api/v1/courses/${COURSE_2}/roster-summary`,
      respond: () =>
        jsonResponse(
          rosterSummary({
            course_id: COURSE_2,
            section_count: 1,
            class_count: 1,
            student_total: 4,
            bound_count: 4,
            unbound_count: 0,
          }),
        ),
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
              course_id: COURSE_1,
              title: "第一次作业",
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

describe("新建作业（v0.32.0 课程级）", () => {
  it("打开表单不写库，且只列出已发布版本", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderComposer();

    const list = await screen.findByTestId("compose-version-list");
    await waitFor(() => expect(within(list).getAllByRole("checkbox")).toHaveLength(1));
    // 草稿版本不出现：引用它必然 409，让用户选一个注定失败的选项是设计缺陷。
    expect(within(list).queryByText(/v2/)).toBeNull();
    expect(postRequests(stub)).toHaveLength(0);
    // 课程级说明在表单上可见：没有课序勾选、也没有指定学生。
    expect(screen.getByTestId("compose-course-wide-notice")).toHaveTextContent(
      "发布后对本课程全部课序与班级",
    );
    expect(screen.queryByTestId("compose-section-list")).toBeNull();
    expect(screen.queryByTestId("compose-student-fieldset")).toBeNull();
  });

  it("预览显示服务端受众统计；发布请求体只有课程/名称/题目（无 section_ids/student_ids）", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderComposer();
    const user = userEvent.setup();

    await fillBase(user);
    await user.click(screen.getByTestId("assignment-compose-preview"));
    const confirm = await screen.findByTestId("assignment-compose-confirm");

    expect(within(confirm).getByTestId("compose-preview-course")).toHaveTextContent("OS-2026A");
    await waitFor(() =>
      expect(within(confirm).getByTestId("compose-preview-section-count")).toHaveTextContent("2"),
    );
    expect(within(confirm).getByTestId("compose-preview-class-count")).toHaveTextContent("3");
    expect(within(confirm).getByTestId("compose-preview-student-total")).toHaveTextContent("10");
    expect(within(confirm).getByTestId("compose-preview-bound")).toHaveTextContent("7");
    expect(within(confirm).getByTestId("compose-preview-unbound")).toHaveTextContent("3");
    expect(within(confirm).getByTestId("compose-preview-questions")).toHaveTextContent(
      "1 个题目版本",
    );
    expect(postRequests(stub)).toHaveLength(0);

    await user.click(screen.getByTestId("assignment-compose-confirm-button"));
    await waitFor(() => expect(postRequests(stub)).toHaveLength(1));

    const body = postRequests(stub)[0].body as Record<string, unknown>;
    expect(body).toMatchObject({
      course_id: COURSE_1,
      title: "第一次作业",
      question_version_ids: [PUBLISHED],
    });
    expect(body).not.toHaveProperty("section_ids");
    expect(body).not.toHaveProperty("student_ids");
  });

  it("不同 course 即使同名也不合并：课程按 course_id 出现独立选项", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderComposer();
    const user = userEvent.setup();

    const courseSelect = await screen.findByLabelText("课程");
    await waitFor(() =>
      expect(within(courseSelect).getAllByRole("option")).toHaveLength(2),
    );
    const optionLabels = within(courseSelect)
      .getAllByRole("option")
      .map((option) => option.textContent ?? "");
    expect(optionLabels[0]).toContain("OS-2026A");
    expect(optionLabels[1]).toContain("OS-2026B");

    await user.selectOptions(courseSelect, COURSE_2);
    await fillBase(user);
    await user.click(screen.getByTestId("assignment-compose-preview"));
    const confirm = await screen.findByTestId("assignment-compose-confirm");
    await waitFor(() =>
      expect(within(confirm).getByTestId("compose-preview-student-total")).toHaveTextContent("4"),
    );
    await user.click(await screen.findByTestId("assignment-compose-confirm-button"));
    await waitFor(() => expect(postRequests(stub)).toHaveLength(1));
    const body = postRequests(stub)[0].body as Record<string, unknown>;
    expect(body.course_id).toBe(COURSE_2);
  });

  it("标题为空或未选题目时不发请求，并在表单上给出错误", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderComposer();
    const user = userEvent.setup();

    await screen.findByTestId("compose-version-list");
    await user.click(screen.getByTestId("assignment-compose-preview"));
    expect(await screen.findByTestId("assignment-compose-error")).toHaveTextContent(
      "请输入作业名称。",
    );
    expect(postRequests(stub)).toHaveLength(0);

    await user.type(screen.getByLabelText("作业名称"), "第一次作业");
    await user.click(screen.getByTestId("assignment-compose-preview"));
    expect(await screen.findByTestId("assignment-compose-error")).toHaveTextContent(
      "请至少选择一个已发布题目版本。",
    );
    expect(postRequests(stub)).toHaveLength(0);
  });

  it("受众统计读取失败时如实说明，但发布仍然可用（受众由服务端判定）", async () => {
    stub = installFetchStub(
      baseRoutes().map((route) =>
        route.path === `/api/v1/courses/${COURSE_1}/roster-summary`
          ? {
              path: `/api/v1/courses/${COURSE_1}/roster-summary`,
              respond: () =>
                problemResponse({
                  status: 503,
                  title: "Unavailable",
                  detail: "roster aggregation unavailable",
                }),
            }
          : route,
      ),
    );
    await signIn("teacher1", "pw");
    renderComposer();
    const user = userEvent.setup();

    await fillBase(user);
    await user.click(screen.getByTestId("assignment-compose-preview"));
    expect(await screen.findByTestId("compose-audience-error")).toHaveTextContent(
      "受众统计暂时读取不到",
    );
    await user.click(screen.getByTestId("assignment-compose-confirm-button"));
    await waitFor(() => expect(postRequests(stub)).toHaveLength(1));
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

  it("服务端 422 给出明确原因，而不是通用失败", async () => {
    stub = installFetchStub(
      baseRoutes({
        onPost: () =>
          problemResponse({
            status: 422,
            title: "Unprocessable Entity",
            detail: "question version does not belong to the course",
            instance: "/api/v1/assignments",
          }),
      }),
    );
    await signIn("teacher1", "pw");
    renderComposer();
    const user = userEvent.setup();

    await fillBase(user);
    await user.click(screen.getByTestId("assignment-compose-preview"));
    await user.click(await screen.findByTestId("assignment-compose-confirm-button"));

    expect(await screen.findByTestId("assignment-compose-error")).toHaveTextContent(
      "服务端拒绝了这个作业（422）",
    );
  });
});
