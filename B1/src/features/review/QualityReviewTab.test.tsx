import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  coursePage,
  courseSummary,
  evaluationQualitySummary,
  questionPublic,
  questionVersionPublic,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { resetLoggerForTests } from "../../logging/logger";
import { QualityReviewTab } from "./QualityReviewTab";
import type { ReviewSearch } from "./search";

/**
 * P13-B 题目质量标签。
 *
 * 三个最容易被写错的点：
 * 1. 没有/非法 `question_version_id` 时**不发任何请求**；B3 对版本 403 时**不**向 B2 拉聚合；
 * 2. 三态安全计数与 `unmeasured_runs` 分开、`infrastructure_error` 单列、规则零命中保留；
 * 3. 时间筛选校验在客户端拦下（结束时间必须晚于开始时间），并且筛选值以 RFC 3339 发送。
 */

let stub: FetchStub | null = null;

beforeEach(() => {
  resetLoggerForTests();
});

afterEach(() => {
  stub?.restore();
  stub = null;
  vi.restoreAllMocks();
});

const SESSION = "/api/v1/auth/session";
const COURSES = "/api/v1/courses";
const VERSION = `/api/v1/question-versions/${UUID_C}`;
const QUESTION = `/api/v1/questions/${UUID_A}`;
const SUMMARY = "/api/v1/evaluation-runs/quality-summary";

function routesFor(overrides?: {
  versionRespond?: () => Response;
  summaryRespond?: () => Response;
  courses?: ReturnType<typeof courseSummary>[];
}): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: SESSION,
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: ["teacher"],
            scope: [{ course_id: UUID_A, section_id: UUID_B }],
          }),
        ),
    },
    {
      method: "GET",
      path: COURSES,
      respond: () => jsonResponse(coursePage(overrides?.courses ?? [courseSummary()])),
    },
    {
      method: "GET",
      path: "/api/v1/questions",
      respond: () =>
        jsonResponse({
          items: [
            questionPublic({
              course_id: UUID_A,
              versions: [
                {
                  question_version_id: UUID_C,
                  version: 3,
                  status: "published",
                  published_at: "2026-09-15T07:20:00.000Z",
                },
              ],
            }),
          ],
          next_cursor: null,
        }),
    },
    {
      method: "GET",
      path: VERSION,
      respond: () => overrides?.versionRespond?.() ?? jsonResponse(questionVersionPublic()),
    },
    { method: "GET", path: QUESTION, respond: () => jsonResponse(questionPublic()) },
    {
      method: "GET",
      path: SUMMARY,
      respond: () => overrides?.summaryRespond?.() ?? jsonResponse(evaluationQualitySummary()),
    },
  ];
}

function Harness({ initial }: { initial: ReviewSearch }) {
  const [search, setSearch] = useState(initial);
  return <QualityReviewTab search={search} onChange={setSearch} />;
}

/** 质量页底部有到 P07 的 `Link`：渲染真实 router 上下文，不用假 href。 */
function renderQuality(initial: ReviewSearch) {
  // RouterProvider 只接受 router（不接受 children）：把 Harness 挂在根路由上。
  const rootRoute = createRootRoute({ component: () => <Harness initial={initial} /> });
  const questionsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions/$questionId",
  });
  const reviewRoute = createRoute({ getParentRoute: () => rootRoute, path: "/review" });
  const router = createRouter({
    routeTree: rootRoute.addChildren([questionsRoute, reviewRoute]),
    history: createMemoryHistory({ initialEntries: ["/review"] }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}

const QUALITY_PATH: ReviewSearch = {
  tab: "quality",
  course_id: UUID_A,
  question_version_id: UUID_C,
};

describe("P13-B 题目质量标签", () => {
  it("深链版本：自动预填课程/版本并直接展示统计", async () => {
    stub = installFetchStub(routesFor());
    await signIn("teacher1", "pw");
    renderQuality(QUALITY_PATH);

    expect(await screen.findByText("共 42 次评测")).toBeInTheDocument();
    expect(screen.getByText("6 次未完成")).toBeInTheDocument();
    expect(screen.getByText("1 次发现安全风险")).toBeInTheDocument();
    expect(
      screen.getByText("其中 5 次是系统/运行环境原因，不是学生代码问题。"),
    ).toBeInTheDocument();
    expect(screen.getByText("输出结果不一致")).toBeInTheDocument();
    // 只展示老师需要处理的项：零命中的规则不再占一行。
    expect(screen.queryByText("STAT-PY-013")).toBeNull();
    // 深链把课程/版本填进下拉，老师不用再手选。
    expect(screen.getByTestId("review-quality-course")).toHaveValue(UUID_A);
    expect(screen.getByTestId("review-quality-version")).toHaveValue(UUID_C);

    const summaryRequest = stub!.requests.find(
      (item) => item.method === "GET" && item.url.pathname === SUMMARY,
    );
    expect(summaryRequest?.url.searchParams.get("question_version_id")).toBe(UUID_C);
  });

  it("没有版本时不发任何 B3/B2 请求，只提示从题目详情选择", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: SESSION,
        respond: () =>
          jsonResponse(
            sessionCreated({
              roles: ["teacher"],
              scope: [{ course_id: UUID_A, section_id: UUID_B }],
            }),
          ),
      },
      { method: "GET", path: COURSES, respond: () => jsonResponse(coursePage([courseSummary()])) },
    ]);
    await signIn("teacher1", "pw");
    renderQuality({ tab: "quality" });

    expect(await screen.findByTestId("review-quality-no-version")).toHaveTextContent(
      "请选择题目版本。",
    );
    expect(
      stub!.requests.some(
        (item) =>
          item.url.pathname === VERSION ||
          item.url.pathname === SUMMARY ||
          item.url.pathname === QUESTION,
      ),
    ).toBe(false);
  });

  it("版本格式非法时不发请求，并指出格式问题", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: SESSION,
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"], scope: [] })),
      },
      { method: "GET", path: COURSES, respond: () => jsonResponse(coursePage([courseSummary()])) },
    ]);
    await signIn("teacher1", "pw");
    renderQuality({ tab: "quality", question_version_id: "not-a-uuid" });

    expect(await screen.findByTestId("review-quality-invalid-version")).toHaveTextContent(
      "题目版本格式不正确。",
    );
    expect(stub!.requests.some((item) => item.url.pathname === SUMMARY)).toBe(false);
  });

  it("B3 拒绝版本（403）时显示权限拒绝，且不向 B2 拉聚合", async () => {
    stub = installFetchStub(
      routesFor({
        versionRespond: () =>
          problemResponse({
            status: 403,
            title: "Forbidden",
            detail: "course scope does not cover this question version",
          }),
      }),
    );
    await signIn("teacher1", "pw");
    renderQuality(QUALITY_PATH);

    expect(await screen.findByTestId("review-quality-forbidden")).toHaveTextContent(
      "你没有查看这个题目版本的权限。",
    );
    expect(stub!.requests.some((item) => item.url.pathname === SUMMARY)).toBe(false);
  });

  it("B3 版本不存在（404）时说明不可见，不发聚合请求", async () => {
    stub = installFetchStub(
      routesFor({
        versionRespond: () =>
          problemResponse({ status: 404, title: "Not Found", detail: "no such version" }),
      }),
    );
    await signIn("teacher1", "pw");
    renderQuality(QUALITY_PATH);

    expect(await screen.findByTestId("review-quality-version-not-found")).toHaveTextContent(
      "题目版本不存在或不可见。",
    );
    expect(stub!.requests.some((item) => item.url.pathname === SUMMARY)).toBe(false);
  });

  it("attempt_count = 0 只说明没有评测记录，不给健康结论", async () => {
    stub = installFetchStub(
      routesFor({
        summaryRespond: () =>
          jsonResponse(
            evaluationQualitySummary({
              attempt_count: 0,
              case_failure_rates: [],
              failure_categories: [],
              unmeasured_runs: 0,
              security_found: 0,
              security_not_found: 0,
              security_not_checked: 0,
            }),
          ),
      }),
    );
    await signIn("teacher1", "pw");
    renderQuality(QUALITY_PATH);

    expect(await screen.findByTestId("review-quality-empty")).toHaveTextContent(
      "此版本在所选时间范围内没有评测记录。",
    );
  });

  it("没有失败用例或代码问题时，不堆空表", async () => {
    stub = installFetchStub(
      routesFor({
        summaryRespond: () =>
          jsonResponse(
            evaluationQualitySummary({
              attempt_count: 3,
              case_failure_rates: [{ ordinal: 0, attempts: 3, failures: 0 }],
              failure_categories: [],
              static_rule_hits: [],
            }),
          ),
      }),
    );
    await signIn("teacher1", "pw");
    renderQuality(QUALITY_PATH);

    expect(await screen.findByText("共 3 次评测")).toBeInTheDocument();
    expect(screen.getByTestId("review-quality-no-case-failures")).toHaveTextContent(
      "这次统计里没有失败的测试用例。",
    );
    expect(screen.queryByTestId("review-quality-rules")).toBeNull();
    expect(screen.queryByTestId("review-quality-categories")).toBeNull();
  });

  it("聚合读取失败：保留筛选并给出可重试的错误（含 request_id）", async () => {
    stub = installFetchStub(
      routesFor({
        summaryRespond: () =>
          problemResponse({ status: 503, title: "Service Unavailable", detail: "database down" }),
      }),
    );
    await signIn("teacher1", "pw");
    renderQuality(QUALITY_PATH);

    expect(await screen.findByTestId("review-quality-unavailable")).toHaveTextContent(
      "质量数据暂时无法读取，请重试。",
    );
    expect(screen.getByTestId("review-quality-error")).toHaveTextContent("请求 ID");
    // 错误态不是空态：不显示"没有评测记录"。
    expect(screen.queryByTestId("review-quality-empty")).toBeNull();
  });

  it("结束时间早于开始时间：就地报错且不发第二次查询", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(routesFor());
    await signIn("teacher1", "pw");
    renderQuality(QUALITY_PATH);
    await screen.findByText("共 42 次评测");

    // datetime-local 只接受本地无时区形式，用 change 直接设值（逐字符输入没有意义）。
    fireEvent.change(screen.getByTestId("review-quality-from"), {
      target: { value: "2026-09-15T12:00" },
    });
    fireEvent.change(screen.getByTestId("review-quality-to"), {
      target: { value: "2026-09-15T10:00" },
    });
    const before = stub!.requests.filter((item) => item.url.pathname === SUMMARY).length;
    await user.click(screen.getByTestId("review-quality-apply"));

    expect(screen.getByText("结束时间必须晚于开始时间。")).toBeInTheDocument();
    expect(stub!.requests.filter((item) => item.url.pathname === SUMMARY)).toHaveLength(before);
  });

  it("时间范围以 RFC 3339 发送；清除时间后重新查询全范围", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(routesFor());
    await signIn("teacher1", "pw");
    renderQuality(QUALITY_PATH);
    await screen.findByText("共 42 次评测");

    fireEvent.change(screen.getByTestId("review-quality-from"), {
      target: { value: "2026-09-15T10:00" },
    });
    fireEvent.change(screen.getByTestId("review-quality-to"), {
      target: { value: "2026-09-15T11:00" },
    });
    await user.click(screen.getByTestId("review-quality-apply"));

    await waitFor(() => {
      const requests = stub!.requests.filter((item) => item.url.pathname === SUMMARY);
      const latest = requests.at(-1);
      const from = latest?.url.searchParams.get("from");
      const to = latest?.url.searchParams.get("to");
      expect(from).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/);
      expect(to).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/);
    });

    await user.click(await screen.findByTestId("review-quality-clear-time"));
    await waitFor(() => {
      const requests = stub!.requests.filter((item) => item.url.pathname === SUMMARY);
      const latest = requests.at(-1);
      expect(latest?.url.searchParams.get("from")).toBeNull();
      expect(latest?.url.searchParams.get("to")).toBeNull();
    });
  });

  it("课程下拉只展示令牌作用域内的课程", async () => {
    const other = courseSummary({ course_id: UUID_C, course_code: "OTHER-1", title: "别的课" });
    stub = installFetchStub(routesFor({ courses: [courseSummary(), other] }));
    await signIn("teacher1", "pw");
    renderQuality({ tab: "quality" });

    const select = await screen.findByTestId("review-quality-course");
    await waitFor(() => {
      const labels = Array.from(select.querySelectorAll("option")).map(
        (option) => option.textContent ?? "",
      );
      expect(labels.some((label) => label.includes("OS-2026"))).toBe(true);
      expect(labels.some((label) => label.includes("OTHER-1"))).toBe(false);
    });
  });
});
