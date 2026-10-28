import { afterEach, describe, expect, it } from "vitest";
import { useState } from "react";
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
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type StubbedRoute,
  type FetchStub,
} from "../../testing/httpBoundary";
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
  questionVersionPublic,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { setLogSink } from "../../logging/logger";
import { AuthoringPage } from "./AuthoringPage";

/**
 * 出题页：写路径、受保护字段的不可回显约束、发布门禁与两类信号面板。
 *
 * 这里刻意断言**请求体**而不只是页面文案：出题是一个写操作，"表单看起来能用"与
 * "发出去的 JSON 是契约形状"是两件事。
 */

const QUESTION_ID = questionPublic().question_id;
const DRAFT_VERSION = "00000000-0000-4000-8000-0000000000d1";
const NEW_VERSION = "00000000-0000-4000-8000-0000000000d2";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function draftQuestion() {
  return questionPublic({
    status: "draft",
    versions: [
      {
        question_version_id: DRAFT_VERSION,
        version: 1,
        status: "draft",
        published_at: "2026-09-15T07:20:00.000Z",
      },
    ],
  });
}

function publishedQuestion() {
  return questionPublic({
    status: "published",
    versions: [
      {
        question_version_id: UUID_A,
        version: 2,
        status: "published",
        published_at: "2026-09-15T07:20:00.000Z",
      },
    ],
  });
}

function baseRoutes(extra: StubbedRoute[] = []): StubbedRoute[] {
  // `extra` 排在前面：`installFetchStub` 取第一个匹配的路由，因此测试可以用同名路径覆盖默认边界。
  return [
    ...extra,
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
    },
    {
      path: "/api/v1/courses",
      respond: () => jsonResponse(coursePage([courseSummary()])),
    },
    {
      path: `/api/v1/questions/${QUESTION_ID}/diagnostics`,
      respond: () => jsonResponse(comparisonDiagnostics({ findings: [] })),
    },
  ];
}

/** 出题页现在有「向导」与「AI 出题」两个视图，视图开关由父级持有（真实页面里是 URL 上的 ai）。 */
function Harness({
  questionId,
  generationId = null,
}: {
  questionId: string | null;
  generationId?: string | null;
}) {
  const [aiView, setAiView] = useState(false);
  return (
    <AuthoringPage
      questionId={questionId}
      generationId={generationId}
      aiView={aiView}
      onAiViewChange={setAiView}
    />
  );
}

function renderAuthoring(questionId: string | null, generationId: string | null = null) {
  const rootRoute = createRootRoute({
    component: () => <Harness questionId={questionId} generationId={generationId} />,
  });
  const detailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions/$questionId",
    component: () => <span>detail target</span>,
  });
  const editRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions/$questionId/edit",
    component: () => <span>edit target</span>,
  });
  const testRouter = createRouter({
    routeTree: rootRoute.addChildren([detailRoute, editRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return renderWithProviders(<RouterProvider router={testRouter} />);
}

/** 课程下拉的选项来自 B4，必须先等它渲染出来再选（否则 selectOptions 找不到该值）。 */
async function selectCourse(user: ReturnType<typeof userEvent.setup>) {
  await goToStep(user, "basic");
  const courseSelect = await screen.findByLabelText("课程");
  await waitFor(() => expect(within(courseSelect).getAllByRole("option")).toHaveLength(2));
  await user.selectOptions(courseSelect, UUID_A);
}

/** 向导一次只渲染一步：每次交互前先切到对应步骤。 */
async function goToStep(
  user: ReturnType<typeof userEvent.setup>,
  id: "basic" | "stem" | "reference" | "cases" | "comparison",
) {
  await user.click(await screen.findByTestId(`wizard-step-${id}`));
  await screen.findByTestId(`step-${id}`);
}

/** 「运行配置」默认收起：改运行方式 / 退出码 / 文件上限之前先展开它。 */
async function openRuntimeConfig(user: ReturnType<typeof userEvent.setup>) {
  const details = screen.getByTestId("runtime-config") as HTMLDetailsElement;
  if (!details.open) {
    await user.click(within(details).getByText("运行配置"));
  }
  await within(details).findByLabelText("运行方式");
}

/** 命令期望退出码现在预置 0：要验证自定义值必须先清空。 */
async function setAdapterExitCode(user: ReturnType<typeof userEvent.setup>, value: string) {
  await goToStep(user, "comparison");
  await openRuntimeConfig(user);
  const input = screen.getByLabelText("命令期望退出码");
  await user.clear(input);
  await user.type(input, value);
}

async function selectCourseInAiPanel(user: ReturnType<typeof userEvent.setup>) {
  const panelSelect = await screen.findByLabelText("这道题出给哪门课");
  await waitFor(() =>
    expect(within(panelSelect).getAllByRole("option").length).toBeGreaterThan(1),
  );
  await user.selectOptions(panelSelect, UUID_A);
}

/** 填满一次合法的新建表单（POST 与 PATCH 都要求这四个必需字段，因此测试里都要填）。 */
async function fillCreateForm(user: ReturnType<typeof userEvent.setup>) {
  await selectCourse(user);
  await user.type(screen.getByLabelText("题目标题"), "统计文件行数");
  await user.type(screen.getByLabelText("标签"), "Process Scheduling, unix");
  await goToStep(user, "stem");
  await user.type(screen.getByLabelText("题干"), "# 统计行数");
  await goToStep(user, "reference");
  await user.type(screen.getByLabelText("参考答案"), "print(2)");
  await goToStep(user, "cases");
  await user.type(screen.getByLabelText(/用例名/), "基本用例");
  // 分值不再预置 1：要保存就必须由教师填一个合法分值。
  await user.type(screen.getByLabelText("分值"), "1");
  await user.type(screen.getByLabelText("标准输入"), "a\nb\n");
  // `stdin` 题型必须在 manifest 上声明 args / stdin / 命令退出码之一（B3 的适配器校验），
  // 因此这里填退出码——只填用例级 stdin 会被服务端 422 拒绝。
  await setAdapterExitCode(user, "0");
  await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
}

/**
 * 编辑路径**不**凭空渲染用例行（读不到服务端那份集合就不假装知道有几个），因此要显式加一行；
 * 而提交用例会整体替换那份读不到的集合（CHG-0017），所以还必须勾选那次确认。
 */
async function addCaseAndConfirm(user: ReturnType<typeof userEvent.setup>) {
  await goToStep(user, "cases");
  await user.click(screen.getByTestId("case-add"));
  await user.type(screen.getAllByLabelText(/用例名/).at(-1) as HTMLElement, "用例一");
  await user.type(screen.getAllByLabelText("分值").at(-1) as HTMLElement, "1");
  await user.click(screen.getByLabelText(/我确认/));
}

/** `stdin` 题型的 manifest 必须声明 args / stdin / 退出码之一，编辑路径也要补上。 */
async function fillAdapterExitCode(user: ReturnType<typeof userEvent.setup>) {
  await setAdapterExitCode(user, "0");
}

describe("分步向导", () => {
  it("一次只显示一步；完成基本信息后左导航的圆点变成实心对勾", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    // 初始：只有「基本信息」这一步的内容在页面上。
    expect(await screen.findByTestId("step-basic")).toBeInTheDocument();
    expect(screen.queryByTestId("step-stem")).toBeNull();
    expect(screen.getByTestId("wizard-dot-basic")).toHaveAttribute("data-done", "false");
    expect(screen.getByTestId("wizard-dot-basic")).toHaveTextContent("");

    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "统计行数");

    expect(screen.getByTestId("wizard-dot-basic")).toHaveAttribute("data-done", "true");
    expect(screen.getByTestId("wizard-dot-basic")).toHaveTextContent("✓");
    // 还没写题干，所以第二步仍是空心圆。
    expect(screen.getByTestId("wizard-dot-stem")).toHaveAttribute("data-done", "false");
  });

  it("切换步骤不丢已经填的内容", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "统计行数");
    await goToStep(user, "stem");
    await user.type(screen.getByLabelText("题干"), "# 统计");

    await goToStep(user, "basic");
    expect(screen.getByLabelText("题目标题")).toHaveValue("统计行数");
    await goToStep(user, "stem");
    expect(screen.getByLabelText("题干")).toHaveValue("# 统计");
  });

  it("每一步底部都有「保存草稿」；缺课程/标题/题干时只是禁用，不再写提示", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    // 第 1 步：没选课程。
    expect(await screen.findByTestId("authoring-save")).toBeDisabled();
    // 2.0 的意见：保存按钮旁边不再挂"请先选择课程 / 请先填写题干"这类提示文字。
    expect(screen.queryByTestId("save-blocker")).toBeNull();

    await selectCourse(user);
    expect(screen.getByTestId("authoring-save")).toBeDisabled();
    await user.type(screen.getByLabelText("题目标题"), "统计行数");
    expect(screen.getByTestId("authoring-save")).toBeDisabled();

    await goToStep(user, "stem");
    expect(screen.getByTestId("authoring-save")).toBeDisabled();
    await user.type(screen.getByLabelText("题干"), "# 统计");
    expect(screen.getByTestId("authoring-save")).toBeEnabled();

    // 其他步骤同样带一个保存按钮。
    for (const step of ["reference", "cases", "comparison"] as const) {
      await goToStep(user, step);
      expect(screen.getByTestId("authoring-save")).toBeEnabled();
    }
  });

  it("顶部只留一行小字提示「请先选择课程」，不再占一条横幅", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    const hint = await screen.findByTestId("authoring-course-required");
    // 提示还在（教师要知道缺什么），但它只是一小段文字，不是整行横幅。
    expect(hint.tagName).toBe("P");
    expect(hint).toHaveTextContent("请先选择课程");
    expect(screen.getByTestId("authoring-cancel")).toBeInTheDocument();
  });

  it("测试用例没填内容时第 4 步仍是空心圆，填了才算完成；步骤之间有连接线", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    // 新建时默认有一个空用例，那不算"填过"。
    expect(await screen.findByTestId("wizard-dot-cases")).toHaveAttribute("data-done", "false");

    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "统计行数");
    await goToStep(user, "stem");
    await user.type(screen.getByLabelText("题干"), "# 统计");
    await goToStep(user, "reference");
    await user.type(screen.getByLabelText("参考答案"), "print(1)");
    await goToStep(user, "cases");
    await user.type(screen.getByLabelText("标准输入"), "a\n");

    expect(screen.getByTestId("wizard-dot-cases")).toHaveAttribute("data-done", "true");
    expect(screen.getByTestId("wizard-dot-cases")).toHaveTextContent("✓");

    // 连接线：上一步完成了就是实线，没完成是虚线（data-done 同时给出可断言的钩子）。
    expect(screen.getByTestId("wizard-line-basic")).toHaveAttribute("data-done", "true");
    expect(screen.getByTestId("wizard-line-cases")).toHaveAttribute("data-done", "true");
    expect(screen.queryByTestId("wizard-line-comparison")).toBeNull();
  });

  it("课程到末页时不再显示「已到末页」", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await screen.findByTestId("step-basic");
    // 夹具里只有一门课程且没有下一页：分页器整块不渲染。
    expect(screen.queryByTestId("authoring-courses-end")).toBeNull();
    expect(screen.queryByTestId("authoring-courses-load-more")).toBeNull();
  });

  it("「AI 出题」是蓝色主按钮，紧挨着「题干」标题", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await goToStep(user, "stem");
    const ai = await screen.findByTestId("authoring-ai");
    expect(ai).toHaveTextContent("AI 出题");
    expect(ai.className).toContain("button");
    expect(ai.className).not.toContain("button--ghost");
    // 和标题在同一个标题元素里，所以视觉上紧挨着「题干」两个字。
    expect(ai.closest(".panel__title")).not.toBeNull();
  });

  it("运行配置默认收起，展开后才是运行方式与文件上限", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await goToStep(user, "comparison");
    const details = screen.getByTestId("runtime-config") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(screen.getByLabelText("数值容差")).toBeDisabled();

    await openRuntimeConfig(user);
    expect(details.open).toBe(true);
    expect(screen.getByLabelText("运行方式")).toBeInTheDocument();
    expect(screen.getByLabelText("文件总大小上限")).toBeInTheDocument();
  });

  it("数值容差只在选中「数值容差」时可填", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await goToStep(user, "comparison");
    const tolerance = screen.getByLabelText("数值容差");
    expect(tolerance).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("标准输出比较"), "numeric_tolerance");
    expect(tolerance).toBeEnabled();

    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
    expect(tolerance).toBeDisabled();
  });
});

describe("新建题目（POST /api/v1/questions）", () => {
  it("course_id 走查询参数，受保护内容进入请求体，标签按契约形状归一化", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          method: "POST",
          path: "/api/v1/questions",
          respond: () =>
            jsonResponse(
              questionPublic({
                status: "draft",
                versions: [
                  {
                    question_version_id: NEW_VERSION,
                    version: 1,
                    status: "draft",
                    published_at: "2026-09-15T07:20:00.000Z",
                  },
                ],
              }),
            ),
        },
        {
          path: `/api/v1/question-versions/${NEW_VERSION}`,
          respond: () =>
            jsonResponse(
              questionVersionPublic({
                question_version_id: NEW_VERSION,
                version: 1,
                status: "draft",
                content_digest: SHA_A,
              }),
            ),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    // 未选课程时不能提交：course_id 是契约要求的查询参数。
    const save = await screen.findByTestId("authoring-save");
    expect(save).toBeDisabled();
    await fillCreateForm(user);
    await user.click(screen.getByTestId("authoring-save"));

    const request = await waitFor(() => {
      const found = stub?.requests.find(
        (item) => item.method === "POST" && item.url.pathname === "/api/v1/questions",
      );
      if (!found) throw new Error("尚未发出创建请求");
      return found;
    });
    expect(request.url.searchParams.get("course_id")).toBe(UUID_A);
    const body = request.body as Record<string, unknown>;
    expect(body.title).toBe("统计文件行数");
    expect(body.reference_answer).toBe("print(2)");
    expect(body.tags).toEqual(["process-scheduling", "unix"]);
    // 新建不携带 revision_note（契约说它只与 as_new_version 一起有意义）。
    expect(body.revision_note).toBeUndefined();
    const cases = body.cases as Record<string, unknown>[];
    expect(cases).toHaveLength(1);
    expect(cases[0].name).toBe("基本用例");
    expect(cases[0].ordinal).toBe(0);
    expect(cases[0].stdin).toBe("a\nb\n");
    expect(cases[0].visible_to_student).toBe(false);
    expect((body.adapter as Record<string, unknown>).comparison).toEqual({ stdout: "exact" });

    expect(await screen.findByTestId("authoring-created")).toBeInTheDocument();
    expect(screen.getByText(/保存的是草稿/)).toBeInTheDocument();
  });

  it("受保护字段留空时不发这两个键（CHG-0017：省略 = 服务端保留已存值）", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          method: "POST",
          path: "/api/v1/questions",
          respond: () =>
            jsonResponse(
              questionPublic({
                status: "draft",
                versions: [
                  {
                    question_version_id: NEW_VERSION,
                    version: 1,
                    status: "draft",
                    published_at: "2026-09-15T07:20:00.000Z",
                  },
                ],
              }),
            ),
        },
        {
          path: `/api/v1/question-versions/${NEW_VERSION}`,
          respond: () =>
            jsonResponse(
              questionVersionPublic({
                question_version_id: NEW_VERSION,
                version: 1,
                status: "draft",
                content_digest: SHA_A,
              }),
            ),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "只有标题");
    await goToStep(user, "stem");
    await user.type(screen.getByLabelText("题干"), "题干");
    await setAdapterExitCode(user, "0");
    await goToStep(user, "comparison");
    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
    // 参考答案留空、用例留空（把新建时那一行邀请删掉）。
    await goToStep(user, "cases");
    await user.click(screen.getByTestId("case-remove-0"));
    await user.click(await screen.findByTestId("case-delete-confirm-action"));
    await user.click(screen.getByTestId("authoring-save"));

    const request = await waitFor(() => {
      const found = stub?.requests.find(
        (item) => item.method === "POST" && item.url.pathname === "/api/v1/questions",
      );
      if (!found) throw new Error("尚未发出创建请求");
      return found;
    });
    const body = request.body as Record<string, unknown>;
    // 关键（CHG-0017）：这两个键**根本不出现**，而不是发一个空值。
    // 服务端因此保留该版本已有的参考答案与用例集合；新建时"没有可保留的"由服务端表达。
    expect(body).not.toHaveProperty("reference_answer");
    expect(body).not.toHaveProperty("cases");
    // required 里的四个字段照发（省略语义不适用于它们）。
    expect(body.title).toBe("只有标题");
    expect(body.stem_markdown).toBe("题干");
    expect(body.language).toBe("python");
    expect(body.adapter).toBeDefined();
    expect(await screen.findByTestId("authoring-created")).toBeInTheDocument();
  });

  it("保存成功后不再允许重复创建（第二次点保存不会产生第二道题）", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          method: "POST",
          path: "/api/v1/questions",
          respond: () =>
            jsonResponse(
              questionPublic({
                status: "draft",
                versions: [
                  {
                    question_version_id: NEW_VERSION,
                    version: 1,
                    status: "draft",
                    published_at: "2026-09-15T07:20:00.000Z",
                  },
                ],
              }),
            ),
        },
        {
          path: `/api/v1/question-versions/${NEW_VERSION}`,
          respond: () =>
            jsonResponse(
              questionVersionPublic({
                question_version_id: NEW_VERSION,
                version: 1,
                status: "draft",
                content_digest: SHA_A,
              }),
            ),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await fillCreateForm(user);
    await user.click(screen.getByTestId("authoring-save"));
    await screen.findByTestId("authoring-created");

    // 这一页仍是 P08（questionId 为 null）：再点一次就会 POST 出第二道题，因此按钮停用，
    // 并明确告诉教师下一步该走「继续编辑」。
    expect(screen.getByTestId("authoring-save")).toBeDisabled();
    expect(screen.getByTestId("authoring-created-next")).toHaveTextContent("继续编辑");
    // 图片与夹具上传同样停用：本页的插入/声明不会被保存，去 P09 才能写进题目。
    await goToStep(user, "stem");
    expect(screen.getByTestId("image-disabled")).toHaveTextContent("继续编辑");
    const posts = stub.requests.filter(
      (item) => item.method === "POST" && item.url.pathname === "/api/v1/questions",
    );
    expect(posts).toHaveLength(1);
  });

  it("file 题型可选：夹具区出现，且没有上传输入夹具时拒绝保存", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    // file 不再是"不可选"的题型：夹具端点改用教师自己的令牌后，浏览器可以完整创建它。
    await goToStep(user, "comparison");
    await openRuntimeConfig(user);
    await user.selectOptions(screen.getByLabelText("运行方式"), "file");
    expect(await screen.findByTestId("fixture-editor")).toBeInTheDocument();
    // 未保存草稿前没有版本 id，因此上传控件是禁用的并说明原因（不假装上传成功）。
    expect(await screen.findByTestId("fixture-disabled")).toHaveTextContent("先保存草稿");

    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "文件题");
    await goToStep(user, "stem");
    await user.type(screen.getByLabelText("题干"), "题干");
    await goToStep(user, "reference");
    await goToStep(user, "reference");
    await user.type(screen.getByLabelText("参考答案"), "print(2)");
    await goToStep(user, "cases");
    await user.type(screen.getByLabelText(/用例名/), "用例一");
    await goToStep(user, "comparison");
    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
    await user.click(screen.getByTestId("authoring-save"));

    // 没有输入夹具的 file 题编译不出 package，所以在本地就先拒绝，不发请求。
    expect(await screen.findByTestId("authoring-issues")).toHaveTextContent(
      "请至少上传一个输入文件",
    );
    expect(stub.requests.filter((item) => item.url.pathname === "/api/v1/questions")).toHaveLength(
      0,
    );
  });
});

describe("修改草稿（PATCH /api/v1/questions/{question_id}）", () => {
  it("草稿就地修改：不传 as_new_version，且不发 revision_note", async () => {
    stub = installFetchStub(
      baseRoutes([
        { path: `/api/v1/questions/${QUESTION_ID}`, method: "GET", respond: () => jsonResponse(draftQuestion()) },
        {
          path: `/api/v1/question-versions/${DRAFT_VERSION}`,
          respond: () =>
            jsonResponse(
              questionVersionPublic({
                question_version_id: DRAFT_VERSION,
                version: 1,
                status: "draft",
                max_files: 3,
              }),
            ),
        },
        {
          method: "PATCH",
          path: `/api/v1/questions/${QUESTION_ID}`,
          // CHG-0016：PATCH 返回 `QuestionPublic`（`title`/`tags` 不在版本 DTO 上）。
          respond: () =>
            jsonResponse(
              questionPublic({
                status: "draft",
                versions: [
                  {
                    question_version_id: DRAFT_VERSION,
                    version: 1,
                    status: "draft",
                    published_at: "2026-09-15T07:20:00.000Z",
                  },
                ],
              }),
            ),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    // 可读回的字段被预填；受保护字段保持为空并显式说明。
    await waitFor(() => expect(screen.getByLabelText("题目标题")).toHaveValue(questionPublic().title));
    expect(screen.getByLabelText("标签")).toHaveValue("unix");
    await goToStep(user, "reference");
    await goToStep(user, "reference");
    expect(screen.getByLabelText("参考答案")).toHaveValue("");
    // 草稿就地修改时不显示「版本说明」：它只属于"另存为新版本"那一次写入。
    expect(screen.queryByLabelText("版本说明")).toBeNull();

    await user.type(screen.getByLabelText("参考答案"), "print(2)");
    await addCaseAndConfirm(user);
    await fillAdapterExitCode(user);
    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
    await user.click(screen.getByTestId("authoring-save"));

    const request = await waitFor(() => {
      const found = stub?.requests.find((item) => item.method === "PATCH");
      if (!found) throw new Error("尚未发出修改请求");
      return found;
    });
    expect(request.url.searchParams.get("as_new_version")).toBeNull();
    const body = request.body as Record<string, unknown>;
    expect(body.revision_note).toBeUndefined();
    expect(body.reference_answer).toBe("print(2)");
    expect(await screen.findByTestId("authoring-saved")).toBeInTheDocument();
  });

  it("最新版本已发布：保存为新版本，带 as_new_version=true 与 revision_note", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          path: `/api/v1/questions/${QUESTION_ID}`,
          method: "GET",
          respond: () => jsonResponse(publishedQuestion()),
        },
        {
          path: `/api/v1/question-versions/${UUID_A}`,
          respond: () =>
            jsonResponse(questionVersionPublic({ question_version_id: UUID_A, version: 2 })),
        },
        {
          method: "PATCH",
          path: `/api/v1/questions/${QUESTION_ID}`,
          // CHG-0016：PATCH 的响应是 `QuestionPublic`（不是版本 DTO）。
          respond: () =>
            jsonResponse(
              questionPublic({
                status: "published",
                versions: [
                  {
                    question_version_id: NEW_VERSION,
                    version: 3,
                    status: "draft",
                    published_at: "2026-09-18T07:20:00.000Z",
                  },
                  { question_version_id: UUID_A, version: 2, status: "published" },
                ],
              }),
            ),
        },
        {
          path: `/api/v1/question-versions/${NEW_VERSION}`,
          respond: () =>
            jsonResponse(
              questionVersionPublic({
                question_version_id: NEW_VERSION,
                version: 3,
                status: "draft",
              }),
            ),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    expect(await screen.findByTestId("authoring-version-mode")).toHaveTextContent(
      "另存为一个新版本",
    );
    await goToStep(user, "reference");
    await user.type(await screen.findByLabelText("参考答案"), "print(2)");
    await addCaseAndConfirm(user);
    await fillAdapterExitCode(user);
    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
    await goToStep(user, "comparison");
    await user.type(screen.getByLabelText("版本说明"), "例题的截止时间写错了");
    await user.click(screen.getByTestId("authoring-save"));

    const request = await waitFor(() => {
      const found = stub?.requests.find((item) => item.method === "PATCH");
      if (!found) throw new Error("尚未发出修改请求");
      return found;
    });
    expect(request.url.searchParams.get("as_new_version")).toBe("true");
    expect((request.body as Record<string, unknown>).revision_note).toBe(
      "例题的截止时间写错了",
    );
    // 保存结果展示的是**服务端返回的**新版本标识：先由 PATCH 的 `versions[0]` 得到，
    // 再取回版本 DTO 渲染"待验证后才能发布"。断言它等于 versions[0]，而不是"调用成功"。
    expect(await screen.findByTestId("authoring-saved")).toHaveTextContent("版本 3");
    const versionRequest = stub.requests.find(
      (item) => item.url.pathname === `/api/v1/question-versions/${NEW_VERSION}`,
    );
    expect(versionRequest).toBeDefined();
  });

  it("修改草稿的审计日志记的是真实版本标识（CHG-0016 的运行时回归）", async () => {
    // 契约此前把 PATCH 的响应声明成版本 DTO，于是 `version.question_version_id` 在运行时
    // 恒为 undefined，而类型检查发现不了。这条用例断言日志里的是**服务端返回的**那个 id。
    const lines: Record<string, unknown>[] = [];
    setLogSink((records) => {
      lines.push(...(records as unknown as Record<string, unknown>[]));
    });
    try {
      stub = installFetchStub(
        baseRoutes([
          {
            path: `/api/v1/questions/${QUESTION_ID}`,
            method: "GET",
            respond: () => jsonResponse(draftQuestion()),
          },
          {
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
            method: "PATCH",
            path: `/api/v1/questions/${QUESTION_ID}`,
            respond: () =>
              jsonResponse(
                questionPublic({
                  status: "draft",
                  versions: [
                    {
                      question_version_id: DRAFT_VERSION,
                      version: 1,
                      status: "draft",
                      published_at: "2026-09-18T07:20:00.000Z",
                    },
                  ],
                }),
              ),
          },
        ]),
      );
      const user = userEvent.setup();
      await signIn("teacher1", "pw");
      renderAuthoring(QUESTION_ID);

      await goToStep(user, "reference");
      await user.type(await screen.findByLabelText("参考答案"), "print(2)");
      await addCaseAndConfirm(user);
      await fillAdapterExitCode(user);
      await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
      await user.click(screen.getByTestId("authoring-save"));
      await screen.findByTestId("authoring-saved");

      const updateLog = lines.filter((line) => line.event === "question_draft_updated").at(-1);
      expect(updateLog).toBeDefined();
      expect(updateLog?.question_version_id).toBe(DRAFT_VERSION);
      expect(updateLog?.version).toBe(1);
      expect(updateLog?.status).toBe("draft");
      // 审计字段一个都不能是 undefined（这正是旧契约声明造成的症状）。
      expect(Object.values(updateLog ?? {})).not.toContain(undefined);
    } finally {
      setLogSink(null);
    }
  });
});

describe("验证并发布入口（CHG-0019 / ADR-0015）", () => {
  it("草稿版本显示验证入口与门禁说明，未验证前没有发布动作", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          path: `/api/v1/questions/${QUESTION_ID}`,
          method: "GET",
          respond: () => jsonResponse(draftQuestion()),
        },
        {
          path: `/api/v1/question-versions/${DRAFT_VERSION}`,
          respond: () =>
            jsonResponse(
              questionVersionPublic({
                question_version_id: DRAFT_VERSION,
                version: 1,
                status: "draft",
                content_digest: SHA_A,
              }),
            ),
        },
      ]),
    );
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    const panel = await screen.findByTestId("reference-verification");
    // 入口在：这是 ADR-0015 解掉的死结（此前这一版只能找运维跑脚本）。
    expect(within(panel).getByTestId("reference-verify")).toBeInTheDocument();
    // 发布条件必须可见，且只说人话。
    expect(within(panel).getByTestId("reference-gate")).toHaveTextContent("检验有记录");

    // 未验证 → 没有可点的发布动作，且没有任何验证结论。
    expect(within(panel).queryByTestId("reference-publish")).toBeNull();
    expect(within(panel).queryByTestId("reference-verification-result")).toBeNull();
  });
});

describe("两类信号（确定性规则 vs 模型建议）", () => {
  it("诊断未命中规则时显示「检查过、无问题」，与「未启用」不是同一句话", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          path: `/api/v1/questions/${QUESTION_ID}`,
          method: "GET",
          respond: () => jsonResponse(draftQuestion()),
        },
        {
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
      ]),
    );
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    const clean = await screen.findByTestId("diagnostics-clean");
    expect(clean).toHaveTextContent("没发现问题");
    // 「未启用」属于另一张卡（模型辅助）：诊断面板不显示它——两种"空"必须长得不一样。
    expect(screen.queryByTestId("assistance-unavailable")).toBeNull();
    // 辅助面板是另一张卡：在请求之前不显示任何"结果"。
    expect(screen.queryByTestId("assistance-result")).toBeNull();
  });

  it("未配置 provider（409）时显示「未启用」，与「没有建议」区分开", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          path: `/api/v1/questions/${QUESTION_ID}`,
          method: "GET",
          respond: () => jsonResponse(draftQuestion()),
        },
        {
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
          method: "POST",
          path: `/api/v1/questions/${QUESTION_ID}/authoring-assistance`,
          respond: () =>
            problemResponse({
              status: 409,
              title: "Conflict",
              detail: "no model provider is configured",
            }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    await screen.findByTestId("diagnostics-clean");
    await user.click(screen.getByTestId("request-assistance"));

    const unavailable = await screen.findByTestId("assistance-unavailable");
    expect(unavailable).toHaveTextContent("暂未开通");

    expect(screen.queryByTestId("assistance-suggestions")).toBeNull();
  });

  it("有建议时：建议、provenance 与 disclaimers 原文一起显示，并标明是模型建议", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          path: `/api/v1/questions/${QUESTION_ID}`,
          method: "GET",
          respond: () => jsonResponse(draftQuestion()),
        },
        {
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
          method: "POST",
          path: `/api/v1/questions/${QUESTION_ID}/authoring-assistance`,
          respond: () => jsonResponse(authoringAssistance()),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    await screen.findByTestId("diagnostics-clean");
    await user.click(screen.getByTestId("request-assistance"));

    expect(await screen.findByTestId("assistance-suggestions")).toHaveTextContent(
      "题干第 2 段",
    );
    expect(screen.getByTestId("assistance-provenance")).toHaveTextContent("example-model-v1");
    const disclaimers = screen.getByTestId("assistance-disclaimers");
    expect(disclaimers).toHaveTextContent("由模型生成，非确定性结论。");
    expect(disclaimers).toHaveTextContent("不参与判分，不作为申诉依据。");
    expect(screen.getByTestId("authoring-assistance")).toHaveTextContent("AI 给的是参考意见");

    const request = stub.requests.find(
      (item) => item.url.pathname === `/api/v1/questions/${QUESTION_ID}/authoring-assistance`,
    );
    expect(request?.body).toEqual({ topics: ["quality"], notes: null });
  });

  it("自查命中时按级别与用例位置展示（只说人话，不显示规则编号）", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          path: `/api/v1/questions/${QUESTION_ID}/diagnostics`,
          respond: () => jsonResponse(comparisonDiagnostics()),
        },
        {
          path: `/api/v1/questions/${QUESTION_ID}`,
          method: "GET",
          respond: () => jsonResponse(draftQuestion()),
        },
        {
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
      ]),
    );
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    const table = await screen.findByTestId("diagnostics-table");
    expect(within(table).getByText("建议检查")).toBeInTheDocument();
    expect(table).toHaveTextContent("第 2 个用例");
  });
});

async function openAi(user: ReturnType<typeof userEvent.setup>) {
  // AI 出题是独立视图：入口在「题干」步的标题右侧。
  await goToStep(user, "stem");
  await user.click(await screen.findByTestId("authoring-ai"));
  await screen.findByTestId("generation-panel");
}

describe("AI 结构化草稿生成（AI-FLOW-01A）", () => {
  const GENERATION_ID = UUID_A;

  /** 生成相关三个端点：能力、创建、读取/丢弃。 */
  function generationRoutes(
    capabilities: Partial<ReturnType<typeof generationCapabilities>> = {},
    generation = questionDraftGeneration({ generation_id: GENERATION_ID }),
  ): StubbedRoute[] {
    return [
      {
        path: "/api/v1/question-draft-generations/capabilities",
        respond: () => jsonResponse(generationCapabilities(capabilities)),
      },
      {
        method: "POST",
        path: "/api/v1/question-draft-generations",
        respond: () => jsonResponse(generation),
      },
      {
        method: "GET",
        path: `/api/v1/question-draft-generations/${GENERATION_ID}`,
        respond: () => jsonResponse(generation),
      },
      {
        method: "DELETE",
        path: `/api/v1/question-draft-generations/${GENERATION_ID}`,
        respond: () => jsonResponse({}),
      },
    ];
  }

  it("还没选课程时状态标签说「待选择课程」，而不是宣称 AI 不可用", async () => {
    stub = installFetchStub(baseRoutes(generationRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await goToStep(user, "stem");
    await user.click(await screen.findByTestId("authoring-ai"));

    expect(await screen.findByTestId("generation-pill")).toHaveTextContent("待选择课程");
    expect(screen.getByTestId("generation-pill").textContent ?? "").not.toContain("暂不可用");
  });

  it("课程下拉就在 AI 面板里：选完立刻可用，不用滚到下面的基本信息", async () => {
    stub = installFetchStub(baseRoutes(generationRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await goToStep(user, "stem");
    await user.click(await screen.findByTestId("authoring-ai"));

    // 面板内的下拉有自己的标签，避免和「基本信息」里的同名控件互相遮盖。
    await selectCourseInAiPanel(user);

    // 选中后能力查询才会发出，标签随之变成可用；此时只差教学目标。
    expect(await screen.findByTestId("generation-pill")).toHaveTextContent("AI 出题可用");
    expect(screen.getByTestId("generation-reason")).toHaveTextContent("请先填写教学目标");
    await user.type(screen.getByLabelText("教学目标"), "统计每个单词出现的次数");
    expect(screen.getByTestId("generation-generate")).toBeEnabled();
    // 选完课程后，面板里的"选课"下拉换成了生成表单（同一份状态，不是两份）。
    expect(screen.queryByLabelText("这道题出给哪门课")).toBeNull();
  });

  it("AI 入口就在标题栏：还没选课程也能看到，点开后有按钮并说明先选课程", async () => {
    stub = installFetchStub(baseRoutes(generationRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    // 第一屏（未选课程）就能看到入口，不需要滚到表单底部。
    await goToStep(user, "stem");
    const entry = await screen.findByTestId("authoring-ai");
    expect(entry).toHaveTextContent("AI 出题");

    await user.click(entry);
    // 面板里「生成草稿」始终存在：禁用 + 说明原因，而不是整块消失。
    const generate = await screen.findByTestId("generation-generate");
    expect(generate).toBeDisabled();
    expect(screen.getByTestId("generation-reason")).toHaveTextContent("请先选择课程");
    expect(screen.getByTestId("generation-no-course")).toBeInTheDocument();
  });

  it("没有出题权限时 AI 面板说清楚，不显示技术错误", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          path: "/api/v1/question-draft-generations/capabilities",
          respond: () =>
            problemResponse({
              status: 403,
              title: "Forbidden",
              detail: "Role or course scope does not permit this operation.",
            }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await goToStep(user, "stem");
    await user.click(await screen.findByTestId("authoring-ai"));
    await selectCourseInAiPanel(user);

    expect(await screen.findByTestId("generation-forbidden")).toHaveTextContent("没有出题权限");
    expect(screen.getByTestId("generation-generate")).toBeDisabled();
    // 不把服务端原文贴到教师界面上。
    const panel = screen.getByTestId("generation-panel");
    expect(panel.textContent ?? "").not.toContain("Role or course scope");
  });

  it("未配置 provider 时如实显示「未启用」，且生成按钮不可用、不发 POST", async () => {
    stub = installFetchStub(
      baseRoutes(generationRoutes({ provider_status: "unavailable", provider: null, model: null })),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await selectCourse(user);

    await openAi(user);
    expect(await screen.findByTestId("generation-unavailable")).toHaveTextContent("暂未开通");
    expect(screen.getByTestId("generation-generate")).toBeDisabled();
    expect(
      stub.requests.filter(
        (item) =>
          item.method === "POST" && item.url.pathname === "/api/v1/question-draft-generations",
      ),
    ).toHaveLength(0);
  });

  it("显式点击才请求模型：目标为空时按钮禁用，请求体只带公开约束", async () => {
    stub = installFetchStub(baseRoutes(generationRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await openAi(user);
    await selectCourseInAiPanel(user);

    const generate = await screen.findByTestId("generation-generate");
    expect(generate).toBeDisabled(); // 空目标不消耗任何 provider 调用

    await user.type(screen.getByLabelText("教学目标"), "让学生学会读标准输入");
    await user.click(screen.getByTestId("generation-generate"));

    const request = await waitFor(() => {
      const found = stub?.requests.find(
        (item) =>
          item.method === "POST" && item.url.pathname === "/api/v1/question-draft-generations",
      );
      if (!found) throw new Error("尚未发出生成请求");
      return found;
    });
    // course_id 是查询参数；body 里只有目标与约束，没有参考答案、用例或学生数据。
    expect(request.url.searchParams.get("course_id")).toBe(UUID_A);
    const body = request.body as Record<string, unknown>;
    expect(body.learning_objective).toBe("让学生学会读标准输入");
    expect(body.language).toBe("python");
    expect(body.adapter_kind).toBe("stdin");
    for (const forbidden of [
      "reference_answer",
      "cases",
      "student_id",
      "source_code",
      "expected_stdout",
    ]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it("候选会填入编辑器，字段问题、来源与免责声明同时显示（候选不是成品）", async () => {
    stub = installFetchStub(baseRoutes(generationRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await openAi(user);
    await selectCourseInAiPanel(user);

    await user.type(screen.getByLabelText("教学目标"), "统计行数");
    await user.click(screen.getByTestId("generation-generate"));

    // 状态是 needs_review：可编辑，但有字段要补。
    expect(await screen.findByTestId("generation-status")).toHaveTextContent("需要你");
    // 字段问题指出具体控件（不是一句泛泛的说明）。
    const warnings = screen.getByTestId("generation-warnings");
    expect(warnings).toHaveTextContent("第 2 个用例的分值");
    expect(screen.getByTestId("generation-provenance")).toHaveTextContent("example-model-v1");
    expect(screen.getByTestId("generation-expiry")).toBeInTheDocument();
    expect(screen.getByTestId("generation-disclaimers")).toHaveTextContent(
      "模型生成的是候选草稿",
    );

    // 候选被填进同一张表单：标题、题干、参考答案、用例都到位，权重为 null 的那个留空。
    expect(screen.getByText("学生看不到")).toBeInTheDocument();
    expect(screen.getByTestId("generation-protected")).toHaveTextContent("参考答案");
    await user.click(screen.getByTestId("ai-back"));
    await screen.findByTestId("wizard-nav");
    await goToStep(user, "basic");
    expect(screen.getByLabelText("题目标题")).toHaveValue("统计标准输入的行数");
    await goToStep(user, "reference");
    expect(screen.getByLabelText("参考答案")).toHaveValue(
      "import sys\nprint(len(sys.stdin.read().splitlines()))\n",
    );
    await goToStep(user, "cases");
    expect(screen.getByTestId("case-1")).toBeInTheDocument();
    // 受保护内容只在教师侧展开区里出现，并明确标注学生看不到。
  });

  it("刷新后用 generation_id 恢复服务端原始候选", async () => {
    stub = installFetchStub(baseRoutes(generationRoutes()));
    await signIn("teacher1", "pw");
    renderAuthoring(null, GENERATION_ID);
    await selectCourse(userEvent.setup());
    await goToStep(userEvent.setup(), "stem");
    await userEvent.setup().click(await screen.findByTestId("authoring-ai"));

    // 面板里带着 URL 上的候选 id：不需要再点一次"生成"。
    expect(await screen.findByTestId("generation-result")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByTestId("ai-back"));
    await goToStep(userEvent.setup(), "basic");
    await waitFor(() =>
      expect(screen.getByLabelText("题目标题")).toHaveValue("统计标准输入的行数"),
    );
    expect(
      stub.requests.filter(
        (item) =>
          item.method === "GET" &&
          item.url.pathname === `/api/v1/question-draft-generations/${GENERATION_ID}`,
      ),
    ).toHaveLength(1);
  });

  it("丢弃候选需要确认，确认后调用 DELETE", async () => {
    stub = installFetchStub(baseRoutes(generationRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null, GENERATION_ID);
    await selectCourse(user);
    await goToStep(user, "stem");
    await user.click(await screen.findByTestId("authoring-ai"));

    await user.click(await screen.findByTestId("generation-discard"));
    // 关闭预览不等于删除：必须再确认一次。
    expect(await screen.findByText("丢弃这份 AI 草稿？")).toBeInTheDocument();
    await user.click(screen.getByTestId("generation-discard-confirm"));

    await waitFor(() => {
      const deleted = stub?.requests.find((item) => item.method === "DELETE");
      expect(deleted?.url.pathname).toBe(
        `/api/v1/question-draft-generations/${GENERATION_ID}`,
      );
    });
  });

  it("预算用尽（429）时显示可重试说明，而不是把异常当成成功", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          path: "/api/v1/question-draft-generations/capabilities",
          respond: () => jsonResponse(generationCapabilities({ requests_remaining: 0 })),
        },
        {
          method: "POST",
          path: "/api/v1/question-draft-generations",
          respond: () =>
            problemResponse({
              status: 429,
              title: "Too Many Requests",
              detail: "generation budget exhausted; the window frees up in 600 seconds",
            }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await openAi(user);
    await selectCourseInAiPanel(user);

    await user.type(await screen.findByLabelText("教学目标"), "生成一道题");
    await user.click(screen.getByTestId("generation-generate"));

    const banner = await screen.findByTestId("generation-budget-exhausted");
    expect(banner).toHaveTextContent("稍后再试");
    expect(screen.queryByTestId("generation-result")).toBeNull();
  });
});

describe("出题页的可见性前提", () => {  it("编辑页会取最新版本以决定「就地改」还是「新建版本」", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          path: `/api/v1/questions/${QUESTION_ID}`,
          method: "GET",
          respond: () => jsonResponse(draftQuestion()),
        },
        {
          path: `/api/v1/question-versions/${DRAFT_VERSION}`,
          respond: () =>
            jsonResponse(
              questionVersionPublic({
                question_version_id: DRAFT_VERSION,
                version: 1,
                status: "draft",
                max_total_bytes: 4096,
              }),
            ),
        },
      ]),
    );
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    await waitFor(() =>
      expect(screen.getByLabelText("题目标题")).toHaveValue(questionPublic().title),
    );
    await goToStep(userEvent.setup(), "comparison");
    expect(screen.getByLabelText("文件总大小上限")).toHaveValue("4096");
    const versionRequest = stub.requests.find((item) =>
      item.url.pathname.startsWith("/api/v1/question-versions/"),
    );
    expect(versionRequest?.url.pathname).toBe(`/api/v1/question-versions/${DRAFT_VERSION}`);
  });
});
