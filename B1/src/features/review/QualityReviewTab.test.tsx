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
  UUID_D,
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
import { DEMO_COURSE_ID, DEMO_SECTION_ID, DEMO_VERSION_ID, DEMO_VERSION_V2_ID } from "./demoData";
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
  /** 版本下拉的可选项；默认只有 UUID_C 一个。 */
  versions?: { question_version_id: string; version: number; published_at: string }[];
}): StubbedRoute[] {
  const versions = overrides?.versions ?? [
    {
      question_version_id: UUID_C,
      version: 3,
      status: "published" as const,
      published_at: "2026-09-15T07:20:00.000Z",
    },
  ];
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
              versions: versions.map((item) => ({ ...item, status: "published" as const })),
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
  it("深链版本（从题目详情进来）：只显示这道题，直接出统计，没有课程/版本下拉", async () => {
    stub = installFetchStub(routesFor());
    await signIn("teacher1", "pw");
    renderQuality({ ...QUALITY_PATH, origin: "question" });

    expect(await screen.findByTestId("review-quality-answerers-value")).toHaveTextContent("18");
    expect(screen.getByTestId("review-quality-attempts-value")).toHaveTextContent("42");
    // 系统故障 = 5 次基础设施问题 + 6 次资源用量缺失。
    expect(screen.getByTestId("review-quality-system-value")).toHaveTextContent("11");
    expect(screen.getByTestId("review-quality-security-value")).toHaveTextContent("1");
    expect(screen.getByTestId("review-quality-unchecked-value")).toHaveTextContent("14");
    expect(screen.getByTestId("review-quality-hint")).toHaveTextContent("存在异常");
    // 失败率降序：第 2 个用例 31/42 排在前面。
    const table = screen.getByTestId("review-quality-cases-table");
    const rows = table.querySelectorAll("tbody tr");
    expect(rows[0]).toHaveTextContent("第 2 个用例");
    expect(rows[0]).toHaveTextContent("74%");
    expect(rows[0]).toHaveTextContent("11 / 31");
    // 异常汇总竖排：每类一行。
    const abnormal = screen.getByTestId("review-quality-abnormal-text");
    expect(abnormal.tagName).toBe("UL");
    expect(abnormal.querySelectorAll("li").length).toBeGreaterThan(1);
    expect(abnormal).toHaveTextContent("输出结果不一致：11 次");

    // 只能看这道题：下拉不在，题目版本以只读文字出现。
    expect(screen.queryByTestId("review-quality-course")).toBeNull();
    expect(screen.queryByTestId("review-quality-version")).toBeNull();
    expect(screen.getByTestId("review-quality-locked")).toHaveTextContent("统计文件行数");

    const summaryRequest = stub!.requests.find(
      (item) => item.method === "GET" && item.url.pathname === SUMMARY,
    );
    expect(summaryRequest?.url.searchParams.get("question_version_id")).toBe(UUID_C);
  });

  it("总览页：只选版本不发请求，点「查询」才出现汇总", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(routesFor());
    await signIn("teacher1", "pw");
    renderQuality({ tab: "quality", course_id: UUID_A });

    // 只选版本（还没查询）：不发聚合请求，也不显示数字。
    const versionSelect = await screen.findByTestId("review-quality-version");
    await waitFor(() => {
      expect(versionSelect.querySelectorAll("option").length).toBeGreaterThan(1);
    });
    await user.selectOptions(versionSelect, UUID_C);
    expect(screen.queryByTestId("review-quality-answerers-value")).toBeNull();
    expect(screen.getByTestId("review-quality-no-version")).toHaveTextContent("点「查询」");
    expect(
      stub!.requests.some((item) => item.url.pathname === SUMMARY),
    ).toBe(false);

    await user.click(screen.getByTestId("review-quality-apply"));
    expect(await screen.findByTestId("review-quality-answerers-value")).toHaveTextContent("18");
    const summaryRequest = stub!.requests.find(
      (item) => item.method === "GET" && item.url.pathname === SUMMARY,
    );
    expect(summaryRequest?.url.searchParams.get("question_version_id")).toBe(UUID_C);
  });

  it("查询后又换了版本：旧汇总收起来，提示重新查询", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(
      routesFor({
        versions: [
          {
            question_version_id: UUID_C,
            version: 3,
            published_at: "2026-09-15T07:20:00.000Z",
          },
          {
            question_version_id: UUID_D,
            version: 4,
            published_at: "2026-10-01T07:20:00.000Z",
          },
        ],
      }),
    );
    await signIn("teacher1", "pw");
    renderQuality(QUALITY_PATH);

    expect(await screen.findByTestId("review-quality-answerers-value")).toHaveTextContent("18");
    await user.selectOptions(screen.getByTestId("review-quality-version"), UUID_D);
    expect(await screen.findByTestId("review-quality-selection-changed")).toHaveTextContent(
      "点「查询」",
    );
    expect(screen.queryByTestId("review-quality-answerers-value")).toBeNull();
  });

  it("没有版本时不发任何 B3/B2 请求，提示先查询", async () => {
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
      "点「查询」",
    );
    // 没有版本时也给一条"看示例"的路，否则首次访问只有一句提示。
    expect(screen.getByTestId("review-quality-empty-demo")).toBeInTheDocument();
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
              answerer_count: 0,
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
      "当前条件暂无评测记录",
    );
    expect(screen.queryByTestId("review-quality-counts")).toBeNull();
  });

  it("样本少与没有异常时，用提示条和一句空态说明，不堆空表", async () => {
    stub = installFetchStub(
      routesFor({
        summaryRespond: () =>
          jsonResponse(
            evaluationQualitySummary({
              answerer_count: 3,
              attempt_count: 3,
              case_failure_rates: [{ ordinal: 0, attempts: 3, failures: 0 }],
              failure_categories: [],
              static_rule_hits: [],
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

    expect(await screen.findByTestId("review-quality-hint")).toHaveTextContent(
      "样本较少，统计仅供参考",
    );
    expect(screen.getByTestId("review-quality-answerers-value")).toHaveTextContent("3");
    // 没有失败也列出用例，失败率是 0；不是把整张表藏掉。
    const table = screen.getByTestId("review-quality-cases-table");
    expect(table.querySelector("tbody tr")).toHaveTextContent("第 1 个用例");
    expect(table.querySelector("tbody tr")).toHaveTextContent("0%");
    expect(screen.getByTestId("review-quality-abnormal-none")).toHaveTextContent(
      "未检测到系统故障与安全风险",
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
    await screen.findByTestId("review-quality-attempts-value");

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
    await screen.findByTestId("review-quality-attempts-value");

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

/**
 * 示例预览（`?demo=1`）：评审的是**同一套界面**，因此示例必须完全不发 B3/B2 请求，
 * 并且切换版本、改时间范围都真的会改变数字——否则样例证明不了筛选逻辑。
 */
describe("P13-B 题目质量 · 示例预览", () => {
  function sessionOnly(): StubbedRoute[] {
    return [
      {
        method: "POST",
        path: SESSION,
        respond: () =>
          jsonResponse(
            sessionCreated({
              roles: ["teacher"],
              scope: [{ course_id: DEMO_COURSE_ID, section_id: DEMO_SECTION_ID }],
            }),
          ),
      },
    ];
  }

  it("不发 B3/B2 请求，直接展示示例聚合；切到 v2 看得到样本不足", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(sessionOnly());
    await signIn("teacher1", "pw");
    renderQuality({ tab: "quality", demo: "1" });

    expect(await screen.findByTestId("review-demo-banner")).toHaveTextContent("示例预览");
    expect(await screen.findByTestId("review-quality-answerers-value")).toHaveTextContent("18");
    expect(screen.getByTestId("review-quality-attempts-value")).toHaveTextContent("42");
    // 系统故障 = 5 次基础设施问题 + 6 次资源用量缺失。
    expect(screen.getByTestId("review-quality-system-value")).toHaveTextContent("11");
    expect(screen.getByTestId("review-quality-security-value")).toHaveTextContent("1");
    expect(screen.getByTestId("review-quality-unchecked-value")).toHaveTextContent("14");
    expect(screen.getByTestId("review-quality-hint")).toHaveTextContent("存在异常");

    const table = screen.getByTestId("review-quality-cases-table");
    const rows = table.querySelectorAll("tbody tr");
    expect(rows[0]).toHaveTextContent("第 2 个用例");
    expect(rows[0]).toHaveTextContent("74%");

    // 课程与版本由示例自带：不需要课程目录，也不需要 B3 的题目列表。
    expect(screen.getByTestId("review-quality-course")).toHaveValue(DEMO_COURSE_ID);
    expect(screen.getByTestId("review-quality-version")).toHaveValue(DEMO_VERSION_ID);

    await user.selectOptions(screen.getByTestId("review-quality-version"), DEMO_VERSION_V2_ID);
    await waitFor(() => {
      expect(screen.getByTestId("review-quality-attempts-value")).toHaveTextContent("3");
    });
    expect(screen.getByTestId("review-quality-unchecked-value")).toHaveTextContent("3");
    expect(screen.getByTestId("review-quality-hint")).toHaveTextContent("样本较少");

    const business = stub!.requests.filter(
      (item) => item.method !== "POST" || item.url.pathname !== SESSION,
    );
    expect(business).toHaveLength(0);
  });

  it("从题目详情带着真实版本进入示例时，回落到示例版本而不是空汇总", async () => {
    // 真实场景：在某个题目详情点「查看题目质量」得到真实 question_version_id，再点「查看示例」。
    // 用真实版本去查示例聚合只能得到"暂无评测记录"，所以示例只认示例自己的版本。
    stub = installFetchStub(sessionOnly());
    await signIn("teacher1", "pw");
    renderQuality({
      tab: "quality",
      demo: "1",
      course_id: UUID_A,
      question_version_id: UUID_C,
    });

    expect(await screen.findByTestId("review-quality-answerers-value")).toHaveTextContent("18");
    expect(screen.getByTestId("review-quality-version")).toHaveValue(DEMO_VERSION_ID);
  });

  it("时间范围真的过滤示例记录（不只在请求参数上出现）", async () => {    const user = userEvent.setup();
    stub = installFetchStub(sessionOnly());
    await signIn("teacher1", "pw");
    renderQuality({ tab: "quality", demo: "1" });
    await screen.findByTestId("review-quality-attempts-value");

    fireEvent.change(screen.getByTestId("review-quality-from"), {
      target: { value: "2026-10-29T00:00" },
    });
    fireEvent.change(screen.getByTestId("review-quality-to"), {
      target: { value: "2026-11-02T00:00" },
    });
    await user.click(screen.getByTestId("review-quality-apply"));

    await waitFor(() => {
      const attempts = Number(
        screen.getByTestId("review-quality-attempts-value").textContent,
      );
      // 区间内外都还有记录：这个断言在任意本地时区下都成立。
      expect(attempts).toBeGreaterThan(0);
      expect(attempts).toBeLessThan(42);
    });
    expect(
      stub!.requests.filter((item) => item.url.pathname === SUMMARY),
    ).toHaveLength(0);
  });
});
