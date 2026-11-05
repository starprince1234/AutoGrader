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
import { installFetchStub, jsonResponse, type StubbedRoute, type FetchStub } from "../../testing/httpBoundary";
import {
  courseSummary,
  questionPublic,
  questionTagCoverage,
  questionVersionPublic,
  referenceVerification,
  sessionCreated,
  UUID_A,
  UUID_B,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { QuestionListPage } from "./QuestionListPage";
import { QUESTION_PAGE_SIZE } from "./useQuestions";

/**
 * 题库可以大于一页（契约的 `limit` 上限是 200），因此列表必须真的翻页。
 * 只取第一页而不给出"还有更多"的信号，会让后面的题目**静默消失**——这是本轮导入
 * 100 道题时暴露出来的问题，所以这里逐条固定翻页行为。
 */

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function pageOf(ids: string[], nextCursor: string | null) {
  return {
    items: ids.map((id) => questionPublic({ question_id: id })),
    next_cursor: nextCursor,
  };
}

function routes(pages: Record<string, ReturnType<typeof pageOf>>): StubbedRoute[] {
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
      respond: ({ url }) => {
        const cursor = url.searchParams.get("cursor");
        // 第一页不带 `cursor`。
        return jsonResponse(cursor === null ? pages[""] : pages[cursor]);
      },
    },
    {
      // 标签覆盖度（CHG-0020）：只要选了课程，本页就会为它单独发一次请求。
      method: "GET",
      path: "/api/v1/questions/coverage",
      respond: () => jsonResponse(questionTagCoverage()),
    },
  ];
}

/**
 * 表格每行的标题是指向详情页的 `<Link>`，出题角色还会看到「新建题目」与逐行编辑入口，
 * 因此需要一个最小路由上下文（只用测试路由表，不引入应用路由表，避免测试与应用装配耦合）。
 */
function renderQuestionList() {
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
  const testRouter = createRouter({
    routeTree: rootRoute.addChildren([newRoute, detailRoute, editRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return renderWithProviders(<RouterProvider router={testRouter} />);
}

async function open(user: ReturnType<typeof userEvent.setup>) {
  await signIn("teacher1", "pw");
  renderQuestionList();
  await screen.findByTestId("question-table");
  return user;
}

describe("题目列表的游标分页", () => {
  it("首屏只加载第一页，并明确给出「还有更多」的入口与已加载数量", async () => {
    stub = installFetchStub(
      routes({
        "": pageOf(["q-1", "q-2"], "cursor-2"),
        "cursor-2": pageOf(["q-3"], null),
      }),
    );
    await open(userEvent.setup());

    const table = screen.getByTestId("question-table");
    expect(within(table).getAllByRole("row")).toHaveLength(3); // 表头 + 2 行
    expect(screen.getByText(/已加载 2 道/)).toBeInTheDocument();
    expect(screen.getByTestId("questions-load-more")).toBeInTheDocument();

    // 请求必须带上契约的 limit，且第一页不带 cursor。
    const first = stub.requests.find((request) =>
      request.url.pathname.endsWith("/api/v1/questions"),
    );
    expect(first?.url.searchParams.get("limit")).toBe(String(QUESTION_PAGE_SIZE));
    expect(first?.url.searchParams.get("cursor")).toBeNull();
  });

  it("「加载更多」追加下一页，到末页后入口消失并显示已到末页", async () => {
    stub = installFetchStub(
      routes({
        "": pageOf(["q-1", "q-2"], "cursor-2"),
        "cursor-2": pageOf(["q-3"], null),
      }),
    );
    const user = await open(userEvent.setup());

    await user.click(screen.getByTestId("questions-load-more"));

    const table = screen.getByTestId("question-table");
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(4)); // 表头 + 3 行
    expect(screen.getByText(/已加载 3 道/)).toBeInTheDocument();
    expect(screen.queryByTestId("questions-load-more")).toBeNull();
    expect(screen.getByTestId("questions-end")).toBeInTheDocument();

    const second = stub.requests.filter((request) =>
      request.url.pathname.endsWith("/api/v1/questions"),
    );
    expect(second.at(-1)?.url.searchParams.get("cursor")).toBe("cursor-2");
  });

  it("单页就到底时不显示「加载更多」，只显示已到末页", async () => {
    stub = installFetchStub(routes({ "": pageOf(["q-1"], null) }));
    await open(userEvent.setup());

    expect(screen.queryByTestId("questions-load-more")).toBeNull();
    expect(screen.getByTestId("questions-end")).toBeInTheDocument();
    expect(screen.getByText(/已加载 1 道/)).toBeInTheDocument();
  });

  it("按课程筛选会把 course_id 带给服务端（分页不改变筛选语义）", async () => {
    stub = installFetchStub(
      routes({ "": pageOf(["q-1"], "cursor-2"), "cursor-2": pageOf(["q-2"], null) }),
    );
    const user = await open(userEvent.setup());

    await user.selectOptions(await screen.findByLabelText("课程"), UUID_A);

    await waitFor(() => {
      const filtered = stub?.requests.filter(
        (request) =>
          request.url.pathname.endsWith("/api/v1/questions") &&
          request.url.searchParams.get("course_id") === UUID_A,
      );
      expect(filtered?.length ?? 0).toBeGreaterThan(0);
    });
  });

  it("发布状态下拉：默认「已发布」，切到「草稿」只留下草稿", async () => {
    stub = installFetchStub(
      routes({
        "": {
          items: [
            questionPublic({ question_id: "q-published", status: "published" }),
            questionPublic({
              question_id: "q-draft",
              status: "draft",
              versions: [{ question_version_id: UUID_B, version: 1, status: "draft" }],
            }),
          ],
          next_cursor: null,
        },
      }),
    );
    const user = await open(userEvent.setup());

    const state = screen.getByLabelText("发布状态") as HTMLSelectElement;
    expect(state.tagName).toBe("SELECT");
    // 3.1：新增「全部状态」并作为默认。
    expect([...state.options].map((option) => option.value)).toEqual([
      "all",
      "published",
      "draft",
    ]);
    expect(state.value).toBe("all");
    // 默认「全部状态」= 契约的 published_only=false（连草稿一起），且客户端不过滤。
    await waitFor(() => {
      const first = stub?.requests.find((request) =>
        request.url.pathname.endsWith("/api/v1/questions"),
      );
      expect(first?.url.searchParams.get("published_only")).toBe("false");
    });
    const initialTable = screen.getByTestId("question-table");
    await waitFor(() => expect(within(initialTable).getAllByRole("row")).toHaveLength(3)); // 表头 + 2 行

    // 切到「已发布」：请求改成 published_only=true。
    await user.selectOptions(state, "published");
    await waitFor(() => {
      const last = stub?.requests
        .filter((request) => request.url.pathname.endsWith("/api/v1/questions"))
        .at(-1);
      expect(last?.url.searchParams.get("published_only")).toBe("true");
    });

    // 契约没有"只要草稿"的参数：这一档是 published_only=false + 客户端按状态过滤。
    await user.selectOptions(state, "draft");
    await waitFor(() => {
      const last = stub?.requests
        .filter((request) => request.url.pathname.endsWith("/api/v1/questions"))
        .at(-1);
      expect(last?.url.searchParams.get("published_only")).toBe("false");
    });
    const table = screen.getByTestId("question-table");
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(2)); // 表头 + 1 行草稿
    expect(within(table).getByText("统计文件行数")).toBeInTheDocument();
  });

  it("题干搜索框按已加载题目的题干文字过滤，并给出无命中提示", async () => {
    stub = installFetchStub(
      routes({
        "": {
          items: [
            questionPublic({ question_id: "q-1", stem_markdown: "# 统计行数\n实现 wc -l" }),
            questionPublic({ question_id: "q-2", title: "字符串反转", stem_markdown: "把输入反转输出" }),
          ],
          next_cursor: null,
        },
      }),
    );
    const user = await open(userEvent.setup());

    const table = screen.getByTestId("question-table");
    expect(within(table).getAllByRole("row")).toHaveLength(3);

    await user.type(screen.getByLabelText("搜索题干"), "wc -l");
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(2));
    expect(within(table).getByText("统计文件行数")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("搜索题干"));
    await user.type(screen.getByLabelText("搜索题干"), "不存在的文字");
    expect(await screen.findByText("没有找到包含这些文字的题目")).toBeInTheDocument();
  });

  it("题目列表在知识点覆盖之前，新建题目在「题库」标题右侧", async () => {
    stub = installFetchStub(routes({ "": pageOf(["q-1"], null) }));
    await open(userEvent.setup());

    const table = screen.getByTestId("question-table");
    const coverage = screen.getByTestId("question-coverage");
    // DOCUMENT_POSITION_FOLLOWING = 4：覆盖度必须排在列表之后。
    expect(table.compareDocumentPosition(coverage) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const link = screen.getByTestId("question-new");
    expect(link.closest("div.inline")?.textContent).toContain("题库");
  });

  it("标签改成下拉：选项来自题库里已有的标签，选中后按 tags 单选发送", async () => {
    stub = installFetchStub(
      routes({
        "": {
          items: [
            questionPublic({ question_id: "q-1", status: "published", tags: ["unix"] }),
            questionPublic({ question_id: "q-2", status: "published", tags: ["scheduling"] }),
          ],
          next_cursor: null,
        },
      }),
    );
    const user = await open(userEvent.setup());

    const tags = screen.getByLabelText("标签") as HTMLSelectElement;
    expect(tags.tagName).toBe("SELECT");
    // 「全部标签」+ 已加载题目里出现过的标签（去重、排序）。
    expect([...tags.options].map((option) => option.value)).toEqual([
      "",
      "scheduling",
      "unix",
    ]);
    expect(tags.value).toBe("");

    await user.selectOptions(tags, "unix");
    await waitFor(() => {
      const last = stub?.requests
        .filter((request) => request.url.pathname.endsWith("/api/v1/questions"))
        .at(-1);
      expect(last?.url.searchParams.getAll("tags")).toEqual(["unix"]);
    });
  });

  it("题目列表显示标签（写路径补上之后，读路径必须能看到标了什么）", async () => {
    stub = installFetchStub([
      ...routes({ "": { items: [questionPublic({ tags: ["unix", "scheduling"] })], next_cursor: null } }),
    ]);
    await open(userEvent.setup());

    const table = await screen.findByTestId("question-table");
    expect(within(table).getByText("unix")).toBeInTheDocument();
    expect(within(table).getByText("scheduling")).toBeInTheDocument();
  });

  it("草稿行可以直接在列表发布：先请求检验，通过后立即发布", async () => {
    stub = installFetchStub([
      ...routes({
        "": {
          items: [
            questionPublic({
              question_id: "q-draft",
              status: "draft",
              versions: [{ question_version_id: UUID_B, version: 1, status: "draft" }],
            }),
          ],
          next_cursor: null,
        },
      }),
      {
        method: "POST",
        path: "/api/v1/reference-verifications",
        respond: () => jsonResponse(referenceVerification()),
      },
      {
        method: "POST",
        path: `/api/v1/question-versions/${UUID_B}/publish`,
        respond: () => jsonResponse(questionVersionPublic({ status: "published" })),
      },
    ]);
    const user = await open(userEvent.setup());

    const publish = await screen.findByTestId("publish-q-draft");
    expect(publish).toBeEnabled();
    await user.click(publish);

    // 顺序是"先检验、再发布"：发布门禁要求存证已记录。
    await waitFor(() => {
      const verifyRequest = stub?.requests.find((request) =>
        request.url.pathname.endsWith("/api/v1/reference-verifications"),
      );
      expect(verifyRequest?.body).toEqual({ question_version_id: UUID_B });
    });
    await waitFor(() => {
      const publishRequest = stub?.requests.find(
        (request) => request.url.pathname === `/api/v1/question-versions/${UUID_B}/publish`,
      );
      expect(publishRequest?.method).toBe("POST");
    });
    expect(await screen.findByTestId("toast")).toHaveTextContent("已发布");
  });

  it("检验没通过时不发布，只说明原因", async () => {
    stub = installFetchStub([
      ...routes({
        "": {
          items: [
            questionPublic({
              question_id: "q-draft",
              status: "draft",
              versions: [{ question_version_id: UUID_B, version: 1, status: "draft" }],
            }),
          ],
          next_cursor: null,
        },
      }),
      {
        method: "POST",
        path: "/api/v1/reference-verifications",
        respond: () =>
          jsonResponse(
            referenceVerification({ cases_passed: 1, cases_total: 2, attestation_id: null }),
          ),
      },
    ]);
    const user = await open(userEvent.setup());

    await user.click(await screen.findByTestId("publish-q-draft"));
    expect(await screen.findByTestId("toast")).toHaveTextContent("还没通过检验");
    expect(
      stub.requests.filter((request) => request.url.pathname.endsWith("/publish")),
    ).toHaveLength(0);
  });

  it("已发布题目的「发布」按钮置灰", async () => {
    stub = installFetchStub(routes({ "": pageOf(["q-1"], null) }));
    await open(userEvent.setup());

    // 夹具里的 q-1 是已发布状态。
    expect(await screen.findByTestId("publish-q-1")).toBeDisabled();
  });

  it("出题角色看到「新建题目」与逐行编辑入口", async () => {
    stub = installFetchStub(routes({ "": pageOf(["q-1"], null) }));
    await open(userEvent.setup());

    expect(await screen.findByText("新建题目")).toBeInTheDocument();
    expect(screen.getByTestId("edit-q-1")).toBeInTheDocument();
  });

  it("学生看不到题库列表、出题入口、草稿开关与覆盖度（且不发题库请求）", async () => {
    stub = installFetchStub(
      routes({ "": pageOf(["q-1"], null) }).map((route) =>
        route.path === "/api/v1/auth/session"
          ? {
              method: "POST",
              path: "/api/v1/auth/session",
              respond: () => jsonResponse(sessionCreated({ roles: ["student"] })),
            }
          : route,
      ),
    );
    await signIn("student1", "pw");
    renderQuestionList();

    // 学生由作业进入题目，不浏览全量题库：这一页对他只说明去哪里看题。
    expect(await screen.findByTestId("question-list-staff-only")).toBeInTheDocument();
    expect(screen.queryByTestId("question-table")).toBeNull();
    expect(screen.queryByText("新建题目")).toBeNull();
    expect(screen.queryByTestId("edit-q-1")).toBeNull();
    expect(screen.queryByLabelText("搜索题干")).toBeNull();
    // 覆盖度同样不存在（它既不该显示，也不该被请求）。
    expect(screen.queryByTestId("question-coverage")).toBeNull();
    await waitFor(() => {
      const questionRequests = stub?.requests.filter((request) =>
        request.url.pathname.endsWith("/api/v1/questions"),
      );
      expect(questionRequests).toHaveLength(0);
      const coverageRequests = stub?.requests.filter((request) =>
        request.url.pathname.endsWith("/coverage"),
      );
      expect(coverageRequests).toHaveLength(0);
    });
  });
});

describe("题目列表上的标签覆盖度（CHG-0020）", () => {
  it("选课程后，覆盖度与列表落在同一个作用域（course_id 与 published_only 一起变）", async () => {
    stub = installFetchStub(routes({ "": pageOf(["q-1"], null) }));
    const user = await open(userEvent.setup());

    // 没有课程作用域时契约的 course_id 填不出来：一格请求都不发。
    expect(screen.getByTestId("coverage-no-course")).toBeInTheDocument();
    expect(
      stub.requests.filter((request) => request.url.pathname.endsWith("/coverage")),
    ).toHaveLength(0);

    await user.selectOptions(await screen.findByLabelText("课程"), UUID_A);
    await screen.findByTestId("coverage-body");

    const firstCoverage = stub.requests.find((request) =>
      request.url.pathname.endsWith("/coverage"),
    );
    expect(firstCoverage?.url.searchParams.get("course_id")).toBe(UUID_A);
    // 两个数字必须来自同一个作用域：默认「全部状态」时两边都是 published_only=false。
    expect(firstCoverage?.url.searchParams.get("published_only")).toBe("false");
    const lastQuestions = stub.requests
      .filter((request) => request.url.pathname.endsWith("/api/v1/questions"))
      .at(-1);
    expect(lastQuestions?.url.searchParams.get("published_only")).toBe("false");
  });

  it("点覆盖度里的标签即填入既有标签筛选（复用 tags 重复参数）", async () => {
    stub = installFetchStub(routes({ "": pageOf(["q-1"], null) }));
    const user = await open(userEvent.setup());

    await user.selectOptions(await screen.findByLabelText("课程"), UUID_A);
    await user.click(await screen.findByTestId("coverage-tag-unix"));

    // 填进的是既有的标签下拉，请求走既有的 `tags` 重复参数（explode=true）。
    expect((screen.getByLabelText("标签") as HTMLSelectElement).value).toBe("unix");
    await waitFor(() => {
      const last = stub?.requests
        .filter((request) => request.url.pathname.endsWith("/api/v1/questions"))
        .at(-1);
      expect(last?.url.searchParams.getAll("tags")).toEqual(["unix"]);
    });
  });
});
