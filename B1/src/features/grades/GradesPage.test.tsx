import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  binaryResponse,
  installFetchStub,
  jsonResponse,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  UUID_B,
  UUID_RUN,
  coursePage,
  courseSummary,
  enrollmentPublic,
  evaluationCommentary,
  evaluationReport,
  gradeRow,
  learningHintsResponse,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import { GradesPage } from "./GradesPage";
import type { EvaluationReport } from "../../api/types";
import type { EChartsLoader } from "./echartsLoader";

/** 图表加载器桩：jsdom 没有 canvas，真实 ECharts 无法初始化（隔离不可控浏览器能力）。 */
const stubChartLoader: EChartsLoader = async () => ({
  init: () => ({ setOption: () => {}, resize: () => {}, dispose: () => {} }),
});

const TEACHER_ID = "00000000-0000-4000-8000-0000000000e2";

/**
 * 真实形状的 JWT：`sub` 是 UUID。用不透明字符串会让"从令牌取身份"这条路径
 * 在测试里永远走不到（它在真实使用中才是主路径）。
 */
function jwtFor(subject: string): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "EdDSA" })}.${encode({ sub: subject })}.signature`;
}

let objectUrlSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  objectUrlSpy = vi.fn(() => "blob:test");
  URL.createObjectURL = objectUrlSpy as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
});

afterEach(() => {
  vi.restoreAllMocks();
});

const STUDENT_ONE = "00000000-0000-4000-8000-0000000000e2";
const STUDENT_TWO = "00000000-0000-4000-8000-0000000000e4";
const STUDENT_THREE = "00000000-0000-4000-8000-0000000000e6";

function gradeRoutes(
  role: "teacher" | "student",
  options: {
    gradePages?: { items: unknown[]; nextCursor: string | null }[];
    /** 覆盖证据报告边界（用于验证 CHG-0012 字段缺失时的诚实降级）。 */
    report?: EvaluationReport;
  } = {},
): StubbedRoute[] {
  const gradePages =
    options.gradePages ??
    [
      {
        items: [
          gradeRow({
            submission_id: "00000000-0000-4000-8000-0000000000e1",
            student_id: STUDENT_ONE,
            student_number: "20260001",
            score: 90,
            max_score: 100,
            is_pass: true,
            state: "completed",
            evaluation_run_id: "00000000-0000-4000-8000-0000000000e9",
          }),
          gradeRow({
            submission_id: "00000000-0000-4000-8000-0000000000e3",
            student_id: STUDENT_TWO,
            student_number: "20260002",
            score: null,
            max_score: null,
            is_pass: null,
            state: "running",
            evaluation_run_id: UUID_RUN,
          }),
        ],
        nextCursor: null,
      },
    ];

  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: [role],
            scope: [{ course_id: UUID_A, section_id: UUID_B }],
            access_token: jwtFor(TEACHER_ID),
          }),
        ),
    },
    {
      path: "/api/v1/courses",
      respond: () => jsonResponse(coursePage([courseSummary()])),
    },
    {
      path: `/api/v1/courses/${UUID_A}/sections`,
      respond: () => jsonResponse({ items: [{ section_id: UUID_B, section_code: "01" }] }),
    },
    {
      // 题目选择器读取公开题目列表（教师侧才有该筛选）；测试用空列表，标签回退为缩短的版本 ID。
      path: "/api/v1/questions",
      respond: () => jsonResponse({ items: [], next_cursor: null }),
    },
    {
      path: "/api/v1/grades",
      respond: ({ url }) => {
        const cursor = url.searchParams.get("cursor");
        const index = Math.min(cursor === null ? 0 : Number(cursor), gradePages.length - 1);
        const page = gradePages[index];
        return jsonResponse({ items: page.items, next_cursor: page.nextCursor });
      },
    },
    {
      path: `/api/v1/sections/${UUID_B}/enrollments`,
      respond: () =>
        jsonResponse({
          items: [
            enrollmentPublic({
              enrollment_id: "00000000-0000-4000-8000-0000000000f1",
              student_id: STUDENT_ONE,
              student_number: "20260001",
              display_name: "张三",
            }),
            enrollmentPublic({
              enrollment_id: "00000000-0000-4000-8000-0000000000f2",
              student_id: STUDENT_TWO,
              student_number: "20260002",
              display_name: "李四",
            }),
            enrollmentPublic({
              enrollment_id: "00000000-0000-4000-8000-0000000000f3",
              student_id: STUDENT_THREE,
              student_number: "20260003",
              display_name: "王五",
            }),
          ],
          next_cursor: null,
        }),
    },
    {
      path: `/api/v1/evaluation-runs/${UUID_RUN}/report`,
      respond: () => jsonResponse(options.report ?? evaluationReport()),
    },
    {
      // 下钻视图现在**必然**渲染教学评语轨（RQ-T-20），因此这条边界必须声明：
      // 未声明的请求会被 `installFetchStub` 拒绝，测试会静默走进"读取出错"分支。
      path: `/api/v1/evaluation-runs/${UUID_RUN}/commentary`,
      respond: () => jsonResponse(evaluationCommentary()),
    },
    {
      // 学生下钻会渲染学习提示（AI-FLOW-01B），因此这条边界必须声明。
      path: `/api/v1/evaluation-runs/${UUID_RUN}/learning-hints`,
      respond: () =>
        jsonResponse(
          learningHintsResponse({
            status: "not_requested",
            hint_steps: [],
            generation_id: null,
            provider: null,
            model: null,
            prompt_version: null,
            context_scope: [],
          }),
        ),
    },
    {
      path: "/api/v1/grades/export",
      respond: () =>
        binaryResponse({
          body: "student_number,score\n20260001,90\n",
          contentType: "text/csv",
          filename: "grades-export.csv",
        }),
    },
  ];
}

async function selectCourse(user: ReturnType<typeof userEvent.setup>) {
  const courseSelect = await screen.findByLabelText("课程");
  // 课程是成绩查询的必需作用域，所以下拉里只有真实课程（没有"空"选项），且默认选中第一门。
  await waitFor(() => expect(within(courseSelect).getAllByRole("option")).toHaveLength(1));
  await user.selectOptions(courseSelect, UUID_A);
}

/** 打开成绩页并选定课程（课程选项来自 B4，需要等到真正渲染出来）。 */
async function openGrades(
  role: "teacher" | "student",
  options: {
    gradePages?: { items: unknown[]; nextCursor: string | null }[];
    report?: EvaluationReport;
  } = {},
) {
  const stub = installFetchStub(gradeRoutes(role, options));
  const user = userEvent.setup();
  await signIn(`${role}1`, "pw");
  renderWithProviders(<GradesPage chartLoader={stubChartLoader} />);
  await selectCourse(user);
  await screen.findByTestId("grades-table");
  return { stub, user };
}

describe("成绩查询（RQ-B-1-30）", () => {
  it("每个页面恰好一个 h1（页面标题），面板是 h2", async () => {
    const { stub } = await openGrades("teacher");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "成绩" })).toBeInTheDocument();
    stub.restore();
  });

  it("未完成的评测显示状态而不是 0 分，汇总口径明确标注只覆盖已加载行", async () => {
    const { stub } = await openGrades("teacher");

    const table = screen.getByTestId("grades-table");
    // 未完成行按学号定位：状态列与得分列是本地化/格式化的，只有学号是稳定锚点。
    const unfinishedRow = within(table).getByText("20260002").closest("tr") as HTMLTableRowElement;
    const scoreCell = within(unfinishedRow).getByTestId("unfinished-score");
    expect(scoreCell).toHaveTextContent("未评分");
    // 得分列不得出现任何数字：未完成既不是 0 分，也不是任何分数。
    expect(scoreCell.textContent).not.toMatch(/\d/);
    // 状态列显示面向用户的中文状态标签，而不是内部英文状态码（`running`）。
    expect(within(unfinishedRow).getByText("评测中")).toBeInTheDocument();
    // 提交时间固定为定宽 `YYYY-MM-DD HH:mm:ss`，不随运行环境 locale 变化。
    expect(
      within(unfinishedRow).getByText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/),
    ).toBeInTheDocument();

    expect(screen.getByTestId("summary-scope-notice")).toHaveTextContent("统计仅基于已完成评测的成绩");
    expect(screen.getByText(/平均分：90/)).toBeInTheDocument();
    expect(screen.queryByTestId("unfinished-notice")).toBeNull();
    stub.restore();
  });

  it("成绩超过一页时给出「加载更多」，加载后到末页给出明确终点（不静默丢行）", async () => {
    const second = gradeRow({
      submission_id: "00000000-0000-4000-8000-0000000000ea",
      student_id: STUDENT_THREE,
      student_number: "20260003",
      state: "completed",
      score: 70,
      max_score: 100,
      is_pass: true,
      evaluation_run_id: null,
    });
    const { stub } = await openGrades("teacher", {
      gradePages: [
        {
          items: [
            gradeRow({
              submission_id: "00000000-0000-4000-8000-0000000000e1",
              student_id: STUDENT_ONE,
              student_number: "20260001",
              state: "completed",
              evaluation_run_id: null,
            }),
          ],
          nextCursor: "1",
        },
        { items: [second], nextCursor: null },
      ],
    });

    const table = screen.getByTestId("grades-table");

    // 汇总需要全量分页，因此页面会自动取完后续页（不再要求教师手工点"加载更多"）。
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
    expect(table).toHaveTextContent("20260003");
    expect(screen.getByText(/明细（已加载 2 行）/)).toBeInTheDocument();
    expect(screen.queryByTestId("grades-load-more")).toBeNull();
    expect(screen.getByTestId("grades-end")).toBeInTheDocument();
    stub.restore();
  });

  it("下钻到评测详细：教师视图只展示 AI 评分与 AI 评价，不含测试用例与技术详情", async () => {
    const { stub, user } = await openGrades("teacher");

    await user.click(screen.getByTestId(`drilldown-${UUID_RUN}`));

    const drilldown = await screen.findByTestId("grade-drilldown");
    expect(within(drilldown).getByTestId("authoritative-score")).toHaveTextContent("AI 评分：86 / 100");
    expect(await within(drilldown).findByTestId("commentary-text")).toHaveTextContent(
      "本题主要失分在第二个用例",
    );
    expect(within(drilldown).queryByTestId("case-evidence")).toBeNull();
    expect(within(drilldown).queryByTestId("static-findings")).toBeNull();
    expect(within(drilldown).queryByTestId("security-findings")).toBeNull();
    expect(within(drilldown).queryByTestId("binding-details")).toBeNull();

    const reportRequest = stub.requests.find((request) => request.url.pathname.endsWith("/report"));
    expect(reportRequest?.url.searchParams.get("view")).toBe("teacher");
    stub.restore();
  });

  it("学生视图下钻同样只显示 AI 评分与 AI 评价，不泄露受保护信息或测试用例", async () => {
    const { stub, user } = await openGrades("student");

    await user.click(screen.getByTestId(`drilldown-${UUID_RUN}`));
    const drilldown = await screen.findByTestId("grade-drilldown");

    expect(within(drilldown).getByTestId("authoritative-score")).toHaveTextContent("AI 评分：86 / 100");
    expect(await within(drilldown).findByTestId("commentary-text")).toBeInTheDocument();
    expect(within(drilldown).queryAllByTestId("expected-output")).toHaveLength(0);
    expect(within(drilldown).queryByTestId("case-evidence")).toBeNull();
    expect(within(drilldown).queryByTestId("learning-hints")).toBeNull();

    const reportRequest = stub.requests.find((request) => request.url.pathname.endsWith("/report"));
    expect(reportRequest?.url.searchParams.get("view")).toBe("student");
    stub.restore();
  });

  it("学生看不到导出入口（可见性只是体验；服务端仍会 403）", async () => {
    const { stub } = await openGrades("student");
    expect(screen.queryByTestId("open-export")).toBeNull();
    stub.restore();
  });

  it("导出：由 B2 生成文件后下载，B1 不重新拼接数据", async () => {
    const { stub, user } = await openGrades("teacher");

    await user.click(screen.getByTestId("open-export"));
    await user.selectOptions(await screen.findByLabelText("格式"), "csv");
    await user.click(screen.getByTestId("download-export"));

    await waitFor(() => expect(objectUrlSpy).toHaveBeenCalledTimes(1));
    const exportRequest = stub.requests.find((request) =>
      request.url.pathname.endsWith("/grades/export"),
    );
    expect(exportRequest?.url.searchParams.get("course_id")).toBe(UUID_A);
    expect(exportRequest?.url.searchParams.get("format")).toBe("csv");
    // 导出与页面共用同一筛选范围：没有输入时间就不发送 from/to。
    expect(exportRequest?.url.searchParams.get("from")).toBeNull();
    expect(await screen.findByTestId("export-success")).toHaveTextContent("成绩已下载");
    stub.restore();
  });

  it("导出被服务端拒绝（403）时原样展示，不伪造文件", async () => {
    const stub = installFetchStub(
      gradeRoutes("teacher").map((route) =>
        route.path === "/api/v1/grades/export"
          ? {
              path: "/api/v1/grades/export",
              respond: () =>
                new Response(
                  JSON.stringify({
                    type: "https://autograder.local/problems/forbidden",
                    title: "Forbidden",
                    status: 403,
                    detail: "students may not export",
                    instance: "/api/v1/grades/export",
                  }),
                  { status: 403, headers: { "content-type": "application/problem+json" } },
                ),
            }
          : route,
      ),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderWithProviders(<GradesPage chartLoader={stubChartLoader} />);
    await selectCourse(user);
    await screen.findByTestId("grades-table");

    await user.click(screen.getByTestId("open-export"));
    await user.click(screen.getByTestId("download-export"));
    expect(await screen.findByTestId("export-error")).toHaveTextContent("没有导出成绩的权限");
    expect(objectUrlSpy).not.toHaveBeenCalled();
    stub.restore();
  });
});

describe("谁还没交（RQ-B-1-30 验收 4 的操作化形态）", () => {
  /** 打开「谁还没交」标签页：该视图按课序名单比对，所以先选定课序再切换标签。 */
  async function openMissingTab(routes: StubbedRoute[]) {
    const stub = installFetchStub(routes);
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderWithProviders(<GradesPage chartLoader={stubChartLoader} />);
    await selectCourse(user);
    await screen.findByTestId("grades-table");
    await user.selectOptions(screen.getByLabelText("课序"), UUID_B);
    await user.click(screen.getByRole("tab", { name: "仍未提交" }));
    return { stub, user };
  }

  /** 用给定的成绩行替换默认的成绩边界。 */
  function withGrades(rows: unknown[]): StubbedRoute[] {
    return gradeRoutes("teacher").map((route) =>
      route.path === "/api/v1/grades"
        ? {
            path: "/api/v1/grades",
            respond: () => jsonResponse({ items: rows, next_cursor: null }),
          }
        : route,
    );
  }

  /** 用给定的名单行替换默认的名单边界。 */
  function withEnrollments(items: unknown[]): StubbedRoute[] {
    return gradeRoutes("teacher").map((route) =>
      route.path === `/api/v1/sections/${UUID_B}/enrollments`
        ? {
            path: `/api/v1/sections/${UUID_B}/enrollments`,
            respond: () => jsonResponse({ items, next_cursor: null }),
          }
        : route,
    );
  }

  it("名单与成绩按 student_id 外连接：未提交 / 技术失败 / 已评分各自成类", async () => {
    const { stub } = await openMissingTab(
      withGrades([
        // 张三：已完成
        gradeRow({
          submission_id: "00000000-0000-4000-8000-0000000000e1",
          student_id: STUDENT_ONE,
          student_number: null,
          display_name: null,
          state: "completed",
          score: 90,
          max_score: 100,
          is_pass: true,
          evaluation_run_id: null,
        }),
        // 李四：技术失败（已提交，但评测本身失败）
        gradeRow({
          submission_id: "00000000-0000-4000-8000-0000000000e3",
          student_id: STUDENT_TWO,
          student_number: null,
          display_name: null,
          state: "failed",
          score: null,
          max_score: null,
          is_pass: null,
          evaluation_run_id: null,
        }),
      ]),
    );

    expect(await screen.findByTestId("cohort-summary")).toHaveTextContent(
      "未提交 1，技术失败 1",
    );

    const table = screen.getByTestId("cohort-table");
    expect(within(table).getAllByRole("row")).toHaveLength(4); // 表头 + 3 人
    // 王五在名单里但没有任何提交 → 未提交，且得分列不出现数字（未提交 ≠ 0 分）
    const missing = within(table).getByText("20260003").closest("tr") as HTMLTableRowElement;
    expect(within(missing).getByText("未提交")).toBeInTheDocument();
    expect(within(missing).getByTestId("cohort-unscored").textContent).not.toMatch(/\d/);
    // 李四是技术失败，不是"未提交"
    const failed = within(table).getByText("20260002").closest("tr") as HTMLTableRowElement;
    expect(within(failed).getByText("技术失败")).toBeInTheDocument();
    // 姓名与学号来自**名单侧**（B2 的对应字段恒为 null）
    expect(table).toHaveTextContent("张三");
    stub.restore();
  });

  it("未绑定账号的名单行单列一类，不被算成未提交，也不编造 student_id", async () => {
    const { stub } = await openMissingTab(
      withEnrollments([
        enrollmentPublic({
          enrollment_id: "00000000-0000-4000-8000-0000000000f3",
          student_id: null,
          student_number: "20260003",
          display_name: "王五",
        }),
      ]),
    );

    expect(await screen.findByTestId("cohort-summary")).toHaveTextContent("未绑定账号 1");
    expect(screen.getByTestId("cohort-summary")).toHaveTextContent("未提交 0");
    stub.restore();
  });

  it("学生看不到这个标签页（服务端仍独立判定）", async () => {
    const { stub } = await openGrades("student");
    expect(screen.queryByRole("tab", { name: "仍未提交" })).toBeNull();
    stub.restore();
  });

  it("到自动翻页上限时说明被截断，而不是永远「加载中」，也不给出任何提交判定", async () => {
    // 服务端永远返回同一个 next_cursor：分页永远有"下一页"，直到撞上自动翻页上限。
    const endless = gradeRoutes("teacher").map((route) =>
      route.path === "/api/v1/grades"
        ? {
            path: "/api/v1/grades",
            respond: () =>
              jsonResponse({
                items: [
                  gradeRow({
                    submission_id: "00000000-0000-4000-8000-0000000000e1",
                    student_id: STUDENT_ONE,
                    state: "completed",
                    evaluation_run_id: null,
                  }),
                ],
                next_cursor: "always-more",
              }),
          }
        : route,
    );
    const { stub } = await openMissingTab(endless);

    const notice = await screen.findByTestId(
      "cohort-truncated",
      {},
      { timeout: 5000 },
    );
    expect(notice).toHaveTextContent(`结果较多，仅展示前 ${MAX_AUTO_PAGES} 页`);

    const panel = notice.closest("section") as HTMLElement;
    // 上限是**终局**：这里不能出现 spinner（否则是一个永远不会结束的"加载中"）。
    expect(within(panel).queryAllByRole("status")).toHaveLength(0);
    expect(within(panel).queryByTestId("cohort-loading-all")).toBeNull();
    // 也不能出现任何判定：既不显示"未提交"计数，也不显示判定表。
    expect(within(panel).queryByTestId("cohort-summary")).toBeNull();
    expect(within(panel).queryByTestId("cohort-table")).toBeNull();

    // 而且真的停下来了：请求数有上界，没有无限增长。
    // 上界是 `3 × MAX_AUTO_PAGES`：明细页与「谁还没交」各有一轮自动翻页，而测试的 QueryClient
    // `staleTime: 0` 会在挂载新观察者时把已加载的 40 页再重取一遍。判据是**有界且到达上限后停止**，
    // 不是恰好等于页数。
    const gradeRequests = stub.requests.filter(
      (request) => request.url.pathname === "/api/v1/grades",
    );
    expect(gradeRequests.length).toBeGreaterThanOrEqual(MAX_AUTO_PAGES);
    expect(gradeRequests.length).toBeLessThanOrEqual(MAX_AUTO_PAGES * 3 + 8);
    stub.restore();
  });
});
