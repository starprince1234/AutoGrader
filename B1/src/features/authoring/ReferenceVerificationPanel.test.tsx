import { afterEach, describe, expect, it } from "vitest";
import { useState } from "react";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type FetchStub,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  SHA_A,
  UUID_A,
  questionVersionPublic,
  referenceVerification,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { ReferenceVerificationPanel } from "./ReferenceVerificationPanel";
import type { ReferenceVerification } from "../../api/types";

/**
 * 「检验并发布」面板。
 *
 * 断言的是**行为与结论的三态**，不锁死文案细节：全通过 ≠ 可发布、环境不可用 ≠ 题目不合格、
 * 未通过只给逐用例结论。界面文案是给教师看的白话，因此这里**不再**断言任何内部标识
 * （摘要、存证编号、状态码、服务端 detail 原文）——那些既不该出现在页面上，也就不该被测试固化。
 *
 * 仍然锁死的三条行为：一次点击只发一次检验、发布是独立的显式动作、结论不跨版本。
 */

const VERIFY_PATH = "/api/v1/reference-verifications";
const VERSION_ID = questionVersionPublic().question_version_id;

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function routes(options: {
  verify: () => Response;
  publish?: () => Response;
  version?: ReturnType<typeof questionVersionPublic>;
}): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
    },
    { method: "POST", path: VERIFY_PATH, respond: options.verify },
    {
      method: "POST",
      path: `/api/v1/question-versions/${VERSION_ID}/publish`,
      respond: options.publish ?? (() => jsonResponse(options.version ?? questionVersionPublic())),
    },
  ];
}

function verificationRequests(active: FetchStub) {
  return active.requests.filter((request) => request.url.pathname === VERIFY_PATH);
}

function publishRequests(active: FetchStub) {
  return active.requests.filter((request) => request.url.pathname.endsWith("/publish"));
}

/** 未发布版本才是这道门的对象：已发布的版本面板整块不渲染。 */
function draftVersion(overrides: Parameters<typeof questionVersionPublic>[0] = {}) {
  return questionVersionPublic({ status: "draft", content_digest: SHA_A, ...overrides });
}

async function open(options: Parameters<typeof routes>[0], version = draftVersion()) {
  const active = installFetchStub(routes(options));
  await signIn("teacher1", "pw");
  renderWithProviders(<ReferenceVerificationPanel version={version} questionId={UUID_A} />);
  return active;
}

async function verify(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByTestId("reference-verify"));
}

describe("检验的发起", () => {
  it("未检验前不下结论，也不发请求；请求体只有版本标识", async () => {
    const active = await open({
      verify: () => jsonResponse(referenceVerification()),
    });

    expect(screen.getByTestId("reference-verification")).toBeInTheDocument();
    expect(screen.queryByTestId("reference-verification-result")).toBeNull();
    // 发布条件在"还没检验"时也要说清楚。
    const gate = screen.getByTestId("reference-gate");
    expect(gate).toHaveTextContent("所有用例通过");
    expect(gate).toHaveTextContent("检验有记录");
    expect(verificationRequests(active)).toHaveLength(0);

    await verify(userEvent.setup());

    const request = verificationRequests(active)[0];
    expect(request.body).toEqual({ question_version_id: VERSION_ID });
    active.restore();
  });

  it("点一次只检验一次，并说明不会自动重复", async () => {
    const active = await open({
      verify: () => jsonResponse(referenceVerification()),
    });
    await verify(userEvent.setup());

    await screen.findByTestId("reference-verification-result");
    expect(verificationRequests(active)).toHaveLength(1);
    expect(screen.getByTestId("reference-no-auto")).toHaveTextContent("自己点一次");
    active.restore();
  });

  it("没有权限时如实说明，不把它说成「还没检验过」", async () => {
    const active = await open({
      verify: () =>
        problemResponse({
          status: 403,
          title: "Forbidden",
          detail: "Course write scope does not cover this question.",
        }),
    });
    await verify(userEvent.setup());

    const banner = await screen.findByTestId("reference-verify-forbidden");
    expect(banner).toHaveTextContent("没有这道题的编辑权限");
    // 服务端英文 detail 不出现在教师界面上。
    expect(banner.textContent ?? "").not.toContain("Course write scope");
    expect(screen.queryByTestId("reference-verification-result")).toBeNull();
    active.restore();
  });

  it("找不到版本时说清楚，不当成检验失败、也不猜原因", async () => {
    const active = await open({
      verify: () => problemResponse({ status: 404, title: "Not Found", detail: "Not Found" }),
    });
    await verify(userEvent.setup());

    const banner = await screen.findByTestId("reference-verify-notfound");
    expect(banner).toHaveTextContent("找不到这道题的版本");
    expect(banner.textContent ?? "").not.toContain("检验失败");
    expect(screen.queryByTestId("reference-verification-result")).toBeNull();
    active.restore();
  });
});

describe("结论的三种状态", () => {
  it("全部通过 + 有记录 → 可以发布，且发布要人再点一次", async () => {
    const active = await open({
      verify: () => jsonResponse(referenceVerification()),
    });
    const user = userEvent.setup();
    await verify(user);

    const summary = await screen.findByTestId("reference-verification-summary");
    expect(within(summary).getByTestId("reference-summary-counts")).toHaveTextContent("2 / 2");
    expect(screen.getByTestId("reference-attestation")).toHaveTextContent("已记录");
    expect(publishRequests(active)).toHaveLength(0);
    expect(screen.getByTestId("reference-publish")).toBeInTheDocument();

    await user.click(screen.getByTestId("reference-publish"));

    const published = await screen.findByTestId("reference-publish-done");
    expect(published).toHaveTextContent("已发布");
    expect(publishRequests(active)).toHaveLength(1);
    active.restore();
  });

  it("全部通过但结果没有被记录 → 不能发布，并说明原因", async () => {
    const active = await open({
      verify: () => jsonResponse(referenceVerification({ attestation_id: null })),
    });
    await verify(userEvent.setup());

    const banner = await screen.findByTestId("reference-verify-unrecorded");
    expect(banner).toHaveTextContent("没有被记录");
    expect(banner).toHaveTextContent("重新检验");
    expect(screen.getByTestId("reference-attestation")).toHaveTextContent("没有被记录");
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    expect(screen.getByTestId("reference-publish-blocked")).toBeInTheDocument();
    active.restore();
  });

  it("有用例没过 → 逐个用例给出结论，不给期望输出", async () => {
    const failed: ReferenceVerification = referenceVerification({
      cases_passed: 1,
      cases_total: 2,
      attestation_id: null,
      cases: [
        { ordinal: 0, passed: true, failure_category: null },
        { ordinal: 1, passed: false, failure_category: "output_mismatch" },
      ],
    });
    const active = await open({ verify: () => jsonResponse(failed) });
    await verify(userEvent.setup());

    const banner = await screen.findByTestId("reference-verify-not-passed");
    expect(banner).toHaveTextContent("有 1 个用例没通过");
    expect(banner).toHaveTextContent("参考答案还不能让所有用例通过");

    const table = screen.getByTestId("reference-verification-cases");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(rows[1]).getByTestId("reference-case-outcome-0")).toHaveTextContent("通过");
    expect(within(rows[2]).getByTestId("reference-case-outcome-1")).toHaveTextContent("未通过");
    expect(screen.getByTestId("reference-case-category-1")).toHaveTextContent("输出不一致");
    expect(screen.queryByTestId("reference-case-category-0")).toBeNull();
    expect(screen.getByTestId("reference-cases-meaning")).toHaveTextContent("不显示期望输出");
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    active.restore();
  });

  it("用例全部没跑起来 → 环境结论：不说题目不合格，也不说通过", async () => {
    const infra: ReferenceVerification = referenceVerification({
      cases_passed: 0,
      cases_total: 2,
      attestation_id: null,
      cases: [
        { ordinal: 0, passed: false, failure_category: "infrastructure_error" },
        { ordinal: 1, passed: false, failure_category: "infrastructure_error" },
      ],
    });
    const active = await open({ verify: () => jsonResponse(infra) });
    await verify(userEvent.setup());

    const banner = await screen.findByTestId("reference-verify-environment");
    expect(banner).toHaveTextContent("运行环境暂时不可用");
    expect(banner.textContent ?? "").not.toContain("成功");
    expect(banner.textContent ?? "").not.toContain("验证失败");
    expect(screen.queryByTestId("reference-verify-not-passed")).toBeNull();
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    expect(screen.getByTestId("reference-case-category-0")).toHaveTextContent("运行环境不可用");
    active.restore();
  });

  it("服务不可用时同样不下结论，也不把内部错误原文贴出来", async () => {
    const active = await open({
      verify: () =>
        problemResponse({
          status: 503,
          title: "Service Unavailable",
          detail: "B3 request failed: TimeoutError",
        }),
    });
    await verify(userEvent.setup());

    const banner = await screen.findByTestId("reference-verify-environment");
    expect(banner).toHaveTextContent("运行环境暂时不可用");
    expect(banner.textContent ?? "").not.toContain("TimeoutError");
    expect(banner.textContent ?? "").not.toContain("成功");
    expect(screen.queryByTestId("reference-verification-result")).toBeNull();
    active.restore();
  });

  it("部分失败（含环境错误）→ 按未通过呈现，逐用例给类别", async () => {
    const mixed: ReferenceVerification = referenceVerification({
      cases_passed: 0,
      cases_total: 2,
      attestation_id: null,
      cases: [
        { ordinal: 0, passed: false, failure_category: "infrastructure_error" },
        { ordinal: 1, passed: false, failure_category: "output_mismatch" },
      ],
    });
    const active = await open({ verify: () => jsonResponse(mixed) });
    await verify(userEvent.setup());

    expect(await screen.findByTestId("reference-verify-not-passed")).toBeInTheDocument();
    expect(screen.queryByTestId("reference-verify-environment")).toBeNull();
    expect(screen.getByTestId("reference-case-category-0")).toHaveTextContent("运行环境不可用");
    expect(screen.getByTestId("reference-case-category-1")).toHaveTextContent("输出不一致");
    active.restore();
  });
});

describe("结论的时效性", () => {
  it("检验之后题目又改过 → 需要重新检验，不能发布", async () => {
    const active = await open(
      {
        verify: () => jsonResponse(referenceVerification({ content_digest: "b".repeat(64) })),
      },
      draftVersion({ content_digest: SHA_A }),
    );
    await verify(userEvent.setup());

    const banner = await screen.findByTestId("reference-verify-stale");
    expect(banner).toHaveTextContent("又改过");
    expect(banner).toHaveTextContent("重新检验");
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    active.restore();
  });

  it("题目还不能检验时说明缺什么，而不是放行", async () => {
    const withoutDigest = draftVersion();
    delete (withoutDigest as { content_digest?: string }).content_digest;
    const active = await open({ verify: () => jsonResponse(referenceVerification()) }, withoutDigest);
    await verify(userEvent.setup());

    expect(await screen.findByTestId("reference-verify-digest-unknown")).toHaveTextContent(
      "还不能检验",
    );
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    active.restore();
  });

  it("发布被拒绝时如实说明，不伪装成功", async () => {
    const active = await open({
      verify: () => jsonResponse(referenceVerification()),
      publish: () =>
        problemResponse({
          status: 422,
          title: "Unprocessable Entity",
          detail:
            "no reference-answer attestation matches the version's current content digest",
        }),
    });
    const user = userEvent.setup();
    await verify(user);
    await user.click(await screen.findByTestId("reference-publish"));

    const error = await screen.findByTestId("reference-publish-error");
    expect(error).toHaveTextContent("发布没有成功");
    expect(error.textContent ?? "").not.toContain("attestation");
    expect(screen.queryByTestId("reference-publish-done")).toBeNull();
    active.restore();
  });
});

describe("结论不跨版本（结构性质，不靠清理代码）", () => {
  it("同一个实例换了版本 → 上一次的结论不再出现", async () => {
    const otherVersion = draftVersion({
      question_version_id: "00000000-0000-4000-8000-0000000000d7",
    });
    function Harness() {
      const [version, setVersion] = useState(draftVersion());
      return (
        <>
          <button type="button" data-testid="switch-version" onClick={() => setVersion(otherVersion)}>
            switch
          </button>
          <ReferenceVerificationPanel version={version} questionId={UUID_A} />
        </>
      );
    }

    const active = installFetchStub(
      routes({ verify: () => jsonResponse(referenceVerification()) }),
    );
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness />);

    const user = userEvent.setup();
    await user.click(screen.getByTestId("reference-verify"));
    await screen.findByTestId("reference-verification-result");

    await user.click(screen.getByTestId("switch-version"));

    expect(screen.queryByTestId("reference-verification-result")).toBeNull();
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    active.restore();
  });

  it("已发布的版本不再提供任何动作", async () => {
    const active = installFetchStub(
      routes({ verify: () => jsonResponse(referenceVerification()) }),
    );
    await signIn("teacher1", "pw");
    renderWithProviders(
      <ReferenceVerificationPanel
        version={questionVersionPublic({ status: "published", content_digest: SHA_A })}
        questionId={UUID_A}
      />,
    );

    expect(screen.queryByTestId("reference-verify")).toBeNull();
    expect(screen.queryByTestId("reference-verification")).toBeNull();
    active.restore();
  });
});
