import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
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
  evaluationReport,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { GradeDrilldown } from "./GradeDrilldown";
import type { EvaluationCommentary, EvaluationReport } from "../../api/types";

function drilldownRoutes(options: {
  report?: EvaluationReport;
  commentary?: EvaluationCommentary;
} = {}): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () => jsonResponse(sessionCreated({ roles: ["teacher"], access_token: "t" })),
    },
    {
      method: "GET",
      path: `/api/v1/evaluation-runs/${UUID_RUN}/report`,
      respond: () => jsonResponse(options.report ?? evaluationReport()),
    },
    {
      method: "GET",
      path: `/api/v1/evaluation-runs/${UUID_RUN}/commentary`,
      respond: () => jsonResponse(options.commentary ?? evaluationCommentary()),
    },
  ];
}

async function open(options: {
  report?: EvaluationReport;
  commentary?: EvaluationCommentary;
  view?: "student" | "teacher";
} = {}) {
  const stub = installFetchStub(drilldownRoutes(options));
  await signIn(options.view === "student" ? "student1" : "teacher1", "pw");
  renderWithProviders(<GradeDrilldown runId={UUID_RUN} view={options.view ?? "teacher"} />);
  await screen.findByTestId("grade-drilldown");
  return { stub };
}

describe("评测详情：仅保留 AI 评分与 AI 评价", () => {
  it("展示 AI 评分结果与 AI 评价，彻底移除测试用例、静态检查、安全结论、技术详情、证据包与学习提示", async () => {
    const { stub } = await open();

    const authoritative = await screen.findByTestId("authoritative-score");
    expect(authoritative).toHaveTextContent("AI 评分：86 / 100 · 通过");
    expect(await screen.findByTestId("commentary-text")).toHaveTextContent(
      "本题主要失分在第二个用例",
    );

    expect(screen.queryByTestId("case-evidence")).toBeNull();
    expect(screen.queryByTestId("static-findings")).toBeNull();
    expect(screen.queryByTestId("security-findings")).toBeNull();
    expect(screen.queryByTestId("binding-details")).toBeNull();
    expect(screen.queryByTestId("appeal-pack")).toBeNull();
    expect(screen.queryByTestId("learning-hints")).toBeNull();
    expect(screen.queryByTestId("advisory-score")).toBeNull();
    expect(screen.queryByTestId("overall-summary")).toBeNull();
    stub.restore();
  });

  it("学生视图同样只显示 AI 评分与 AI 评价，不含测试用例与旧提示板块", async () => {
    const { stub } = await open({ view: "student" });

    expect(await screen.findByTestId("authoritative-score")).toHaveTextContent("AI 评分：86 / 100");
    expect(await screen.findByTestId("commentary-text")).toBeInTheDocument();
    expect(screen.queryByTestId("case-evidence")).toBeNull();
    expect(screen.queryByTestId("learning-hints")).toBeNull();
    expect(screen.queryByTestId("advisory-score")).toBeNull();
    stub.restore();
  });

  it("评语生成失败时 AI 评分照常显示", async () => {
    const { stub } = await open({
      commentary: evaluationCommentary({ status: "failed", text: null, advisory_score: null }),
    });

    expect(await screen.findByTestId("commentary-failed")).toBeInTheDocument();
    expect(screen.getByTestId("authoritative-score")).toHaveTextContent("86 / 100");
    expect(screen.queryByTestId("advisory-score")).toBeNull();
    stub.restore();
  });

  it("报告不可得时（404）仍提示友好文案并展示 AI 评价", async () => {
    const stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"], access_token: "t" })),
      },
      {
        method: "GET",
        path: `/api/v1/evaluation-runs/${UUID_RUN}/report`,
        respond: () =>
          problemResponse({
            status: 404,
            title: "Not Found",
            detail: "The evaluation run exists but has no report yet (state: failed).",
          }),
      },
      {
        method: "GET",
        path: `/api/v1/evaluation-runs/${UUID_RUN}/commentary`,
        respond: () => jsonResponse(evaluationCommentary()),
      },
    ]);
    await signIn("teacher1", "pw");
    renderWithProviders(<GradeDrilldown runId={UUID_RUN} view="teacher" />);

    expect(await screen.findByTestId("report-missing")).toHaveTextContent("暂时没有可查看的详细结果");
    expect(await screen.findByTestId("commentary-text")).toHaveTextContent("第二个用例");
    expect(screen.queryByTestId("appeal-pack")).toBeNull();
    stub.restore();
  });
});
