import { afterEach, describe, expect, it, vi } from "vitest";
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
  coursePage,
  courseSummary,
  questionPublic,
  questionTagCoverage,
  sessionCreated,
  UUID_A,
  UUID_B,
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

const PUBLISHED_VERSION = "00000000-0000-4000-8000-0000000000d2";

/** 每道题一个**独立**的版本 id：共用一个 id 会让"重复 id 被 422 拒绝"这类问题看不见。 */
function versionOf(questionId: string): string {
  const digits = [...questionId].reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return `00000000-0000-4000-8000-${String(digits % 100000).padStart(12, "0")}`;
}

function draftQuestion(questionId: string) {
  return questionPublic({
    question_id: questionId,
    title: `草稿 ${questionId}`,
    status: "draft",
    versions: [{ question_version_id: versionOf(questionId), version: 1, status: "draft" }],
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
      respond: () => jsonResponse(
          coursePage([
            courseSummary(),
            // 题目夹具的课程是 UUID_B：课程目录里必须真的有它，否则这些题就是"查无此课"——
            // 线上那个「—」分组正是这么来的，用例不该再拿它当正常数据。
            courseSummary({ course_id: UUID_B, course_code: "QB", title: "题库课程" }),
          ]),
        ),
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
        path: `/api/v1/question-versions/${versionOf("q-draft")}`,
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
        `/api/v1/question-versions/${versionOf("q-draft")}`,
      );
    });
    expect(await screen.findByText("草稿已删除。")).toBeInTheDocument();
  });

  it("删除失败时如实说明原因（409 = 已发布或被作业引用）", async () => {
    stub = installFetchStub([
      ...baseRoutes([draftQuestion("q-draft")]),
      {
        method: "DELETE",
        path: `/api/v1/question-versions/${versionOf("q-draft")}`,
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
  it("勾选后出现批量操作条；批量删除只发一次请求，删掉的行真的从列表消失", async () => {
    let deleted = false;
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
      },
      {
        path: "/api/v1/courses",
        respond: () => jsonResponse(
          coursePage([
            courseSummary(),
            // 题目夹具的课程是 UUID_B：课程目录里必须真的有它，否则这些题就是"查无此课"——
            // 线上那个「—」分组正是这么来的，用例不该再拿它当正常数据。
            courseSummary({ course_id: UUID_B, course_code: "QB", title: "题库课程" }),
          ]),
        ),
      },
      {
        // 删除之后再取列表：只剩没被删的那道题（服务端真实行为）。
        method: "GET",
        path: "/api/v1/questions",
        respond: () =>
          jsonResponse({
            items: deleted ? [draftQuestion("q-b")] : [draftQuestion("q-a"), draftQuestion("q-b")],
            next_cursor: null,
          }),
      },
      {
        method: "GET",
        path: "/api/v1/questions/coverage",
        respond: () => jsonResponse(questionTagCoverage()),
      },
      {
        method: "POST",
        path: "/api/v1/question-versions/batch-operations",
        respond: ({ request }) => {
          deleted = true;
          const ids = (request.body as { question_version_ids: string[] }).question_version_ids;
          return jsonResponse({
            results: ids.map((id) => ({
              question_version_id: id,
              status: "applied",
              error_code: null,
            })),
          });
        },
      },
    ]);
    const user = userEvent.setup();
    await openList();

    // 未进入批量模式时没有任何复选框（需求 1）。
    expect(screen.queryByTestId("select-q-a")).toBeNull();
    await user.click(screen.getByTestId("batch-mode-toggle"));
    await user.click(await screen.findByTestId("select-q-a"));
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
        question_version_ids: [versionOf("q-a"), versionOf("q-b")],
      });
    });
    // 常驻结果横幅（不是 2.5 秒就消失的 toast），并把结果如实写出来。
    expect(await screen.findByTestId("batch-result")).toHaveTextContent("2 项已全部成功。");
    // 关键：被删的行必须从列表里消失，而不是"点了确认、行还在"。
    await waitFor(() => expect(screen.queryByTestId("delete-q-a")).toBeNull());
    expect(screen.getByTestId("delete-q-b")).toBeInTheDocument();
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
              { question_version_id: versionOf("q-a"), status: "applied", error_code: null },
              { question_version_id: UUID_B, status: "failed", error_code: "conflict" },
            ],
          }),
      },
    ]);
    const user = userEvent.setup();
    await openList();

    // 「全选当前列表」把已发布的也算进来：批量发布的版本集合只包含草稿。
    await user.click(screen.getByTestId("batch-mode-toggle"));
    await user.click(await screen.findByTestId("select-all-0"));
    await user.click(screen.getByTestId("batch-publish"));

    await waitFor(() => {
      const batches =
        stub?.requests.filter((request) =>
          request.url.pathname.endsWith("/api/v1/question-versions/batch-operations"),
        ) ?? [];
      expect(batches[0]?.body).toEqual({
        operation: "publish",
        question_version_ids: [versionOf("q-a")],
      });
    });
    expect(await screen.findByText(/1 项成功，1 项未处理。/)).toBeInTheDocument();
  });

  it("选中的题目没有草稿版本时，不发请求，只提示原因", async () => {
    stub = installFetchStub([...baseRoutes([publishedQuestion("q-published")])]);
    const user = userEvent.setup();
    await openList();

    await user.click(screen.getByTestId("batch-mode-toggle"));
    await user.click(await screen.findByTestId("select-q-published"));
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

    await user.click(screen.getByTestId("batch-mode-toggle"));
    await user.click(await screen.findByTestId("select-all-0"));
    expect(screen.getByTestId("batch-bar")).toHaveTextContent("已选 2 项");
    await user.click(screen.getByTestId("select-all-0"));
    expect(screen.getByTestId("batch-selected-count")).toHaveTextContent("已选 0 项");
    expect(UUID_A).not.toBe(versionOf("q-a"));
  });
});

describe("题目列表：批量操作入口与选中数量（需求 1）", () => {
  it("初始不渲染复选框；点【批量操作】后每行出现复选框，再点一次收起", async () => {
    stub = installFetchStub([...baseRoutes([draftQuestion("q-a"), draftQuestion("q-b")])]);
    const user = userEvent.setup();
    await openList();

    const toggle = screen.getByTestId("batch-mode-toggle");
    expect(toggle).toHaveTextContent("批量操作");
    expect(screen.queryByTestId("select-q-a")).toBeNull();
    expect(screen.queryByTestId("select-q-b")).toBeNull();

    await user.click(toggle);
    expect(await screen.findByTestId("select-q-a")).toBeInTheDocument();
    expect(screen.getByTestId("select-q-b")).toBeInTheDocument();

    await user.click(toggle);
    expect(screen.queryByTestId("select-q-a")).toBeNull();
  });

  it("按钮旁的数量随勾选实时变化", async () => {
    stub = installFetchStub([...baseRoutes([draftQuestion("q-a"), draftQuestion("q-b")])]);
    const user = userEvent.setup();
    await openList();

    const count = screen.getByTestId("batch-selected-count");
    expect(count).toHaveTextContent("已选 0 项");

    await user.click(screen.getByTestId("batch-mode-toggle"));
    await user.click(await screen.findByTestId("select-q-a"));
    expect(count).toHaveTextContent("已选 1 项");

    await user.click(screen.getByTestId("select-q-b"));
    expect(count).toHaveTextContent("已选 2 项");

    await user.click(screen.getByTestId("select-q-a"));
    expect(count).toHaveTextContent("已选 1 项");
  });

  it("勾选框使用强调色实心样式（accent 类名）", async () => {
    stub = installFetchStub([...baseRoutes([draftQuestion("q-a")])]);
    const user = userEvent.setup();
    await openList();

    await user.click(screen.getByTestId("batch-mode-toggle"));
    const box = (await screen.findByTestId("select-q-a")) as HTMLInputElement;
    expect(box.className).toContain("question-select");
    expect(box.type).toBe("checkbox");
  });
});

describe("题目列表：操作按钮同一行（需求 2）", () => {
  it("继续编辑 / 发布 / 删除三个按钮在同一个 nowrap 容器里", async () => {
    stub = installFetchStub([...baseRoutes([draftQuestion("q-draft")])]);
    await openList();

    const remove = screen.getByTestId("delete-q-draft");
    const container = remove.parentElement as HTMLElement;
    expect(container.className).toBe("row-actions");
    // 同一容器内也有编辑与发布——"不要单独换行"就是"不能各占一个容器"。
    expect(within(container).getByTestId("edit-q-draft")).toBeInTheDocument();
    expect(within(container).getByTestId("publish-q-draft")).toBeInTheDocument();
  });
});

describe("题目列表：单条删除后保持滚动位置（需求 3）", () => {
  it("删除一道题后把视口放回删除前的位置，而不是回到顶部", async () => {
    // 浏览器在列表重排时会把视口带回顶部；这里用一个可变的 scrollY 模拟那一跳：
    // 请求发出时记下当前位置，然后把它改成 0（"已经跳回顶部"），看页面是否恢复。
    let scrollPosition = 640;
    const scrollTargets: number[] = [];
    let scrollYSpy: { mockRestore: () => void } | null = null;

    stub = installFetchStub([
      ...baseRoutes([draftQuestion("q-a"), draftQuestion("q-b")]),
      {
        method: "DELETE",
        path: `/api/v1/question-versions/${versionOf("q-a")}`,
        respond: () => {
          // DELETE 发出后列表重排，浏览器把视口带回顶部。
          scrollPosition = 0;
          return new Response(null, { status: 204 });
        },
      },
    ]);

    try {
      const user = userEvent.setup();
      await openList();

      // 登录/渲染过程本身会动到这两个全局，所以模拟"滚动位置"放在列表出现之后。
      scrollYSpy = vi.spyOn(window, "scrollY", "get").mockImplementation(() => scrollPosition);
      vi.spyOn(window, "scrollTo").mockImplementation((options) => {
        // 只把**明确给了目标位置**的调用记成"页面被滚到某处"。
        const top =
          typeof options === "number" ? options : (options as ScrollToOptions | undefined)?.top;
        if (typeof top === "number") {
          scrollPosition = top;
          scrollTargets.push(top);
        }
      });

      await user.click(screen.getByTestId("delete-q-a"));
      await user.click(
        within(await screen.findByTestId("delete-draft-modal")).getByTestId(
          "delete-draft-confirm",
        ),
      );
      // 恢复是在渲染之后做的（两次动画帧 + 一次 250ms 兜底），所以等"滚到 640"这件事本身。
      await waitFor(() => expect(scrollTargets).toContain(640), { timeout: 2000 });
      expect(scrollPosition).toBe(640);
    } finally {
      scrollYSpy?.mockRestore();
    }
  });
});

describe("题库列表：批量删除失败要说明真实原因", () => {
  it("422 时横幅带上服务端 detail，而不是笼统的\"请稍后重试\"", async () => {
    stub = installFetchStub([
      ...baseRoutes([draftQuestion("q-a")]),
      {
        method: "POST",
        path: "/api/v1/question-versions/batch-operations",
        respond: () =>
          jsonResponse(
            {
              type: "https://autograder.local/problems/unprocessable-entity",
              title: "Unprocessable Entity",
              status: 422,
              detail: "question_version_ids must not contain duplicates",
              instance: "/api/v1/question-versions/batch-operations",
            },
            422,
          ),
      },
    ]);
    const user = userEvent.setup();
    await openList();

    await user.click(screen.getByTestId("batch-mode-toggle"));
    await user.click(await screen.findByTestId("select-q-a"));
    await user.click(screen.getByTestId("batch-delete"));
    await user.click(
      within(await screen.findByTestId("delete-draft-modal")).getByTestId(
        "delete-draft-confirm",
      ),
    );

    const banner = await screen.findByTestId("batch-result");
    expect(banner.textContent ?? "").toContain("must not contain duplicates");
    expect(banner.textContent ?? "").not.toContain("请稍后重试");
  });

  it("401 时提示登录过期（而不是让人一直重试）", async () => {
    stub = installFetchStub([
      ...baseRoutes([draftQuestion("q-a")]),
      {
        method: "POST",
        path: "/api/v1/question-versions/batch-operations",
        respond: () =>
          jsonResponse(
            {
              type: "https://autograder.local/problems/unauthorized",
              title: "Unauthorized",
              status: 401,
              detail: "a bearer token is required",
              instance: "/api/v1/question-versions/batch-operations",
            },
            401,
          ),
      },
    ]);
    const user = userEvent.setup();
    await openList();

    await user.click(screen.getByTestId("batch-mode-toggle"));
    await user.click(await screen.findByTestId("select-q-a"));
    await user.click(screen.getByTestId("batch-delete"));
    await user.click(
      within(await screen.findByTestId("delete-draft-modal")).getByTestId(
        "delete-draft-confirm",
      ),
    );

    expect(await screen.findByTestId("batch-result")).toHaveTextContent("登录已过期");
  });
});

describe("题库列表：批量删除的结果必须可见", () => {
  it("逐项失败时用常驻横幅写清原因，而不是只闪一个 toast", async () => {
    stub = installFetchStub([
      ...baseRoutes([draftQuestion("q-a")]),
      {
        method: "POST",
        path: "/api/v1/question-versions/batch-operations",
        respond: () =>
          jsonResponse({
            results: [
              { question_version_id: versionOf("q-a"), status: "failed", error_code: "forbidden" },
            ],
          }),
      },
    ]);
    const user = userEvent.setup();
    await openList();

    await user.click(screen.getByTestId("batch-mode-toggle"));
    await user.click(await screen.findByTestId("select-q-a"));
    await user.click(screen.getByTestId("batch-delete"));
    await user.click(
      within(await screen.findByTestId("delete-draft-modal")).getByTestId(
        "delete-draft-confirm",
      ),
    );

    const banner = await screen.findByTestId("batch-result");
    expect(banner.textContent ?? "").toContain("0 项成功，1 项未处理。");
    expect(banner.textContent ?? "").toContain("没有权限");
    // 没删掉的行必须还在（不能因为"结果不好看"就把行摘掉）。
    expect(screen.getByTestId("delete-q-a")).toBeInTheDocument();
  });

  it("批量删除进行中与完成后都把选择清空，但结果横幅留着", async () => {
    stub = installFetchStub([
      ...baseRoutes([draftQuestion("q-a")]),
      {
        method: "POST",
        path: "/api/v1/question-versions/batch-operations",
        respond: () =>
          jsonResponse({
            results: [
              { question_version_id: versionOf("q-a"), status: "applied", error_code: null },
            ],
          }),
      },
    ]);
    const user = userEvent.setup();
    await openList();

    await user.click(screen.getByTestId("batch-mode-toggle"));
    await user.click(await screen.findByTestId("select-q-a"));
    expect(screen.getByTestId("batch-selected-count")).toHaveTextContent("已选 1 项");

    await user.click(screen.getByTestId("batch-delete"));
    await user.click(
      within(await screen.findByTestId("delete-draft-modal")).getByTestId(
        "delete-draft-confirm",
      ),
    );

    await waitFor(() =>
      expect(screen.getByTestId("batch-selected-count")).toHaveTextContent("已选 0 项"),
    );
    expect(screen.getByTestId("batch-result")).toHaveTextContent("1 项已全部成功。");
    await user.click(screen.getByTestId("batch-result-dismiss"));
    expect(screen.queryByTestId("batch-result")).toBeNull();
  });
});

describe("题库列表：分页重叠不会重复发版本 id（契约 uniqueItems）", () => {
  it("两页里出现同一道题时只渲染一行，批量删除的 id 也不重复", async () => {
    // 游标分页是两次请求：中间有人新建题目时页边界会滑动，同一道题就会出现在两页里。
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
      },
      {
        path: "/api/v1/courses",
        respond: () => jsonResponse(
          coursePage([
            courseSummary(),
            // 题目夹具的课程是 UUID_B：课程目录里必须真的有它，否则这些题就是"查无此课"——
            // 线上那个「—」分组正是这么来的，用例不该再拿它当正常数据。
            courseSummary({ course_id: UUID_B, course_code: "QB", title: "题库课程" }),
          ]),
        ),
      },
      {
        method: "GET",
        path: "/api/v1/questions",
        respond: ({ url }) =>
          jsonResponse(
            url.searchParams.get("cursor") === null
              ? { items: [draftQuestion("q-a"), draftQuestion("q-b")], next_cursor: "cursor-2" }
              : // 第二页把 q-b 又带了一次
                { items: [draftQuestion("q-b"), draftQuestion("q-c")], next_cursor: null },
          ),
      },
      {
        method: "GET",
        path: "/api/v1/questions/coverage",
        respond: () => jsonResponse(questionTagCoverage()),
      },
      {
        method: "POST",
        path: "/api/v1/question-versions/batch-operations",
        respond: ({ request }) => {
          const ids = (request.body as { question_version_ids: string[] }).question_version_ids;
          return jsonResponse({
            results: ids.map((id) => ({
              question_version_id: id,
              status: "applied",
              error_code: null,
            })),
          });
        },
      },
    ]);
    const user = userEvent.setup();
    await openList();

    await user.click(screen.getByTestId("batch-mode-toggle"));
    await user.click(screen.getByTestId("questions-load-more"));
    await waitFor(() => expect(screen.getByTestId("delete-q-c")).toBeInTheDocument());

    // 同一道题只渲染一次（q-b 不会有两行）。
    expect(screen.getAllByTestId("delete-q-b")).toHaveLength(1);

    for (const box of screen.getAllByTestId(/select-all-/)) {
      await user.click(box);
    }
    await user.click(screen.getByTestId("batch-delete"));
    await user.click(
      within(await screen.findByTestId("delete-draft-modal")).getByTestId("delete-draft-confirm"),
    );

    await waitFor(() => {
      const batches =
        stub?.requests.filter((request) =>
          request.url.pathname.endsWith("/api/v1/question-versions/batch-operations"),
        ) ?? [];
      const ids = (batches[0]?.body as { question_version_ids: string[] }).question_version_ids;
      // 三道题、三个版本，且**不含重复**（重复会被契约的 uniqueItems 判 422）。
      expect(ids).toHaveLength(3);
      expect(new Set(ids).size).toBe(3);
    });
  });
});

describe("批量操作：取消选择", () => {
  it("点「取消选择」清空勾选、计数归零、退出批量模式，一次点击回到正常列表", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
      },
      {
        path: "/api/v1/courses",
        respond: () => jsonResponse(
          coursePage([
            courseSummary(),
            // 题目夹具的课程是 UUID_B：课程目录里必须真的有它，否则这些题就是"查无此课"——
            // 线上那个「—」分组正是这么来的，用例不该再拿它当正常数据。
            courseSummary({ course_id: UUID_B, course_code: "QB", title: "题库课程" }),
          ]),
        ),
      },
      {
        method: "GET",
        path: "/api/v1/questions",
        respond: () => jsonResponse({ items: [draftQuestion("q-a"), draftQuestion("q-b")], next_cursor: null }),
      },
      {
        method: "GET",
        path: "/api/v1/questions/coverage",
        respond: () => jsonResponse(questionTagCoverage()),
      },
    ]);
    const user = userEvent.setup();
    await openList();

    const toggle = screen.getByTestId("batch-mode-toggle");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await user.click(await screen.findByTestId("select-q-a"));
    await user.click(screen.getByTestId("select-q-b"));
    expect(screen.getByTestId("batch-bar")).toHaveTextContent("已选 2 项");

    // 一次点击就要回到正常列表：不是"只清勾选、还得再点一次批量操作"。
    await user.click(screen.getByTestId("batch-clear"));

    expect(screen.queryByTestId("batch-bar")).toBeNull();
    expect(screen.queryByTestId("batch-publish")).toBeNull();
    expect(screen.queryByTestId("batch-delete")).toBeNull();
    expect(screen.queryByTestId("batch-clear")).toBeNull();
    // 勾选框随之消失（批量模式已退出），计数归零。
    expect(screen.queryByTestId("select-q-a")).toBeNull();
    expect(screen.getByTestId("batch-selected-count")).toHaveTextContent("已选 0 项");
    expect(screen.getByTestId("batch-mode-toggle")).toHaveAttribute("aria-pressed", "false");

    // 「批量操作」仍可再次打开，重新进入时是干净的（没有残留勾选）。
    await user.click(screen.getByTestId("batch-mode-toggle"));
    expect(await screen.findByTestId("batch-bar")).toHaveTextContent("已选 0 项");
    expect(screen.getByTestId("select-q-a")).not.toBeChecked();
  });
});
