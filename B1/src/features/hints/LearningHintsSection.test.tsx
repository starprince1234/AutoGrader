import { afterEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { installFetchStub, jsonResponse, problemResponse } from "../../testing/httpBoundary";
import { UUID_RUN, learningHintsResponse } from "../../testing/contractFixtures";
import { LearningHintsSection } from "./LearningHintsSection";

const PATH = `/api/v1/evaluation-runs/${UUID_RUN}/learning-hints`;
let originalFetch: typeof globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function renderHints(options: {
  runState?: "completed" | "running" | "rejected" | "failed";
  get?: () => Response;
  post?: () => Response;
  questionVersionId?: string;
}) {
  originalFetch = globalThis.fetch;
  const stub = installFetchStub([
    {
      method: "GET",
      path: PATH,
      respond: options.get ?? (() => jsonResponse(learningHintsResponse())),
    },
    {
      method: "POST",
      path: PATH,
      respond: options.post ?? (() => jsonResponse(learningHintsResponse())),
    },
  ]);
  renderWithProviders(
    <LearningHintsSection
      runId={UUID_RUN}
      runState={options.runState ?? "completed"}
      questionVersionId={options.questionVersionId ?? "00000000-0000-4000-8000-0000000000c3"}
    />,
  );
  return stub;
}

describe("学生分层学习提示（AI-FLOW-01B）", () => {
  it("ready：先显示第一条，逐层展开，隐藏/重新展开不重新请求模型", async () => {
    const stub = renderHints({});
    const user = userEvent.setup();

    expect(await screen.findByTestId("hint-step-knowledge")).toHaveTextContent("先看清输入格式");
    // 第二条尚未展开：逐层展示只是学习节奏控制。
    expect(screen.queryByTestId("hint-step-direction")).toBeNull();

    await user.click(screen.getByTestId("hints-reveal-next"));
    expect(screen.getByTestId("hint-step-direction")).toHaveTextContent("从失败类别排查");

    // 隐藏第一条 → 文本消失；再展开 → 文本回来。都不发新请求。
    await user.click(screen.getByTestId("hints-hide-knowledge"));
    expect(screen.queryByTestId("hint-text-knowledge")).toBeNull();
    await user.click(screen.getByTestId("hints-show-knowledge"));
    expect(screen.getByTestId("hint-text-knowledge")).toBeInTheDocument();

    const posts = stub.requests.filter((request) => request.method === "POST");
    expect(posts).toHaveLength(0);
    // 规范性标注必须随内容显示；提示不得出现分数/参考分。
    expect(screen.getByTestId("hints-disclaimers").textContent).toContain("不参与评分");
    expect(screen.queryByTestId("advisory-score")).toBeNull();
    expect(screen.getByTestId("learning-hints").textContent).not.toMatch(/参考分/);
    stub.restore();
  });

  it("not_requested：点击生成后显示真实返回，请求带幂等键", async () => {
    const stub = renderHints({
      get: () => jsonResponse(learningHintsResponse({ status: "not_requested", hint_steps: [], generation_id: null, provider: null, model: null, prompt_version: null, context_scope: [] })),
      post: () => jsonResponse(learningHintsResponse()),
    });
    const user = userEvent.setup();

    expect(await screen.findByTestId("hints-not-requested")).toBeInTheDocument();
    await user.click(screen.getByTestId("hints-generate"));

    expect(await screen.findByTestId("hint-step-knowledge")).toBeInTheDocument();
    const post = stub.requests.find((request) => request.method === "POST");
    expect(post?.headers.get("idempotency-key")).toBeTruthy();
    // 生成期间不重复调用模型：按钮在请求中消失/禁用由 mutation 状态驱动。
    expect(stub.requests.filter((request) => request.method === "POST")).toHaveLength(1);
    stub.restore();
  });

  it("unavailable：本部署未启用时如实说明，权威成绩不受影响", async () => {
    const stub = renderHints({
      get: () =>
        jsonResponse(
          learningHintsResponse({
            status: "unavailable",
            hint_steps: [],
            generation_id: null,
            provider: null,
            model: null,
            prompt_version: null,
            context_scope: [],
          }),
        ),
    });
    expect(await screen.findByTestId("hints-unavailable")).toHaveTextContent("未启用");
    expect(screen.queryByTestId("hints-generate")).toBeNull();
    stub.restore();
  });

  it("failed：说明暂时无法生成，并允许显式重试", async () => {
    const stub = renderHints({
      get: () =>
        jsonResponse(
          learningHintsResponse({
            status: "failed",
            hint_steps: [],
            generation_id: null,
            provider: "p",
            model: "m",
            prompt_version: "v",
            context_scope: [],
            failure_category: "invalid_model_output",
          }),
        ),
    });
    expect(await screen.findByTestId("hints-failed")).toHaveTextContent("暂时无法生成");
    expect(screen.getByTestId("hints-generate")).toBeInTheDocument();
    stub.restore();
  });

  it("评测未完成：不请求提示，也不给生成入口", async () => {
    const stub = renderHints({ runState: "running" });
    expect(await screen.findByTestId("hints-not-completed")).toHaveTextContent("评测完成后");
    expect(stub.requests).toHaveLength(0);
    expect(screen.queryByTestId("hints-generate")).toBeNull();
    stub.restore();
  });

  it("他人 run（403）：显示无权查看，不渲染任何提示内容", async () => {
    const stub = renderHints({
      get: () =>
        problemResponse({
          status: 403,
          title: "Forbidden",
          detail: "not the owner of this evaluation run",
        }),
    });
    expect(await screen.findByTestId("hints-forbidden")).toHaveTextContent("无权查看");
    expect(screen.queryByTestId("hint-step-knowledge")).toBeNull();
    stub.restore();
  });

  it("返回作业按钮把当前 version id 交给调用方（保留本地草稿）", async () => {
    let opened: string | null = null;
    const stub = installFetchStub([
      { method: "GET", path: PATH, respond: () => jsonResponse(learningHintsResponse()) },
    ]);
    const user = userEvent.setup();
    renderWithProviders(
      <LearningHintsSection
        runId={UUID_RUN}
        runState="completed"
        questionVersionId="00000000-0000-4000-8000-0000000000c3"
        onBackToQuestion={(versionId) => {
          opened = versionId;
        }}
      />,
    );
    await user.click(await screen.findByTestId("hints-back"));
    await waitFor(() => expect(opened).toBe("00000000-0000-4000-8000-0000000000c3"));
    stub.restore();
  });
});
