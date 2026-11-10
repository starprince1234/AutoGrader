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
  assignmentNotificationDelivery,
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
 * 1. 「已完成 / 未完成」覆盖**全班名单**（含未绑定账号的学生），分母就是全班人数；
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
const UUID_NOTIFICATION = "00000000-0000-4000-8000-000000000401";
const UUID_DELIVERY_FAILED = "00000000-0000-4000-8000-000000000402";
const UUID_DELIVERY_UNBOUND = "00000000-0000-4000-8000-000000000403";

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
      // 公告没有记录（404）；提醒那份返回逐人投递，用于浮窗里的「投递明细」。
      respond: ({ url }) => {
        const kind = url.searchParams.get("kind") ?? "publication";
        if (kind !== "reminder") {
          return jsonResponse({ detail: "not found" }, 404);
        }
        return jsonResponse({
          summary: assignmentNotificationSummary({
            notification_id: UUID_NOTIFICATION,
            kind: "reminder",
            recipient_total: 2,
            sent_count: 0,
            pending_count: 1,
            failed_count: 1,
            skipped_unbound_count: 1,
            status: "partial",
          }),
          deliveries: {
            items: [
              assignmentNotificationDelivery({
                delivery_id: UUID_DELIVERY_FAILED,
                student_number: "20260002",
                display_name: "李四",
                status: "failed",
                reason_code: "smtp_error",
                reason_detail: "SMTP 发送失败(已记录失败, 稍后按退避重试; 教师也可手动重试)",
              }),
              assignmentNotificationDelivery({
                delivery_id: UUID_DELIVERY_UNBOUND,
                student_number: "20260003",
                display_name: "王五",
                status: "skipped_unbound",
                reason_code: "no_account",
                reason_detail: "学生尚未注册账号, 未发送",
                user_id: null,
              }),
            ],
            next_cursor: null,
          },
        });
      },
    },
    {
      // 重试：只重置失败行，返回更新后的 summary。
      method: "POST",
      path: `/api/v1/courses/${COURSE}/assignments/${UUID_A}/notifications/retry`,
      respond: () =>
        jsonResponse(
          assignmentNotificationSummary({
            notification_id: UUID_NOTIFICATION,
            kind: "reminder",
            recipient_total: 2,
            sent_count: 0,
            pending_count: 2,
            failed_count: 0,
            skipped_unbound_count: 0,
            status: "pending",
          }),
        ),
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
      // GET = 当前人工评分（204 表示还没评过）；POST = 保存教师分。
      path: `/api/v1/submissions/${SUB_S1_V1}/manual-grade`,
      respond: ({ request }) =>
        request.method === "POST"
          ? jsonResponse({
              submission_id: SUB_S1_V1,
              score: 88,
              score_source: "manual",
              grade_status: "graded",
              graded_at: "2026-11-10T00:00:00.000Z",
              updated_at: "2026-11-10T00:00:00.000Z",
              revision: 2,
            })
          : new Response(null, { status: 204 }),
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
            status: "partial",
            // 收件人 = 两名未完成学生；其中未绑定账号的那位会被服务端记为 skipped。
            recipient_total: 2,
            sent_count: 0,
            pending_count: 1,
            failed_count: 0,
            skipped_unbound_count: 1,
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
  it("已完成/未完成覆盖全班名单：未绑定账号的学生也在未完成里", async () => {
    stub = installFetchStub(routes());
    await signIn("teacher1", "pw");
    renderGrading();

    // 全班 3 人（张三交了一题、李四没交、王五未绑定账号）→ 已完成 1 / 3、未完成 2 / 3。
    expect(await screen.findByTestId("grading-completed-group")).toHaveTextContent("已完成 1 / 3");
    expect(screen.getByTestId("grading-pending-group")).toHaveTextContent("未完成 2 / 3");
    // 不再有"另有 N 名未绑定账号"的旁注：他们就在未完成名单里。
    expect(screen.queryByTestId("grading-unbound")).toBeNull();

    // 未绑定账号的行照样列出，但不可点（没有账号就没有作答可看）。
    // 「未完成」分组默认展开，这里不再点折叠按钮（点了反而会收起）。
    const unboundRow = await screen.findByTestId(`grading-student-pending-${E3}`);
    expect(unboundRow.tagName).toBe("DIV");
    expect(unboundRow).toHaveTextContent("未绑定账号");
    // 名单是"固定高度 + 内部滚动"的区域：可聚焦，键盘能滚动查看全班。
    const pendingList = screen.getByTestId("grading-pending-list");
    expect(pendingList).toHaveAttribute("tabindex", "0");
    expect(pendingList).toHaveAttribute("aria-label", expect.stringContaining("未完成"));
    // 顶部「已处理作业数量」与左栏同一口径。
    expect(screen.getByTestId("grading-processed")).toHaveTextContent("1 / 3");
  });

  it("点学生进入右栏大窗口：作答内容 + 评分输入框（默认 AI 分）", async () => {
    stub = installFetchStub(routes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderGrading();

    await user.click(await screen.findByTestId("grading-toggle-completed"));
    await user.click(await screen.findByTestId(`grading-student-completed-${E1}`));

    const window = await screen.findByTestId("grading-student-window");
    expect(window).toHaveTextContent("张三");

    // 选中学生后直接看作答详情：不重复渲染题干；标题行右侧是「返回作业题干」。
    expect(screen.queryByTestId("grading-student-detail")).toBeNull();
    expect(screen.queryByTestId("grading-student-stem")).toBeNull();
    expect(screen.getByTestId("grading-answer")).toBeInTheDocument();
    expect(window.querySelector(".panel__head")).toContainElement(
      screen.getByTestId("grading-back-to-question"),
    );

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

    // 再点学生：评分是一个输入框，默认就是 AI 分；没有「修改分数」按钮、也没有抽屉。
    await user.click(screen.getByTestId(`grading-student-completed-${E1}`));
    const scoreInput = (await screen.findByTestId("grading-score-input")) as HTMLInputElement;
    expect(scoreInput.value).toBe("80");
    expect(screen.queryByTestId("grading-manual-grade")).toBeNull();
    expect(screen.queryByTestId("manual-grade-drawer")).toBeNull();
    expect(screen.getByTestId("grading-score-hint")).toHaveTextContent("不改就按它");
  });

  it("评分输入：改数字后失焦即按教师分保存；非法输入不发请求", async () => {
    stub = installFetchStub(routes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderGrading();

    await user.click(await screen.findByTestId("grading-toggle-completed"));
    await user.click(await screen.findByTestId(`grading-student-completed-${E1}`));
    const input = (await screen.findByTestId("grading-score-input")) as HTMLInputElement;

    // 1) 非法值（超过 100）：只提示，不发请求。
    await user.clear(input);
    await user.type(input, "120");
    await user.tab();
    expect(await screen.findByTestId("grading-score-feedback")).toHaveTextContent(
      "0–100 的数字",
    );
    expect(
      stub!.requests.filter(
        (item) =>
          item.method === "POST" &&
          item.url.pathname === `/api/v1/submissions/${SUB_S1_V1}/manual-grade`,
      ),
    ).toHaveLength(0);

    // 2) 合规值：保存为教师分，请求体带分数与乐观锁版本。
    await user.clear(input);
    await user.type(input, "88");
    await user.keyboard("{Enter}");
    await waitFor(() => {
      const call = stub!.requests.find(
        (item) =>
          item.method === "POST" &&
          item.url.pathname === `/api/v1/submissions/${SUB_S1_V1}/manual-grade`,
      );
      expect(call).toBeTruthy();
      const body = call!.body as { score: number; expected_updated_at: string };
      expect(body.score).toBe(88);
      expect(typeof body.expected_updated_at).toBe("string");
    });
    expect(await screen.findByTestId("grading-score-feedback")).toHaveTextContent("已保存");
  });

  it("题干视图标题行右侧是「返回作业列表」；学生视图同一位置换成「返回作业题干」", async () => {
    stub = installFetchStub(routes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderGrading();

    // 题干视图：按钮在标题行右侧（`.panel__actions`），与题干标题平行。
    const stem = await screen.findByTestId("grading-question-window");
    const backToList = screen.getByTestId("grading-back-to-list");
    expect(backToList).toHaveTextContent("返回作业列表");
    expect(stem.querySelector(".panel__head")).toContainElement(backToList);
    expect(backToList.closest(".panel__actions")).not.toBeNull();
    expect(backToList).toHaveAttribute("href", "/assignments");

    // 学生视图：同一个位置是「返回作业题干」，不再出现「返回作业列表」。
    await user.click(screen.getByTestId("grading-toggle-completed"));
    await user.click(await screen.findByTestId(`grading-student-completed-${E1}`));
    const window = await screen.findByTestId("grading-student-window");
    expect(screen.queryByTestId("grading-back-to-list")).toBeNull();
    expect(window.querySelector(".panel__actions")).toContainElement(
      screen.getByTestId("grading-back-to-question"),
    );
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

    // 浮窗默认不出现：题干下方不再常驻提醒方框。
    await screen.findByTestId("grading-question-window");
    expect(screen.queryByTestId("grading-reminder")).toBeNull();

    // 触发点是「未完成」标题行右侧的「一键提醒」。
    const openButton = screen.getByTestId("grading-remind-open");
    expect(openButton).toHaveTextContent("一键提醒");
    expect(openButton.closest(".grading-group__head")).not.toBeNull();
    await user.click(openButton);

    const prompt = await screen.findByTestId("grading-remind-prompt");
    // 位置：浮窗（不是嵌在页面里的方框）。
    const reminder = screen.getByTestId("grading-reminder");
    expect(reminder).toHaveAttribute("role", "dialog");
    expect(screen.getByTestId("grading-question-window")).not.toContainElement(reminder);
    // 长提示语必须可换行：用带样式的块而不是不换行的 <pre>（否则会把浮窗撑破）。
    expect(prompt.querySelector("pre")).toBeNull();
    expect(prompt.querySelector(".grading-remind__prompt")).not.toBeNull();

    expect(prompt).toHaveTextContent("你还没有提交课程");
    expect(prompt).toHaveTextContent("第一次作业");
    // 没有截止时间时提示语要写明，而不是留空。
    expect(prompt).toHaveTextContent("未设置截止时间");

    // 收件人 = 全部未完成学生（李四 + 未绑定账号的王五）：不再把未绑定的人排除掉。
    expect(screen.getByTestId("grading-remind-recipients")).toHaveTextContent("2 名学生");

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
      // 两名未完成学生都提交为收件人：E2（李四）与 E3（王五，未绑定账号）。
      expect(body.enrollment_ids).toEqual([E2, E3]);
      expect(body.note).toBe("下周一上课前交");
    });

    // 「已安排」不等于「已送达」：两者分别显示，并逐条说明未成功的原因。
    expect(await screen.findByTestId("grading-remind-scheduled")).toHaveTextContent("已安排提醒");
    expect(screen.getByTestId("grading-remind-counts")).toHaveTextContent("待发送 1");
    expect(screen.getByTestId("grading-remind-skipped-unbound")).toHaveTextContent(
      "还没有绑定账号",
    );

    // 投递明细：逐人列出未送达的人与原因，并且只读 kind=reminder 那一份。
    const detail = await screen.findByTestId("grading-remind-delivery-list");
    expect(detail).toHaveTextContent("李四");
    expect(detail).toHaveTextContent("SMTP 发送失败");
    expect(detail).toHaveTextContent("王五");
    expect(detail).toHaveTextContent("未绑定账号");

    // 重试只针对提醒这一批：请求必须带 kind=reminder。
    await user.click(screen.getByTestId("grading-remind-retry"));
    await waitFor(() => {
      const call = stub!.requests.find(
        (item) =>
          item.method === "POST" &&
          item.url.pathname ===
            `/api/v1/courses/${COURSE}/assignments/${UUID_A}/notifications/retry`,
      );
      expect(call).toBeTruthy();
      expect(call!.url.searchParams.get("kind")).toBe("reminder");
    });

    // 关闭后回到页面，随时随地可以再打开。
    await user.click(screen.getByTestId("grading-remind-close"));
    await waitFor(() => expect(screen.queryByTestId("grading-reminder")).toBeNull());
    await user.click(screen.getByTestId("grading-remind-open"));
    expect(await screen.findByTestId("grading-reminder")).toBeInTheDocument();
  });
});
