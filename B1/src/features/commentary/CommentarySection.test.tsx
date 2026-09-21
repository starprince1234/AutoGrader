import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_RUN,
  evaluationCommentary,
  sessionCreated,
} from "../../testing/contractFixtures";
import { commentaryPath } from "./useCommentary";
import { CommentarySection } from "./CommentarySection";
import { signIn } from "../../auth/sessionStore";
import type { Role } from "../../api/types";

/**
 * 教学评语轨（RQ-T-20）与 AI 参考分（RQ-T-30）。
 *
 * 断言口径取自契约原文：
 * - `disclaimers` 必须**逐字**显示（`EvaluationCommentary.disclaimers` 的 `description`）；
 * - `unavailable` 是配置而不是失败（`ADR-0009` 决策 4），因此不能渲染成错误；
 * - `failed` 只影响本轨，**不得**影响分数展示；
 * - `advisory_score` 为 `null` 时整块不渲染；
 * - 参考分的 DOI 外观必须与权威分不同（在 `GradeDrilldown.test.tsx` 里断言并列与顺序）。
 */

function commentaryRoutes(
  role: Role,
  options: {
    get?: () => Response;
    post?: () => Response;
  } = {},
): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () => jsonResponse(sessionCreated({ roles: [role], access_token: "t" })),
    },
    {
      method: "GET",
      path: commentaryPath(UUID_RUN),
      respond: () => options.get?.() ?? jsonResponse(evaluationCommentary()),
    },
    {
      method: "POST",
      path: commentaryPath(UUID_RUN),
      respond: () => options.post?.() ?? jsonResponse(evaluationCommentary()),
    },
  ];
}

async function open(
  role: Role,
  options: { get?: () => Response; post?: () => Response } = {},
) {
  const stub = installFetchStub(commentaryRoutes(role, options));
  const user = userEvent.setup();
  await signIn(`${role}1`, "pw");
  renderWithProviders(<CommentarySection runId={UUID_RUN} />);
  await waitFor(() => expect(screen.getByTestId("commentary")).toBeInTheDocument());
  return { stub, user };
}

afterEach(() => {
  // 让每个用例从干净状态开始：桩与 mock 都不跨用例。
  vi.restoreAllMocks();
});

describe("教学评语轨（RQ-T-20）", () => {
  it("unavailable 显示为「本部署未启用」，不是错误；disclaimers 仍然逐字显示", async () => {
    const { stub } = await open("teacher", {
      get: () =>
        jsonResponse(
          evaluationCommentary({
            status: "unavailable",
            text: null,
            advisory_score: null,
            provenance: null,
          }),
        ),
    });

    const banner = await screen.findByTestId("commentary-unavailable");
    expect(banner).toHaveTextContent("本部署未启用教学评语");
    expect(banner).toHaveTextContent("不是错误");
    // 配置状态不能被渲染成失败，也不能有"加载中"残留。
    expect(screen.queryByTestId("commentary-error")).toBeNull();
    expect(screen.queryByTestId("commentary-failed")).toBeNull();
    expect(screen.queryByTestId("commentary-text")).toBeNull();
    // 契约：disclaimers 永远存在，即使 `status` 不是 ready、即使参考分为 null。
    expect(screen.getByTestId("commentary-disclaimers")).toHaveTextContent(
      "不参与判分：不进入成绩、导出与结果摘要。",
    );
    stub.restore();
  });

  it("ready 渲染评语正文，并把 disclaimers 三条逐字列出", async () => {
    const { stub } = await open("teacher");

    expect(await screen.findByTestId("commentary-text")).toHaveTextContent(
      "本题主要失分在第二个用例",
    );
    const disclaimers = screen.getByTestId("commentary-disclaimers");
    // 逐字（`getByText` 默认精确匹配整段文本）。
    expect(disclaimers).toHaveTextContent("由模型生成，非确定性内容。");
    expect(disclaimers).toHaveTextContent("不参与判分：不进入成绩、导出与结果摘要。");
    expect(disclaimers).toHaveTextContent("不作为申诉依据。");
    stub.restore();
  });

  it("failed 给出可读原因，且明确说明分数轨道不受影响", async () => {
    const { stub } = await open("teacher", {
      get: () =>
        jsonResponse(
          evaluationCommentary({ status: "failed", text: null, advisory_score: null }),
        ),
    });

    const banner = await screen.findByTestId("commentary-failed");
    expect(banner).toHaveTextContent("评语生成被尝试过但没有成功");
    expect(banner).toHaveTextContent("权威分与逐用例证据都不受它影响");
    // 失败态不得渲染正文，也不得悄悄显示参考分。
    expect(screen.queryByTestId("commentary-text")).toBeNull();
    expect(screen.queryByTestId("advisory-score")).toBeNull();
    stub.restore();
  });

  it("pending 说明「已受理、尚未完成」，不编造正文", async () => {
    const { stub } = await open("teacher", {
      get: () =>
        jsonResponse(
          evaluationCommentary({ status: "pending", text: null, advisory_score: null }),
        ),
    });

    expect(await screen.findByTestId("commentary-pending")).toHaveTextContent("尚未完成");
    expect(screen.queryByTestId("commentary-text")).toBeNull();
    stub.restore();
  });

  it("403 显示为课程级策略说明（服务端判定），而不是通用失败", async () => {
    const { stub } = await open("student", {
      get: () =>
        problemResponse({
          status: 403,
          title: "Forbidden",
          detail: "Commentary visibility is disabled for students in this course.",
        }),
    });

    const banner = await screen.findByTestId("commentary-forbidden");
    expect(banner).toHaveTextContent("403");
    expect(banner).toHaveTextContent("默认关闭");
    // 403 不是"读取出错"：不得同时出现通用错误框（那会把授权结论说成故障）。
    expect(screen.queryByTestId("commentary-error")).toBeNull();
    stub.restore();
  });

  it("读取失败（500）走通用错误态，仍然不影响分数展示", async () => {
    const { stub } = await open("teacher", {
      get: () =>
        problemResponse({ status: 500, title: "Server Error", detail: "commentary backend down" }),
    });

    expect(await screen.findByTestId("commentary-error")).toBeInTheDocument();
    expect(screen.queryByTestId("commentary-forbidden")).toBeNull();
    stub.restore();
  });

  it("尚未生成（404，本部署实测的真实行为）显示为信息而不是错误", async () => {
    const { stub } = await open("teacher", {
      get: () =>
        problemResponse({
          status: 404,
          title: "Not Found",
          detail:
            "No commentary has been generated for this evaluation run yet. Generate it with POST on this path. This is not the same as a missing run.",
        }),
    });

    const banner = await screen.findByTestId("commentary-not-generated");
    // 服务端的原文必须被带出来：它明确区分了"没生成过"与"运行不存在"。
    expect(banner).toHaveTextContent("No commentary has been generated");
    expect(banner).toHaveTextContent("This is not the same as a missing run.");
    // 404 不是"读取出错"：不得走通用错误态（那会把"还没生成"说成"出错了"）。
    expect(screen.queryByTestId("commentary-error")).toBeNull();
    stub.restore();
  });
});

describe("AI 评分参考（RQ-T-30 / ADR-0014）", () => {
  it("非 null 时渲染参考分，并带「不是分数／不参与判分／不作为申诉依据」标注", async () => {
    const { stub } = await open("teacher");

    const block = await screen.findByTestId("advisory-score");
    expect(block).toHaveTextContent("AI 参考分（顾问性）");
    expect(block).toHaveTextContent("62 / 100");
    expect(block).toHaveTextContent("不参与判分、不参与申诉");
    expect(screen.getByTestId("advisory-score-rationale")).toHaveTextContent("第二个用例未通过");
    expect(screen.getByTestId("advisory-score-confidence")).toHaveTextContent("中");
    stub.restore();
  });

  it("为 null 时**不渲染**参考分区块（不以 0 代替），只说明本次没有产生", async () => {
    const { stub } = await open("teacher", {
      get: () => jsonResponse(evaluationCommentary({ advisory_score: null })),
    });

    expect(await screen.findByTestId("commentary-text")).toBeInTheDocument();
    expect(screen.queryByTestId("advisory-score")).toBeNull();
    // 正文照常，且明确说明"本次没有参考分"而不是留白。
    expect(screen.getByTestId("advisory-absent")).toHaveTextContent("不渲染");
    stub.restore();
  });

  it("服务端漏发 disclaimers（契约违规）时，参考分整块不渲染并报告事实", async () => {
    const { stub } = await open("teacher", {
      get: () => jsonResponse(evaluationCommentary({ disclaimers: [] })),
    });

    expect(await screen.findByTestId("commentary-disclaimers-missing")).toHaveTextContent(
      "契约要求它至少一项",
    );
    // 契约原话：渲染参考分却不带标注，就是把模型的建议当成成绩。
    expect(screen.queryByTestId("advisory-score")).toBeNull();
    stub.restore();
  });

  it("参考分只被读取与显示：成绩轨道的计算/导出模块里不出现任何 advisory 引用", () => {
    // `ADR-0014` 决策 3 在客户端的落点。这是**反向断言**：不是"检查它被正确使用"，
    // 而是"检查它根本没有进入这些模块"——求和、排序、导出只要碰了它，这一条就会失败。
    //
    // 用 Vite 的 `?raw` glob 读源码：本仓库没有 `@types/node`，测试里不能用 `node:fs`。
    const modules = import.meta.glob("../grades/*.ts", {
      query: "?raw",
      import: "default",
      eager: true,
    }) as Record<string, string>;
    const wanted = ["aggregate.ts", "cohort.ts", "useGrades.ts"];
    const found = Object.entries(modules).filter(([key]) =>
      wanted.some((name) => key.endsWith(name)),
    );
    // 先断言"确实读到了这些文件"：否则一次重命名就会让这条断言变成永远通过的空检查。
    expect(found.map(([key]) => key).sort()).toHaveLength(wanted.length);
    for (const [key, source] of found) {
      expect(source, `${key} 不得引用 advisory（参考分不属于成绩轨道）`).not.toMatch(/advisory/i);
    }
  });
});

describe("评语生成（教师/助教）", () => {
  it("教师可以请求生成，并复用服务端返回的状态（写回缓存，不重复 GET）", async () => {
    const { stub, user } = await open("teacher", {
      get: () =>
        jsonResponse(
          evaluationCommentary({ status: "unavailable", text: null, advisory_score: null }),
        ),
      post: () => jsonResponse(evaluationCommentary({ status: "pending", text: null })),
    });

    await user.click(await screen.findByTestId("regenerate-commentary"));
    await waitFor(() =>
      expect(screen.getByTestId("commentary-pending")).toBeInTheDocument(),
    );
    const posts = stub.requests.filter(
      (request) => request.method === "POST" && request.url.pathname === commentaryPath(UUID_RUN),
    );
    expect(posts).toHaveLength(1);
    // 写回缓存后不再额外 GET：否则页面会先显示 pending（202 的答案）又跳回 unavailable。
    const gets = stub.requests.filter(
      (request) => request.method === "GET" && request.url.pathname === commentaryPath(UUID_RUN),
    );
    expect(gets).toHaveLength(1);
    stub.restore();
  });

  it("未配置 provider（409）显示「未启用」而不是通用失败", async () => {
    const { stub, user } = await open("teacher", {
      post: () =>
        problemResponse({
          status: 409,
          title: "Conflict",
          detail: "No commentary provider is configured (AUTOGRADER_COMMENTARY_PROVIDER).",
        }),
    });

    await user.click(await screen.findByTestId("regenerate-commentary"));
    const banner = await screen.findByTestId("commentary-not-enabled");
    expect(banner).toHaveTextContent("本部署未启用评语生成");
    expect(banner).toHaveTextContent("AUTOGRADER_COMMENTARY_PROVIDER");
    expect(screen.queryByTestId("commentary-generate-error")).toBeNull();
    stub.restore();
  });

  it("学生看不到生成入口（契约：生成是教师/助教的编辑动作），但阅读路径不受影响", async () => {
    const { stub } = await open("student");

    expect(await screen.findByTestId("commentary-text")).toBeInTheDocument();
    expect(screen.queryByTestId("regenerate-commentary")).toBeNull();
    stub.restore();
  });
});
