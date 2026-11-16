import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { resetLoggerForTests } from "../../logging/logger";
import { ReviewPage } from "./ReviewPage";
import type { ReviewSearch } from "./search";

/**
 * 标签内容在 `MailReviewTab.test.tsx` 里有组件测试；这里只钉页面外壳：
 * 复核页只剩邮件隔离，返回按钮回作业列表，旧的多标签/示例入口不再存在。
 */
vi.mock("./MailReviewTab", () => ({
  MailReviewTab: () => <div data-testid="mail-tab-stub" />,
}));

function Harness({ initial }: { initial: ReviewSearch }) {
  const [search, setSearch] = useState(initial);
  return <ReviewPage search={search} onChange={setSearch} />;
}

function renderReview(initial: ReviewSearch = {}) {
  const rootRoute = createRootRoute({ component: () => <Harness initial={initial} /> });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      createRoute({ getParentRoute: () => rootRoute, path: "/assignments" }),
    ]),
    history: createMemoryHistory({ initialEntries: ["/review"] }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}

afterEach(() => {
  resetLoggerForTests();
  vi.clearAllMocks();
});

describe("复核页（只剩邮件隔离）", () => {
  it("页头返回作业列表，内容是邮件提交", async () => {
    renderReview({ course_id: "00000000-0000-4000-8000-0000000000a1" });
    const back = await screen.findByTestId("review-back");
    expect(back).toHaveTextContent("返回作业列表");
    expect(back).toHaveAttribute("href", "/assignments");
    expect(screen.getByTestId("mail-tab-stub")).toBeInTheDocument();
  });

  it("不再有题目质量标签，也没有示例开关", async () => {
    renderReview();
    await screen.findByTestId("mail-tab-stub");
    expect(screen.queryByTestId("review-tabs")).toBeNull();
    expect(screen.queryByTestId("review-demo-toggle")).toBeNull();
    expect(screen.queryByText("题目质量")).toBeNull();
  });
});
