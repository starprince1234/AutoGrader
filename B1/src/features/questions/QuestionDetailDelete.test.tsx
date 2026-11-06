import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { installFetchStub, jsonResponse, type FetchStub } from "../../testing/httpBoundary";
import {
  questionPublic,
  questionVersionPublic,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { QuestionDetailPage } from "./QuestionDetailPage";

/**
 * 题目详情页的「删除草稿」。
 *
 * 固定的三件事：按钮只对**草稿版本**出现、点击先确认再发 `DELETE`、成功之后回到题库列表
 * （题目被软删后，详情页已经没有可看的内容）。
 */

const QUESTION_ID = "00000000-0000-4000-8000-0000000000e1";
const DRAFT_VERSION = "00000000-0000-4000-8000-0000000000e2";
const PUBLISHED_VERSION = "00000000-0000-4000-8000-0000000000e3";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function Harness() {
  const [versionId, setVersionId] = useState<string | null>(null);
  return (
    <QuestionDetailPage
      questionId={QUESTION_ID}
      versionId={versionId}
      onSelectVersion={setVersionId}
    />
  );
}

function renderDetail() {
  // 根路由只负责渲染子路由：Harness 挂在 `/` 上，跳转后才会真的换成列表页。
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: Harness,
  });
  const listRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions",
    component: () => <span>list target</span>,
  });
  const editRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions/$questionId/edit",
    component: () => <span>edit target</span>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute, listRoute, editRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}

const draftQuestion = () =>
  questionPublic({
    question_id: QUESTION_ID,
    status: "draft",
    versions: [{ question_version_id: DRAFT_VERSION, version: 1, status: "draft" }],
  });

const publishedQuestion = () =>
  questionPublic({
    question_id: QUESTION_ID,
    status: "published",
    versions: [
      { question_version_id: PUBLISHED_VERSION, version: 1, status: "published" },
    ],
  });

describe("题目详情：删除草稿", () => {
  it("草稿题在「编辑草稿」之后显示「删除草稿」，确认后发 DELETE 并回到题库列表", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
      },
      {
        method: "GET",
        path: `/api/v1/questions/${QUESTION_ID}`,
        respond: () => jsonResponse(draftQuestion()),
      },
      {
        method: "GET",
        path: `/api/v1/question-versions/${DRAFT_VERSION}`,
        respond: () =>
          jsonResponse(
            questionVersionPublic({
              question_version_id: DRAFT_VERSION,
              version: 1,
              status: "draft",
            }),
          ),
      },
      {
        method: "DELETE",
        path: `/api/v1/question-versions/${DRAFT_VERSION}`,
        respond: () => new Response(null, { status: 204 }),
      },
    ]);

    await signIn("teacher1", "pw");
    const user = userEvent.setup();
    renderDetail();

    const edit = await screen.findByTestId("edit-question");
    const remove = await screen.findByTestId("delete-draft");
    // 位置要求：「删除草稿」紧跟在「编辑草稿」后面。
    expect(edit.nextSibling).toBe(remove);

    await user.click(remove);
    const modal = await screen.findByTestId("delete-draft-modal");
    await user.click(within(modal).getByTestId("delete-draft-confirm"));

    await waitFor(() => {
      const deletes = stub?.requests.filter((request) => request.method === "DELETE") ?? [];
      expect(deletes).toHaveLength(1);
      expect(deletes[0]?.url.pathname).toBe(`/api/v1/question-versions/${DRAFT_VERSION}`);
    });
    expect(await screen.findByText("list target")).toBeInTheDocument();
  });

  it("已发布题没有「删除草稿」（只能归档）", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
      },
      {
        method: "GET",
        path: `/api/v1/questions/${QUESTION_ID}`,
        respond: () => jsonResponse(publishedQuestion()),
      },
      {
        method: "GET",
        path: `/api/v1/question-versions/${PUBLISHED_VERSION}`,
        respond: () =>
          jsonResponse(
            questionVersionPublic({
              question_version_id: PUBLISHED_VERSION,
              version: 1,
              status: "published",
            }),
          ),
      },
    ]);

    await signIn("teacher1", "pw");
    renderDetail();

    await screen.findByTestId("edit-question");
    expect(screen.queryByTestId("delete-draft")).toBeNull();
  });

  it("删除被拒（409）时留在原页并说明原因", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
      },
      {
        method: "GET",
        path: `/api/v1/questions/${QUESTION_ID}`,
        respond: () => jsonResponse(draftQuestion()),
      },
      {
        method: "GET",
        path: `/api/v1/question-versions/${DRAFT_VERSION}`,
        respond: () =>
          jsonResponse(
            questionVersionPublic({
              question_version_id: DRAFT_VERSION,
              version: 1,
              status: "draft",
            }),
          ),
      },
      {
        method: "DELETE",
        path: `/api/v1/question-versions/${DRAFT_VERSION}`,
        respond: () =>
          jsonResponse(
            {
              type: "https://autograder.local/problems/conflict",
              title: "Conflict",
              status: 409,
              detail: "referenced by an assignment",
              instance: "/api/v1/question-versions/x",
            },
            409,
          ),
      },
    ]);

    await signIn("teacher1", "pw");
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByTestId("delete-draft"));
    await user.click(
      within(await screen.findByTestId("delete-draft-modal")).getByTestId(
        "delete-draft-confirm",
      ),
    );

    const banner = await screen.findByTestId("delete-draft-error");
    expect(banner.textContent ?? "").toMatch(/不能删除|没有成功/);
    expect(screen.queryByText("list target")).toBeNull();
  });
});
