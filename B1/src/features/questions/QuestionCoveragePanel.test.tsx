import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type FetchStub,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import { questionTagCoverage, UUID_A } from "../../testing/contractFixtures";
import { QuestionCoveragePanel } from "./QuestionCoveragePanel";

/**
 * 题库标签覆盖度（CHG-0020）。
 *
 * 断言的重点不是"数字显示出来了"，而是契约在 schema 的 description 里点名的三条读法：
 * - 可校验的恒等式**只有** `tagged + untagged == total`（含反向断言：逐标签计数之和不存在于页面）；
 * - `untagged_questions` 单独一格（"没有标签"不能被读成"没有题目"）；
 * - 标签顺序原样渲染（不再排一次）。
 * 另外三处：404 是**数据驱动**的信息态（只说本部署取不到该端点，不伪造数据、不重发请求）、
 * 403 原样带出服务端 detail，以及**界面上不存在任何关于"对方实现到哪一步"的断言**（有反向断言）。
 *
 * **证据边界**：本文件只证明"界面按契约形状正确工作"。真实浏览器里的读数（真实宿主 + 真实 B3）
 * 见 `docs/phases/active/phase-05-authoring-publish-path-and-0-15-0.md` 的"真机（浏览器）读数"一节。
 */

const COVERAGE_PATH = "/api/v1/questions/coverage";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function coverageRoutes(respond: () => Response): StubbedRoute[] {
  return [{ method: "GET", path: COVERAGE_PATH, respond }];
}

function coverageRequests(active: FetchStub) {
  return active.requests.filter((request) => request.url.pathname === COVERAGE_PATH);
}

function renderPanel(args?: {
  courseId?: string | null;
  publishedOnly?: boolean;
  onSelectTag?: (tag: string) => void;
}) {
  return renderWithProviders(
    <QuestionCoveragePanel
      courseId={args?.courseId === undefined ? UUID_A : args.courseId}
      publishedOnly={args?.publishedOnly ?? true}
      onSelectTag={args?.onSelectTag ?? (() => {})}
    />,
  );
}

describe("标签覆盖度的读取条件（CHG-0020）", () => {
  it("没有课程作用域时不发任何请求，并说明原因", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel({ courseId: null });

    expect(await screen.findByTestId("coverage-no-course")).toHaveTextContent(
      "不发任何请求",
    );
    expect(stub.requests).toHaveLength(0);
  });

  it("只发契约声明的两个查询参数，且 published_only 与列表取同一个值", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel({ publishedOnly: false });

    await screen.findByTestId("coverage-body");
    const requests = coverageRequests(stub);
    expect(requests).toHaveLength(1);
    expect(requests[0].url.searchParams.get("course_id")).toBe(UUID_A);
    expect(requests[0].url.searchParams.get("published_only")).toBe("false");
    // 契约未声明的参数一个都不发（分页参数在这里没有意义）。
    expect([...requests[0].url.searchParams.keys()].sort()).toEqual([
      "course_id",
      "published_only",
    ]);
  });

  it("404 → 信息态（数据驱动）：只说本部署取不到该端点，不猜原因、不伪造任何计数", async () => {
    stub = installFetchStub(
      coverageRoutes(() =>
        problemResponse({ status: 404, title: "Not Found", detail: "Not Found" }),
      ),
    );
    renderPanel();

    const banner = await screen.findByTestId("coverage-endpoint-absent");
    expect(banner).toHaveTextContent("本部署");
    expect(banner).toHaveTextContent("404");
    // 不做客户端兜底：分页下"自己数一遍"的数是不完整的。
    expect(banner).toHaveTextContent("回退到用列表在客户端数一遍");
    // 界面**不得**断言另一个模块的实现进度（这类文案没有过期机制，端点一上线就变成假话）。
    expect(screen.getByTestId("question-coverage").textContent ?? "").not.toContain("尚未实现");
    expect(screen.queryByTestId("coverage-declared-contract")).toBeNull();

    // 不是错误态，也不是空态：页面上一格计数都没有。
    expect(screen.queryByTestId("coverage-error")).toBeNull();
    expect(screen.queryByTestId("coverage-body")).toBeNull();
    expect(screen.queryByTestId("coverage-total-value")).toBeNull();
    expect(screen.queryByTestId("coverage-no-tags")).toBeNull();

    // 没有任何重发：4xx 是确定性结论（`src/app/queryClient.ts` 对 <500 不重试）。
    expect(coverageRequests(stub)).toHaveLength(1);
  });

  it("403 原样呈现服务端 detail，不伪造成「没有数据」", async () => {
    stub = installFetchStub(
      coverageRoutes(() =>
        problemResponse({
          status: 403,
          title: "Forbidden",
          detail: "Course scope does not cover this question bank.",
        }),
      ),
    );
    renderPanel();

    const banner = await screen.findByTestId("coverage-forbidden");
    // 服务端原文逐字带出（不替换成一句前端文案）。
    expect(banner).toHaveTextContent("Course scope does not cover this question bank.");
    expect(banner).toHaveTextContent("这不是「没有数据」");
    // 反向断言：越权拒绝不得渲染成"这个课程没有题"的空态。
    expect(screen.queryByTestId("coverage-body")).toBeNull();
    expect(screen.queryByTestId("coverage-no-tags")).toBeNull();
    expect(screen.queryByText("暂无数据")).toBeNull();
    expect(screen.queryByTestId("coverage-error")).toBeNull();
  });

  it("其余失败（500）走通用错误态，并保留 request_id 可追溯", async () => {
    stub = installFetchStub(
      coverageRoutes(() =>
        problemResponse({ status: 500, title: "Server Error", detail: "聚合失败。" }),
      ),
    );
    renderPanel();

    expect(await screen.findByTestId("coverage-error")).toBeInTheDocument();
    expect(screen.queryByTestId("coverage-endpoint-absent")).toBeNull();
  });

  it("界面上不存在任何「对方实现到哪一步」的断言（这类文案没有过期机制）", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel();
    await screen.findByTestId("coverage-body");

    // 端点可用时，页面必须**没有**任何"尚未实现 / 未实现 / 证据只到夹具"之类的常驻横幅。
    // 它曾经存在过，并在真实浏览器里与旁边的实时计数直接矛盾（真实发生的事）。
    const panel = screen.getByTestId("question-coverage");
    expect(panel.textContent ?? "").not.toContain("尚未实现");
    expect(panel.textContent ?? "").not.toContain("未实现");
    expect(panel.textContent ?? "").not.toContain("契约夹具");
  });
});

describe("恒等式与逐标签计数的读法（CHG-0020 点名处）", () => {
  it("tagged + untagged == total 是页面上唯一被呈现与校验的恒等式", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel();

    const identity = await screen.findByTestId("coverage-identity");
    expect(identity).toHaveTextContent("已标注 12 + 未标注 4 = 16");
    expect(identity).toHaveTextContent("只有这一条");
    expect(screen.getByTestId("coverage-total-value")).toHaveTextContent("16");
    expect(screen.getByTestId("coverage-tagged-value")).toHaveTextContent("12");
    expect(screen.getByTestId("coverage-untagged-value")).toHaveTextContent("4");
  });

  it("三项计数不自洽时如实报告，不挑一个数当真、也不悄悄改成一致", async () => {
    stub = installFetchStub(
      coverageRoutes(() =>
        jsonResponse(questionTagCoverage({ total_questions: 17 })),
      ),
    );
    renderPanel();

    const identity = await screen.findByTestId("coverage-identity");
    expect(identity).toHaveTextContent("已标注 12 + 未标注 4 = 16");
    expect(identity).toHaveTextContent("17");
    expect(identity).toHaveTextContent("不自洽");
    // 服务端给的数照原样显示（不被"修正"成 16）。
    expect(screen.getByTestId("coverage-total-value")).toHaveTextContent("17");
  });

  it("逐标签计数之和不是页面上的任何数字：没有「标签合计」，也不能相加", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel();

    await screen.findByTestId("coverage-body");
    // 夹具的 9 + 9 + 3 = 21 > total_questions(16)：把它渲染成"题目数之和"会得出一个不成立的数。
    expect(screen.queryByText("21")).toBeNull();
    expect(screen.getByTestId("question-coverage").textContent ?? "").not.toContain("21");
    expect(screen.getByTestId("coverage-overlap-note")).toHaveTextContent("不能相加");
    // 页面上不存在任何"把逐标签计数加起来"的单元格：标签表只有标签与各自的题目数。
    const table = screen.getByTestId("coverage-tag-table");
    expect(within(table).getAllByRole("columnheader")).toHaveLength(3);
    expect(within(table).getAllByRole("row")).toHaveLength(4); // 表头 + 3 个标签
  });

  it("untagged_questions 单独一格，不与标签计数混在一起", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel();

    const untagged = await screen.findByTestId("coverage-untagged");
    expect(within(untagged).getByTestId("coverage-untagged-value")).toHaveTextContent("4");
    expect(untagged).toHaveTextContent("没有标签");
    expect(untagged).toHaveTextContent("单独看");
    // 反向断言：这一格里不出现任何标签的计数（9/9/3）。
    expect(within(untagged).queryByText("9")).toBeNull();
    expect(within(untagged).queryByText("3")).toBeNull();
    // 标签表里也不出现"未标注"这个数。
    expect(within(screen.getByTestId("coverage-tags")).queryByText("4")).toBeNull();
  });

  it("空题库：tags 为空数组且 untagged == total 是信息态，不是错误", async () => {
    stub = installFetchStub(
      coverageRoutes(() =>
        jsonResponse(
          questionTagCoverage({
            total_questions: 7,
            tagged_questions: 0,
            untagged_questions: 7,
            tags: [],
          }),
        ),
      ),
    );
    renderPanel();

    const empty = await screen.findByTestId("coverage-no-tags");
    expect(empty).toHaveTextContent("一个标签都没有");
    expect(empty).toHaveTextContent("7 道题");
    expect(empty).toHaveTextContent("不是错误");
    // 不是错误态、也没有空表。
    expect(screen.queryByTestId("coverage-error")).toBeNull();
    expect(screen.queryByTestId("coverage-tag-table")).toBeNull();
    // 恒等式仍然成立并照常展示：0 + 7 = 7。
    expect(screen.getByTestId("coverage-identity")).toHaveTextContent(
      "已标注 0 + 未标注 7 = 7",
    );
    expect(screen.getByTestId("coverage-untagged-value")).toHaveTextContent("7");
  });
});

describe("标签顺序与筛选入口（CHG-0020）", () => {
  it("渲染顺序 == 契约给的顺序，客户端不再排一次", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel();

    const table = await screen.findByTestId("coverage-tag-table");
    const rows = within(table).getAllByRole("row").slice(1); // 去掉表头
    expect(rows.map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual([
      "process-scheduling",
      "unix",
      "scheduling",
    ]);
    // 契约顺序不是字母序（同数 9 的两个标签里 process-scheduling 在前）：
    // 任何"再按 tag 排一次"的实现都会在这里露馅。
    expect(
      rows.map((row) => within(row).getAllByRole("cell")[0].textContent),
    ).not.toEqual(["process-scheduling", "scheduling", "unix"]);
    expect(screen.getByTestId("coverage-tag-order")).toHaveTextContent("不再排一次");
    // 每个标签的题目数可读，且与契约给的数一致。
    expect(screen.getByTestId("coverage-count-process-scheduling")).toHaveTextContent("9");
    expect(screen.getByTestId("coverage-count-unix")).toHaveTextContent("9");
    expect(screen.getByTestId("coverage-count-scheduling")).toHaveTextContent("3");
    // 条形只相对最多的标签（9 → 100%，3 → 33%），不表示"占题目总数的比例"。
    expect(screen.getByTestId("coverage-bar-process-scheduling").style.width).toBe("100%");
    expect(screen.getByTestId("coverage-bar-scheduling").style.width).toBe("33%");
    expect(screen.getByTestId("coverage-bar-note")).toHaveTextContent("不是");
  });

  it("点标签把该标签交给列表既有的标签筛选", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    const onSelectTag = vi.fn();
    const user = userEvent.setup();
    renderPanel({ onSelectTag });

    await screen.findByTestId("coverage-body");
    const tagButton = screen.getByTestId("coverage-tag-unix");
    // 读屏用户需要知道"点这个标签会发生什么"，而不是只听到一个标签名。
    expect(tagButton).toHaveAccessibleName("按标签 unix 筛选题目列表");
    await user.click(tagButton);

    expect(onSelectTag).toHaveBeenCalledTimes(1);
    expect(onSelectTag).toHaveBeenCalledWith("unix");
  });

  it("每个标签都可点：契约（CHG-0022）保证 tag 的形状与 listQuestions.tags 一致", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    const onSelectTag = vi.fn();
    const user = userEvent.setup();
    renderPanel({ onSelectTag });

    await screen.findByTestId("coverage-body");
    // 三行都有按钮：契约把 `pattern` 补进了 `QuestionTagCoverage.tags[].tag`，
    // 因此本页不再需要"这个标签可能不是筛选取值"的分支。
    for (const tag of ["process-scheduling", "unix", "scheduling"]) {
      expect(screen.getByTestId(`coverage-tag-${tag}`)).toBeInTheDocument();
    }
    expect(screen.queryAllByTestId(/^coverage-tag-static-/)).toHaveLength(0);

    await user.click(screen.getByTestId("coverage-tag-scheduling"));
    // 原样把契约给的值交给列表筛选，**不**做任何改写。
    expect(onSelectTag).toHaveBeenCalledWith("scheduling");
  });
});
