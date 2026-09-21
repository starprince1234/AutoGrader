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
  SHA_A,
  UUID_A,
  UUID_B,
  UUID_RUN,
  coursePage,
  courseSummary,
  enrollmentPublic,
  evaluationCommentary,
  evaluationReport,
  gradeRow,
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
    expect(scoreCell).toHaveTextContent("未完成");
    // 得分列不得出现任何数字：未完成既不是 0 分，也不是任何分数。
    expect(scoreCell.textContent).not.toMatch(/\d/);
    // 状态列显示面向用户的中文状态标签，而不是内部英文状态码（`running`）。
    expect(within(unfinishedRow).getByText("评测中")).toBeInTheDocument();
    // 提交时间固定为定宽 `YYYY-MM-DD HH:mm:ss`，不随运行环境 locale 变化。
    expect(
      within(unfinishedRow).getByText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/),
    ).toBeInTheDocument();

    expect(screen.getByTestId("summary-scope-notice")).toHaveTextContent("不是权威汇总");
    expect(screen.getByText(/平均分（已加载行）：90/)).toBeInTheDocument();
    expect(screen.getByTestId("unfinished-notice")).toHaveTextContent("显示状态而不是 0 分");
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
    const { stub, user } = await openGrades("teacher", {
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
    expect(within(table).getAllByRole("row")).toHaveLength(2); // 表头 + 1 行
    expect(screen.getByTestId("grades-load-more")).toBeInTheDocument();

    await user.click(screen.getByTestId("grades-load-more"));

    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
    expect(table).toHaveTextContent("20260003");
    expect(screen.getByText(/成绩明细（已加载 2 行）/)).toBeInTheDocument();
    expect(screen.queryByTestId("grades-load-more")).toBeNull();
    expect(screen.getByTestId("grades-end")).toBeInTheDocument();
    stub.restore();
  });

  it("下钻到逐项证据：教师视图包含逐用例证据与合计校验", async () => {
    const { stub, user } = await openGrades("teacher");

    await user.click(screen.getByTestId(`drilldown-${UUID_RUN}`));

    const drilldown = await screen.findByTestId("grade-drilldown");
    expect(within(drilldown).getByTestId("case-evidence")).toHaveTextContent("资源超限");
    expect(within(drilldown).getByText(/逐项合计与总分一致/)).toBeInTheDocument();
    // 三态安全结论：夹具是 not_found，页面必须显示"未发现"而不是空白或"安全"
    expect(within(drilldown).getByText("未发现")).toBeInTheDocument();
    expect(within(drilldown).getByTestId("static-findings")).toHaveTextContent("PY-UNUSED-IMPORT");
    expect(within(drilldown).getAllByTestId("expected-output").length).toBeGreaterThan(0);

    const reportRequest = stub.requests.find((request) => request.url.pathname.endsWith("/report"));
    expect(reportRequest?.url.searchParams.get("view")).toBe("teacher");
    stub.restore();
  });

  it("逐用例证据：资源峰值、文件改动与确定性说明都能直接读，不用看 JSON", async () => {
    const { stub, user } = await openGrades("teacher");
    await user.click(screen.getByTestId(`drilldown-${UUID_RUN}`));
    const drilldown = await screen.findByTestId("grade-drilldown");

    const failing = within(drilldown).getByTestId(`case-${UUID_B}`);
    // 峰值按人读的单位显示，未测量的那一项（内核态 CPU）单列"未测量"，不是 0。
    expect(within(failing).getByTestId("case-resource")).toHaveTextContent("1.0 MB");
    expect(within(failing).getByTestId("case-resource")).toHaveTextContent("0.25 s");
    expect(within(failing).getAllByTestId("metric-unmeasured")).toHaveLength(1);
    // `limit_hit` 读作"哪个上限被触发"，而不是渲染枚举值 `memory`。
    expect(within(failing).getByTestId("limit-hit")).toHaveTextContent("内存上限");
    // 文件改动：期望/实际摘要与逐文件清单。
    const artifacts = within(failing).getByTestId("case-artifacts");
    expect(within(artifacts).getByTestId("artifact-actual")).toHaveTextContent(SHA_A.slice(0, 8));
    expect(within(artifacts).getByTestId("artifact-digest-verdict")).toHaveTextContent(
      "与期望不一致",
    );
    expect(artifacts).toHaveTextContent("out/count.txt");
    expect(artifacts).toHaveTextContent("input/data.txt");
    // 确定性说明（契约明确它不是模型输出）。
    expect(within(failing).getByTestId("case-explanation")).toHaveTextContent(
      "the memory limit was hit",
    );

    // 通过的那一个用例：`resource`/`artifacts` 都是 null → 如实显示"未测量/无数据"。
    const passed = within(drilldown).getByTestId(`case-${UUID_A}`);
    expect(within(passed).getByTestId("resource-unmeasured")).toHaveTextContent("未测量");
    expect(within(passed).getByTestId("artifacts-missing")).toHaveTextContent("未包含文件级结论");
    expect(within(passed).queryAllByTestId("limit-hit")).toHaveLength(0);
    stub.restore();
  });

  it("证据缺字段（B2 尚未下发 CHG-0012 的字段）时显示未测量，不显示 0", async () => {
    const legacy = evaluationReport();
    const { stub, user } = await openGrades("teacher", {
      report: {
        ...legacy,
        // 用**完全不出现这些键**的对象模拟旧响应：既没有 resource/artifacts/explanation。
        case_evidence: legacy.case_evidence.map((item) => {
          const { resource: _resource, artifacts: _artifacts, explanation: _explanation, ...rest } =
            item;
          return rest;
        }),
      },
    });
    await user.click(screen.getByTestId(`drilldown-${UUID_RUN}`));
    const drilldown = await screen.findByTestId("grade-drilldown");

    const card = within(drilldown).getByTestId(`case-${UUID_A}`);
    expect(within(card).getByTestId("resource-unmeasured")).toHaveTextContent("未测量");
    expect(within(card).getByTestId("artifacts-missing")).toBeInTheDocument();
    expect(within(card).getByTestId("explanation-missing")).toHaveTextContent("未给出说明");
    // 任何"0 B / 0.00 s / 0 MB"式的读数都不允许出现：没测过不等于用了 0
    // （提示文案里出现的那个"0"是解释文字，不是度量值，因此按"数值 + 单位"判定）。
    expect(within(card).getByTestId("case-resource").textContent).not.toMatch(
      /\d+\s*(?:B|KB|MB|s)\b/,
    );
    stub.restore();
  });

  it("学生视图不下发也不渲染受保护的期望输出与期望摘要", async () => {
    const { stub, user } = await openGrades("student");

    await user.click(screen.getByTestId(`drilldown-${UUID_RUN}`));
    const drilldown = await screen.findByTestId("grade-drilldown");

    expect(within(drilldown).queryAllByTestId("expected-output")).toHaveLength(0);
    expect(within(drilldown).queryAllByTestId("expected-output-block")).toHaveLength(0);
    expect(within(drilldown).queryAllByTestId("artifact-expected")).toHaveLength(0);
    // 学生视图连"是否一致"这个由受保护字段推导出来的结论也不显示。
    expect(within(drilldown).queryAllByTestId("artifact-digest-verdict")).toHaveLength(0);
    expect(drilldown.textContent).not.toContain("期望输出（教师视图）");
    expect(drilldown.textContent).not.toContain("期望文件摘要");

    const reportRequest = stub.requests.find((request) => request.url.pathname.endsWith("/report"));
    expect(reportRequest?.url.searchParams.get("view")).toBe("student");
    stub.restore();
  });

  it("学生看不到导出入口（可见性只是体验；服务端仍会 403）", async () => {
    const { stub } = await openGrades("student");
    expect(screen.queryByTestId("export-grades")).toBeNull();
    stub.restore();
  });

  it("导出：由 B2 生成文件后下载，B1 不重新拼接数据", async () => {
    const { stub, user } = await openGrades("teacher");

    await user.selectOptions(screen.getByLabelText("格式"), "csv");
    await user.click(screen.getByTestId("export-grades"));

    await waitFor(() => expect(objectUrlSpy).toHaveBeenCalledTimes(1));
    const exportRequest = stub.requests.find((request) =>
      request.url.pathname.endsWith("/grades/export"),
    );
    expect(exportRequest?.url.searchParams.get("course_id")).toBe(UUID_A);
    expect(exportRequest?.url.searchParams.get("format")).toBe("csv");
    expect(await screen.findByText(/导出已触发/)).toBeInTheDocument();
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

    await user.click(screen.getByTestId("export-grades"));
    expect(await screen.findByTestId("export-error")).toHaveTextContent("403");
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
    await user.selectOptions(screen.getByLabelText("课序（可选）"), UUID_B);
    await user.click(screen.getByRole("tab", { name: "谁还没交" }));
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
    expect(screen.queryByRole("tab", { name: "谁还没交" })).toBeNull();
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
    expect(notice).toHaveTextContent(`已达到自动翻页上限（${MAX_AUTO_PAGES} 页）`);

    const panel = notice.closest("section") as HTMLElement;
    // 上限是**终局**：这里不能出现 spinner（否则是一个永远不会结束的"加载中"）。
    expect(within(panel).queryAllByRole("status")).toHaveLength(0);
    expect(within(panel).queryByTestId("cohort-loading-all")).toBeNull();
    // 也不能出现任何判定：既不显示"未提交"计数，也不显示判定表。
    expect(within(panel).queryByTestId("cohort-summary")).toBeNull();
    expect(within(panel).queryByTestId("cohort-table")).toBeNull();

    // 而且真的停下来了：请求数停在翻页上限附近，没有无限增长。
    // （允许少量背景重取：测试用的 QueryClient `staleTime: 0`，标签切换会触发一次。）
    const gradeRequests = stub.requests.filter(
      (request) => request.url.pathname === "/api/v1/grades",
    );
    expect(gradeRequests.length).toBeGreaterThanOrEqual(MAX_AUTO_PAGES);
    expect(gradeRequests.length).toBeLessThanOrEqual(MAX_AUTO_PAGES + 4);
    stub.restore();
  });
});
