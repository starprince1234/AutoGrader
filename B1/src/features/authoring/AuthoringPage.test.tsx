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

function renderAuthoring(questionId: string | null) {
  const rootRoute = createRootRoute({
    component: () => <AuthoringPage questionId={questionId} />,
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
  const courseSelect = await screen.findByLabelText("课程");
  await waitFor(() => expect(within(courseSelect).getAllByRole("option")).toHaveLength(2));
  await user.selectOptions(courseSelect, UUID_A);
}

/** 填满一次合法的新建表单（PATCH 与 POST 都要求这四个必需字段，因此测试里都要填）。 */
async function fillCreateForm(user: ReturnType<typeof userEvent.setup>) {
  await selectCourse(user);
  await user.type(screen.getByLabelText("题目标题"), "统计文件行数");
  await user.type(screen.getByLabelText("题干"), "# 统计行数");
  await user.type(screen.getByLabelText("标签"), "Process Scheduling, unix");
  await user.type(screen.getByLabelText("参考答案"), "print(2)");
  await user.type(screen.getByLabelText("用例名"), "基本用例");
  await user.type(screen.getByLabelText("标准输入"), "a\nb\n");
  await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
}

/**
 * 编辑路径**不**凭空渲染用例行（读不到服务端那份集合就不假装知道有几个），因此要显式加一行；
 * 而提交用例会整体替换那份读不到的集合（CHG-0017），所以还必须勾选那次确认。
 */
async function addCaseAndConfirm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByTestId("case-add"));
  await user.type(screen.getByLabelText("用例名"), "用例一");
  await user.click(screen.getByLabelText(/我确认/));
}

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
    expect(screen.getByText(/published_only/)).toBeInTheDocument();
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
    await user.type(screen.getByLabelText("题干"), "题干");
    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
    // 参考答案留空、用例留空（把新建时那一行邀请删掉）。
    await user.click(screen.getByTestId("case-remove-0"));
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

  it("该版本使用 kind=file 时明确拒绝保存（夹具摘要只能由服务端计算）", async () => {
    stub = installFetchStub(baseRoutes());
    const user = userEvent.setup();
    await signIn("teacher1", "pw");
    renderAuthoring(null);

    await user.selectOptions(await screen.findByLabelText("测试框架"), "file");
    expect(await screen.findByTestId("adapter-file-locked")).toHaveTextContent("serviceToken");

    await selectCourse(user);
    await user.type(screen.getByLabelText("题目标题"), "文件题");
    await user.type(screen.getByLabelText("题干"), "题干");
    await user.type(screen.getByLabelText("参考答案"), "print(2)");
    await user.type(screen.getByLabelText("用例名"), "用例一");
    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
    await user.click(screen.getByTestId("authoring-save"));

    expect(await screen.findByTestId("authoring-issues")).toHaveTextContent(
      "本页无法声明 `content_sha256`",
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
    expect(screen.getByLabelText("参考答案")).toHaveValue("");
    expect(screen.getByTestId("authoring-protected-unknown")).toBeInTheDocument();
    expect(screen.queryByLabelText("修订说明")).toBeNull();

    await user.type(screen.getByLabelText("参考答案"), "print(2)");
    await addCaseAndConfirm(user);
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
      "as_new_version=true",
    );
    await user.type(await screen.findByLabelText("参考答案"), "print(2)");
    await addCaseAndConfirm(user);
    await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
    await user.type(screen.getByLabelText("修订说明"), "例题的截止时间写错了");
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
    expect(await screen.findByTestId("authoring-saved")).toHaveTextContent(
      `question_version_id = ${NEW_VERSION}`,
    );
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

      await user.type(await screen.findByLabelText("参考答案"), "print(2)");
      await addCaseAndConfirm(user);
      await user.selectOptions(screen.getByLabelText("标准输出比较"), "exact");
      await user.click(screen.getByTestId("authoring-save"));
      await screen.findByTestId("authoring-saved");

      const updateLog = lines.find((line) => line.event === "question_draft_updated");
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
    // 门禁说明与"这个端点不给受保护内容"必须可见。
    expect(within(panel).getByTestId("reference-gate")).toHaveTextContent("attestation_id");
    expect(within(panel).getByTestId("reference-no-protected-content")).toHaveTextContent(
      "不含期望输出",
    );
    expect(within(panel).getByTestId("reference-version-digest")).toHaveTextContent(SHA_A);
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
    expect(clean).toHaveTextContent("检查过、未命中任何规则");
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
    expect(unavailable).toHaveTextContent("未启用");
    expect(unavailable).toHaveTextContent("no model provider is configured");
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
    expect(screen.getByTestId("assistance-advisory")).toHaveTextContent("不构成权威判定");

    const request = stub.requests.find(
      (item) => item.url.pathname === `/api/v1/questions/${QUESTION_ID}/authoring-assistance`,
    );
    expect(request?.body).toEqual({ topics: ["quality"], notes: null });
  });

  it("诊断命中规则时按 rule_id 与严重级别展示（确定性那一条轨）", async () => {
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
    expect(within(table).getByText("CMP-001")).toBeInTheDocument();
    expect(within(table).getByText("警告")).toBeInTheDocument();
    expect(table).toHaveTextContent("第 2 个用例");
  });
});

describe("出题页的可见性前提", () => {
  it("编辑页会取最新版本以决定「就地改」还是「新建版本」", async () => {
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

    await waitFor(() => expect(screen.getByLabelText("合计字节上限")).toHaveValue("4096"));
    const versionRequest = stub.requests.find((item) =>
      item.url.pathname.startsWith("/api/v1/question-versions/"),
    );
    expect(versionRequest?.url.pathname).toBe(`/api/v1/question-versions/${DRAFT_VERSION}`);
  });
});
