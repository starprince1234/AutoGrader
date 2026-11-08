import { afterEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
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
  enrollmentPage,
  enrollmentPublic,
  gradePage,
  gradeRow,
  questionPublic,
  questionVersionPublic,
  rosterSummary,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn, installSessionAuth } from "../../auth/sessionStore";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import { AssignmentDetailPage } from "./AssignmentDetailPage";

/**
 * 教师作业详情（v0.32.0 课程级受众）。
 *
 * 不变量：
 * 1. 名单/成绩未取全时**不显示「未交」**（显示加载/截断）；
 * 2. 顶部受众人数来自服务端 `roster-summary`，不由已加载页加总；
 * 3. 矩阵来自**全课程**名单（含未绑定行），列显示课序/班级/通知投递状态；
 * 4. 通知面板只展示 B4 的投递事实：`scheduled` 不等于已送达；404 显示"通知未安排"；
 * 5. 「查看答案」带 `Authorization` 读取并以文本转义展示，403 有明确文案。
 */

const COURSE = "00000000-0000-4000-8000-0000000000b1";
const SECTION = "00000000-0000-4000-8000-0000000000b2";
const SECTION_2 = "00000000-0000-4000-8000-0000000000b3";
const CLASS = "00000000-0000-4000-8000-0000000000b4";
const CLASS_2 = "00000000-0000-4000-8000-0000000000b5";
const TEACHER = "00000000-0000-4000-8000-0000000000c1";
const S1 = "00000000-0000-4000-8000-0000000000d1";
const S2 = "00000000-0000-4000-8000-0000000000d2";
const S3 = "00000000-0000-4000-8000-0000000000d3";
const E1 = "00000000-0000-4000-8000-0000000000e1";
const E2 = "00000000-0000-4000-8000-0000000000e2";
const E3 = "00000000-0000-4000-8000-0000000000e3";
const E4 = "00000000-0000-4000-8000-0000000000e4";
const D1 = "00000000-0000-4000-8000-0000000000f1";
const D2 = "00000000-0000-4000-8000-0000000000f2";
const D3 = "00000000-0000-4000-8000-0000000000f3";
const D4 = "00000000-0000-4000-8000-0000000000f4";

const V1 = "00000000-0000-4000-8000-000000000101";
const V2 = "00000000-0000-4000-8000-000000000102";
const Q1 = "00000000-0000-4000-8000-000000000201";
const Q2 = "00000000-0000-4000-8000-000000000202";
const SUB_S1_V1 = "00000000-0000-4000-8000-000000000301";
const SUB_S2_V1 = "00000000-0000-4000-8000-000000000302";
const SUB_S2_V2 = "00000000-0000-4000-8000-000000000303";

const ASSIGNMENT = assignmentPublic({
  assignment_id: UUID_A,
  course_id: COURSE,
  title: "第一次作业",
  question_version_ids: [V1, V2],
  notification_state: "scheduled",
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
    section_id: SECTION_2,
    class_id: CLASS_2,
    class_name: "软件工程2班",
    student_id: S3,
    student_number: "20260003",
    display_name: "王五",
  }),
  enrollmentPublic({
    enrollment_id: E4,
    course_id: COURSE,
    section_id: SECTION_2,
    class_id: CLASS_2,
    class_name: "软件工程2班",
    student_id: null,
    student_number: "20260009",
    display_name: "赵六",
  }),
];

const SUMMARY = rosterSummary({
  course_id: COURSE,
  section_count: 2,
  class_count: 2,
  student_total: 4,
  bound_count: 3,
  unbound_count: 1,
  sections: [
    {
      section_id: SECTION,
      section_code: "01",
      term: "2026F",
      class_count: 1,
      student_total: 2,
      bound_count: 2,
      unbound_count: 0,
      classes: [
        {
          class_id: CLASS,
          class_name: "软件工程1班",
          student_total: 2,
          bound_count: 2,
          unbound_count: 0,
        },
      ],
    },
    {
      section_id: SECTION_2,
      section_code: "02",
      term: "2026F",
      class_count: 1,
      student_total: 2,
      bound_count: 1,
      unbound_count: 1,
      classes: [
        {
          class_id: CLASS_2,
          class_name: "软件工程2班",
          student_total: 2,
          bound_count: 1,
          unbound_count: 1,
        },
      ],
    },
  ],
});

const DELIVERIES = [
  assignmentNotificationDelivery({
    delivery_id: D1,
    student_number: "20260001",
    display_name: "张三",
    section_id: SECTION,
    class_id: CLASS,
    status: "sent",
    attempts: 1,
  }),
  assignmentNotificationDelivery({
    delivery_id: D2,
    student_number: "20260002",
    display_name: "李四",
    section_id: SECTION,
    class_id: CLASS,
    status: "failed",
    reason_code: "smtp_error",
    reason_detail: "SMTP error",
    attempts: 3,
    sent_at: null,
  }),
  assignmentNotificationDelivery({
    delivery_id: D3,
    student_number: "20260003",
    display_name: "王五",
    section_id: SECTION_2,
    class_id: CLASS_2,
    status: "skipped_no_contact",
    reason_code: "no_email",
    attempts: 0,
    sent_at: null,
  }),
  assignmentNotificationDelivery({
    delivery_id: D4,
    student_number: "20260009",
    display_name: "赵六",
    section_id: SECTION_2,
    class_id: CLASS_2,
    status: "skipped_unbound",
    reason_code: "no_account",
    attempts: 0,
    sent_at: null,
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

function baseRoutes(
  options: {
    grades?: unknown[];
    gradeNextCursor?: string | null;
    answer?: () => Response;
    delayedRoster?: boolean;
    notifications?: () => Response;
  } = {},
): StubbedRoute[] {
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
      respond: () => jsonResponse(SUMMARY),
    },
    {
      path: `/api/v1/courses/${COURSE}/enrollments`,
      respond: () => {
        if (!options.delayedRoster) return jsonResponse(enrollmentPage(ROSTER));
        return new Promise<Response>((resolve) => {
          setTimeout(() => resolve(jsonResponse(enrollmentPage(ROSTER))), 5000);
        });
      },
    },
    {
      path: "/api/v1/grades",
      respond: () =>
        jsonResponse(
          gradePage((options.grades ?? []) as never[], options.gradeNextCursor ?? null),
        ),
    },
    {
      path: `/api/v1/courses/${COURSE}/assignments/${UUID_A}/notifications`,
      respond:
        options.notifications ??
        (() =>
          jsonResponse({
            summary: assignmentNotificationSummary({
              assignment_id: UUID_A,
              course_id: COURSE,
              status: "partial",
              recipient_total: 4,
              sent_count: 1,
              pending_count: 0,
              failed_count: 1,
              skipped_unbound_count: 1,
              skipped_no_contact_count: 1,
              skipped_inactive_count: 0,
            }),
            deliveries: {
              items: DELIVERIES,
              next_cursor: null,
            },
          })),
    },
    { path: `/api/v1/questions/${Q1}`, respond: () => jsonResponse(questionPublic({ question_id: Q1, title: "进程与线程" })) },
    { path: `/api/v1/questions/${Q2}`, respond: () => jsonResponse(questionPublic({ question_id: Q2, title: "文件与目录" })) },
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
      path: `/api/v1/submissions/${SUB_S2_V1}/files`,
      respond:
        options.answer ??
        (() =>
          new Response("我的答案内容\n第二行\n", {
            status: 200,
            headers: { "content-type": "text/plain" },
          })),
    },
  ];
}

const COMPLETE_GRADES = [
  gradeRow({
    submission_id: SUB_S1_V1,
    assignment_id: UUID_A,
    question_version_id: V1,
    student_id: S1,
    state: "completed",
    score: 80,
    max_score: 100,
    evaluation_run_id: null,
  }),
  gradeRow({
    submission_id: SUB_S2_V1,
    assignment_id: UUID_A,
    question_version_id: V1,
    student_id: S2,
    state: "completed",
    score: 90,
    max_score: 100,
    evaluation_run_id: "00000000-0000-4000-8000-0000000000aa",
  }),
  gradeRow({
    submission_id: SUB_S2_V2,
    assignment_id: UUID_A,
    question_version_id: V2,
    student_id: S2,
    state: "completed",
    score: 70,
    max_score: 100,
    evaluation_run_id: null,
  }),
];

function renderPage(path = `/assignments/${UUID_A}`) {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const page = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments/$assignmentId",
    component: () => <AssignmentDetailPage assignmentId={UUID_A} />,
  });
  const assignments = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments",
    component: () => <span>assignments target</span>,
  });
  const grades = createRoute({
    getParentRoute: () => rootRoute,
    path: "/grades",
    component: () => <span>grades target</span>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([assignments, page, grades]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}

async function openTeacherPage(options: Parameters<typeof baseRoutes>[0] = {}) {
  stub = installFetchStub(baseRoutes(options));
  // 令牌提供者接进 HTTP 客户端，答案读取才会带上 Authorization（被断言的那条语义）。
  installSessionAuth();
  await signIn("teacher1", "pw");
  const view = renderPage();
  await screen.findByTestId("assignment-summary");
  return view;
}

describe("教师作业详情（v0.32.0 课程级）", () => {
  it("概览写明课程级受众与通知调度状态；顶部人数来自服务端 roster-summary", async () => {
    await openTeacherPage({ grades: COMPLETE_GRADES });

    expect(await screen.findByTestId("assignment-audience-summary")).toHaveTextContent(
      "发布后对该课程全部课序与班级",
    );
    expect(screen.getByTestId("assignment-notification-state")).toHaveTextContent(
      "已写入通知事件",
    );

    await waitFor(() =>
      expect(screen.getByTestId("roster-section-count")).toHaveTextContent("2"),
    );
    expect(screen.getByTestId("roster-class-count")).toHaveTextContent("2");
    expect(screen.getByTestId("roster-student-total")).toHaveTextContent("4");
    expect(screen.getByTestId("roster-bound-count")).toHaveTextContent("3");
    expect(screen.getByTestId("roster-unbound-count")).toHaveTextContent("1");
  });

  it("汇总计数按全课程名单聚合：未绑定行单列且不参与「未交」", async () => {
    await openTeacherPage({ grades: COMPLETE_GRADES });

    await waitFor(() => expect(screen.getByTestId("assignment-counts")).toBeInTheDocument());
    expect(screen.getByTestId("count-all")).toHaveTextContent("4");
    // 张三只交一题 → 部分；李四两题完成 → 已交；王五没交 → 未交；赵六未绑定 → 单独计数。
    expect(screen.getByTestId("count-not-submitted")).toHaveTextContent("1");
    expect(screen.getByTestId("count-partial")).toHaveTextContent("1");
    expect(screen.getByTestId("count-submitted")).toHaveTextContent("1");
    expect(screen.getByTestId("count-unbound")).toHaveTextContent("1");
  });

  it("矩阵包含课序/班级/通知投递状态；未绑定行的通知显示「未注册」", async () => {
    await openTeacherPage({ grades: COMPLETE_GRADES });
    const matrix = await screen.findByTestId("assignment-matrix-table");

    await waitFor(() =>
      expect(within(matrix).getByTestId(`student-status-${E2}`)).toHaveTextContent("已交"),
    );
    const unboundRow = within(matrix).getByTestId(`student-status-${E4}`).closest("tr") as HTMLElement;
    expect(unboundRow).toHaveTextContent("软件工程2班");
    await waitFor(() =>
      expect(within(unboundRow).getByTestId(`student-notification-${E4}`)).toHaveTextContent(
        "未注册",
      ),
    );

    const row = within(matrix).getByTestId(`student-status-${E1}`).closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("软件工程1班");
    await waitFor(() =>
      expect(within(row).getByTestId(`student-notification-${E1}`)).toHaveTextContent("已送达"),
    );
  });

  it("展开学生逐题矩阵：状态、得分、评测链接与查看答案（带 Authorization 的文本读取）", async () => {
    await openTeacherPage({ grades: COMPLETE_GRADES });
    const matrix = await screen.findByTestId("assignment-matrix-table");

    await waitFor(() =>
      expect(within(matrix).getByTestId(`student-status-${E2}`)).toHaveTextContent("已交"),
    );
    await userEvent.setup().click(screen.getByTestId(`expand-student-${E2}`));

    const questionTable = await screen.findByTestId("student-question-table");
    expect(within(questionTable).getAllByRole("row")).toHaveLength(3); // 表头 + 2 题
    expect(within(questionTable).getByTestId("student-question-row-1")).toHaveTextContent(
      "90 / 100",
    );
    expect(within(questionTable).getByTestId("student-run-link-1")).toBeInTheDocument();

    await userEvent.setup().click(screen.getByTestId("view-answer-1"));
    const modal = await screen.findByTestId("answer-modal");
    const answer = await within(modal).findByTestId("answer-content");
    expect(answer).toHaveTextContent("我的答案内容");

    const request = stub?.requests.find(
      (candidate) => candidate.url.pathname === `/api/v1/submissions/${SUB_S2_V1}/files`,
    );
    expect(request?.url.searchParams.get("path")).toBe("1.txt");
    // 关键：不是裸 href，而是携带 Bearer 的请求。
    expect(request?.headers.get("authorization")).toMatch(/^Bearer /);
  });

  it("答案 403 时显示明确的权限文案", async () => {
    await openTeacherPage({
      grades: COMPLETE_GRADES,
      answer: () =>
        new Response(
          JSON.stringify({
            type: "https://autograder.local/problems/403",
            title: "Forbidden",
            status: 403,
            detail: "outside the caller's course scope",
            instance: "/api/v1/submissions/x/files",
          }),
          { status: 403, headers: { "content-type": "application/problem+json" } },
        ),
    });
    await screen.findByTestId("assignment-matrix-table");
    await userEvent.setup().click(screen.getByTestId(`expand-student-${E2}`));
    await userEvent.setup().click(await screen.findByTestId("view-answer-1"));
    expect(await screen.findByTestId("answer-forbidden")).toHaveTextContent(
      "没有权限读取这个提交的答案",
    );
  });

  it("名单未取全时不显示提交计数与「未交」，但服务端受众统计与题目表仍可用", async () => {
    await openTeacherPage({ grades: COMPLETE_GRADES, delayedRoster: true });
    await screen.findByTestId("assignment-questions-table");
    expect(screen.queryByTestId("assignment-counts")).toBeNull();
    expect(screen.queryByTestId("count-not-submitted")).toBeNull();
    expect(screen.queryByTestId("assignment-matrix-table")).toBeNull();
    expect(screen.getByTestId("assignment-matrix-loading")).toBeInTheDocument();
    // 服务端聚合与本地名单无关，仍应显示。
    expect(screen.getByTestId("roster-student-total")).toHaveTextContent("4");
  });

  it("成绩被截断时说明截断且不给任何「未交」结论", async () => {
    await openTeacherPage({ gradeNextCursor: "always-more" });
    const notice = await screen.findByTestId(
      "assignment-summary-truncated",
      {},
      { timeout: 5000 },
    );
    expect(notice).toHaveTextContent(`已达到自动翻页上限（${MAX_AUTO_PAGES} 页）`);
    expect(screen.queryByTestId("count-not-submitted")).toBeNull();
    expect(screen.queryByTestId("assignment-matrix-table")).toBeNull();
  });

  it("通知面板显示投递汇总与逐人状态/原因，不把跳过写成已送达", async () => {
    await openTeacherPage({ grades: COMPLETE_GRADES });
    const panel = await screen.findByTestId("assignment-notifications");

    await waitFor(() =>
      expect(within(panel).getByTestId("notification-sent")).toHaveTextContent("1"),
    );
    expect(within(panel).getByTestId("notification-recipient-total")).toHaveTextContent("4");
    expect(within(panel).getByTestId("notification-failed")).toHaveTextContent("1");
    expect(within(panel).getByTestId("notification-skipped-unbound")).toHaveTextContent("1");
    expect(within(panel).getByTestId("notification-skipped-no-contact")).toHaveTextContent("1");

    const table = within(panel).getByTestId("notification-delivery-table");
    expect(table).toHaveTextContent("SMTP 发送失败");
    expect(table).toHaveTextContent("账号没有邮箱");
    expect(within(table).getByTestId(`delivery-status-${D3}`)).toHaveTextContent("无联系方式");
  });

  it("B4 没有通知记录（404）时显示「通知未安排」，重试调 B3 安排后刷新投递结果", async () => {
    let scheduled = false;
    stub = installFetchStub([
      ...baseRoutes({ grades: COMPLETE_GRADES }),
      {
        method: "POST",
        path: `/api/v1/assignments/${UUID_A}/notifications`,
        respond: () => {
          scheduled = true;
          return jsonResponse(assignmentPublic({ ...ASSIGNMENT, notification_state: "scheduled" }));
        },
      },
    ].map((route) =>
      route.path === `/api/v1/courses/${COURSE}/assignments/${UUID_A}/notifications`
        ? {
            path: route.path,
            respond: () => {
              if (!scheduled) {
                return new Response(
                  JSON.stringify({
                    type: "https://autograder.local/problems/404",
                    title: "Not Found",
                    status: 404,
                    detail: "no notification for this assignment",
                    instance: `/api/v1/courses/${COURSE}/assignments/${UUID_A}/notifications`,
                  }),
                  { status: 404, headers: { "content-type": "application/problem+json" } },
                );
              }
              return jsonResponse({
                summary: assignmentNotificationSummary({
                  assignment_id: UUID_A,
                  course_id: COURSE,
                  status: "pending",
                  recipient_total: 4,
                  sent_count: 0,
                  pending_count: 4,
                  failed_count: 0,
                  skipped_unbound_count: 0,
                  skipped_no_contact_count: 0,
                  skipped_inactive_count: 0,
                }),
                deliveries: { items: DELIVERIES, next_cursor: null },
              });
            },
          }
        : route,
    ));
    installSessionAuth();
    await signIn("teacher1", "pw");
    renderPage();

    expect(await screen.findByTestId("notification-not-scheduled")).toHaveTextContent(
      "通知未安排",
    );
    await userEvent.setup().click(screen.getByTestId("notification-schedule"));

    await waitFor(() => {
      const posts = (stub?.requests ?? []).filter(
        (request) =>
          request.method === "POST" &&
          request.url.pathname === `/api/v1/assignments/${UUID_A}/notifications`,
      );
      expect(posts).toHaveLength(1);
    });
    // 刷新后显示的是投递结果（pending），且调度成功提示不写成"已送达"。
    expect(await screen.findByTestId("notification-summary")).toBeInTheDocument();
    expect(screen.getByTestId("notification-schedule-result")).toHaveTextContent(
      "不代表邮件已送达",
    );
  });
});
