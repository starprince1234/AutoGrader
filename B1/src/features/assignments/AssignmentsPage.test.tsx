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
  const review = createRoute({
    getParentRoute: () => rootRoute,
    path: "/review",
    component: () => <span>review target</span>,
  });
  const testRouter = createRouter({
    routeTree: rootRoute.addChildren([assignmentDetail, review]),
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

    expect(await screen.findByText(/当前账号还没有任何有效选课/)).toBeInTheDocument();
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
    expect(notice).toHaveTextContent(`已达到自动翻页上限（${MAX_AUTO_PAGES} 页）`);
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

describe("作业中心（教师侧提交情况）", () => {
  const teacherGrades = [
    // A1：张三只交第一题（部分）；李四两题都完成（已交）；王五未交。
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

  async function openProgress(
    extra: Omit<Parameters<typeof routes>[0], "role"> = {},
  ) {
    stub = installFetchStub(routes({ ...extra, role: "teacher" }));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAt("/", () => <AssignmentsPage />);
    await user.click(await screen.findByRole("tab", { name: "提交情况" }));
    await screen.findByTestId("assignment-progress-table");
    return user;
  }

  it("「邮件提交」旁的条数与复核页同一批：同样的课程/课序与待处理筛选", async () => {
    stub = installFetchStub(routes({ role: "teacher", pendingQuarantines: 3 }));
    await signIn("teacher1", "pw");
    renderAt("/", () => <AssignmentsPage />);
    const badge = await screen.findByTestId("assignments-quarantine-badge");
    await waitFor(() => expect(badge).toHaveTextContent("3"));
    expect(badge).toHaveAttribute("aria-label", "有 3 条待处理的邮件记录");

    // 徽标请求必须和「邮件提交」链接带过去的条件一致，否则会出现"徽标 7 条、复核页更少"。
    const request = stub!.requests.find(
      (item) => item.method === "GET" && item.url.pathname === "/api/v1/intake/quarantines",
    );
    expect(request?.url.searchParams.get("resolved")).toBe("false");
    expect(request?.url.searchParams.get("held")).toBe("false");
    expect(request?.url.searchParams.get("course_id")).toBe(COURSE);
    expect(request?.url.searchParams.get("section_id")).toBe(SECTION);
    // 与复核页第一页同一分页大小，两个数字才对得上。
    expect(request?.url.searchParams.get("limit")).toBe("50");
  });

  it("待处理超过一页时显示「50+」（不把一页计数当总数）", async () => {
    stub = installFetchStub(routes({ role: "teacher", pendingQuarantines: 150 }));
    await signIn("teacher1", "pw");
    renderAt("/", () => <AssignmentsPage />);
    const badge = await screen.findByTestId("assignments-quarantine-badge");
    await waitFor(() => expect(badge).toHaveTextContent("50+"));
  });

  it("按新分类统计已交/部分/未交；列表视图说明作业是课程级受众", async () => {
    await openProgress({ grades: teacherGrades });

    expect(await screen.findByTestId(`submitted-${UUID_A}`)).toHaveTextContent("1");
    expect(screen.getByTestId(`partial-${UUID_A}`)).toHaveTextContent("1");
    expect(screen.getByTestId(`notSubmitted-${UUID_A}`)).toHaveTextContent("1");

    expect(screen.getByTestId(`submitted-${UUID_C}`)).toHaveTextContent("1");
    expect(screen.getByTestId(`notSubmitted-${UUID_C}`)).toHaveTextContent("2");

    // 旧的多课序展示（assignment-sections-*）已删除：作业不再有目标课序列表。
    expect(screen.queryByTestId(`assignment-sections-${UUID_A}`)).toBeNull();
  });

  it("教师列表视图显示课程级受众说明，且不出现 section_ids 派生列", async () => {
    stub = installFetchStub(routes({ role: "teacher", grades: teacherGrades }));
    await signIn("teacher1", "pw");
    renderAt("/", () => <AssignmentsPage />);

    expect(await screen.findByTestId("my-assignments-table")).toBeInTheDocument();
    expect(screen.getByTestId("assignment-course-wide")).toHaveTextContent(
      "发布后对该课程全部课序与班级",
    );
    expect(screen.queryByText("目标课序")).toBeNull();
  });

  it("未绑定账号的名单行不参与「未交」判定", async () => {
    await openProgress({
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

    // 可判定人数是 1（另一行没绑定账号）→ 未交 1，而不是 2。
    expect(await screen.findByTestId(`notSubmitted-${UUID_A}`)).toHaveTextContent("1");
  });

  it("成绩行到自动翻页上限时说明被截断，不渲染任何计数", async () => {
    stub = installFetchStub(routes({ role: "teacher", gradeNextCursor: "always-more" }));
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
    expect(screen.queryByTestId(`notSubmitted-${UUID_A}`)).toBeNull();
  });
});
