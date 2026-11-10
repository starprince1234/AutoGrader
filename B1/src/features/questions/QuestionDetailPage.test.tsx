import { afterEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { installFetchStub, jsonResponse, type FetchStub } from "../../testing/httpBoundary";
import {
  SHA_A,
  UUID_A,
  questionPublic,
  questionVersionPublic,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { QuestionDetailPage } from "./QuestionDetailPage";

const QUESTION_ID = questionPublic().question_id;
const VERSION_A = UUID_A;

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

/** 版本卡已删除，页面不再有切换入口：直接以深链的形式给出 `versionId`。 */
function Harness() {
  return <QuestionDetailPage questionId={QUESTION_ID} versionId={VERSION_A} />;
}

/**
 * 题目详情里有指向「我的作业」的 `<Link>`（作答入口只在作业工作区），因此测试需要一个
 * 最小路由上下文（只用测试路由表，不引入应用路由表，避免测试与应用装配耦合）。
 */
function renderQuestionDetail() {
  const rootRoute = createRootRoute({ component: Harness });
  const assignmentsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments",
    component: () => <span>assignments target</span>,
  });
  const editRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions/$questionId/edit",
    component: () => <span>edit target</span>,
  });
  const testRouter = createRouter({
    routeTree: rootRoute.addChildren([assignmentsRoute, editRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return renderWithProviders(<RouterProvider router={testRouter} />);
}

describe("题目详情与版本机制（RQ-B-1-40）", () => {
  it("渲染公开 DTO 的题干，并说明受控图形资源只以元数据出现（不伪造图片地址）", async () => {
    stub = installFetchStub([
      {
        path: `/api/v1/questions/${QUESTION_ID}`,
        respond: () => jsonResponse(questionPublic()),
      },
    ]);

    renderQuestionDetail();

    const stem = await screen.findByTestId("question-stem-body");
    expect(within(stem).getByText("统计文件行数")).toBeInTheDocument();
    expect(within(stem).getByText("wc -l")).toBeInTheDocument();
    // 题干本身不含图片地址；资产只以 `stem_assets` 元数据出现，页面不替它拼一个地址出来。
    expect(document.querySelector("img")).toBeNull();
  });

  it("「题目版本」卡片整块不再渲染；学生仍能在题干下方看到作答入口", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["student"] })),
      },
      {
        path: `/api/v1/questions/${QUESTION_ID}`,
        respond: () => jsonResponse(questionPublic()),
      },
      {
        path: `/api/v1/question-versions/${VERSION_A}`,
        respond: () =>
          jsonResponse(
            questionVersionPublic({
              question_version_id: VERSION_A,
              version: 3,
              status: "published",
              stem_markdown: "第三版题干",
            }),
          ),
      },
    ]);
    await signIn("student1", "pw");
    renderQuestionDetail();

    // 题干仍然是主要内容。
    expect(await screen.findByTestId("question-stem-body")).toBeInTheDocument();

    // 版本卡（选择版本 / 版本详情 / 此版本题干 / 相关信息）整体消失，且不留空白卡片。
    expect(screen.queryByTestId("version-select")).toBeNull();
    expect(screen.queryByTestId("version-panel")).toBeNull();
    expect(screen.queryByTestId("version-related")).toBeNull();
    expect(screen.queryByTestId("version-stem-body")).toBeNull();
    expect(screen.queryByTestId("version-stem-label")).toBeNull();
    expect(screen.queryByTestId("historical-version-notice")).toBeNull();
    expect(screen.queryByText("选择版本")).toBeNull();
    expect(screen.queryByText("相关信息")).toBeNull();

    // 学生入口保留，只是从版本卡里挪到了题干下面；提交是作业范围的，所以它指向「我的作业」。
    const entry = await screen.findByTestId("go-to-assignments");
    expect(entry.getAttribute("href") ?? "").toContain("/assignments");
  });

  it("公开 DTO 若混入受保护字段，页面拒绝渲染并报错（UI 层双重校验）", async () => {
    stub = installFetchStub([
      {
        path: `/api/v1/questions/${QUESTION_ID}`,
        respond: () => jsonResponse({ ...questionPublic(), reference_answer: "print(3)" }),
      },
    ]);

    renderQuestionDetail();

    expect(await screen.findByTestId("error-state")).toHaveTextContent("受保护字段");
    expect(screen.queryByTestId("stem")).toBeNull();
  });

  it("版本框里不再有「作答入口对学生开放…」这句提示；返回题库列表在右上角", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
      },
      {
        path: `/api/v1/questions/${QUESTION_ID}`,
        respond: () => jsonResponse(questionPublic()),
      },
      {
        path: `/api/v1/question-versions/${VERSION_A}`,
        respond: () => jsonResponse(questionVersionPublic({ question_version_id: VERSION_A })),
      },
    ]);
    await signIn("teacher1", "pw");
    renderQuestionDetail();

    await screen.findByTestId("question-stem-body");
    expect(screen.queryByTestId("submit-student-only")).toBeNull();
    expect(screen.queryByText(/作答入口对学生开放/)).toBeNull();
    // 返回按钮在标题同一行的右上角。
    const back = screen.getByTestId("back-to-list");
    expect(back.closest(".page-head")).not.toBeNull();
    expect(back.className).toContain("page-head__end");
  });

  it("编辑入口带上来源：from=detail（3.0：编辑页要显示「返回题目」）", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
      },
      {
        path: `/api/v1/questions/${QUESTION_ID}`,
        respond: () => jsonResponse(questionPublic({ status: "draft" })),
      },
      {
        path: `/api/v1/question-versions/${VERSION_A}`,
        respond: () => jsonResponse(questionVersionPublic({ question_version_id: VERSION_A })),
      },
    ]);
    await signIn("teacher1", "pw");
    renderQuestionDetail();

    const link = await screen.findByTestId("edit-question");
    expect(link.getAttribute("href") ?? "").toContain("from=detail");
  });

  it("出题角色看到编辑入口；学生看不到，出题入口不会指向死路", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["student"] })),
      },
      {
        path: `/api/v1/questions/${QUESTION_ID}`,
        respond: () => jsonResponse(questionPublic()),
      },
      {
        path: `/api/v1/question-versions/${VERSION_A}`,
        respond: () =>
          jsonResponse(questionVersionPublic({ question_version_id: VERSION_A })),
      },
    ]);
    await signIn("student1", "pw");
    renderQuestionDetail();

    await screen.findByTestId("question-stem-body");
    expect(screen.queryByTestId("edit-question")).toBeNull();
  });

  it("教师看到未发布版本的验证并发布入口；未验证前没有发布动作，学生看不到该面板", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
      },
      {
        path: `/api/v1/questions/${QUESTION_ID}`,
        respond: () => jsonResponse(questionPublic({ status: "draft" })),
      },
      {
        path: `/api/v1/question-versions/${VERSION_A}`,
        respond: () =>
          jsonResponse(
            questionVersionPublic({
              question_version_id: VERSION_A,
              version: 3,
              status: "draft",
              content_digest: SHA_A,
            }),
          ),
      },
    ]);
    await signIn("teacher1", "pw");
    renderQuestionDetail();

    const panel = await screen.findByTestId("reference-verification");
    expect(within(panel).getByTestId("reference-verify")).toBeInTheDocument();
    // 2.0：门禁的说明文字已删，语义仍在服务端与按钮行为里（见 ReferenceVerificationPanel 测试）。
    expect(within(panel).queryByTestId("reference-gate")).toBeNull();
    // 未验证 → 没有发布动作。
    expect(within(panel).queryByTestId("reference-publish")).toBeNull();
    // 编辑入口在位：写路径不再是"看得到改不了"。
    expect(screen.getByTestId("edit-question")).toBeInTheDocument();
    // 3.0：「这个版本尚未发布……」那条说明已删。
    expect(screen.queryByTestId("version-not-published")).toBeNull();
  });
});

