import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  useNavigate,
  useSearch,
} from "@tanstack/react-router";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { resetLoggerForTests } from "../../logging/logger";
import { ReviewPage } from "./ReviewPage";
import { parseReviewSearch, type ReviewSearch } from "./search";

/**
 * 两个标签的内容在别处已有组件测试；这里只钉路由/返回按钮/标签独立状态。
 * 桩会把自己的 `search.demo` 暴露成属性：示例开关是否真的进了 URL 由它来证明。
 */
vi.mock("./MailReviewTab", () => ({
  MailReviewTab: ({ search }: { search: ReviewSearch }) => (
    <div data-testid="mail-tab-stub" data-demo={search.demo ?? "off"} />
  ),
}));
vi.mock("./QualityReviewTab", () => ({
  QualityReviewTab: ({ search }: { search: ReviewSearch }) => (
    <div data-testid="quality-tab-stub" data-demo={search.demo ?? "off"} />
  ),
}));

const UUID_A = "00000000-0000-4000-8000-0000000000a1";
const UUID_C = "00000000-0000-4000-8000-0000000000c1";

function Harness({ initial }: { initial: ReviewSearch }) {
  const [search, setSearch] = useState(initial);
  return (
    <>
      <ReviewPage search={search} onChange={setSearch} />
      <pre data-testid="search-state">{JSON.stringify(search)}</pre>
    </>
  );
}

function renderReview(initial: ReviewSearch) {
  const rootRoute = createRootRoute({ component: () => <Harness initial={initial} /> });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      createRoute({ getParentRoute: () => rootRoute, path: "/questions" }),
      createRoute({ getParentRoute: () => rootRoute, path: "/assignments" }),
    ]),
    history: createMemoryHistory({ initialEntries: ["/review"] }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}

/** 生产路径的复刻：`/review` 路由 + `validateSearch`，点「查看示例」真的走一次 URL 解析。 */
function renderRoutedReview(initialUrl: string) {
  function Routed() {
    const search = useSearch({ from: "/review" });
    const navigate = useNavigate();
    return (
      <ReviewPage
        search={search}
        onChange={(next) => {
          void navigate({ to: "/review", search: next });
        }}
      />
    );
  }
  const rootRoute = createRootRoute({ component: Routed });
  const reviewRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/review",
    validateSearch: parseReviewSearch,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      reviewRoute,
      createRoute({ getParentRoute: () => rootRoute, path: "/questions" }),
      createRoute({ getParentRoute: () => rootRoute, path: "/assignments" }),
    ]),
    history: createMemoryHistory({ initialEntries: [initialUrl] }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}

afterEach(() => {
  resetLoggerForTests();
  vi.clearAllMocks();
});

describe("复核页的返回入口与标签独立状态", () => {
  it("从题目详情进入质量标签时返回题库列表", async () => {
    renderReview({
      tab: "quality",
      origin: "question",
      course_id: UUID_A,
      question_version_id: UUID_C,
    });
    const back = await screen.findByTestId("review-back");
    expect(back).toHaveTextContent("返回题库列表");
    expect(back).toHaveAttribute("href", "/questions");
    expect(screen.getByTestId("quality-tab-stub")).toBeInTheDocument();
  });

  it("直接打开复核页时返回作业列表", async () => {
    renderReview({ tab: "quality", course_id: UUID_A });
    const back = await screen.findByTestId("review-back");
    expect(back).toHaveTextContent("返回作业列表");
    expect(back).toHaveAttribute("href", "/assignments");
  });

  it("切换标签保留各自的课程/状态条件", async () => {
    const user = userEvent.setup();
    renderReview({ tab: "mail", course_id: UUID_A, resolved: "false" });

    await user.click(
      within(await screen.findByTestId("review-tabs")).getByText("题目质量"),
    );
    let state = JSON.parse(
      (await screen.findByTestId("search-state")).textContent ?? "{}",
    );
    expect(state.tab).toBe("quality");
    expect(state.course_id).toBeUndefined();
    expect(state.mail_course_id).toBe(UUID_A);

    await user.click(
      within(await screen.findByTestId("review-tabs")).getByText("邮件提交"),
    );
    state = JSON.parse((await screen.findByTestId("search-state")).textContent ?? "{}");
    expect(state.tab).toBe("mail");
    expect(state.course_id).toBe(UUID_A);
    expect(state.resolved).toBe("false");
  });
});

/**
 * 线上要评判的正是这一条链路：点「查看示例」→ URL 带上 demo=1 → 路由重新解析 → 两个标签收到
 * 示例标记。它曾经断在"路由把 `demo=1` 解析成数字 1，而解析器只比较字符串"上，所以这里不再
 * 只测组件 props，而是真的走一次 `validateSearch`。
 */
describe("示例预览开关", () => {
  it("点「查看示例」进入示例模式，切标签仍保留，再点退出", async () => {
    const user = userEvent.setup();
    renderRoutedReview("/review");

    expect(await screen.findByTestId("mail-tab-stub")).toHaveAttribute("data-demo", "off");
    const toggle = await screen.findByTestId("review-demo-toggle");
    expect(toggle).toHaveTextContent("查看示例");

    await user.click(toggle);
    await waitFor(() => {
      expect(screen.getByTestId("mail-tab-stub")).toHaveAttribute("data-demo", "1");
    });
    expect(screen.getByTestId("review-demo-toggle")).toHaveTextContent("退出示例");

    await user.click(
      within(screen.getByTestId("review-tabs")).getByText("题目质量"),
    );
    await waitFor(() => {
      expect(screen.getByTestId("quality-tab-stub")).toHaveAttribute("data-demo", "1");
    });

    await user.click(screen.getByTestId("review-demo-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("quality-tab-stub")).toHaveAttribute("data-demo", "off");
    });
  });

  it("直接打开 `/review?demo=1`（刷新/分享）也留在示例模式", async () => {
    renderRoutedReview("/review?demo=1");
    await waitFor(() => {
      expect(screen.getByTestId("mail-tab-stub")).toHaveAttribute("data-demo", "1");
    });
    expect(screen.getByTestId("review-demo-toggle")).toHaveTextContent("退出示例");
  });
});
