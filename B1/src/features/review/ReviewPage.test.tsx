import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { resetLoggerForTests } from "../../logging/logger";
import { ReviewPage } from "./ReviewPage";
import type { ReviewSearch } from "./search";

/** 两个标签的内容在别处已有组件测试；这里只钉路由/返回按钮/标签独立状态。 */
vi.mock("./MailReviewTab", () => ({
  MailReviewTab: () => <div data-testid="mail-tab-stub" />,
}));
vi.mock("./QualityReviewTab", () => ({
  QualityReviewTab: () => <div data-testid="quality-tab-stub" />,
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
