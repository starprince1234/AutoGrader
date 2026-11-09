import { afterEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Outlet,
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
  assignmentNotificationSummary,
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
import { GradingPage } from "./GradingPage";

/**
 * 批改界面（PDF 的「作业情况（子界面）」）。
 *
 * 不变量：
 * 1. 「已完成 / 未完成」只数**可判定**的名单行，未绑定账号单列；
 * 2. 名单/成绩未取全或截断时**不给任何数字**；
 * 3. 点学生进入右栏大窗口，左上角「返回」回到作业题干；
 * 4. 右栏给出作答内容与「修改分数」入口（老师有权修改）；
 * 5. 作业得分按百分制换算，缺交的题计 0（不会把"只交一题且满分"显示成 100）。
 */

const COURSE = "00000000-0000-4000-8000-0000000000b1";
const SECTION = "00000000-0000-4000-8000-0000000000b2";
const CLASS = "00000000-0000-4000-8000-0000000000b3";
const TEACHER = "00000000-0000-4000-8000-0000000000c1";
const S1 = "00000000-0000-4000-8000-0000000000d1";
const S2 = "00000000-0000-4000-8000-0000000000d2";
const E1 = "00000000-0000-4000-8000-0000000000e1";
const E2 = "00000000-0000-4000-8000-0000000000e2";
const E3 = "00000000-0000-4000-8000-0000000000e3";
const V1 = "00000000-0000-4000-8000-000000000101";
const V2 = "00000000-0000-4000-8000-000000000102";
const Q1 = "00000000-0000-4000-8000-000000000201";
const Q2 = "00000000-0000-4000-8000-000000000202";
const SUB_S1_V1 = "00000000-0000-4000-8000-000000000301";

const ASSIGNMENT = assignmentPublic({
  assignment_id: UUID_A,
  course_id: COURSE,
  title: "第一次作业",
  question_version_ids: [V1, V2],
  status: "open",
  // 没有截止时间：提醒提示语必须写明「未设置截止时间」，而不是留空。
  due_at: null,
});

const ROSTER = [
  enrollmentPublic({
    enrollment_id: E1,
    course_id: COURSE,
    section_id: SECTION,
    class_id: CLASS,
    class_name: "软件工程1班",
    student_id: S1,
    student_number: "20260001",
    display_name: "张三",
  }),
  enrollmentPublic({
    enrollment_id: E2,
    course_id: COURSE,
    section_id: SECTION,
    class_id: CLASS,
    class_name: "软件工程1班",
    student_id: S2,
    student_number: "20260002",
    display_name: "李四",
  }),
  enrollmentPublic({
    enrollment_id: E3,
    course_id: COURSE,
    section_id: SECTION,
    class_id: CLASS,
    class_name: "软件工程1班",
    student_id: null,
    student_number: "20260003",
    display_name: "王五",
  }),
];

// 张三交了两题里的第一题（80/100）；李四一题没交。
const GRADES = [
  gradeRow({
    submission_id: SUB_S1_V1,
    assignment_id: UUID_A,
    question_version_id: V1,
    student_id: S1,
    state: "completed",
    evaluation_run_id: null,
    score: 80,
    max_score: 100,
  }),
];

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

function routes(options: { gradeNextCursor?: string | null } = {}): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: ["teacher"],
            scope: [{ course_id: COURSE, section_id: SECTION }],
            access_token: jwtFor(TEACHER),
          }),
        ),
    },
    { path: `/api/v1/assignments/${UUID_A}`, respond: () => jsonResponse(ASSIGNMENT) },
    {
      path: `/api/v1/courses/${COURSE}/roster-summary`,
      respond: () => jsonResponse({ detail: "not found" }, 404),
    },
    {
      path: `/api/v1/courses/${COURSE}/assignments/${UUID_A}/notifications`,
      respond: () => jsonResponse({ detail: "not found" }, 404),
    },
    {
      path: `/api/v1/courses/${COURSE}/enrollments`,
      respond: () => jsonResponse(enrollmentPage(ROSTER)),
    },
    {
      path: "/api/v1/grades",
      respond: () => jsonResponse(gradePage(GRADES, options.gradeNextCursor ?? null)),
    },
    {
      path: `/api/v1/questions/${Q1}`,
      respond: () =>
        jsonResponse(questionPublic({ question_id: Q1, title: "进程与线程", stem_markdown: "进程与线程的区别？" })),
    },
    {
      path: `/api/v1/questions/${Q2}`,
      respond: () =>
        jsonResponse(questionPublic({ question_id: Q2, title: "文件与目录", stem_markdown: "解释 inode。" })),
    },
    {
      path: `/api/v1/question-versions/${V1}`,
      respond: () =>
        jsonResponse(questionVersionPublic({ question_version_id: V1, question_id: Q1, version: 1 })),
    },
    {
      path: `/api/v1/question-versions/${V2}`,
      respond: () =>
        jsonResponse(questionVersionPublic({ question_version_id: V2, question_id: Q2, version: 1 })),
    },
    {
      path: `/api/v1/submissions/${SUB_S1_V1}/files`,
      respond: () =>
        new Response("进程是资源分配单位\n", {
          status: 200,
          headers: { "content-type": "text/plain" },
        }),
    },
    {
      path: `/api/v1/submissions/${SUB_S1_V1}/manual-grade`,
      respond: () => new Response(null, { status: 204 }),
    },
    {
      // 人工评分抽屉会顺带读一次来信内容；这不是邮件提交，按契约返回 404。
      path: `/api/v1/submissions/${SUB_S1_V1}/intake-message`,
      respond: () => jsonResponse({ detail: "not found" }, 404),
    },
    {
      path: "/api/v1/courses",
      respond: () =>
        jsonResponse(coursePage([courseSummary({ course_id: COURSE, title: "操作系统" })])),
    },
    {
      method: "POST",
      path: `/api/v1/courses/${COURSE}/assignments/${UUID_A}/notifications/remind`,
      respond: () =>
        jsonResponse(
          assignmentNotificationSummary({
            assignment_id: UUID_A,
            course_id: COURSE,
            status: "pending",
            recipient_total: 1,
            sent_count: 0,
            pending_count: 1,
            failed_count: 0,
            skipped_unbound_count: 0,
            skipped_no_contact_count: 0,
            skipped_inactive_count: 0,
          }),
        ),
    },
  ];
}

function renderGrading() {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const gradingRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments/$assignmentId/grading",
    validateSearch: (search: Record<string, unknown>) => ({
      version: typeof search.version === "string" ? search.version : undefined,
      student: typeof search.student === "string" ? search.student : undefined,
    }),
    component: () => (
      <GradingPage
        assignmentId={UUID_A}
        initialVersionId={V1}
        initialStudentId={null}
      />
    ),
  });
  const assignmentsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments",
    component: () => <span>assignments target</span>,
  });
  const detailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments/$assignmentId",
    component: () => <span>detail target</span>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([gradingRoute, assignmentsRoute, detailRoute]),
    history: createMemoryHistory({
      initialEntries: [`/assignments/${UUID_A}/grading`],
    }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}

describe("批改界面（作业情况子界面）", () => {
  it("已完成/未完成只数可判定人数，未绑定账号单列", async () => {
    stub = installFetchStub(routes());
    await signIn("teacher1", "pw");
    renderGrading();

    // 可判定人数 = 2（王五那行未绑定账号）→ 已完成 1 / 2、未完成 1 / 2。
    expect(await screen.findByTestId("grading-completed-group")).toHaveTextContent("已完成 1 / 2");
    expect(screen.getByTestId("grading-pending-group")).toHaveTextContent("未完成 1 / 2");
    expect(screen.getByTestId("grading-unbound")).toHaveTextContent(
      "另有 1 名学生的名单行未绑定账号",
    );
    // 顶部「已处理作业数量」与左栏同一口径。
    expect(screen.getByTestId("grading-processed")).toHaveTextContent("1 / 2");
  });

  it("点学生进入右栏大窗口：作答内容 + 百分制得分 + 返回题干 + 改分入口", async () => {
    stub = installFetchStub(routes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderGrading();

    await user.click(await screen.findByTestId("grading-toggle-completed"));
    await user.click(await screen.findByTestId(`grading-student-completed-${E1}`));

    const window = await screen.findByTestId("grading-student-window");
    expect(window).toHaveTextContent("张三");

    // 答案文件带 Authorization 读取后以文本呈现。
    expect(await screen.findByTestId("answer-content")).toHaveTextContent("进程是资源分配单位");

    // 百分制：只完成 1/2 题且 80/100 → (0.8 / 2) * 100 = 40，而不是 80。
    expect(screen.getByTestId("grading-student-score")).toHaveTextContent("40");

    // 左上角返回 → 回到作业题干（作业详情）。
    await user.click(screen.getByTestId("grading-back-to-question"));
    await waitFor(() =>
      expect(screen.queryByTestId("grading-student-window")).toBeNull(),
    );
    expect(screen.getByTestId("grading-question-window")).toHaveTextContent("进程与线程");

    // 再点学生 →「修改分数」打开人工评分抽屉（老师有权修改）。
    await user.click(screen.getByTestId(`grading-student-completed-${E1}`));
    await user.click(await screen.findByTestId("grading-manual-grade"));
    expect(await screen.findByTestId("manual-grade-drawer")).toBeInTheDocument();
  });

  it("「返回作业列表」在题干框架内、题干上方", async () => {
    stub = installFetchStub(routes());
    await signIn("teacher1", "pw");
    renderGrading();

    const stem = await screen.findByTestId("grading-question-window");
    const back = screen.getByTestId("grading-back-to-list");
    expect(stem).toContainElement(back);
    expect(back).toHaveTextContent("返回作业列表");
    // 题干标题在按钮下方（DOM 顺序），不是页头里的悬浮按钮。
    const title = screen.getByTestId("grading-question-title");
    expect(
      back.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("成绩行被截断时不给任何已完成/未完成数字", async () => {
    stub = installFetchStub(routes({ gradeNextCursor: "always-more" }));
    await signIn("teacher1", "pw");
    renderGrading();

    expect(await screen.findByTestId("grading-truncated")).toBeInTheDocument();
    expect(screen.queryByTestId("grading-completed-group")).toBeNull();
    expect(screen.queryByTestId("grading-pending-group")).toBeNull();
    expect(screen.getByTestId("grading-processed")).toHaveTextContent("读取中");
  });
});

describe("批改界面 · 邮件提醒（自动提示语 / 一键提醒）", () => {
  it("提示语只读、收件人只来自未完成，且成功提示区分「已安排」与「已送达」", async () => {
    stub = installFetchStub(routes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderGrading();

    const prompt = await screen.findByTestId("grading-remind-prompt");
    // 位置：邮件提醒在右栏题干下方（与题干面板同一列），不单独占左栏一栏。
    const stem = screen.getByTestId("grading-question-window");
    const reminder = screen.getByTestId("grading-reminder");
    expect(stem.parentElement).toContainElement(reminder);
    expect(screen.getByTestId("grading-completed-group")).not.toContainElement(reminder);
    // 长提示语必须可换行：用带样式的块而不是不换行的 <pre>（否则会把面板撑破）。
    expect(prompt.querySelector("pre")).toBeNull();
    expect(prompt.querySelector(".grading-remind__prompt")).not.toBeNull();

    expect(prompt).toHaveTextContent("你还没有提交课程");
    expect(prompt).toHaveTextContent("第一次作业");
    // 没有截止时间时提示语要写明，而不是留空。
    expect(prompt).toHaveTextContent("未设置截止时间");

    // 收件人 = 未完成且已绑定账号：李四；未绑定账号的王五被排除并单独说明。
    expect(screen.getByTestId("grading-remind-recipients")).toHaveTextContent("1 名学生");
    expect(screen.getByTestId("grading-remind-recipients")).toHaveTextContent("1 名未绑定账号");

    await user.type(screen.getByTestId("grading-remind-note"), "下周一上课前交");
    await user.click(screen.getByTestId("grading-remind-send"));

    await waitFor(() => {
      const call = stub!.requests.find(
        (item) =>
          item.method === "POST" &&
          item.url.pathname ===
            `/api/v1/courses/${COURSE}/assignments/${UUID_A}/notifications/remind`,
      );
      expect(call).toBeTruthy();
      const body = call!.body as {
        title: string;
        enrollment_ids: string[];
        note?: string;
      };
      expect(body.title).toBe("第一次作业");
      // 只提醒未完成且已绑定账号的学生：E2（李四）。未绑定的 E3 不能出现在收件人里。
      expect(body.enrollment_ids).toEqual([E2]);
      expect(body.note).toBe("下周一上课前交");
    });

    // 「已安排」不等于「已送达」：两者分别显示。
    expect(await screen.findByTestId("grading-remind-scheduled")).toHaveTextContent("已安排提醒");
    expect(screen.getByTestId("grading-remind-counts")).toHaveTextContent("待发送 1");
  });
});
