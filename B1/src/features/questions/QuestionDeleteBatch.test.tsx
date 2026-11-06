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
import { renderWithProviders } from "../../testing/renderWithProviders";
import { installFetchStub, jsonResponse, type FetchStub, type StubbedRoute } from "../../testing/httpBoundary";
import {
  courseSummary,
  questionPublic,
  questionTagCoverage,
  sessionCreated,
  UUID_A,
  UUID_B,
  UUID_C,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { QuestionListPage } from "./QuestionListPage";

/**
 * 删除草稿与批量操作（题库列表）。
 *
 * 这里固定的是**契约边界**上的行为，而不是像素：
 *
 * 1. 单条删除走 `DELETE /api/v1/question-versions/{id}`，且只对**草稿**版本发起；
 * 2. 批量删除/发布只发**一次** `POST /api/v1/question-versions/batch-operations`，
 *    `operation` 与 `question_version_ids` 都来自勾选的行；
 * 3. 逐项结论要如实呈现：全成功说"全部成功"，有失败要说清"几项没处理、为什么"。
 */

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

const DRAFT_VERSION = "00000000-0000-4000-8000-0000000000d1";
const PUBLISHED_VERSION = "00000000-0000-4000-8000-0000000000d2";

function draftQuestion(questionId: string) {
  return questionPublic({
    question_id: questionId,
    title: `草稿 ${questionId}`,
    status: "draft",
    versions: [{ question_version_id: DRAFT_VERSION, version: 1, status: "draft" }],
  });
}

function publishedQuestion(questionId: string) {
  return questionPublic({
    question_id: questionId,
    title: `已发布 ${questionId}`,
    status: "published",
    versions: [
      { question_version_id: PUBLISHED_VERSION, version: 1, status: "published" },
    ],
  });
}

function baseRoutes(items: ReturnType<typeof questionPublic>[]): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
    },
    {
      path: "/api/v1/courses",
      respond: () => jsonResponse({ items: [courseSummary()], next_cursor: null }),
    },
    {
      path: "/api/v1/questions",
      respond: () => jsonResponse({ items, next_cursor: null }),
    },
    {
      method: "GET",
      path: "/api/v1/questions/coverage",
      respond: () => jsonResponse(questionTagCoverage()),
    },
  ];
}

function renderList() {
  const rootRoute = createRootRoute({ component: () => <QuestionListPage /> });
  const detailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions/$questionId",
    component: () => <span>detail target</span>,
  });
  const newRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions/new",
    component: () => <span>new target</span>,
  });
  const editRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions/$questionId/edit",
    component: () => <span>edit target</span>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([newRoute, detailRoute, editRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}

async function openList() {
  await signIn("teacher1", "pw");
  renderList();
  await screen.findByTestId("question-table");
}

describe("题库列表：删除草稿", () => {
  it("草稿行有红色垃圾桶按钮，已发布行没有；确认后只对该草稿版本发 DELETE", async () => {
    stub = installFetchStub([
      ...baseRoutes([
        draftQuestion("q-draft"),
        publishedQuestion("q-published"),
      ]),
      {
        method: "DELETE",
        path: `/api/v1/question-versions/${DRAFT_VERSION}`,
        respond: () => new Response(null, { status: 204 }),
      },
    ]);
    const user = userEvent.setup();
    await openList();

    expect(screen.getByTestId("delete-q-draft")).toBeEnabled();
    expect(screen.getByTestId("delete-q-published")).toBeDisabled();

    await user.click(screen.getByTestId("delete-q-draft"));
    const modal = await screen.findByTestId("delete-draft-modal");
    await user.click(within(modal).getByTestId("delete-draft-confirm"));

    await waitFor(() => {
      const deletes = stub?.requests.filter((request) => request.method === "DELETE") ?? [];
      expect(deletes).toHaveLength(1);
      expect(deletes[0]?.url.pathname).toBe(
        `/api/v1/question-versions/${DRAFT_VERSION}`,
      );
    });
    expect(await screen.findByText("草稿已删除。")).toBeInTheDocument();
  });

  it("删除失败时如实说明原因（409 = 已发布或被作业引用）", async () => {
    stub = installFetchStub([
      ...baseRoutes([draftQuestion("q-draft")]),
      {
        method: "DELETE",
        path: `/api/v1/question-versions/${DRAFT_VERSION}`,
        respond: () =>
          jsonResponse(
            {
              type: "https://autograder.local/problems/conflict",
              title: "Conflict",
              status: 409,
              detail: "referenced",
              instance: "/api/v1/question-versions/x",
            },
            409,
          ),
      },
    ]);
    const user = userEvent.setup();
    await openList();

    await user.click(screen.getByTestId("delete-q-draft"));
    await user.click(
      within(await screen.findByTestId("delete-draft-modal")).getByTestId(
        "delete-draft-confirm",
      ),
    );

    expect(
      await screen.findByText("这个版本不能删除：它已经发布，或者被作业引用。"),
    ).toBeInTheDocument();
  });
});

describe("题库列表：批量操作", () => {
  it("勾选后出现批量操作条；批量删除只发一次请求并带上两条草稿版本", async () => {
    stub = installFetchStub([
      ...baseRoutes([draftQuestion("q-a"), draftQuestion("q-b")]),
      {
        method: "POST",
        path: "/api/v1/question-versions/batch-operations",
        respond: () =>
          jsonResponse({
            results: [
              { question_version_id: DRAFT_VERSION, status: "applied", error_code: null },
              { question_version_id: UUID_C, status: "applied", error_code: null },
            ],
          }),
      },
    ]);
    const user = userEvent.setup();
    await openList();

    expect(screen.queryByTestId("batch-bar")).toBeNull();
    await user.click(screen.getByTestId("select-q-a"));
    await user.click(screen.getByTestId("select-q-b"));
    expect(await screen.findByTestId("batch-bar")).toHaveTextContent("已选 2 项");

    await user.click(screen.getByTestId("batch-delete"));
    await user.click(
      within(await screen.findByTestId("delete-draft-modal")).getByTestId(
        "delete-draft-confirm",
      ),
    );

    await waitFor(() => {
      const batches =
        stub?.requests.filter((request) =>
          request.url.pathname.endsWith("/api/v1/question-versions/batch-operations"),
        ) ?? [];
      expect(batches).toHaveLength(1);
      expect(batches[0]?.method).toBe("POST");
      expect(batches[0]?.body).toEqual({
        operation: "delete",
        question_version_ids: [DRAFT_VERSION, DRAFT_VERSION],
      });
    });
    expect(await screen.findByText("2 项已全部成功。")).toBeInTheDocument();
  });

  it("批量发布的逐项结论要说清几项未处理", async () => {
    stub = installFetchStub([
      ...baseRoutes([draftQuestion("q-a"), publishedQuestion("q-published")]),
      {
        method: "POST",
        path: "/api/v1/question-versions/batch-operations",
        respond: () =>
          jsonResponse({
            results: [
              { question_version_id: DRAFT_VERSION, status: "applied", error_code: null },
              { question_version_id: UUID_B, status: "failed", error_code: "conflict" },
            ],
          }),
      },
    ]);
    const user = userEvent.setup();
    await openList();

    // 「全选当前列表」把已发布的也算进来：批量发布的版本集合只包含草稿。
    await user.click(screen.getByTestId("select-all-0"));
    await user.click(screen.getByTestId("batch-publish"));

    await waitFor(() => {
      const batches =
        stub?.requests.filter((request) =>
          request.url.pathname.endsWith("/api/v1/question-versions/batch-operations"),
        ) ?? [];
      expect(batches[0]?.body).toEqual({
        operation: "publish",
        question_version_ids: [DRAFT_VERSION],
      });
    });
    expect(await screen.findByText(/1 项成功，1 项未处理。/)).toBeInTheDocument();
  });

  it("选中的题目没有草稿版本时，不发请求，只提示原因", async () => {
    stub = installFetchStub([...baseRoutes([publishedQuestion("q-published")])]);
    const user = userEvent.setup();
    await openList();

    await user.click(screen.getByTestId("select-q-published"));
    await user.click(screen.getByTestId("batch-delete"));

    expect(
      await screen.findByText("选中的题目里没有草稿版本，无法执行这个操作。"),
    ).toBeInTheDocument();
    const batches =
      stub?.requests.filter((request) =>
        request.url.pathname.endsWith("/api/v1/question-versions/batch-operations"),
      ) ?? [];
    expect(batches).toHaveLength(0);
    // 未发出请求，但「已选 1 项」的提示仍在——选择状态本身没有被吞掉。
    expect(screen.getByTestId("batch-bar")).toHaveTextContent("已选 1 项");
  });
});

describe("题库列表：表头全选", () => {
  it("表头复选框按分组全选/取消", async () => {
    stub = installFetchStub([...baseRoutes([draftQuestion("q-a"), draftQuestion("q-b")])]);
    const user = userEvent.setup();
    await openList();

    await user.click(screen.getByTestId("select-all-0"));
    expect(screen.getByTestId("batch-bar")).toHaveTextContent("已选 2 项");
    await user.click(screen.getByTestId("select-all-0"));
    expect(screen.queryByTestId("batch-bar")).toBeNull();
    expect(UUID_A).not.toBe(DRAFT_VERSION);
  });
});
