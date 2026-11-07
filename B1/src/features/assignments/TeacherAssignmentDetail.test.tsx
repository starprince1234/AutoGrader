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
  assignmentPublic,
  courseSummary,
  coursePage,
  enrollmentPage,
  enrollmentPublic,
  gradePage,
  gradeRow,
  questionPublic,
  questionVersionPublic,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn, installSessionAuth } from "../../auth/sessionStore";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import { AssignmentDetailPage } from "./AssignmentDetailPage";

/**
 * 教师作业详情（CHG-0032 §7.2）：概览 + 汇总计数 + 题目表 + 逐人逐题矩阵 + 查看答案。
 *
 * 三条不变量：
 * 1. 名单/成绩未取全时**不显示「未交」**（显示加载/截断）；
 * 2. 多课序受众的名单并行拉取（不假设单 section）；
 * 3. 「查看答案」带 `Authorization` 读取并以文本转义展示，403 有明确文案。
 */

const COURSE = "00000000-0000-4000-8000-0000000000b1";
const SECTION = "00000000-0000-4000-8000-0000000000b2";
const SECTION_2 = "00000000-0000-4000-8000-0000000000b3";
const TEACHER = "00000000-0000-4000-8000-0000000000c1";
const S1 = "00000000-0000-4000-8000-0000000000d1";
const S2 = "00000000-0000-4000-8000-0000000000d2";
const S3 = "00000000-0000-4000-8000-0000000000d3";
const E1 = "00000000-0000-4000-8000-0000000000e1";
const E2 = "00000000-0000-4000-8000-0000000000e2";
const E3 = "00000000-0000-4000-8000-0000000000e3";

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
  section_ids: [SECTION, SECTION_2],
  student_ids: [],
  title: "第一次作业",
  question_version_ids: [V1, V2],
});

const ROSTER_SECTION = [
  enrollmentPublic({
    enrollment_id: E1,
    section_id: SECTION,
    student_id: S1,
    student_number: "20260001",
    display_name: "张三",
  }),
  enrollmentPublic({
    enrollment_id: E2,
    section_id: SECTION,
    student_id: S2,
    student_number: "20260002",
    display_name: "李四",
  }),
];
const ROSTER_SECTION_2 = [
  enrollmentPublic({
    enrollment_id: E3,
    section_id: SECTION_2,
    student_id: S3,
    student_number: "20260003",
    display_name: "王五",
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

function baseRoutes(options: {
  grades?: unknown[];
  gradeNextCursor?: string | null;
  answer?: () => Response;
  delayedSecondRoster?: boolean;
} = {}): StubbedRoute[] {
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
      path: "/api/v1/courses",
      respond: () => jsonResponse(coursePage([courseSummary({ course_id: COURSE })])),
    },
    {
      path: `/api/v1/courses/${COURSE}/sections`,
      respond: () =>
        jsonResponse({
          items: [
            { section_id: SECTION, course_id: COURSE, section_code: "01", term: "2026F" },
            { section_id: SECTION_2, course_id: COURSE, section_code: "02", term: "2026F" },
          ],
        }),
    },
    {
      path: `/api/v1/sections/${SECTION}/enrollments`,
      respond: () => jsonResponse(enrollmentPage(ROSTER_SECTION)),
    },
    {
      path: `/api/v1/sections/${SECTION_2}/enrollments`,
      respond: () => {
        if (!options.delayedSecondRoster) return jsonResponse(enrollmentPage(ROSTER_SECTION_2));
        return new Promise<Response>((resolve) => {
          setTimeout(() => resolve(jsonResponse(enrollmentPage(ROSTER_SECTION_2))), 5000);
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
      respond: options.answer ?? (() => new Response("我的答案内容\n第二行\n", { status: 200, headers: { "content-type": "text/plain" } })),
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

describe("教师作业详情（CHG-0032）", () => {
  it("概览展示多课序受众；汇总计数按互斥分类给出六项", async () => {
    await openTeacherPage({ grades: COMPLETE_GRADES });

    const targetSections = await screen.findByTestId("assignment-target-sections");
    await waitFor(() => expect(targetSections).toHaveTextContent("01 · 2026F"));
    expect(targetSections).toHaveTextContent("02 · 2026F");

    await waitFor(() => expect(screen.getByTestId("assignment-counts")).toBeInTheDocument());
    expect(screen.getByTestId("count-all")).toHaveTextContent("3");
    // 张三只交一题 → 部分；李四两题完成 → 已交；王五没交 → 未交。
    expect(screen.getByTestId("count-not-submitted")).toHaveTextContent("1");
    expect(screen.getByTestId("count-partial")).toHaveTextContent("1");
    expect(screen.getByTestId("count-submitted")).toHaveTextContent("1");
    expect(screen.getByTestId("count-evaluating")).toHaveTextContent("0");
    expect(screen.getByTestId("count-failed")).toHaveTextContent("0");
  });

  it("题目表并列显示题号、标题与执行模式（一题一行、独立错误态）", async () => {
    await openTeacherPage({ grades: COMPLETE_GRADES });
    const table = await screen.findByTestId("assignment-questions-table");
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
    expect(table).toHaveTextContent("进程与线程");
    expect(table).toHaveTextContent("文件与目录");
    expect(table).toHaveTextContent("Bash");
  });

  it("展开学生逐题矩阵：状态、得分、评测链接与查看答案（带 Authorization 的文本读取）", async () => {
    await openTeacherPage({ grades: COMPLETE_GRADES });
    const matrix = await screen.findByTestId("assignment-matrix-table");

    // 李四两题都完成 → 已交；展开后逐题可见。
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

  it("名单未取全时不显示计数与「未交」，但题目表仍可用", async () => {
    await openTeacherPage({ grades: COMPLETE_GRADES, delayedSecondRoster: true });
    await screen.findByTestId("assignment-questions-table");
    expect(screen.queryByTestId("assignment-counts")).toBeNull();
    expect(screen.queryByTestId("count-not-submitted")).toBeNull();
    expect(screen.queryByTestId("assignment-matrix-table")).toBeNull();
    expect(screen.getByTestId("assignment-matrix-loading")).toBeInTheDocument();
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

  it("未绑定账号的受众行如实标注，不参与「未交」判定", async () => {
    stub = installFetchStub(
      baseRoutes({
        grades: COMPLETE_GRADES,
      }).map((route) =>
        route.path === `/api/v1/sections/${SECTION}/enrollments`
          ? {
              path: `/api/v1/sections/${SECTION}/enrollments`,
              respond: () =>
                jsonResponse(
                  enrollmentPage([
                    ...ROSTER_SECTION,
                    enrollmentPublic({
                      enrollment_id: "00000000-0000-4000-8000-0000000000e9",
                      section_id: SECTION,
                      student_id: null,
                      student_number: "20260009",
                      display_name: "赵六",
                    }),
                  ]),
                ),
            }
          : route,
      ),
    );
    await signIn("teacher1", "pw");
    renderPage();
    await screen.findByTestId("assignment-matrix-table");    // 人数含未绑定行，但"未交"只数可判定的学生（1 人）。
    await waitFor(() =>
      expect(screen.getByTestId("count-not-submitted")).toHaveTextContent("1"),
    );
    expect(screen.getByTestId("count-unbound")).toHaveTextContent("1");
  });
});
