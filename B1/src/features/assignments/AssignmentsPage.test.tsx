import { afterEach, describe, expect, it } from "vitest";
import type { ReactElement } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
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
  UUID_C,
  UUID_D,
  assignmentPublic,
  coursePage,
  courseSummary,
  enrollmentPage,
  enrollmentPublic,
  gradePage,
  gradeRow,
  questionPublic,
  questionVersionPublic,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import { AssignmentDetailPage } from "./AssignmentDetailPage";
import { AssignmentsPage } from "./AssignmentsPage";

/**
 * 作业中心（`AssignmentPublic`）。
 *
 * 两条最重要的结论都不是"页面能打开"：
 * 1. 学生看到的进度来自**本人成绩行**，且成绩行没取完时不给判定；
 * 2. 教师看到的"已提交 / 未提交"来自名单×成绩的**同一套连接**（`cohort.ts`），
 *    未绑定账号的行不参与判定。
 */

const V1 = "00000000-0000-4000-8000-000000000101";
const V2 = "00000000-0000-4000-8000-000000000102";
const V3 = "00000000-0000-4000-8000-000000000103";
const Q1 = "00000000-0000-4000-8000-000000000301";
const S1 = "00000000-0000-4000-8000-000000000201";
const S2 = "00000000-0000-4000-8000-000000000202";
const S3 = "00000000-0000-4000-8000-000000000203";

const A1 = assignmentPublic({
  assignment_id: UUID_A,
  title: "第一次作业",
  question_version_ids: [V1, V2],
});
const A2 = assignmentPublic({
  assignment_id: UUID_C,
  title: "第二次作业",
  question_version_ids: [V3],
  status: "archived",
  due_at: null,
});

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

function routes(options: {
  role: "student" | "teacher";
  assignments?: { items: unknown[]; nextCursor: string | null }[];
  grades?: unknown[];
  enrollments?: unknown[];
}): StubbedRoute[] {
  const assignmentPages = options.assignments ?? [
    { items: [A1, A2], nextCursor: null },
  ];
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: [options.role],
            scope: [{ course_id: UUID_B, section_id: UUID_B }],
            access_token: jwtFor(S1),
          }),
        ),
    },
    {
      path: "/api/v1/assignments",
      respond: ({ url }) => {
        const cursor = url.searchParams.get("cursor");
        const index = Math.min(
          cursor === null ? 0 : Number(cursor),
          assignmentPages.length - 1,
        );
        const page = assignmentPages[index];
        return jsonResponse({ items: page.items, next_cursor: page.nextCursor });
      },
    },
    {
      path: "/api/v1/grades",
      respond: () => jsonResponse(gradePage((options.grades ?? []) as never[])),
    },
    {
      path: "/api/v1/courses",
      respond: () => jsonResponse(coursePage([courseSummary({ course_id: UUID_B })])),
    },
    {
      path: `/api/v1/courses/${UUID_B}/sections`,
      respond: () => jsonResponse({ items: [{ section_id: UUID_B, section_code: "01" }] }),
    },
    {
      path: `/api/v1/sections/${UUID_B}/enrollments`,
      respond: () =>
        jsonResponse(
          options.enrollments === undefined
            ? enrollmentPage([])
            : { items: options.enrollments, next_cursor: null },
        ),
    },
    {
      // 单条作业读取：详情页不再遍历列表，因此这里按 id 精确响应。
      path: `/api/v1/assignments/${UUID_A}`,
      respond: () => {
        const found = assignmentPages
          .flatMap((page) => page.items)
          .find((item) => (item as { assignment_id?: string }).assignment_id === UUID_A);
        return found
          ? jsonResponse(found)
          : problemResponse({
              status: 404,
              title: "Not Found",
              detail: "assignment not found",
              instance: `/api/v1/assignments/${UUID_A}`,
            });
      },
    },
    {
      // 题目标题只存在于题目 DTO（版本 DTO 不含 title）。
      path: `/api/v1/questions/${Q1}`,
      respond: () => jsonResponse(questionPublic({ question_id: Q1, title: "进程与线程" })),
    },
    {
      path: `/api/v1/question-versions/${V1}`,
      respond: () => jsonResponse(questionVersionPublic({ question_version_id: V1, version: 1, question_id: Q1 })),
    },
    {
      path: `/api/v1/question-versions/${V2}`,
      respond: () => jsonResponse(questionVersionPublic({ question_version_id: V2, version: 2, question_id: Q1 })),
    },
    {
      path: `/api/v1/question-versions/${V3}`,
      respond: () => jsonResponse(questionVersionPublic({ question_version_id: V3, version: 1, question_id: Q1 })),
    },
  ];
}

/**
 * 最小测试路由：页面里的 `<Link>` 需要路由上下文。只声明页面真正指向的目标，
 * 不引入应用路由表（避免测试与应用装配耦合）。
 */
function renderAt(path: string, element: () => ReactElement) {
  const rootRoute = createRootRoute({ component: element });
  const assignmentDetail = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments/$assignmentId",
    component: () => <span>detail target</span>,
  });
  const submit = createRoute({
    getParentRoute: () => rootRoute,
    path: "/submit/$questionVersionId",
    component: () => <span>submit target</span>,
  });
  const questionDetail = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions/$questionId",
    component: () => <span>question target</span>,
  });
  const testRouter = createRouter({
    routeTree: rootRoute.addChildren([assignmentDetail, submit, questionDetail]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  return renderWithProviders(<RouterProvider router={testRouter} />);
}

describe("作业中心（C1：学生侧）", () => {
  it("列出作用域内的作业，用本人成绩行算进度，并把未提交的题挂到作答页", async () => {
    stub = installFetchStub(
      routes({
        role: "student",
        grades: [
          gradeRow({
            submission_id: UUID_D,
            question_version_id: V1,
            student_id: S1,
            state: "completed",
            score: 90,
            max_score: 100,
            is_pass: true,
            evaluation_run_id: null,
          }),
        ],
      }),
    );
    await signIn("student1", "pw");
    renderAt("/", () => <AssignmentsPage />);

    const table = await screen.findByTestId("my-assignments-table");
    expect(table).toHaveTextContent("第一次作业");
    expect(table).toHaveTextContent("第二次作业");

    // A1 有两个题目版本，本人只交了 V1 → 部分完成 1/2，动作指向**还没交的** V2。
    const row = within(table).getByText("第一次作业").closest("tr") as HTMLTableRowElement;
    expect(within(row).getByText("部分完成")).toBeInTheDocument();
    expect(within(row).getByTestId(`my-progress-${UUID_A}`)).toHaveTextContent("已完成 1 / 2");
    const answerLink = within(row).getByRole("link", { name: "继续作答" });
    expect(answerLink).toHaveAttribute("href", expect.stringContaining(`/submit/${V2}`));

    // A2 一题未交 → 未提交，动作是"作答"，指向它唯一的版本。
    const second = within(table).getByText("第二次作业").closest("tr") as HTMLTableRowElement;
    expect(within(second).getByText("未提交")).toBeInTheDocument();
    expect(within(second).getByRole("link", { name: "作答" })).toHaveAttribute(
      "href",
      expect.stringContaining(`/submit/${V3}`),
    );
    // 截止时间：A2 未设置，如实显示而不是空白或占位日期
    expect(second).toHaveTextContent("未设置截止时间");
  });

  it("本人成绩行还没到位时不给出进度判定（分页不完整会让「未提交」说错话）", async () => {
    const delayed = new Promise<Response>((resolve) => {
      setTimeout(
        () => resolve(jsonResponse(gradePage([]))),
        5000,
      );
    });
    stub = installFetchStub(
      routes({ role: "student", grades: [] }).map((route) =>
        route.path === "/api/v1/grades"
          ? { path: "/api/v1/grades", respond: () => delayed }
          : route,
      ),
    );
    await signIn("student1", "pw");
    renderAt("/", () => <AssignmentsPage />);

    // 作业列表已到位，但本人成绩行还在路上：进度列只能是"加载中"，不能是"未提交"。
    const table = await screen.findByTestId("my-assignments-table");
    expect(within(table).getByTestId(`my-progress-loading-${UUID_A}`)).toHaveTextContent("加载中");
    expect(screen.queryByTestId(`my-progress-${UUID_A}`)).toBeNull();
    expect(table).not.toHaveTextContent("未提交");
  });

  it("学生看不到教师侧的「提交情况」标签（服务端仍独立判定）", async () => {
    stub = installFetchStub(routes({ role: "student" }));
    await signIn("student1", "pw");
    renderAt("/", () => <AssignmentsPage />);

    await screen.findByTestId("my-assignments-table");
    expect(screen.queryByRole("tab", { name: "提交情况" })).toBeNull();
  });

  it("作业列表到自动翻页上限时说明被截断（不是「加载中」，也不给进度判定）", async () => {
    // 永远有下一页：撞上自动翻页上限后必须**停止**并如实说明。
    stub = installFetchStub(
      routes({ role: "student" }).map((route) =>
        route.path === "/api/v1/assignments"
          ? {
              path: "/api/v1/assignments",
              respond: () => jsonResponse({ items: [A1], next_cursor: "always-more" }),
            }
          : route,
      ),
    );
    await signIn("student1", "pw");
    renderAt("/", () => <AssignmentsPage />);

    const notice = await screen.findByTestId("assignments-truncated", {}, { timeout: 5000 });
    expect(notice).toHaveTextContent(`已达到自动翻页上限（${MAX_AUTO_PAGES} 页）`);

    const panel = notice.closest("section") as HTMLElement;
    expect(within(panel).queryAllByRole("status")).toHaveLength(0);
    expect(screen.queryByTestId("my-assignments-table")).toBeNull();
    // 被截断的列表不能支撑"我还没交"这类判定。
    expect(screen.queryByText("未提交")).toBeNull();
  });
});

describe("作业中心（C1：教师侧提交情况）", () => {
  const roster = [
    enrollmentPublic({
      enrollment_id: UUID_A,
      student_id: S1,
      student_number: "20260001",
      display_name: "张三",
    }),
    enrollmentPublic({
      enrollment_id: UUID_C,
      student_id: S2,
      student_number: "20260002",
      display_name: "李四",
    }),
    enrollmentPublic({
      enrollment_id: UUID_D,
      student_id: S3,
      student_number: "20260003",
      display_name: "王五",
    }),
  ];

  function teacherRoutes(extraGrades: unknown[], enrollments: unknown[] = roster) {
    return routes({ role: "teacher", grades: extraGrades, enrollments });
  }

  async function openProgress(extraGrades: unknown[], enrollments?: unknown[]) {
    stub = installFetchStub(teacherRoutes(extraGrades, enrollments));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAt("/", () => <AssignmentsPage />);
    await user.click(await screen.findByRole("tab", { name: "提交情况" }));
    await screen.findByTestId("assignment-progress-table");
    return user;
  }

  it("逐作业统计已提交 / 未提交，复用名单×成绩的连接（未绑定账号不参与判定）", async () => {
    await openProgress([
      // S1 交了 A1 的 V1
      gradeRow({
        submission_id: UUID_D,
        question_version_id: V1,
        student_id: S1,
        state: "completed",
        evaluation_run_id: null,
      }),
      // S2 交了 A2 的 V3，但评测技术失败（仍是"已提交"）
      gradeRow({
        submission_id: UUID_A,
        question_version_id: V3,
        student_id: S2,
        state: "failed",
        score: null,
        max_score: null,
        is_pass: null,
        evaluation_run_id: null,
      }),
    ]);

    // A1：1 人已交，2 人未交（三名学生都可判定）
    expect(await screen.findByTestId(`submitted-${UUID_A}`)).toHaveTextContent("1 / 3");
    expect(screen.getByTestId(`missing-${UUID_A}`)).toHaveTextContent("2");
    // A2：S2 已交（技术失败也算提交），S1/S3 未交
    expect(screen.getByTestId(`submitted-${UUID_C}`)).toHaveTextContent("1 / 3");
    expect(screen.getByTestId(`missing-${UUID_C}`)).toHaveTextContent("2");
  });

  it("未绑定账号的学生不参与「已提交 / 未提交」判定", async () => {
    await openProgress(
      [],
      [
        enrollmentPublic({
          enrollment_id: UUID_A,
          student_id: S1,
          student_number: "20260001",
          display_name: "张三",
        }),
        enrollmentPublic({
          enrollment_id: UUID_C,
          student_id: null,
          student_number: "20260002",
          display_name: "李四",
        }),
      ],
    );

    // 可判定人数是 1（另一行没绑定账号），未提交 1 —— 而不是"未提交 2"。
    expect(await screen.findByTestId(`submitted-${UUID_A}`)).toHaveTextContent("0 / 1");
    expect(screen.getByTestId(`missing-${UUID_A}`)).toHaveTextContent("1");
  });

  it("展开单个作业可以看到逐人名单（未绑定账号的行如实标注）", async () => {
    const user = await openProgress([]);

    await user.click(screen.getByTestId(`toggle-roster-${UUID_A}`));

    const table = await screen.findByTestId("assignment-roster-table");
    expect(within(table).getAllByRole("row")).toHaveLength(4); // 表头 + 3 人
    expect(table).toHaveTextContent("张三");
    expect(within(table).getAllByText("未提交")).toHaveLength(3);
  });

  it("成绩行到自动翻页上限时说明被截断，不渲染提交统计也不转圈", async () => {
    stub = installFetchStub(
      teacherRoutes([], roster).map((route) =>
        route.path === "/api/v1/grades"
          ? {
              path: "/api/v1/grades",
              respond: () =>
                jsonResponse({
                  items: [
                    gradeRow({
                      submission_id: UUID_D,
                      question_version_id: V1,
                      student_id: S1,
                      state: "completed",
                      evaluation_run_id: null,
                    }),
                  ],
                  next_cursor: "always-more",
                }),
            }
          : route,
      ),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAt("/", () => <AssignmentsPage />);
    await user.click(await screen.findByRole("tab", { name: "提交情况" }));

    const notice = await screen.findByTestId(
      "assignment-progress-truncated",
      {},
      { timeout: 5000 },
    );
    expect(notice).toHaveTextContent(`已达到自动翻页上限（${MAX_AUTO_PAGES} 页）`);
    expect(within(notice).queryAllByRole("status")).toHaveLength(0);
    expect(screen.queryByTestId("assignment-progress-table")).toBeNull();
    // 逐作业的"已提交 / 未提交"数字一个都不能出现。
    expect(screen.queryByTestId(`submitted-${UUID_A}`)).toBeNull();
    expect(screen.queryByTestId(`missing-${UUID_A}`)).toBeNull();
  });
});

describe("作业详情", () => {
  it("按 id 单条读取作业，并列出题目名称与版本（学生另见本人状态）", async () => {
    stub = installFetchStub(
      routes({
        role: "student",
        grades: [
          gradeRow({
            submission_id: UUID_D,
            question_version_id: V1,
            student_id: S1,
            state: "completed",
            evaluation_run_id: null,
          }),
        ],
      }),
    );
    await signIn("student1", "pw");
    renderAt(`/assignments/${UUID_A}`, () => <AssignmentDetailPage assignmentId={UUID_A} />);

    expect(await screen.findByTestId("assignment-title")).toHaveTextContent("第一次作业");
    expect(screen.getByTestId("assignment-breadcrumb-current")).toHaveTextContent("第一次作业");

    const table = await screen.findByTestId("assignment-versions-table");
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3)); // 表头 + 2 版本
    expect(table).toHaveTextContent("已提交");
    expect(table).toHaveTextContent("未提交");
    expect(await within(table).findAllByText("进程与线程")).toHaveLength(2);
  });

  it("作业不存在时明确说找不到（服务端 404，不伪造空作业）", async () => {
    stub = installFetchStub(routes({ role: "student", assignments: [] }));
    await signIn("student1", "pw");
    renderAt(`/assignments/${UUID_A}`, () => <AssignmentDetailPage assignmentId={UUID_A} />);

    expect(await screen.findByText(/找不到这个作业/)).toBeInTheDocument();
  });

  it("越权读取显示权限拒绝，而不是「找不到」或空列表", async () => {
    stub = installFetchStub([
      ...routes({ role: "student" }).filter(
        (route) => route.path !== `/api/v1/assignments/${UUID_A}`,
      ),
      {
        path: `/api/v1/assignments/${UUID_A}`,
        respond: () =>
          problemResponse({
            status: 403,
            title: "Forbidden",
            detail: "outside the caller's course scope",
            instance: `/api/v1/assignments/${UUID_A}`,
          }),
      },
    ]);
    await signIn("student1", "pw");
    renderAt(`/assignments/${UUID_A}`, () => <AssignmentDetailPage assignmentId={UUID_A} />);

    expect(await screen.findByText(/没有查看这个课程的权限/)).toBeInTheDocument();
    expect(screen.queryByText(/找不到这个作业/)).toBeNull();
  });

  it("本人成绩行未取完时版本行不显示「未提交」", async () => {
    stub = installFetchStub(
      routes({ role: "student" }).map((route) =>
        route.path === "/api/v1/grades"
          ? {
              path: "/api/v1/grades",
              respond: () =>
                jsonResponse({
                  items: [
                    gradeRow({
                      submission_id: UUID_D,
                      question_version_id: V1,
                      student_id: S1,
                      state: "completed",
                      evaluation_run_id: null,
                    }),
                  ],
                  next_cursor: "always-more",
                }),
            }
          : route,
      ),
    );
    await signIn("student1", "pw");
    renderAt(`/assignments/${UUID_A}`, () => <AssignmentDetailPage assignmentId={UUID_A} />);

    const table = await screen.findByTestId("assignment-versions-table");
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
    // 不变量：成绩行没取完时，任何一行都不得断言“未提交”。
    expect(within(table).queryByText("未提交")).toBeNull();
  });
});
