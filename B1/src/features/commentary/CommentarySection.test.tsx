import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
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
  vi.restoreAllMocks();
});

describe("AI 评价区块", () => {
  it("unavailable 显示简洁提示，不出现冗余免责或参考分框", async () => {
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
    expect(banner).toHaveTextContent("暂未启用 AI 评价");
    expect(screen.queryByTestId("commentary-error")).toBeNull();
    expect(screen.queryByTestId("commentary-failed")).toBeNull();
    expect(screen.queryByTestId("commentary-text")).toBeNull();
    expect(screen.queryByTestId("commentary-disclaimers")).toBeNull();
    expect(screen.queryByTestId("commentary-provenance")).toBeNull();
    expect(screen.queryByTestId("advisory-score")).toBeNull();
    stub.restore();
  });

  it("ready 渲染纯文本 AI 评价且隐藏使用说明/出处/参考分", async () => {
    const { stub } = await open("teacher");

    expect(await screen.findByTestId("commentary-text")).toHaveTextContent(
      "本题主要失分在第二个用例",
    );
    expect(screen.queryByTestId("commentary-disclaimers")).toBeNull();
    expect(screen.queryByTestId("commentary-provenance")).toBeNull();
    expect(screen.queryByTestId("advisory-score")).toBeNull();
    stub.restore();
  });

  it("ready 对【综合评价】【亮点与优点】等维度分块与列表化呈现", async () => {
    const structured =
      "【综合评价】整体实现简洁准确。【亮点与优点】- 使用 range(1,101) 遍历 - 代码仅两行无冗余【改进建议】- 可补充主函数入口守卫";
    const { stub } = await open("student", {
      get: () => jsonResponse(evaluationCommentary({ text: structured })),
    });

    const textBlock = await screen.findByTestId("commentary-text");
    expect(textBlock).toHaveTextContent("【综合评价】");
    expect(textBlock).toHaveTextContent("【亮点与优点】");
    expect(textBlock).toHaveTextContent("【改进建议】");
    const listItems = within(textBlock).getAllByRole("listitem");
    expect(listItems).toHaveLength(3);
    expect(listItems[0]).toHaveTextContent("使用 range(1,101) 遍历");
    expect(listItems[1]).toHaveTextContent("代码仅两行无冗余");
    expect(listItems[2]).toHaveTextContent("可补充主函数入口守卫");
    stub.restore();
  });

  it("failed 状态给出清晰提示且不渲染正文", async () => {
    const { stub } = await open("teacher", {
      get: () =>
        jsonResponse(
          evaluationCommentary({ status: "failed", text: null, advisory_score: null }),
        ),
    });
    expect(await screen.findByTestId("commentary-failed")).toHaveTextContent("AI 评价生成未成功");
    expect(screen.queryByTestId("commentary-text")).toBeNull();
    stub.restore();
  });

  it("pending 状态给出正在生成提示且不渲染正文", async () => {
    const { stub } = await open("teacher", {
      get: () =>
        jsonResponse(
          evaluationCommentary({ status: "pending", text: null, advisory_score: null }),
        ),
    });
    expect(await screen.findByTestId("commentary-pending")).toHaveTextContent("正在生成");
    expect(screen.queryByTestId("commentary-text")).toBeNull();
    stub.restore();
  });

  it("403 无权限时提示无权查看该评价", async () => {
    const { stub } = await open("student", {
      get: () =>
        problemResponse({
          status: 403,
          title: "Forbidden",
          detail: "Commentary visibility is disabled for students in this course.",
        }),
    });
    expect(await screen.findByTestId("commentary-forbidden")).toHaveTextContent("无权查看该评价");
    expect(screen.queryByTestId("commentary-error")).toBeNull();
    stub.restore();
  });

  it("500 服务异常时展示通用错误态", async () => {
    const { stub } = await open("teacher", {
      get: () =>
        problemResponse({ status: 500, title: "Server Error", detail: "commentary backend down" }),
    });
    expect(await screen.findByTestId("commentary-error")).toBeInTheDocument();
    stub.restore();
  });

  it("404 尚未生成时提示暂未生成 AI 评价", async () => {
    const { stub } = await open("teacher", {
      get: () =>
        problemResponse({
          status: 404,
          title: "Not Found",
          detail: "No commentary has been generated for this evaluation run yet.",
        }),
    });
    expect(await screen.findByTestId("commentary-not-generated")).toHaveTextContent("暂未生成 AI 评价");
    expect(screen.queryByTestId("commentary-error")).toBeNull();
    stub.restore();
  });

  it("成绩轨道的计算与导出模块不引用 advisory", () => {
    const modules = import.meta.glob("../grades/*.ts", {
      query: "?raw",
      import: "default",
      eager: true,
    }) as Record<string, string>;
    const wanted = ["aggregate.ts", "cohort.ts", "useGrades.ts"];
    const found = Object.entries(modules).filter(([key]) =>
      wanted.some((name) => key.endsWith(name)),
    );
    expect(found.map(([key]) => key).sort()).toHaveLength(wanted.length);
    for (const [key, source] of found) {
      expect(source, `${key} 不得引用 advisory`).not.toMatch(/advisory/i);
    }
  });
});

describe("AI 评价重新生成（教师/助教）", () => {
  it("教师可以请求重新生成并复用服务端响应", async () => {
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
    const gets = stub.requests.filter(
      (request) => request.method === "GET" && request.url.pathname === commentaryPath(UUID_RUN),
    );
    expect(gets).toHaveLength(1);
    stub.restore();
  });

  it("未配置模型（409）显示未启用而非通用错误", async () => {
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
    expect(banner).toHaveTextContent("暂未启用 AI 评价生成");
    expect(screen.queryByTestId("commentary-generate-error")).toBeNull();
    stub.restore();
  });

  it("学生看不到重新生成按钮", async () => {
    const { stub } = await open("student");

    expect(await screen.findByTestId("commentary-text")).toBeInTheDocument();
    expect(screen.queryByTestId("regenerate-commentary")).toBeNull();
    stub.restore();
  });
});
