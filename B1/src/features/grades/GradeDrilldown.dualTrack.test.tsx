// 评分详情里的双轨分解：最终分之外必须能看见"用例轨 / AI 轨"两半与权重。


import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";

import type { EvaluationReport } from "../../api/types";
import { evaluationReport, sessionCreated } from "../../testing/contractFixtures";
import { installFetchStub } from "../../testing/httpBoundary";
import { jsonResponse } from "../../testing/httpBoundary";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { GradeDrilldown } from "./GradeDrilldown";

const UUID_RUN = "00000000-0000-4000-8000-000000000004";

function routes(report: EvaluationReport) {
  return [
    {
      method: "POST" as const,
      path: "/api/v1/auth/session",
      respond: () => jsonResponse(sessionCreated({ roles: ["teacher"], access_token: "t" })),
    },
    {
      method: "GET" as const,
      path: `/api/v1/evaluation-runs/${UUID_RUN}/report`,
      respond: () => jsonResponse(report),
    },
    {
      method: "GET" as const,
      path: `/api/v1/evaluation-runs/${UUID_RUN}/commentary`,
      respond: () => jsonResponse({ commentary: null, provenance: null, disclaimers: [] }),
    },
  ];
}

function withDualTrack(report: EvaluationReport, aiAvailable: boolean): EvaluationReport {
  return {
    ...report,
    overall: {
      ...report.overall,
      score: aiAvailable ? 74.8 : 70,
      max_score: 100,
      is_pass: true,
      dual_track: {
        case_score: 7,
        case_max: 10,
        case_ratio: 70,
        case_percent: 60,
        ai_score: aiAvailable ? 82 : null,
        ai_percent: 40,
        ai_available: aiAvailable,
      },
    },
  };
}

describe("双轨分解", () => {
  it("展示用例轨与 AI 轨各自的分数与权重", async () => {
    const stub = installFetchStub(routes(withDualTrack(evaluationReport(), true)));
    renderWithProviders(<GradeDrilldown runId={UUID_RUN} view="teacher" />);
    await screen.findByTestId("grade-drilldown");

    const breakdown = await screen.findByTestId("dual-track-breakdown");
    expect(breakdown).toHaveTextContent("用例轨：7 / 10（70%）");
    expect(breakdown).toHaveTextContent("占最终分 60%");
    expect(breakdown).toHaveTextContent("AI 轨：82 / 100，占最终分 40%");
    stub.restore();
  });

  it("AI 未参与时明说权重并入用例轨，而不是显示 0 分", async () => {
    const stub = installFetchStub(routes(withDualTrack(evaluationReport(), false)));
    renderWithProviders(<GradeDrilldown runId={UUID_RUN} view="teacher" />);
    await screen.findByTestId("grade-drilldown");

    const breakdown = await screen.findByTestId("dual-track-breakdown");
    expect(breakdown).toHaveTextContent("AI 轨未参与本次评测");
    expect(breakdown).toHaveTextContent("权重并入用例轨");
    expect(breakdown).not.toHaveTextContent("AI 轨：0 / 100");
    stub.restore();
  });

  it("没有双轨的旧报告不显示该区块", async () => {
    const stub = installFetchStub(routes(evaluationReport()));
    renderWithProviders(<GradeDrilldown runId={UUID_RUN} view="teacher" />);
    await screen.findByTestId("grade-drilldown");

    expect(screen.queryByTestId("dual-track-breakdown")).toBeNull();
    stub.restore();
  });
});
