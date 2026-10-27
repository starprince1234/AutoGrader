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
 * 知识点覆盖（标签覆盖度）。
 *
 * 断言的是教师能从中读出什么：
 * - 三个数各自独立（总数 / 已打标签 / 还没打标签），"没打标签"单独一格；
 * - 各标签的题数按服务端给的顺序渲染，条形只用于互相比较；
 * - 同一题可以有多个标签，所以各标签题数**不能相加**（页面上不存在这个合计数）；
 * - 没有课程、没有权限、读取失败都各自说清楚，不伪装成"没有数据"。
 *
 * 界面文案刻意是白话：这里不断言任何内部标识或服务端英文原文。
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

describe("读取条件", () => {
  it("还没选课程时不发请求，只提示先选课程", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel({ courseId: null });

    expect(await screen.findByTestId("coverage-no-course")).toHaveTextContent("先选择课程");
    expect(stub.requests).toHaveLength(0);
  });

  it("只发课程与发布范围两个参数，且与列表取同一个值", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel({ publishedOnly: false });

    await screen.findByTestId("coverage-body");
    const requests = coverageRequests(stub);
    expect(requests).toHaveLength(1);
    expect(requests[0].url.searchParams.get("course_id")).toBe(UUID_A);
    expect(requests[0].url.searchParams.get("published_only")).toBe("false");
    expect([...requests[0].url.searchParams.keys()].sort()).toEqual([
      "course_id",
      "published_only",
    ]);
  });

  it("没有权限时如实说明，不渲染成「没有数据」", async () => {
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
    expect(banner).toHaveTextContent("没有这门课的查看权限");
    expect(banner.textContent ?? "").not.toContain("Course scope");
    expect(screen.queryByTestId("coverage-body")).toBeNull();
    expect(screen.queryByTestId("coverage-no-tags")).toBeNull();
  });

  it("读取失败时给一句可读的提示，不显示原始错误", async () => {
    stub = installFetchStub(
      coverageRoutes(() =>
        problemResponse({ status: 500, title: "Server Error", detail: "aggregate failed" }),
      ),
    );
    renderPanel();

    const banner = await screen.findByTestId("coverage-error");
    expect(banner).toHaveTextContent("暂时读取不到");
    expect(banner.textContent ?? "").not.toContain("aggregate failed");
    expect(coverageRequests(stub)).toHaveLength(1);
  });
});

describe("三个数怎么读", () => {
  it("总数、已打标签、还没打标签各自一格", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel();

    expect(await screen.findByTestId("coverage-total-value")).toHaveTextContent("16");
    expect(screen.getByTestId("coverage-tagged-value")).toHaveTextContent("12");
    expect(screen.getByTestId("coverage-untagged-value")).toHaveTextContent("4");
    expect(screen.getByTestId("coverage-untagged")).toHaveTextContent("还没打标签");
  });

  it("各标签题数不能相加：页面上没有这个合计数", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel();

    await screen.findByTestId("coverage-body");
    // 夹具里 9 + 9 + 3 = 21 > 总数 16：把它当成"题目数"会得出一个不成立的数字。
    expect(screen.getByTestId("question-coverage").textContent ?? "").not.toContain("21");
    expect(screen.getByTestId("coverage-overlap-note")).toHaveTextContent("不能相加");
    const table = screen.getByTestId("coverage-tag-table");
    expect(within(table).getAllByRole("columnheader")).toHaveLength(3);
    expect(within(table).getAllByRole("row")).toHaveLength(4);
  });

  it("这道题一个标签都没有时是提示，不是错误", async () => {
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

    expect(await screen.findByTestId("coverage-no-tags")).toHaveTextContent("还没有给任何题目打标签");
    expect(screen.queryByTestId("coverage-error")).toBeNull();
    expect(screen.queryByTestId("coverage-tag-table")).toBeNull();
    expect(screen.getByTestId("coverage-untagged-value")).toHaveTextContent("7");
  });
});

describe("标签与筛选入口", () => {
  it("按服务端给的顺序渲染，每个标签的题数与条形都可读", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    renderPanel();

    const table = await screen.findByTestId("coverage-tag-table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual([
      "process-scheduling",
      "unix",
      "scheduling",
    ]);
    expect(screen.getByTestId("coverage-count-process-scheduling")).toHaveTextContent("9");
    expect(screen.getByTestId("coverage-count-unix")).toHaveTextContent("9");
    expect(screen.getByTestId("coverage-count-scheduling")).toHaveTextContent("3");
    // 条形只相对最多的标签（9 → 100%，3 → 33%），不是"占题目总数的比例"。
    expect(screen.getByTestId("coverage-bar-process-scheduling").style.width).toBe("100%");
    expect(screen.getByTestId("coverage-bar-scheduling").style.width).toBe("33%");
  });

  it("点标签会把这个标签交给列表筛选（读屏也能听懂会发生什么）", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    const onSelectTag = vi.fn();
    const user = userEvent.setup();
    renderPanel({ onSelectTag });

    await screen.findByTestId("coverage-body");
    const tagButton = screen.getByTestId("coverage-tag-unix");
    expect(tagButton).toHaveAccessibleName("按标签「unix」筛选题目");
    await user.click(tagButton);

    expect(onSelectTag).toHaveBeenCalledTimes(1);
    expect(onSelectTag).toHaveBeenCalledWith("unix");
  });

  it("每个标签都能点，值原样交给筛选", async () => {
    stub = installFetchStub(coverageRoutes(() => jsonResponse(questionTagCoverage())));
    const onSelectTag = vi.fn();
    const user = userEvent.setup();
    renderPanel({ onSelectTag });

    await screen.findByTestId("coverage-body");
    for (const tag of ["process-scheduling", "unix", "scheduling"]) {
      expect(screen.getByTestId(`coverage-tag-${tag}`)).toBeInTheDocument();
    }
    await user.click(screen.getByTestId("coverage-tag-scheduling"));
    expect(onSelectTag).toHaveBeenCalledWith("scheduling");
  });
});
