import { afterEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
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
import { signIn } from "../../auth/sessionStore";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import { AssignmentDetailPage } from "./AssignmentDetailPage";

/**
 * 作业详情页（`/assignments/{assignment_id}`）的角色分流。
 *
 * 这一页的关键变化：**教师的作业详情就是 PDF 的「作业情况（子界面）」**（批改界面），
 * 不再是「概览 + 汇总 + 学生矩阵」那套并列视图。因此这里断言的是"详情路由渲染了批改界面"，
 * 以及旧页面承担的两件事（发布通知投递记录、受众规模）仍然可达。
 *
 * 学生分支仍然进入作业工作区，其行为由 `AssignmentWorkspace.test.tsx` 覆盖；这里只验证
 * 教师视图不会出现学生工作区的入口。
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
const Q1 = "00000000-0000-4000-8000-000000000201";
const SUB_S1_V1 = "00000000-0000-4000-8000-000000000301";

const ASSIGNMENT = assignmentPublic({
  assignment_id: UUID_A,
  course_id: COURSE,
  title: "第一次作业",
  question_version_ids: [V1],
  status: "open",
  due_at: null,
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
  // 未绑定账号：不参与「已交/未交」判定。
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

const SUMMARY = rosterSummary({
  course_id: COURSE,
  section_count: 1,
  class_count: 1,
  student_total: 3,
  bound_count: 2,
  unbound_count: 1,
  sections: [
    {
      section_id: SECTION,
      section_code: "01",
      term: "2026F",
      class_count: 1,
      student_total: 3,
      bound_count: 2,
      unbound_count: 1,
      classes: [
        {
          class_id: CLASS,
          class_name: "软件工程1班",
          student_total: 3,
          bound_count: 2,
          unbound_count: 1,
        },
      ],
    },
  ],
});

const NOTIFICATIONS = {
  summary: assignmentNotificationSummary({
    assignment_id: UUID_A,
    course_id: COURSE,
    status: "partial",
    recipient_total: 3,
    sent_count: 1,
    pending_count: 0,
    failed_count: 1,
    skipped_unbound_count: 1,
    skipped_no_contact_count: 0,
    skipped_inactive_count: 0,
  }),
  deliveries: {
    items: [
      assignmentNotificationDelivery({
        delivery_id: "00000000-0000-4000-8000-0000000000f1",
        student_number: "20260001",
        display_name: "张三",
        section_id: SECTION,
        class_id: CLASS,
        status: "sent",
        attempts: 1,
      }),
      assignmentNotificationDelivery({
        delivery_id: "00000000-0000-4000-8000-0000000000f2",
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
    ],
    next_cursor: null,
  },
};

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

function routes(
  options: {
    grades?: unknown[];
    gradeNextCursor?: string | null;
    answer?: () => Response;
    delayedRoster?: boolean;
    notifications?: () => Response;
    rosterSummaryFails?: boolean;
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
      respond: () =>
        options.rosterSummaryFails
          ? jsonResponse({ detail: "boom" }, 500)
          : jsonResponse(SUMMARY),
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
      // 默认「B4 没有这条通知」：面板必须显示「通知未安排」而不是「已送达」。
      respond:
        options.notifications ??
        (() => jsonResponse({ detail: "no notification", title: "Not Found" }, 404)),
    },
    {
      path: `/api/v1/questions/${Q1}`,
      respond: () =>
        jsonResponse(questionPublic({ question_id: Q1, title: "进程与线程" })),
    },
    {
      path: `/api/v1/question-versions/${V1}`,
      respond: () =>
        jsonResponse(
          questionVersionPublic({ question_version_id: V1, question_id: Q1, version: 1 }),
        ),
    },
    {
      path: `/api/v1/submissions/${SUB_S1_V1}/files`,
      respond:
        options.answer ??
        (() =>
          new Response("进程是资源分配单位\n", {
            status: 200,
            headers: { "content-type": "text/plain" },
          })),
    },
  ];
}

function renderDetail() {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const detail = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments/$assignmentId",
    validateSearch: (search: Record<string, unknown>) => {
      const result: { question?: string; version?: string; student?: string } = {};
      for (const key of ["question", "version", "student"] as const) {
        const value = search[key];
        if (typeof value === "string" && value !== "") result[key] = value;
      }
      return result;
    },
    component: () => {
      const search = detail.useSearch();
      return (
        <AssignmentDetailPage
          assignmentId={UUID_A}
          initialSelectedVersionId={search.version ?? null}
          initialSelectedStudentId={search.student ?? null}
        />
      );
    },
  });
  const grading = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments/$assignmentId/grading",
    component: () => <span>grading target</span>,
  });
  const list = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments",
    component: () => <span>assignments target</span>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([detail, grading, list]),
    history: createMemoryHistory({ initialEntries: [`/assignments/${UUID_A}`] }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}

describe("作业详情页（教师）= 批改界面", () => {
  it("详情路由渲染批改界面：左栏已完成/未完成，旧矩阵视图不再出现", async () => {
    stub = installFetchStub(routes());
    await signIn("teacher1", "pw");
    renderDetail();

    expect(await screen.findByTestId("grading-completed-group")).toBeInTheDocument();
    expect(screen.getByTestId("grading-pending-group")).toBeInTheDocument();
    // 旧页面（概览 + 汇总 + 学生矩阵）已被替换，不能同时存在两套判定 UI。
    expect(screen.queryByTestId("assignment-matrix")).toBeNull();
    expect(screen.queryByTestId("teacher-overview")).toBeNull();
    // 面包屑不自指：详情路由下只显示作业名，不再叠一层「批改界面」。
    expect(screen.getByTestId("grading-breadcrumb-current")).toHaveTextContent("第一次作业");
  });

  it("已完成/未完成按全课程名单聚合，未绑定账号单列且不进分母", async () => {
    stub = installFetchStub(routes());
    await signIn("teacher1", "pw");
    renderDetail();

    // 可判定人数 = 2（王五那行未绑定账号）。
    expect(await screen.findByTestId("grading-completed-group")).toHaveTextContent(
      "已完成 0 / 2",
    );
    expect(screen.getByTestId("grading-pending-group")).toHaveTextContent("未完成 2 / 2");
    expect(screen.getByTestId("grading-unbound")).toHaveTextContent(
      "另有 1 名学生的名单行未绑定账号",
    );
  });

  it("点学生后右栏显示作答内容；答案 403 时给出明确的权限文案", async () => {
    stub = installFetchStub(
      routes({
        grades: [
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
        ],
        answer: () => jsonResponse({ detail: "forbidden" }, 403),
      }),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderDetail();

    await user.click(await screen.findByTestId("grading-toggle-completed"));
    await user.click(await screen.findByTestId(`grading-student-completed-${E1}`));

    expect(await screen.findByTestId("grading-student-window")).toHaveTextContent("张三");
    expect(await screen.findByTestId("answer-forbidden")).toHaveTextContent(
      "没有权限读取这个提交的答案",
    );
    // 左上角返回题干：右栏大窗口回题干模式。
    await user.click(screen.getByTestId("grading-back-to-question"));
    await waitFor(() => expect(screen.queryByTestId("grading-student-window")).toBeNull());
    expect(screen.getByTestId("grading-question-window")).toBeInTheDocument();
  });

  it("名单未取全时不显示任何已完成/未完成数字", async () => {
    stub = installFetchStub(routes({ delayedRoster: true }));
    await signIn("teacher1", "pw");
    renderDetail();

    expect(await screen.findByTestId("grading-incomplete")).toBeInTheDocument();
    expect(screen.queryByTestId("grading-completed-group")).toBeNull();
    expect(screen.getByTestId("grading-processed")).toHaveTextContent("读取中");
  });

  it("成绩被截断时说明截断且不给任何判定", async () => {
    stub = installFetchStub(routes({ gradeNextCursor: "always-more" }));
    await signIn("teacher1", "pw");
    renderDetail();

    const notice = await screen.findByTestId("grading-truncated");
    expect(notice).toHaveTextContent(`已达到自动翻页上限（${MAX_AUTO_PAGES} 页）`);
    expect(screen.queryByTestId("grading-completed-group")).toBeNull();
    expect(screen.queryByTestId("grading-pending-group")).toBeNull();
  });

  it("旧详情页承担的两个能力仍可达：受众规模 + 发布通知投递记录", async () => {
    stub = installFetchStub(
      routes({ notifications: () => jsonResponse(NOTIFICATIONS) }),
    );
    await signIn("teacher1", "pw");
    renderDetail();

    // 折叠区默认收起，但内容在 DOM 中且可见于展开后（details/summary 语义）。
    expect(await screen.findByTestId("grading-notifications-details")).toBeInTheDocument();
    expect(await screen.findByTestId("roster-student-total")).toHaveTextContent("3");
    expect(screen.getByTestId("roster-unbound-count")).toHaveTextContent("1");
    // 投递事实：sent 才是已送达，skipped 不会被写成已送达。
    expect(screen.getByTestId("grading-notifications-details")).toHaveTextContent("已送达");
  });

  it("B4 没有通知记录时显示「通知未安排」，不把它当成已送达", async () => {
    stub = installFetchStub(routes({ rosterSummaryFails: true }));
    await signIn("teacher1", "pw");
    renderDetail();

    expect(await screen.findByTestId("grading-notifications-details")).toBeInTheDocument();
    expect(await screen.findByTestId("assignment-roster-summary-error")).toBeInTheDocument();
    expect(
      await screen.findByTestId("notification-not-scheduled"),
    ).toHaveTextContent("通知未安排");
  });
});
