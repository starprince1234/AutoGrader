import { afterEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { installFetchStub, jsonResponse, type FetchStub } from "../../testing/httpBoundary";
import {
  SHA_A,
  UUID_A,
  authoringAssistance,
  comparisonDiagnostics,
  coursePage,
  courseSummary,
  generationCapabilities,
  questionDraftGeneration,
  questionPublic,
  questionTagCoverage,
  questionVersionPublic,
  referenceVerification,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { QuestionListPage } from "../questions/QuestionListPage";
import { QuestionDetailPage } from "../questions/QuestionDetailPage";
import { AuthoringPage } from "./AuthoringPage";

/**
 * 界面语言的守卫：教师页面上不得出现内部/接口层面的说法。
 *
 * 这条用例存在的理由是具体的：这些页面此前写满了"契约 / DTO / provider / 存证 / content_digest /
 * 403 / cases[1].weight"之类只有开发者看得懂的字样，教师读不懂也不该读。文案改动很容易在某次
 * 复制粘贴里退回去，因此把它做成一条**扫渲染文本**的机械断言，而不是靠复查。
 *
 * 只扫正常态（数据齐、面板展开）：错误态由共享组件展示可复制的诊断信息，那属于给维护者看的。
 */

const BANNED = [
  "契约",
  "DTO",
  "adapter",
  "manifest",
  "provider",
  "prompt",
  "存证",
  "attestation",
  "content_digest",
  "published_only",
  "question_version_id",
  "question_id",
  "course_id",
  "stem_markdown",
  "reference_answer",
  "端点",
  "接口",
  "后端",
  "沙箱",
  "幂等",
  "CMP-",
  "CHG-",
  "cases[",
  "403",
  "404",
  "409",
  "422",
  "429",
  "502",
  "503",
];

const UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/i;
const DIGEST_PATTERN = /\b[0-9a-f]{32,64}\b/;

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function assertPlainLanguage(text: string) {
  for (const token of BANNED) {
    expect(text, `教师界面上出现了技术说法：${token}`).not.toContain(token);
  }
  expect(text, "教师界面上出现了内部编号").not.toMatch(UUID_PATTERN);
  expect(text, "教师界面上出现了校验码/摘要").not.toMatch(DIGEST_PATTERN);
}

/** 最小路由上下文：这些页面里都有指向其它页的链接。 */
function renderAt(children: ReactNode) {
  const rootRoute = createRootRoute({ component: () => <>{children}</> });
  const routes = [
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/questions/$questionId",
      component: () => <span>detail</span>,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/questions/$questionId/edit",
      component: () => <span>edit</span>,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/questions/new",
      component: () => <span>new</span>,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/questions",
      component: () => <span>list</span>,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/submit/$questionVersionId",
      component: () => <span>submit</span>,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/quality",
      component: () => <span>quality</span>,
    }),
  ];
  const testRouter = createRouter({
    routeTree: rootRoute.addChildren(routes),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return renderWithProviders(<RouterProvider router={testRouter} />);
}

/**
 * 新建题目 + AI 视图：真实页面用 URL 上的 `ai` 决定视图，这里用同样的两个回调驱动。
 */
function NewQuestionWithAi() {
  const [ai, setAi] = useState(false);
  return <AuthoringPage questionId={null} aiView={ai} onAiViewChange={setAi} />;
}

const SESSION = () => jsonResponse(sessionCreated({ roles: ["teacher"] }));
const COURSES = () => jsonResponse(coursePage([courseSummary()]));

describe("教师界面只说人话", () => {
  it("题库列表与知识点覆盖", async () => {
    stub = installFetchStub([
      { method: "POST", path: "/api/v1/auth/session", respond: SESSION },
      { path: "/api/v1/courses", respond: COURSES },
      { path: "/api/v1/questions", respond: () => jsonResponse({ items: [questionPublic()], next_cursor: null }) },
      { path: "/api/v1/questions/coverage", respond: () => jsonResponse(questionTagCoverage()) },
    ]);
    await signIn("teacher1", "pw");
    renderAt(<QuestionListPage />);

    const courseSelect = await screen.findByLabelText("课程");
    await waitFor(() =>
      expect(within(courseSelect).getAllByRole("option").length).toBeGreaterThan(1),
    );
    await userEvent.setup().selectOptions(courseSelect, UUID_A);
    await screen.findByTestId("coverage-body");
    assertPlainLanguage(document.body.textContent ?? "");
  });

  it("题目详情（含版本与差异）", async () => {
    stub = installFetchStub([
      { method: "POST", path: "/api/v1/auth/session", respond: SESSION },
      { path: `/api/v1/questions/${questionPublic().question_id}`, respond: () => jsonResponse(questionPublic()) },
      {
        path: `/api/v1/question-versions/${UUID_A}`,
        respond: () =>
          jsonResponse(
            questionVersionPublic({
              question_version_id: UUID_A,
              status: "published",
              content_digest: SHA_A,
              revision_note: "改正了示例说明",
            }),
          ),
      },
    ]);
    await signIn("teacher1", "pw");
    renderAt(
      <QuestionDetailPage questionId={questionPublic().question_id} versionId={UUID_A} onSelectVersion={() => {}} />,
    );

    await screen.findByTestId("version-panel");
    assertPlainLanguage(document.body.textContent ?? "");
  });

  it("新建题目：AI 面板展开、草稿填进编辑器之后", async () => {
    const user = userEvent.setup();
    stub = installFetchStub([
      { method: "POST", path: "/api/v1/auth/session", respond: SESSION },
      { path: "/api/v1/courses", respond: COURSES },
      {
        path: "/api/v1/question-draft-generations/capabilities",
        respond: () => jsonResponse(generationCapabilities()),
      },
      {
        method: "POST",
        path: "/api/v1/question-draft-generations",
        respond: () => jsonResponse(questionDraftGeneration()),
      },
    ]);
    await signIn("teacher1", "pw");
    renderAt(<NewQuestionWithAi />);

    const courseSelect = await screen.findByLabelText("课程");
    await waitFor(() =>
      expect(within(courseSelect).getAllByRole("option").length).toBeGreaterThan(1),
    );
    await user.selectOptions(courseSelect, UUID_A);
    // AI 入口在「题干」步的标题右侧。
    await user.click(screen.getByTestId("wizard-step-stem"));
    await screen.findByTestId("step-stem");
    await user.click(screen.getByTestId("authoring-ai"));
    await screen.findByTestId("generation-panel");
    await user.type(screen.getByLabelText("教学目标"), "统计每个单词出现的次数");
    await user.click(screen.getByTestId("generation-generate"));
    await screen.findByTestId("generation-result");

    assertPlainLanguage(document.body.textContent ?? "");
  });

  it("编辑草稿：自查命中 + AI 建议 + 检验结果", async () => {
    const user = userEvent.setup();
    const questionId = questionPublic().question_id;
    stub = installFetchStub([
      { method: "POST", path: "/api/v1/auth/session", respond: SESSION },
      { path: "/api/v1/courses", respond: COURSES },
      { path: `/api/v1/questions/${questionId}`, respond: () => jsonResponse(questionPublic({ status: "draft" })) },
      {
        path: `/api/v1/question-versions/${UUID_A}`,
        respond: () =>
          jsonResponse(
            questionVersionPublic({
              question_version_id: UUID_A,
              status: "draft",
              content_digest: SHA_A,
            }),
          ),
      },
      {
        path: `/api/v1/questions/${questionId}/diagnostics`,
        respond: () => jsonResponse(comparisonDiagnostics()),
      },
      {
        method: "POST",
        path: `/api/v1/questions/${questionId}/authoring-assistance`,
        respond: () => jsonResponse(authoringAssistance()),
      },
      {
        method: "POST",
        path: "/api/v1/reference-verifications",
        respond: () => jsonResponse(referenceVerification()),
      },
    ]);
    await signIn("teacher1", "pw");
    renderAt(<AuthoringPage questionId={questionId} />);

    await user.click(await screen.findByTestId("request-assistance"));
    await screen.findByTestId("assistance-result");
    await user.click(screen.getByTestId("reference-verify"));
    await screen.findByTestId("reference-verification-result");

    assertPlainLanguage(document.body.textContent ?? "");
  });
});
