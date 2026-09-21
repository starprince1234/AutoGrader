import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { installFetchStub, jsonResponse, problemResponse, type StubbedRoute } from "../../testing/httpBoundary";
import {
  UUID_RUN,
  evaluationCommentary,
  evaluationReport,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { GradeDrilldown } from "./GradeDrilldown";
import type { EvaluationCommentary, EvaluationReport } from "../../api/types";

/**
 * 下钻视图里的"两轨并列"（RQ-T-20 / RQ-T-30，`ADR-0014` 决策 3）。
 *
 * 这一组断言是验收项本身，而不是排版偏好：
 * - 权威分与参考分必须在**同一区域**同时可读（藏起来不算）；
 * - 权威分**先出现**，且外观标记与参考分不同（参考分不得显得更权威）；
 * - 参考分与权威分的数值不同（夹具刻意如此），页面不得把它们合并成同一个数；
 * - 评语 `failed` 时权威分照常渲染。
 */

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
} = {}) {
  const stub = installFetchStub(drilldownRoutes(options));
  await signIn("teacher1", "pw");
  renderWithProviders(<GradeDrilldown runId={UUID_RUN} view="teacher" />);
  await screen.findByTestId("grade-drilldown");
  return { stub };
}

describe("证据下钻：权威分与 AI 参考分并列（RQ-T-30 / ADR-0014）", () => {
  it("权威分先出现，参考分紧随其后且视觉标记不同（不得显得更权威）", async () => {
    const { stub } = await open();

    const region = await screen.findByTestId("score-region");
    const authoritative = screen.getByTestId("authoritative-score");
    const advisory = screen.getByTestId("advisory-score");
    // 并列：两者都在同一个区域内（同一个分数区），不是分页/折叠到别处。
    expect(region).toContainElement(authoritative);
    expect(region).toContainElement(advisory);

    // 权威分在前：`DOCUMENT_POSITION_FOLLOWING` 表示 advisory 出现在 authoritative 之后。
    expect(
      authoritative.compareDocumentPosition(advisory) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // 视觉标记必须不同：权威分用 `score-authoritative`，参考分不得复用它的类名，
    // 也不得使用"通过"的绿色 pill（那正是"看起来像成绩"的写法）。
    expect(authoritative.className).toContain("score-authoritative");
    expect(advisory.className).toContain("advisory-score");
    expect(advisory.className).not.toContain("score-authoritative");
    expect(advisory.querySelector(".pill--ok")).toBeNull();
    expect(advisory.querySelector(".score-authoritative")).toBeNull();

    // 两个数值不同且各自独立呈现：权威分 86，参考分 62——不得合并、不得互相覆盖。
    expect(authoritative).toHaveTextContent("86 / 100");
    expect(advisory).toHaveTextContent("62 / 100");
    expect(advisory).not.toHaveTextContent("86");
    stub.restore();
  });

  it("参考分为 null 时分数区只剩权威分，不出现空壳或 0 分参考", async () => {
    const { stub } = await open({
      commentary: evaluationCommentary({ advisory_score: null }),
    });

    expect(await screen.findByTestId("authoritative-score")).toHaveTextContent("86 / 100");
    expect(screen.queryByTestId("advisory-score")).toBeNull();
    // 不能有任何参考分占位（既不能是 0 分，也不能是空壳元素）。
    expect(screen.queryByTestId("advisory-score-value")).toBeNull();
    expect(screen.getByTestId("score-region")).toHaveTextContent("86 / 100");
    stub.restore();
  });

  it("评语生成失败时权威分与逐用例证据照常渲染（两轨互不影响）", async () => {
    const { stub } = await open({
      commentary: evaluationCommentary({ status: "failed", text: null, advisory_score: null }),
    });

    expect(await screen.findByTestId("commentary-failed")).toBeInTheDocument();
    expect(screen.getByTestId("authoritative-score")).toHaveTextContent("86 / 100");
    expect(screen.getByTestId("case-evidence")).toBeInTheDocument();
    // 失败不得把参考分"补"出来。
    expect(screen.queryByTestId("advisory-score")).toBeNull();
    stub.restore();
  });

  it("证据包入口在同一视图内可到达，但默认不发请求（按需加载）", async () => {
    const { stub } = await open();

    expect(await screen.findByTestId("appeal-pack")).toBeInTheDocument();
    expect(screen.getByTestId("load-appeal-pack")).toBeInTheDocument();
    const appealCalls = stub.requests.filter((request) =>
      request.url.pathname.endsWith("/appeal-pack"),
    );
    expect(appealCalls).toHaveLength(0);
    stub.restore();
  });

  it("报告不可得时（failed 运行没有报告）评语轨仍然可达，不被报告的错误吞掉", async () => {
    // 本部署实测：`GET .../report` 对 failed 运行返回 404（"A report exists only after a
    // successful run."），而同一个运行的评语可以正常生成与读取。两轨分开就必须体现在这里。
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

    // 报告的错误照常展示……
    expect(await screen.findByTestId("error-state")).toHaveTextContent("404");
    expect(await screen.findByTestId("commentary-text")).toHaveTextContent("第二个用例");
    // ……评语与参考分同时可用（错误框不得成为它们的唯一出口）。
    expect(screen.getByTestId("advisory-score")).toBeInTheDocument();
    // 证据包内嵌报告，因此在这个状态下不提供加载入口。
    expect(screen.queryByTestId("appeal-pack")).toBeNull();
    stub.restore();
  });
});
