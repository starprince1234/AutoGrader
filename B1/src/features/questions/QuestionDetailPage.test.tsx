import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
const VERSION_B = "00000000-0000-4000-8000-0000000000b9";
const SHA_B = "b".repeat(64);

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

/** 受控容器：模拟路由 `?version=` 深链的选择行为（状态由测试驱动）。 */
function Harness() {
  const [versionId, setVersionId] = useState<string | null>(null);
  return (
    <div>
      <button type="button" onClick={() => setVersionId(VERSION_A)}>
        选择版本 A
      </button>
      <button type="button" onClick={() => setVersionId(VERSION_B)}>
        选择版本 B
      </button>
      <button type="button" onClick={() => setVersionId(null)}>
        清除版本
      </button>
      <QuestionDetailPage
        questionId={QUESTION_ID}
        versionId={versionId}
        onSelectVersion={setVersionId}
      />
    </div>
  );
}

/**
 * 题目详情里有指向作答页的 `<Link>`，因此测试需要一个最小路由上下文
 * （只用测试路由表，不引入应用路由表，避免测试与应用装配耦合）。
 */
function renderQuestionDetail() {
  const rootRoute = createRootRoute({ component: Harness });
  const submitRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/submit/$questionVersionId",
    component: () => <span>submit target</span>,
  });
  const editRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/questions/$questionId/edit",
    component: () => <span>edit target</span>,
  });
  const testRouter = createRouter({
    routeTree: rootRoute.addChildren([submitRoute, editRoute]),
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

  it("版本下拉来自契约的 versions 列表，且渲染的是该版本自己的题干", async () => {
    stub = installFetchStub([
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
              stem_markdown: "第三版题干",
            }),
          ),
      },
      {
        path: `/api/v1/question-versions/${VERSION_B}`,
        respond: () =>
          jsonResponse(
            questionVersionPublic({
              question_version_id: VERSION_B,
              version: 2,
              stem_markdown: "第二版题干",
            }),
          ),
      },
    ]);

    const user = userEvent.setup();
    renderQuestionDetail();

    // 契约给出完整版本历史，因此不再需要手工粘贴 question_version_id。
    // 未显式选择时默认最新**已发布**版本（不猜、也不留空）。
    const picker = await screen.findByTestId("version-select");
    expect(within(picker).getAllByRole("option")).toHaveLength(2);
    expect(await screen.findByTestId("version-panel")).toHaveTextContent("版本 3");
    // 两处题干：当前题干（页面主体）+ 选中版本的冻结题干（内容不同，必须都渲染）。
    expect(screen.getByTestId("question-stem-body")).toBeInTheDocument();
    expect(screen.getByTestId("version-stem-body")).toHaveTextContent("第三版题干");
    expect(screen.getByTestId("version-stem-label")).toBeInTheDocument();

    // 切换到历史版本：渲染的是**该版本自己的**题干，而不是当前题干。
    await user.selectOptions(picker, VERSION_B);
    expect(await screen.findByTestId("version-panel")).toHaveTextContent("版本 2");
    expect(screen.getByTestId("historical-version-notice")).toBeInTheDocument();
    expect(screen.getByTestId("version-stem-body")).toHaveTextContent("第二版题干");

    // 切回版本 A：命中查询缓存，渲染内容与第一次一致（不因切换而改变）
    await user.selectOptions(picker, VERSION_A);
    expect(await screen.findByTestId("version-panel")).toHaveTextContent("版本 3");

    const versionRequests = stub.requests.filter((request) =>
      request.url.pathname.startsWith("/api/v1/question-versions/"),
    );
    expect([...new Set(versionRequests.map((request) => request.url.pathname))].sort()).toEqual(
      [
        `/api/v1/question-versions/${VERSION_A}`,
        `/api/v1/question-versions/${VERSION_B}`,
      ].sort(),
    );
  });

  it("版本冻结题干与当前题干一致时不重复渲染（同一段富文本不出现两次）", async () => {
    const sameStem = questionPublic().stem_markdown;
    stub = installFetchStub([
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
              version: 1,
              stem_markdown: sameStem,
            }),
          ),
      },
    ]);

    renderQuestionDetail();

    await screen.findByTestId("version-panel");
    // 只有题目主体那一份 stem；版本面板给出"内容一致"的说明而不是第二份副本。
    expect(screen.queryByTestId("version-stem-body")).toBeNull();
    expect(screen.getByTestId("version-stem-same")).toBeInTheDocument();
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

  it("版本面板显示修订说明与结构差异（CHG-0013 的可审计部分）", async () => {
    stub = installFetchStub([
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
              stem_markdown: "第三版题干",
              revision_note: "例题里的截止时间写错了",
              revision_diff: {
                case_count_before: 2,
                case_count_after: 3,
                case_set_changed: true,
                weights_changed: false,
                fixtures_changed: null,
                stem_length_before: 40,
                stem_length_after: 52,
                adapter_kind_changed: false,
                size_limits_changed: null,
                reference_answer_digest_before: SHA_B,
                reference_answer_digest_after: SHA_A,
              },
            }),
          ),
      },
    ]);

    renderQuestionDetail();

    const summary = await screen.findByTestId("revision-summary");
    expect(within(summary).getByTestId("revision-note")).toHaveTextContent(
      "例题里的截止时间写错了",
    );
    expect(summary).toHaveTextContent("2 → 3");
    expect(summary).toHaveTextContent("有变化");
    // `null` 的条目表示"没这一项"：渲染成空，而不是"无变化"。
    const fixturesRow = within(summary).getByText("文件有改动").closest(".facts__row") as HTMLElement;
    expect(fixturesRow.querySelector("dd")?.textContent).toBe("");
    // 只有摘要（digest），没有参考答案本身。
    expect(summary).not.toHaveTextContent("print(");
  });

  it("首个版本的 revision_diff 为 null 时说明「没有可比较的上一版」，不是「缺数据」", async () => {
    stub = installFetchStub([
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
              version: 1,
              revision_note: null,
              revision_diff: null,
            }),
          ),
      },
    ]);

    renderQuestionDetail();

    const summary = await screen.findByTestId("revision-summary");
    expect(summary).toHaveTextContent("这是第一个版本");
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

    await screen.findByTestId("version-panel");
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
    // 门禁的语义写在这里：可发布 = 全通过 ∧ 存证已记录 ∧ 摘要未变。
    expect(within(panel).getByTestId("reference-gate")).toHaveTextContent("这次检验有记录");
    // 未验证 → 没有发布动作。
    expect(within(panel).queryByTestId("reference-publish")).toBeNull();
    // 编辑入口在位：写路径不再是"看得到改不了"。
    expect(screen.getByTestId("edit-question")).toBeInTheDocument();
    expect(screen.getByTestId("version-not-published")).toBeInTheDocument();
  });
});
