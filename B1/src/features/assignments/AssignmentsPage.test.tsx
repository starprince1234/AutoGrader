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
  type FetchStub,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  UUID_C,
  assignmentPublic,
  coursePage,
  courseSummary,
  currentUser,
  enrollmentPage,
  enrollmentPublic,
  gradePage,
  gradeRow,
  intakeQuarantineView,
  myMembership,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import { AssignmentsPage } from "./AssignmentsPage";

/**
 * 作业中心（v0.32.0 课程级）。
 *
 * 两条最重要的结论都不是"页面能打开"：
 * 1. 学生视图的课程/课序/班级来自 `GET /me` 的 memberships（多课程可切换），
 *    本人进度来自**本人成绩行**（按 assignment_id 归属），成绩行没取完时不给判定；
 * 2. 教师视图的"已交 / 部分 / 未交"来自**当前筛选课序**的名单 × 成绩连接；
 *    作业本身对课程全部课序/班级生效，界面不再展示/依赖作业的 section_ids。
 */

const COURSE = "00000000-0000-4000-8000-0000000000b1";
const COURSE_2 = "00000000-0000-4000-8000-0000000000b2";
const SECTION = "00000000-0000-4000-8000-0000000000c1";
const SECTION_2 = "00000000-0000-4000-8000-0000000000c2";
const CLASS = "00000000-0000-4000-8000-0000000000d1";
const CLASS_2 = "00000000-0000-4000-8000-0000000000d2";
const STUDENT = "00000000-0000-4000-8000-0000000000e1";
const S1 = "00000000-0000-4000-8000-0000000000f1";
const S2 = "00000000-0000-4000-8000-0000000000f2";
const S3 = "00000000-0000-4000-8000-0000000000f3";

const V1 = "00000000-0000-4000-8000-000000000101";
const V2 = "00000000-0000-4000-8000-000000000102";
const V3 = "00000000-0000-4000-8000-000000000103";

const A1 = assignmentPublic({
  assignment_id: UUID_A,
  course_id: COURSE,
  title: "第一次作业",
  question_version_ids: [V1, V2],
});
const A2 = assignmentPublic({
  assignment_id: UUID_C,
  course_id: COURSE,
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

const ROSTER = [
  enrollmentPublic({
    enrollment_id: UUID_A,
    course_id: COURSE,
    section_id: SECTION,
    class_id: CLASS,
    class_name: "软件工程1班",
    student_id: S1,
    student_number: "20260001",
    display_name: "张三",
  }),
  enrollmentPublic({
    enrollment_id: UUID_C,
    course_id: COURSE,
    section_id: SECTION,
    class_id: CLASS,
    class_name: "软件工程1班",
    student_id: S2,
    student_number: "20260002",
    display_name: "李四",
  }),
  enrollmentPublic({
    enrollment_id: "00000000-0000-4000-8000-0000000000e3",
    course_id: COURSE,
    section_id: SECTION,
    class_id: CLASS,
    class_name: "软件工程1班",
    student_id: S3,
    student_number: "20260003",
    display_name: "王五",
  }),
];

function routes(options: {
  role: "student" | "teacher";
  grades?: unknown[];
  gradeNextCursor?: string | null;
  enrollments?: unknown[];
  pendingQuarantines?: number;
  memberships?: ReturnType<typeof myMembership>[];
}): StubbedRoute[] {
  const common: StubbedRoute[] = [
    {
      method: "GET",
      path: "/api/v1/intake/quarantines",
      respond: ({ url }) => {
        const limit = Number(url.searchParams.get("limit") ?? "50");
        const total = options.pendingQuarantines ?? 0;
        const items = Array.from({ length: Math.min(total, limit) }, (_, index) =>
          intakeQuarantineView({
            intake_quarantine_id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
          }),
        );
        return jsonResponse({ items, next_cursor: total > limit ? "more" : null });
      },
    },
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: [options.role],
            scope: [{ course_id: COURSE, section_id: SECTION }],
            access_token: jwtFor(options.role === "student" ? STUDENT : S1),
          }),
        ),
    },
    {
      path: "/api/v1/assignments",
      respond: () => jsonResponse({ items: [A1, A2], next_cursor: null }),
    },
    {
      path: "/api/v1/grades",
      respond: () =>
        jsonResponse(gradePage((options.grades ?? []) as never[], options.gradeNextCursor ?? null)),
    },
  ];

  if (options.role === "student") {
    return [
      ...common,
      {
        path: "/api/v1/me",
        respond: () =>
          jsonResponse(
            currentUser({
              user_id: STUDENT,
              memberships:
                options.memberships ??
                [
                  myMembership({
                    course_id: COURSE,
                    course_title: "操作系统",
                    section_id: SECTION,
                    section_code: "01",
                    class_id: CLASS,
                    class_name: "软件工程1班",
                  }),
                ],
            }),
          ),
      },
    ];
  }

  return [
    ...common,
    {
      path: "/api/v1/courses",
      respond: () => jsonResponse(coursePage([courseSummary({ course_id: COURSE })])),
    },
    {
      path: `/api/v1/courses/${COURSE}/sections`,
      respond: () =>
        jsonResponse({
          items: [
            {
              section_id: SECTION,
              course_id: COURSE,
              section_code: "01",
              term: "2026F",
              class_count: 1,
              enrollment_count: 3,
            },
            {
              section_id: SECTION_2,
              course_id: COURSE,
              section_code: "02",
              term: "2026F",
              class_count: 0,
              enrollment_count: 0,
            },
          ],
        }),
    },
    {
      path: `/api/v1/sections/${SECTION}/enrollments`,
      respond: () =>
        jsonResponse(
          options.enrollments === undefined
            ? enrollmentPage(ROSTER)
            : { items: options.enrollments, next_cursor: null },
        ),
    },
    {
      method: "POST",
      path: `/api/v1/assignments/${UUID_A}/close`,
      respond: () => jsonResponse({ ...A1, status: "closed" }),
    },
  ];
}

/** 最小测试路由：页面里的 `<Link>` 需要路由上下文。 */
function renderAt(path: string, element: () => ReactElement) {
  const rootRoute = createRootRoute({ component: element });
  const assignmentDetail = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments/$assignmentId",
    component: () => <span>detail target</span>,
  });
  const assignmentGrading = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments/$assignmentId/grading",
    component: () => <span>grading target</span>,
  });
  const review = createRoute({
    getParentRoute: () => rootRoute,
    path: "/review",
    component: () => <span>review target</span>,
  });
  const testRouter = createRouter({
    routeTree: rootRoute.addChildren([assignmentDetail, assignmentGrading, review]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  return renderWithProviders(<RouterProvider router={testRouter} />);
}

describe("作业中心（学生侧）", () => {
  it("展示本人姓名/学号与全部课程、课序、班级；列出作业并给出进度", async () => {
    stub = installFetchStub(
      routes({
        role: "student",
        memberships: [
          myMembership({
            course_id: COURSE,
            course_title: "操作系统",
            section_id: SECTION,
            section_code: "01",
            class_id: CLASS,
            class_name: "软件工程1班",
          }),
          myMembership({
            course_id: COURSE_2,
            course_title: "数据结构",
            section_id: "00000000-0000-4000-8000-0000000000c9",
            section_code: "02",
            class_id: CLASS_2,
            class_name: "数据结构1班",
          }),
        ],
        grades: [
          gradeRow({
            assignment_id: UUID_A,
            question_version_id: V1,
            student_id: STUDENT,
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

    const memberships = await screen.findByTestId("my-memberships");
    await waitFor(() =>
      expect(within(memberships).getByTestId("my-identity")).toHaveTextContent("张三"),
    );
    expect(within(memberships).getByTestId("my-identity")).toHaveTextContent("20260001");
    const list = screen.getByTestId("my-membership-list");
    expect(within(list).getByTestId(`my-membership-${COURSE}`)).toHaveTextContent(
      "操作系统 · 课序 01 · 班级 软件工程1班",
    );
    expect(within(list).getByTestId(`my-membership-${COURSE_2}`)).toHaveTextContent("数据结构");

    // 多课程：提供课程切换。
    expect(await screen.findByTestId("student-course-switch")).toBeInTheDocument();

    const table = await screen.findByTestId("my-assignments-table");
    const row = within(table).getByText("第一次作业").closest("tr") as HTMLTableRowElement;
    expect(within(row).getByText("部分提交")).toBeInTheDocument();
    expect(within(row).getByTestId(`my-progress-${UUID_A}`)).toHaveTextContent("已完成 1 / 2");
    expect(within(row).getByRole("link", { name: "继续作答" })).toHaveAttribute(
      "href",
      expect.stringContaining(`question=${V2}`),
    );

    const second = within(table).getByText("第二次作业").closest("tr") as HTMLTableRowElement;
    expect(within(second).getByText("未交")).toBeInTheDocument();
    expect(second).toHaveTextContent("未设置截止时间");
  });

  it("切换课程会按选中 course_id 重新请求作业列表", async () => {
    stub = installFetchStub(
      routes({
        role: "student",
        memberships: [
          myMembership({ course_id: COURSE, course_title: "操作系统" }),
          myMembership({ course_id: COURSE_2, course_title: "数据结构" }),
        ],
      }),
    );
    await signIn("student1", "pw");
    renderAt("/", () => <AssignmentsPage />);

    const select = await screen.findByLabelText("课程");
    await userEvent.setup().selectOptions(select, COURSE_2);

    await waitFor(() => {
      const assigned = (stub?.requests ?? []).filter(
        (request) => request.url.pathname === "/api/v1/assignments",
      );
      expect(
        assigned.some((request) => request.url.searchParams.get("course_id") === COURSE_2),
      ).toBe(true);
    });
  });

  it("不在任何课程名单中时明确说明，而不是显示空列表", async () => {
    stub = installFetchStub(routes({ role: "student", memberships: [] }));
    await signIn("student1", "pw");
    renderAt("/", () => <AssignmentsPage />);

    expect(await screen.findByText(/当前账号还没有有效选课/)).toBeInTheDocument();
    expect(screen.queryByTestId("my-assignments-table")).toBeNull();
  });

  it("本人成绩行还没到位时不给出进度判定", async () => {
    const delayed = new Promise<Response>((resolve) => {
      setTimeout(() => resolve(jsonResponse(gradePage([]))), 5000);
    });
    stub = installFetchStub(
      routes({ role: "student" }).map((route) =>
        route.path === "/api/v1/grades" ? { path: "/api/v1/grades", respond: () => delayed } : route,
      ),
    );
    await signIn("student1", "pw");
    renderAt("/", () => <AssignmentsPage />);

    const table = await screen.findByTestId("my-assignments-table");
    const loadingCell = within(table).getByTestId(`my-progress-loading-${UUID_A}`);
    expect(loadingCell.querySelector(".inline-loader")).not.toBeNull();
    expect(screen.queryByTestId(`my-progress-${UUID_A}`)).toBeNull();
    // "未交"这个结论一个都不能出现。
    expect(within(table).queryByText("未交")).toBeNull();
  });

  it("成绩行被截断时如实说明，不给进度判定", async () => {
    stub = installFetchStub(
      routes({ role: "student", gradeNextCursor: "always-more" }),
    );
    await signIn("student1", "pw");
    renderAt("/", () => <AssignmentsPage />);

    const table = await screen.findByTestId("my-assignments-table");
    const notice = await within(table).findByTestId(`my-progress-truncated-${UUID_A}`);
    expect(notice).toHaveTextContent("成绩未取完");
    expect(within(table).queryByText("未交")).toBeNull();
  });

  it("作业列表到自动翻页上限时说明被截断（不是加载中，也不给判定）", async () => {
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
    expect(notice).toHaveTextContent(`结果较多，仅展示前 ${MAX_AUTO_PAGES} 页`);
    const panel = notice.closest("section") as HTMLElement;
    expect(within(panel).queryAllByRole("status")).toHaveLength(0);
    expect(screen.queryByTestId("my-assignments-table")).toBeNull();
    expect(screen.queryByText("未交")).toBeNull();
  });

  it("学生看不到教师侧的「提交情况」标签", async () => {
    stub = installFetchStub(routes({ role: "student" }));
    await signIn("student1", "pw");
    renderAt("/", () => <AssignmentsPage />);

    await screen.findByTestId("my-assignments-table");
    expect(screen.queryByRole("tab", { name: "提交情况" })).toBeNull();
  });
});

describe("作业中心（教师侧作业列表）", () => {
  const teacherGrades = [
    // A1：张三只交第一题（部分提交）；李四两题都完成；王五未交。
    gradeRow({
      submission_id: "00000000-0000-4000-8000-0000000000f1",
      assignment_id: UUID_A,
      question_version_id: V1,
      student_id: S1,
      state: "completed",
      evaluation_run_id: null,
    }),
    gradeRow({
      submission_id: "00000000-0000-4000-8000-0000000000f2",
      assignment_id: UUID_A,
      question_version_id: V1,
      student_id: S2,
      state: "completed",
      evaluation_run_id: null,
    }),
    gradeRow({
      submission_id: "00000000-0000-4000-8000-0000000000f3",
      assignment_id: UUID_A,
      question_version_id: V2,
      student_id: S2,
      state: "completed",
      evaluation_run_id: null,
    }),
    // A2：王五完成；张三/李四未交。
    gradeRow({
      submission_id: "00000000-0000-4000-8000-0000000000f4",
      assignment_id: UUID_C,
      question_version_id: V3,
      student_id: S3,
      state: "completed",
      evaluation_run_id: null,
    }),
  ];

  async function openTeacherList(
    extra: Omit<Parameters<typeof routes>[0], "role"> = {},
  ) {
    stub = installFetchStub(routes({ ...extra, role: "teacher" }));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAt("/", () => <AssignmentsPage />);
    await screen.findByTestId("teacher-assignments-table");
    return user;
  }

  it("作业列表按 PDF 结构：作业名 / 状态 / 截止时间 / 题目数 / 已处理作业数量", async () => {
    await openTeacherList({ grades: teacherGrades });

    // 已处理作业数量 = 至少交过一题的人数 / 可判定人数。
    expect(await screen.findByTestId(`handled-${UUID_A}`)).toHaveTextContent("2 / 3");
    expect(screen.getByTestId(`handled-${UUID_C}`)).toHaveTextContent("1 / 3");

    // 状态：服务端 status 决定 —— open = 进行，archived = 结束。
    expect(screen.getByTestId("teacher-assignments-table")).toHaveTextContent("进行");
    expect(screen.getByTestId("teacher-assignments-table")).toHaveTextContent("结束");

    // 未设置结束时间的行必须明确写出来（这是"手动结束"存在的理由）。
    expect(screen.getByTestId(`no-end-${UUID_C}`)).toHaveTextContent("未设置结束时间");

    // 题目数量可点，直达批改界面。
    const gradingLink = screen.getByTestId(`grading-link-${UUID_A}`);
    expect(gradingLink).toHaveTextContent("2");
    expect(gradingLink.getAttribute("href")).toContain(`/assignments/${UUID_A}/grading`);
  });

  it("状态下拉按「进行 / 结束」筛选列表", async () => {
    const user = await openTeacherList({ grades: teacherGrades });

    await user.selectOptions(screen.getByLabelText("状态"), "ended");
    expect(screen.getByTestId("teacher-assignments-table")).toHaveTextContent("第二次作业");
    expect(screen.getByTestId("teacher-assignments-table")).not.toHaveTextContent("第一次作业");

    await user.selectOptions(screen.getByLabelText("状态"), "running");
    expect(screen.getByTestId("teacher-assignments-table")).toHaveTextContent("第一次作业");
    expect(screen.getByTestId("teacher-assignments-table")).not.toHaveTextContent("第二次作业");
  });

  it("未绑定账号的名单行同样计入「已处理作业数量」的分母", async () => {
    await openTeacherList({
      grades: [],
      enrollments: [
        enrollmentPublic({
          enrollment_id: UUID_A,
          course_id: COURSE,
          section_id: SECTION,
          class_id: CLASS,
          class_name: "软件工程1班",
          student_id: S1,
          student_number: "20260001",
          display_name: "张三",
        }),
        enrollmentPublic({
          enrollment_id: UUID_C,
          course_id: COURSE,
          section_id: SECTION,
          class_id: CLASS,
          class_name: "软件工程1班",
          student_id: null,
          student_number: "20260002",
          display_name: "李四",
        }),
      ],
    });

    // 分母是整份名单 2 人（含未绑定账号的那一行）→ 0 / 2：不能把人算没了。
    expect(await screen.findByTestId(`handled-${UUID_A}`)).toHaveTextContent("0 / 2");
  });

  it("成绩行到自动翻页上限时说明被截断，不渲染任何计数", async () => {
    stub = installFetchStub(routes({ role: "teacher", gradeNextCursor: "always-more" }));
    await signIn("teacher1", "pw");
    renderAt("/", () => <AssignmentsPage />);

    const notice = await screen.findByTestId(
      "assignment-progress-truncated",
      {},
      { timeout: 5000 },
    );
    expect(notice).toHaveTextContent(`结果较多，仅展示前 ${MAX_AUTO_PAGES} 页`);
    expect(within(notice).queryAllByRole("status")).toHaveLength(0);
    expect(screen.queryByTestId("teacher-assignments-table")).toBeNull();
    expect(screen.queryByTestId(`handled-${UUID_A}`)).toBeNull();
  });

  it("手动结束：二次确认后只发一次 close 请求，成功后列表状态变为结束", async () => {
    const user = await openTeacherList({ grades: teacherGrades });

    // 已结束的作业没有「手动结束」按钮，避免重复结束。
    expect(screen.queryByTestId(`close-assignment-${UUID_C}`)).toBeNull();

    await user.click(screen.getByTestId(`close-assignment-${UUID_A}`));
    const dialog = await screen.findByTestId("assignment-close-confirm");
    expect(dialog).toHaveTextContent("结束后，学生将无法再提交");

    await user.click(screen.getByTestId("assignment-close-confirm-button"));

    await waitFor(() =>
      expect(
        stub!.requests.filter(
          (item) =>
            item.method === "POST" &&
            item.url.pathname === `/api/v1/assignments/${UUID_A}/close`,
        ),
      ).toHaveLength(1),
    );
    expect(await screen.findByTestId("assignments-created-notice")).toHaveTextContent(
      "作业已结束",
    );
    // 服务端返回 closed 后，该行的状态列必须写成「结束」而不是「进行」。
    await waitFor(() =>
      expect(screen.getByTestId(`due-${UUID_A}`).closest("tr")).toHaveTextContent("结束"),
    );
  });
});
