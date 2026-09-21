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
 * 「验证并发布」面板（CHG-0019 / ADR-0015）。
 *
 * 断言的重点是**契约点名的三处不能混**：
 * - **全通过 ≠ 可发布**：`attestation_id` 为 `null` 表示 B3 没有记录存证（执行者与记录者分离），
 *   此时不能发布，而且必须说出来；
 * - **`infrastructure_error` / `503` 是环境结论**：文案必须指出"验证需要 Linux 沙箱环境"，
 *   **不含**"验证失败"，也**不含**任何"通过"字样（本机 Windows 上这是常态，不是异常）；
 * - **未通过**：逐用例呈现 `failure_category`，且不提供任何"看期望输出/参考解"的入口。
 *
 * 另外三条：不做自动重试（一次点击 = 一次沙箱作业）、不做自动发布（两个显式动作）、
 * 结论不跨版本泄漏（换版本即重新挂载）。
 *
 * **证据边界**：本文件用契约夹具驱动，证明的是"界面按契约形状正确工作"。
 * 真实宿主上的读数（对草稿返回 `422` + 原样 detail）见 `docs/phases/active/phase-05-…` 的
 * "验收后修正"一节——本文件**不能**替代它（夹具世界里这两条路径永远按写入的分支走）。
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
  return active.requests.filter((request) =>
    request.url.pathname.endsWith("/publish"),
  );
}

/** 未发布版本才是这道门的对象：已发布的版本面板整块不渲染。 */
function draftVersion(overrides: Parameters<typeof questionVersionPublic>[0] = {}) {
  return questionVersionPublic({ status: "draft", content_digest: SHA_A, ...overrides });
}

async function open(
  options: Parameters<typeof routes>[0],
  version = draftVersion(),
) {
  const active = installFetchStub(routes(options));
  await signIn("teacher1", "pw");
  renderWithProviders(
    <ReferenceVerificationPanel version={version} questionId={UUID_A} />,
  );
  return active;
}

async function verify(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByTestId("reference-verify"));
}

describe("验证的发起与证据边界（CHG-0019）", () => {
  it("未验证前不显示任何结论，也不发任何请求；请求体只有 question_version_id", async () => {
    const active = await open({
      verify: () => jsonResponse(referenceVerification()),
    });

    expect(screen.getByTestId("reference-verification")).toBeInTheDocument();
    expect(screen.queryByTestId("reference-verification-result")).toBeNull();
    // 证据边界与门禁说明在**未验证**时也要可见（它们描述的正是这个时刻）。
    expect(screen.getByTestId("reference-no-protected-content")).toHaveTextContent(
      "不含期望输出",
    );
    expect(screen.getByTestId("reference-gate")).toHaveTextContent("attestation_id");
    expect(verificationRequests(active)).toHaveLength(0);

    const user = userEvent.setup();
    await verify(user);

    const request = verificationRequests(active)[0];
    // 契约 `additionalProperties: false` + `required: [question_version_id]`：只发这一个键。
    expect(request.body).toEqual({ question_version_id: VERSION_ID });
    active.restore();
  });

  it("点击一次只发一次请求（不做自动重试：每次验证都会起一次沙箱作业）", async () => {
    const active = await open({
      verify: () => jsonResponse(referenceVerification()),
    });
    const user = userEvent.setup();
    await verify(user);

    await screen.findByTestId("reference-verification-result");
    expect(verificationRequests(active)).toHaveLength(1);
    expect(screen.getByTestId("reference-no-auto")).toHaveTextContent("不做自动重试");
    active.restore();
  });

  it("403（该题目的课程写权限）原样带出服务端 detail，不当成「还没验证过」", async () => {
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
    expect(banner).toHaveTextContent("Course write scope does not cover this question.");
    expect(banner).toHaveTextContent("不是「这道题还没验证过」");
    expect(screen.queryByTestId("reference-verification-result")).toBeNull();
    active.restore();
  });

  it("404 不猜是哪一种：可能该版本不存在，也可能本部署没有提供这条路由", async () => {
    const active = await open({
      verify: () => problemResponse({ status: 404, title: "Not Found", detail: "Not Found" }),
    });
    await verify(userEvent.setup());

    const banner = await screen.findByTestId("reference-verify-notfound");
    expect(banner).toHaveTextContent("该版本不存在");
    expect(banner).toHaveTextContent("本部署");
    expect(banner).toHaveTextContent("不猜");
    // 界面**不得**断言另一个模块的实现进度（这类文案没有过期机制）。
    expect(banner.textContent ?? "").not.toContain("尚未实现");
    // 不能把 404 说成"验证失败"，也不能渲染成成功。
    expect(banner.textContent ?? "").not.toContain("验证失败");
    expect(screen.queryByTestId("reference-verification-result")).toBeNull();
    active.restore();
  });
});

describe("三态呈现（CHG-0019 / ADR-0015）", () => {
  it("全部通过 + 存证已记录 → 可发布，且发布是一个独立的显式动作", async () => {
    const active = await open({
      verify: () => jsonResponse(referenceVerification()),
    });
    const user = userEvent.setup();
    await verify(user);

    const summary = await screen.findByTestId("reference-verification-summary");
    expect(within(summary).getByTestId("reference-summary-counts")).toHaveTextContent("2 / 2");
    expect(screen.getByTestId("reference-attestation")).toHaveTextContent("B3 记录的存证");
    // 没有自动发布：发布请求数为 0，按钮必须由人来点。
    expect(publishRequests(active)).toHaveLength(0);
    expect(screen.getByTestId("reference-publish")).toBeInTheDocument();

    await user.click(screen.getByTestId("reference-publish"));

    const published = await screen.findByTestId("reference-publish-done");
    expect(published).toHaveTextContent("已发布");
    expect(publishRequests(active)).toHaveLength(1);
    active.restore();
  });

  it("全部通过但 attestation_id 为 null（B3 没记下存证）→ 不可发布，并说明原因", async () => {
    const active = await open({
      verify: () => jsonResponse(referenceVerification({ attestation_id: null })),
    });
    await verify(userEvent.setup());

    const banner = await screen.findByTestId("reference-verify-unrecorded");
    expect(banner).toHaveTextContent("B3 没有记录存证");
    expect(banner).toHaveTextContent("还不能发布");
    expect(screen.getByTestId("reference-attestation")).toHaveTextContent("未记录");
    // 关键：全通过**不等于**可发布。
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    expect(screen.getByTestId("reference-publish-blocked")).toBeInTheDocument();
    active.restore();
  });

  it("未通过 → 逐用例呈现失败类别，并说明哪些用例没过", async () => {
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
    expect(banner).toHaveTextContent("1 个用例没有通过");
    expect(banner).toHaveTextContent("不是平台对题目质量的判断");

    const table = screen.getByTestId("reference-verification-cases");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3); // 表头 + 2 个用例
    expect(within(rows[1]).getByTestId("reference-case-outcome-0")).toHaveTextContent("通过");
    expect(within(rows[2]).getByTestId("reference-case-outcome-1")).toHaveTextContent("未通过");
    // 失败类别按契约的同一个词表渲染（复用成绩轨道的类别文案）。
    expect(screen.getByTestId("reference-case-category-1")).toHaveTextContent("输出不一致");
    // 通过的用例没有类别：契约里 `failure_category` 为 null 恰好当且仅当通过。
    expect(screen.queryByTestId("reference-case-category-0")).toBeNull();
    // 粒度到此为止：没有"看期望输出/参考解"的入口。
    expect(screen.getByTestId("reference-cases-meaning")).toHaveTextContent("没有期望输出");
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    active.restore();
  });

  it("failure_category 全是 infrastructure_error → 平台结论：既不写「验证失败」也不写成功", async () => {
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
    expect(banner).toHaveTextContent("平台依赖不可用");
    expect(banner).toHaveTextContent("既不能说这道题合格");
    // 结论**不得超出证据**：`503`/`infrastructure_error` 同时覆盖"没有沙箱"与"上游调用失败"，
    // 从状态码或失败类别无法区分，因此页面不能断言缺的是哪一类依赖。
    expect(banner).toHaveTextContent("本页**不**从状态码推断");
    expect(banner).toHaveTextContent("不是本页能断定的事");
    // 关键反向断言（只用在这条文案上）：既没有"成功"字样，也没有"验证失败"字样。
    expect(banner.textContent ?? "").not.toContain("成功");
    expect(banner.textContent ?? "").not.toContain("验证失败");
    // 不是"未通过"那条结论，也没有可点的发布动作。
    expect(screen.queryByTestId("reference-verify-not-passed")).toBeNull();
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    // 逐用例仍然是契约给的粒度（这里全是基础设施错误）。
    expect(screen.getByTestId("reference-case-category-0")).toHaveTextContent("基础设施错误");
    active.restore();
  });

  it("503 → 同一条中性结论，并把服务端 detail **原样**留在页面上（它是这条信息诚实的原因）", async () => {
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
    expect(banner).toHaveTextContent("平台依赖不可用");
    // 真实读数里服务端给的正是这一句（上游调用失败），它必须逐字可见——
    // 若把它删掉或换成静态断言，读者就失去了唯一的真实原因。
    const detail = screen.getByTestId("reference-verify-environment-detail");
    expect(detail).toHaveTextContent("服务端说明：B3 request failed: TimeoutError");
    // 标题不复述原因，原因只来自 detail。
    expect(detail.textContent ?? "").toContain("TimeoutError");
    expect(banner.textContent ?? "").not.toContain("成功");
    expect(banner.textContent ?? "").not.toContain("验证失败");
    // 没有任何逐用例结论、没有任何可点的动作。
    expect(screen.queryByTestId("reference-verification-result")).toBeNull();
    active.restore();
  });

  it("混合失败（既有内容失败也有基础设施错误）→ 呈现逐用例类别，不整体归因为环境", async () => {
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

    // 不是"全是基础设施错误"，因此按"未通过"呈现，两个类别各自可见。
    expect(await screen.findByTestId("reference-verify-not-passed")).toBeInTheDocument();
    expect(screen.queryByTestId("reference-verify-environment")).toBeNull();
    expect(screen.getByTestId("reference-case-category-0")).toHaveTextContent("基础设施错误");
    expect(screen.getByTestId("reference-case-category-1")).toHaveTextContent("输出不一致");
    active.restore();
  });
});

describe("摘要绑定与结论的时效性（CHG-0008 / CHG-0019）", () => {
  it("结论的 content_digest 与当前版本不一致 → 结论失效，不可发布", async () => {
    const active = await open(
      {
        verify: () =>
          jsonResponse(referenceVerification({ content_digest: "b".repeat(64) })),
      },
      draftVersion({ content_digest: SHA_A }),
    );
    await verify(userEvent.setup());

    const banner = await screen.findByTestId("reference-verify-stale");
    expect(banner).toHaveTextContent("被改过");
    expect(banner).toHaveTextContent("不能发布");
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    active.restore();
  });

  it("版本没有返回 content_digest 时不据此放行发布，并说明原因", async () => {
    const withoutDigest = draftVersion();
    delete (withoutDigest as { content_digest?: string }).content_digest;
    const active = await open(
      { verify: () => jsonResponse(referenceVerification()) },
      withoutDigest,
    );
    await verify(userEvent.setup());

    expect(await screen.findByTestId("reference-verify-digest-unknown")).toHaveTextContent(
      "无法断言这份结论属于它",
    );
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    active.restore();
  });

  it("发布被服务端拒绝（422）时原样展示，不伪装成功", async () => {
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
    expect(error).toHaveTextContent("no reference-answer attestation matches");
    expect(screen.queryByTestId("reference-publish-done")).toBeNull();
    active.restore();
  });
});

describe("结论不跨版本（结构性质，不靠清理代码）", () => {
  it("同一个实例换了版本 → 上一次的结论不再出现", async () => {
    const otherVersion = draftVersion({
      question_version_id: "00000000-0000-4000-8000-0000000000d7",
    });
    // 关键：Provider 只创建一次，切换版本走的是**同一个组件实例**的新 props
    // （用 `rerender` 会换掉 Provider，那样即使没有 `key` 也会"通过"）。
    function Harness() {
      const [version, setVersion] = useState(draftVersion());
      return (
        <>
          <button
            type="button"
            data-testid="switch-version"
            onClick={() => setVersion(otherVersion)}
          >
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

    // 结论只属于它验证的那个版本：换了版本就不该还在屏幕上。
    expect(screen.queryByTestId("reference-verification-result")).toBeNull();
    expect(screen.queryByTestId("reference-publish")).toBeNull();
    active.restore();
  });

  it("已发布的版本不再提供任何动作（门已经过了）", async () => {
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
