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
 * 页面外壳测试；标签内容在 `MailRecordsTab.test.tsx` 里覆盖。
 * 这里只钉三件事：标题是「邮件记录」、页头下方有一句三态说明、返回按钮回作业列表。
 */
vi.mock("./MailRecordsTab", () => ({
  MailRecordsTab: () => <div data-testid="mail-records-stub" />,
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

describe("邮件记录页", () => {
  it("标题为「邮件记录」，返回按钮回作业列表", async () => {
    renderReview({ course_id: "00000000-0000-4000-8000-0000000000a1" });
    expect(
      await screen.findByRole("heading", { level: 1, name: "邮件记录" }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("review-intro")).toBeNull();
    const back = screen.getByTestId("review-back");
    expect(back).toHaveTextContent("返回作业列表");
    expect(back).toHaveAttribute("href", "/assignments");
    expect(screen.getByTestId("mail-records-stub")).toBeInTheDocument();
  });

  it("不再有「复核工作区」的旧标题，也没有题目质量/示例入口", async () => {
    renderReview();
    await screen.findByTestId("mail-records-stub");
    expect(screen.queryByText("复核工作区")).toBeNull();
    expect(screen.queryByTestId("review-tabs")).toBeNull();
    expect(screen.queryByTestId("review-demo-toggle")).toBeNull();
    expect(screen.queryByText("题目质量")).toBeNull();
  });
});
