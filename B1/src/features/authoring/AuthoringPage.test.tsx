import { afterEach, describe, expect, it } from "vitest";
import { useState } from "react";
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
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type StubbedRoute,
  type FetchStub,
} from "../../testing/httpBoundary";
import {
  authoringAssistance,
  comparisonDiagnostics,
  coursePage,
  courseSummary,
  generationCapabilities,
  questionDraftGeneration,
  questionPublic,
  questionVersionPublic,
  referenceVerification,
  sessionCreated,
  SHA_A,
  UUID_A,
  UUID_C,
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
  backTo = "questions",
}: {
  questionId: string | null;
  generationId?: string | null;
  backTo?: "questions" | "detail";
}) {
  const [aiView, setAiView] = useState(false);
  return (
    <AuthoringPage
      questionId={questionId}
      generationId={generationId}
      aiView={aiView}
      onAiViewChange={setAiView}
      backTo={backTo}
    />
  );
}

function renderAuthoring(
  questionId: string | null,
  generationId: string | null = null,
  backTo: "questions" | "detail" = "questions",
) {
  const rootRoute = createRootRoute({
    // 保存成功后页面会跳到详情路由，因此根组件必须给出 Outlet，否则子路由渲染不出来。
    component: () => (
      <>
        <Harness questionId={questionId} generationId={generationId} backTo={backTo} />
        <Outlet />
      </>
    ),
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
  // 三个全局项现在都可以留空（用例自带输入即可评测）；这里仍填退出码，验证它确实随请求发出。
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

/** 编辑路径也显式改一次「命令期望退出码」，验证它进了 manifest。 */
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

  it("从题目详情页进来时，右上角是「返回题目」并回到那道题", async () => {
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
                content_digest: SHA_A,
              }),
            ),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID, null, "detail");

    const back = await screen.findByTestId("authoring-back");
    expect(back).toHaveTextContent("返回题目");
    await user.click(back);
    // 回到的是那道题的详情页（测试路由渲染 detail target）。
    expect(await screen.findByText("detail target")).toBeInTheDocument();
  });

  it("前四步点「下一步」不弹任何确认框，直接进入下一步", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "统计行数");
    await user.click(screen.getByTestId("step-next"));

    expect(await screen.findByTestId("step-stem")).toBeInTheDocument();
    // 没有确认弹窗，也没有"题目已保存"这类提示框（3.0：下一步不该带出保存提示）。
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByTestId("authoring-saved")).toBeNull();
    expect(screen.queryByTestId("authoring-created")).toBeNull();
  });

  it("题干页的「下一步」在框体最下面（图片区之后、右下角）", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await goToStep(user, "stem");
    const uploader = await screen.findByTestId("image-uploader");
    const next = screen.getByTestId("step-next");
    // 图片区在前、下一步在后：老师填完题干与图片再继续。
    expect(
      uploader.compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // 右下角对齐（`.wizard__save` 负责 flex-end）。
    expect(next.parentElement?.className).toContain("wizard__save");
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

  it("前四步底部只有「下一步」，保存草稿只在最后一步", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    // 第 1 步：没有保存按钮，只有"下一步"。
    expect(await screen.findByTestId("step-next")).toBeInTheDocument();
    expect(screen.queryByTestId("authoring-save")).toBeNull();

    // 一路点「下一步」：每一步都不出现保存草稿，进到最后一步才出现。
    for (const expected of ["stem", "reference", "cases"] as const) {
      await user.click(screen.getByTestId("step-next"));
      expect(await screen.findByTestId(`step-${expected}`)).toBeInTheDocument();
      expect(screen.getByTestId("step-next")).toBeInTheDocument();
      expect(screen.queryByTestId("authoring-save")).toBeNull();
    }
    await user.click(screen.getByTestId("step-next"));
    expect(await screen.findByTestId("step-comparison")).toBeInTheDocument();
    // 最后一步：保存草稿在，下一步不在；没填题干时保存是灰的。
    expect(screen.queryByTestId("step-next")).toBeNull();
    expect(screen.getByTestId("authoring-save")).toBeDisabled();

    // 补齐必填项（课程 + 标题 + 题干）后可以保存。
    await goToStep(user, "basic");
    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "统计行数");
    await goToStep(user, "stem");
    await user.type(screen.getByLabelText("题干"), "# 统计");
    await goToStep(user, "comparison");
    expect(screen.getByTestId("authoring-save")).toBeEnabled();
  });

  it("顶部不再有常驻白框：未保存提示改成屏幕中间一闪而过的红色提示", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    expect(await screen.findByTestId("step-basic")).toBeInTheDocument();
    // 白框与「取消」都没了。
    expect(screen.queryByTestId("authoring-dirty")).toBeNull();
    expect(screen.queryByTestId("authoring-cancel")).toBeNull();

    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "统计行数");

    // 改动之后出现一条红色提示（不是常驻横幅）。
    const toast = await screen.findByTestId("toast");
    expect(toast).toHaveTextContent("有还没保存的修改");
    expect(toast.className).toBe("toast");
  });

  it("「返回题库列表」在右上角；没填课程时用一条短提示说明", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    const back = await screen.findByTestId("authoring-back");
    expect(back).toHaveTextContent("返回题库列表");
    // 它和标题在同一行，并被推到最右。
    expect(back.closest(".page-head")).not.toBeNull();
    expect(back.className).toContain("page-head__end");
    // 「请先选择课程」以短提示出现，不再是顶部常驻文案。
    expect(await screen.findByTestId("toast")).toHaveTextContent("请先选择课程");
    expect(screen.queryByTestId("authoring-course-required")).toBeNull();
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

  it("「AI 出题」在页面顶端标题旁边，一进页面就能点", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    const ai = await screen.findByTestId("authoring-ai");
    expect(ai).toHaveTextContent("AI 出题");
    // 蓝色主按钮，和标题在同一行。
    expect(ai.className).toContain("button");
    expect(ai.className).not.toContain("button--ghost");
    expect(ai.closest(".page-head")).not.toBeNull();

    // 题干步里不再有第二个 AI 入口。
    await goToStep(user, "stem");
    expect(screen.getAllByTestId("authoring-ai")).toHaveLength(1);
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

    // 第 1 步只有"下一步"；保存按钮只在最后一步出现。
    expect(await screen.findByTestId("step-next")).toBeInTheDocument();
    expect(screen.queryByTestId("authoring-save")).toBeNull();
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

    // 3.0：保存成功后直接去"查看题目"，不再停在编辑页。
    expect(await screen.findByText("detail target")).toBeInTheDocument();
  });

  it("选完图片才在后台存一份最小草稿并上传：教师不用先手动保存", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          method: "POST",
          path: "/api/v1/questions",
          respond: () => jsonResponse(questionPublic({ status: "draft" })),
        },
        {
          // 草稿建好之后页面会取回它的版本 DTO（用于渲染发布门禁）。
          path: `/api/v1/question-versions/${UUID_A}`,
          respond: () =>
            jsonResponse(
              questionVersionPublic({
                question_version_id: UUID_A,
                version: 1,
                status: "draft",
                content_digest: SHA_A,
              }),
            ),
        },
        {
          method: "POST",
          path: `/api/v1/questions/${UUID_A}/assets`,
          respond: () =>
            jsonResponse({
              asset_id: UUID_C,
              content_type: "image/png",
              size_bytes: 12,
            }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "统计行数");
    await goToStep(user, "stem");
    await user.type(screen.getByLabelText("题干"), "# 统计");

    // 「选择文件」不是假按钮：直接就能选图（不需要先保存草稿）。
    const picker = screen.getByTestId("image-pick");
    expect(picker).toBeEnabled();
    expect(screen.queryByTestId("image-need-draft")).toBeNull();

    const file = new File([new Uint8Array([1, 2, 3])], "shot.png", { type: "image/png" });
    await user.upload(screen.getByTestId("image-file") as HTMLInputElement, file);

    // 先建"够上传"的最小草稿：只发契约必需的四项，不含最后一步才填的内容。
    const createRequest = await waitFor(() => {
      const found = stub?.requests.find(
        (item) => item.method === "POST" && item.url.pathname === "/api/v1/questions",
      );
      if (!found) throw new Error("尚未发出最小草稿请求");
      return found;
    });
    const body = createRequest.body as Record<string, unknown>;
    expect(body.title).toBe("统计行数");
    expect(body.stem_markdown).toBe("# 统计");
    expect(body.language).toBe("python");
    expect((body.adapter as Record<string, unknown>).kind).toBe("stdin");
    expect(body.reference_answer).toBeUndefined();
    expect(body.cases).toBeUndefined();
    expect((body.adapter as Record<string, unknown>).comparison).toBeUndefined();

    // 再上传到那道草稿上，并把图片写进题干。
    await waitFor(() => {
      const uploadRequest = stub?.requests.find(
        (item) => item.method === "POST" && item.url.pathname === `/api/v1/questions/${UUID_A}/assets`,
      );
      expect(uploadRequest).toBeDefined();
      expect((uploadRequest?.body as Record<string, unknown>).content_type).toBe("image/png");
    });
    await waitFor(() => {
      const stem = screen.getByLabelText("题干") as HTMLTextAreaElement;
      expect(stem.value).toContain(`/api/v1/questions/${UUID_A}/assets/${UUID_C}`);
    });
  });

  it("校验失败的原因拼成一句话，不会出现「。；」连在一起", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "统计行数");
    await goToStep(user, "stem");
    await user.type(screen.getByLabelText("题干"), "# 统计");
    await goToStep(user, "comparison");
    // 比对方式没选、用例分值为空 → 两条原因同时出现。
    await user.click(screen.getByTestId("authoring-save"));

    const toast = await screen.findByTestId("toast");
    const text = toast.textContent ?? "";
    expect(text).toContain("；");
    expect(text).not.toContain("。；");
    expect(text.endsWith("。")).toBe(true);
  });

  it("标签留空时按语言与运行方式自动补上（2.0 第 4 条）", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          method: "POST",
          path: "/api/v1/questions",
          respond: () => jsonResponse(questionPublic({ status: "draft" })),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "统计行数");
    // 标签一个字都不填。
    await goToStep(user, "stem");
    await user.type(screen.getByLabelText("题干"), "# 统计");
    await goToStep(user, "cases");
    await user.type(screen.getByLabelText("分值"), "1");
    await goToStep(user, "comparison");
    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
    await user.click(screen.getByTestId("authoring-save"));

    const request = await waitFor(() => {
      const found = stub?.requests.find(
        (item) => item.method === "POST" && item.url.pathname === "/api/v1/questions",
      );
      if (!found) throw new Error("尚未发出创建请求");
      return found;
    });
    // 默认语言是 Python、运行方式是命令行输入（stdin）。
    expect((request.body as Record<string, unknown>).tags).toEqual(["python", "stdin"]);
    // 补出来的标签写回输入框，教师能看到。
    await goToStep(user, "basic");
    expect(screen.getByLabelText("标签")).toHaveValue("python, stdin");
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
    // 保存按钮在最后一步（2.0：前四步只有"下一步"）。
    await goToStep(user, "comparison");
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
    expect(await screen.findByText("detail target")).toBeInTheDocument();
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

    // 3.0：保存成功后直接跳到"查看题目"，这一页已经离开，因此不可能再 POST 出第二道题。
    expect(await screen.findByText("detail target")).toBeInTheDocument();
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
    await user.type(screen.getByLabelText("分值"), "1");
    await goToStep(user, "comparison");
    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
    await user.click(screen.getByTestId("authoring-save"));

    // 没有输入夹具的 file 题编译不出 package，所以在本地就先拒绝，不发请求（原因用红色提示说）。
    expect(await screen.findByTestId("toast")).toHaveTextContent("请至少上传一个输入文件");
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
    // 保存成功后跳到这道题的详情页。
    expect(await screen.findByText("detail target")).toBeInTheDocument();
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
    // 保存成功后带着**服务端返回的**新版本标识跳到详情页（再取回版本 DTO 渲染发布入口）。
    expect(await screen.findByText("detail target")).toBeInTheDocument();
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
      await waitFor(() => {
        expect(
          lines.filter((line) => line.event === "question_draft_updated").length,
        ).toBeGreaterThan(0);
      });

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

describe("编辑页读回草稿的用例（3.0 之后的修改诉求）", () => {
  it("已存的参考答案与用例会被读回来，可以就地修改并保存", async () => {
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
        {
          path: `/api/v1/question-versions/${DRAFT_VERSION}/draft-content`,
          respond: () =>
            jsonResponse({
              reference_answer: "print(2)",
              cases: [
                {
                  name: "基本用例",
                  ordinal: 0,
                  weight: 1,
                  visible_to_student: false,
                  stdin: "a\n",
                  args: [],
                  expected_stdout: "2\n",
                  expected_stderr: null,
                  expected_exit_code: 0,
                },
              ],
            }),
        },
        {
          method: "PATCH",
          path: `/api/v1/questions/${QUESTION_ID}`,
          respond: () => jsonResponse(draftQuestion()),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    // 参考答案被读回来。
    await goToStep(user, "reference");
    await waitFor(() => expect(screen.getByLabelText("参考答案")).toHaveValue("print(2)"));

    // 用例也被读回来：原来那条能看见、能改，不需要"重新声明一遍"。
    await goToStep(user, "cases");
    expect(await screen.findByTestId("case-0")).toBeInTheDocument();
    expect(screen.getByTestId("case-0")).toHaveTextContent("基本用例");
    expect(screen.getByLabelText(/用例名/)).toHaveValue("基本用例");
    // 现在拿到的是真实集合，替换确认框不再出现。
    expect(screen.queryByTestId("authoring-cases-replace")).toBeNull();

    await user.type(screen.getByLabelText("标准输入"), "改过的输入");
    await goToStep(user, "comparison");
    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
    await user.click(screen.getByTestId("authoring-save"));

    const request = await waitFor(() => {
      const found = stub?.requests.find((item) => item.method === "PATCH");
      if (!found) throw new Error("尚未发出修改请求");
      return found;
    });
    const cases = (request.body as Record<string, unknown>).cases as Record<string, unknown>[];
    expect(cases[0].stdin).toBe("a\n改过的输入");
    expect(cases[0].name).toBe("基本用例");
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
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    // 检验并发布在第 5 步里（2.0：保存草稿与它同屏）。
    await goToStep(user, "comparison");
    const panel = await screen.findByTestId("reference-verification");
    // 入口在：这是 ADR-0015 解掉的死结（此前这一版只能找运维跑脚本）。
    expect(within(panel).getByTestId("reference-verify")).toBeInTheDocument();
    // 2.0：常驻的灰色说明文字已删，只留标题与按钮。
    expect(within(panel).queryByTestId("reference-gate")).toBeNull();

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
    expect(screen.getByTestId("authoring-assistance")).toHaveTextContent("AI意见仅供参考");

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
    stub = installFetchStub(baseRoutes(aiDraftRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await goToStep(user, "stem");
    await user.click(await screen.findByTestId("authoring-ai"));

    expect(await screen.findByTestId("generation-pill")).toHaveTextContent("待选择课程");
    expect(screen.getByTestId("generation-pill").textContent ?? "").not.toContain("暂不可用");
  });

  it("课程下拉就在 AI 面板里：选完立刻可用，不用滚到下面的基本信息", async () => {
    stub = installFetchStub(baseRoutes(aiDraftRoutes()));
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
    stub = installFetchStub(baseRoutes(aiDraftRoutes()));
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

  it("难度是四档下拉（简单/中等/较难/困难），默认中等", async () => {
    stub = installFetchStub(baseRoutes(aiDraftRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await openAi(user);
    await selectCourseInAiPanel(user);

    const select = screen.getByLabelText("难度") as HTMLSelectElement;
    expect(select.tagName).toBe("SELECT");
    expect([...select.options].map((option) => option.value)).toEqual([
      "简单",
      "中等",
      "较难",
      "困难",
    ]);
    expect(select.value).toBe("中等");

    await user.selectOptions(select, "困难");
    expect(select.value).toBe("困难");
  });

  it("显式点击才请求模型：目标为空时按钮禁用，请求体只带公开约束", async () => {
    stub = installFetchStub(baseRoutes(aiDraftRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await openAi(user);
    await selectCourseInAiPanel(user);

    const generate = await screen.findByTestId("generation-generate");
    expect(generate).toBeDisabled(); // 空目标不消耗任何 provider 调用

    await user.type(screen.getByLabelText("教学目标"), "让学生学会读标准输入");
    await user.click(screen.getByTestId("generation-generate"));
    // 3.0：生成时就要求模型带上测试用例，并在预览里直接显示条数。
    expect(await screen.findByTestId("generation-proposal-case-count")).toHaveTextContent("2");
    const generationRequest = stub?.requests.find(
      (item) => item.method === "POST" && item.url.pathname === "/api/v1/question-draft-generations",
    );
    expect((generationRequest?.body as Record<string, unknown>).desired_case_count).toBe(2);


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
    // 难度默认「中等」，并且作为参数发给 AI 控制出题复杂度。
    expect(body.difficulty).toBe("中等");
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

  it("候选会填入编辑器，字段问题与唯一全局提示同时显示（候选不是成品）", async () => {
    stub = installFetchStub(baseRoutes(aiDraftRoutes()));
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
    expect(screen.getByTestId("generation-expiry")).toBeInTheDocument();

    // 页面只有一个常驻全局提示；"生成信息"面板与免责清单都已移除，改为悬浮提示。
    expect(screen.getByTestId("generation-global-notice")).toHaveTextContent(
      "草稿仅供参考，请核对所有字段后再保存。",
    );
    expect(screen.queryByTestId("generation-provenance")).toBeNull();
    expect(screen.queryByTestId("generation-disclaimers")).toBeNull();

    // 标题旁的浅灰备注说明"谁能看到"。
    expect(screen.getByTestId("generation-preview-note")).toHaveTextContent(
      "内容仅教师可见，学生无法查看草稿和测试用例。",
    );

    // 候选被填进同一张表单：标题、题干、参考答案、用例都到位，权重为 null 的那个留空。
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
    stub = installFetchStub(baseRoutes(aiDraftRoutes()));
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
    stub = installFetchStub(baseRoutes(aiDraftRoutes()));
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

/** 生成相关端点（能力 / 创建 / 读取 / 丢弃）：这个 describe 之外也需要一份。 */
function aiDraftRoutes(): StubbedRoute[] {
  const generation = questionDraftGeneration({ generation_id: UUID_A });
  return [
    {
      path: "/api/v1/question-draft-generations/capabilities",
      respond: () => jsonResponse(generationCapabilities()),
    },
    {
      method: "POST",
      path: "/api/v1/question-draft-generations",
      respond: () => jsonResponse(generation),
    },
    {
      method: "GET",
      path: `/api/v1/question-draft-generations/${UUID_A}`,
      respond: () => jsonResponse(generation),
    },
    {
      method: "DELETE",
      path: `/api/v1/question-draft-generations/${UUID_A}`,
      respond: () => jsonResponse({}),
    },
  ];
}

describe("AI 草稿页的信息密度（文案、唯一提示、悬浮提示）", () => {
  it("两个按钮旁都有 ⓘ 悬浮提示，内容一致，且不新增弹窗", async () => {
    stub = installFetchStub(baseRoutes(aiDraftRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await openAi(user);
    await selectCourseInAiPanel(user);

    await user.type(screen.getByLabelText("教学目标"), "统计行数");
    await user.click(screen.getByTestId("generation-generate"));

    const generateHint = await screen.findByTestId("generation-hint-generate");
    const applyHint = await screen.findByTestId("generation-hint-apply");
    const expected =
      "AI 草稿仅为初稿，非标准内容，请逐项核对；草稿不会自动入库，需要手动保存；发布前请用参考答案验证测试用例。";
    expect(generateHint).toHaveTextContent(expected);
    expect(applyHint).toHaveTextContent(expected);
    // 提示是悬浮气泡（role=tooltip），不是新的弹窗。
    expect(within(generateHint).getByRole("tooltip")).toHaveTextContent(expected);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("元信息去掉项目符号（不再是列表），仍逐项可读", async () => {
    stub = installFetchStub(baseRoutes(aiDraftRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await openAi(user);
    await selectCourseInAiPanel(user);

    await user.type(screen.getByLabelText("教学目标"), "统计行数");
    await user.click(screen.getByTestId("generation-generate"));

    const facts = await screen.findByTestId("generation-proposal-facts");
    expect(facts.tagName).toBe("DIV");
    expect(facts.querySelector("ul")).toBeNull();
    expect(facts).toHaveTextContent("题目标题");
    expect(facts).toHaveTextContent("语言");
    expect(facts).toHaveTextContent("运行方式");
    expect(facts).toHaveTextContent("标签");
    // 用例条数仍在明面上。
    expect(screen.getByTestId("generation-proposal-case-count")).toHaveTextContent("测试用例");
  });

  it("「确认载入草稿」是原「重新载入 AI 草稿」的文案", async () => {
    stub = installFetchStub(baseRoutes(aiDraftRoutes()));
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);
    await openAi(user);
    await selectCourseInAiPanel(user);

    await user.type(screen.getByLabelText("教学目标"), "统计行数");
    await user.click(screen.getByTestId("generation-generate"));

    const apply = await screen.findByTestId("generation-apply");
    expect(apply).toHaveTextContent("确认载入草稿");
    expect(apply).not.toHaveTextContent("重新载入");
  });
});

describe("另存为新版本：从原题完整内容开始（不是一张空白表单）", () => {
  it("打开编辑页就带出已发布版本的参考代码与全部用例，保存时原样提交并新建版本", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          path: `/api/v1/questions/${QUESTION_ID}`,
          method: "GET",
          respond: () => jsonResponse(publishedQuestion()),
        },
        {
          path: `/api/v1/question-versions/${UUID_A}`,
          method: "GET",
          respond: () =>
            jsonResponse(
              questionVersionPublic({
                question_version_id: UUID_A,
                version: 2,
                status: "published",
                content_digest: SHA_A,
              }),
            ),
        },
        {
          path: `/api/v1/question-versions/${UUID_A}/draft-content`,
          method: "GET",
          respond: () =>
            jsonResponse({
              reference_answer: "print(42)",
              cases: [
                {
                  name: "可见样例",
                  ordinal: 0,
                  weight: 1,
                  visible_to_student: true,
                  stdin: "1\n",
                  args: [],
                  expected_stdout: "42\n",
                  expected_stderr: null,
                  expected_exit_code: 0,
                },
                {
                  name: "隐藏强测",
                  ordinal: 1,
                  weight: 3,
                  visible_to_student: false,
                  stdin: "7\n",
                  args: [],
                  expected_stdout: "14\n",
                  expected_stderr: null,
                  expected_exit_code: 0,
                },
              ],
            }),
        },
        {
          method: "PATCH",
          path: `/api/v1/questions/${QUESTION_ID}`,
          respond: () => jsonResponse(publishedQuestion()),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    // 参考代码从原题带出来，不是空的。
    await goToStep(user, "reference");
    await waitFor(() => expect(screen.getByLabelText("参考答案")).toHaveValue("print(42)"));

    // 两个用例都在：可见样例 + 隐藏强测。
    await goToStep(user, "cases");
    expect(await screen.findByTestId("case-0")).toHaveTextContent("可见样例");
    expect(screen.getByTestId("case-1")).toHaveTextContent("隐藏强测");
    // 内容是从服务端载入的"既有集合"，不需要再点一次"替换用例"确认。
    expect(screen.queryByTestId("authoring-cases-replace")).toBeNull();

    // 对比方式与退出码不在公开版本 DTO 里（既有行为）：这是老师每次都要选的两项。
    await fillAdapterExitCode(user);
    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");

    // 保存按钮在最后一步（对比设置）；另存为新版本必须写清"这一版改了什么"。
    await goToStep(user, "comparison");
    const note = screen.getByLabelText("版本说明");
    expect(note).toHaveValue("");
    await user.type(note, "改写输出示例，用例保持不变");
    await user.click(screen.getByTestId("authoring-save"));

    const request = await waitFor(() => {
      const found = stub?.requests.find((item) => item.method === "PATCH");
      if (!found) throw new Error("还没有发出保存请求");
      return found;
    });
    // 保存走 as_new_version：新版本号由服务端 +1，旧版本不动。
    expect(request.url.searchParams.get("as_new_version")).toBe("true");
    const body = request.body as Record<string, unknown>;
    const cases = body.cases as Record<string, unknown>[];
    expect(cases.map((item) => item.name)).toEqual(["可见样例", "隐藏强测"]);
    expect(cases[0].visible_to_student).toBe(true);
    expect(cases[1].visible_to_student).toBe(false);
    expect(cases[0].weight).toBe(1);
    expect(cases[1].weight).toBe(3);
    expect(cases[0].stdin).toBe("1\n");
    expect(cases[0].expected_stdout).toBe("42\n");
    expect(cases[1].stdin).toBe("7\n");
    expect(cases[1].expected_stdout).toBe("14\n");
    expect(cases[1].expected_exit_code).toBe(0);
  });
});

describe("检验结果里点未通过的用例：直接跳到用例编辑区并定位", () => {
  it("点红色序号后切到用例步骤，该用例被选中且光标落在它的输入框", async () => {
    stub = installFetchStub(
      baseRoutes([
        {
          path: `/api/v1/questions/${QUESTION_ID}`,
          method: "GET",
          respond: () => jsonResponse(draftQuestion()),
        },
        {
          path: `/api/v1/question-versions/${DRAFT_VERSION}`,
          method: "GET",
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
        {
          path: `/api/v1/question-versions/${DRAFT_VERSION}/draft-content`,
          method: "GET",
          respond: () =>
            jsonResponse({
              reference_answer: "print(1)",
              cases: [0, 1, 2].map((ordinal) => ({
                name: `用例${ordinal + 1}`,
                ordinal,
                weight: 1,
                visible_to_student: ordinal === 0,
                stdin: `${ordinal}\n`,
                args: [],
                expected_stdout: `${ordinal}\n`,
                expected_stderr: null,
                expected_exit_code: 0,
              })),
            }),
        },
        {
          method: "POST",
          path: "/api/v1/reference-verifications",
          respond: () =>
            jsonResponse(
              referenceVerification({
                cases_total: 3,
                cases_passed: 2,
                cases: [
                  { ordinal: 0, passed: true, failure_category: null },
                  { ordinal: 1, passed: false, failure_category: "output_mismatch" },
                  { ordinal: 2, passed: true, failure_category: null },
                ],
                attestation_id: null,
              }),
            ),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(QUESTION_ID);

    // 检验在最后一步（对比设置）下面。
    await goToStep(user, "comparison");
    await user.click(await screen.findByTestId("reference-verify"));

    // 第 2 条（ordinal 1）未通过：序号是红色按钮。
    const failed = await screen.findByTestId("reference-case-open-1");
    expect(failed).toHaveClass("verify-case-open");
    await user.click(failed);

    // 切到用例步骤，并定位到那一条。
    const casesPanel = await screen.findByTestId("step-cases");
    const card = within(casesPanel).getByTestId("case-1");
    expect(card).toHaveAttribute("data-selected", "true");
    expect(card).toHaveClass("case-card--selected");
    // 光标在它的标准输入里，可以直接改。
    expect(document.activeElement).toBe(within(card).getByLabelText("标准输入"));
  });
});
