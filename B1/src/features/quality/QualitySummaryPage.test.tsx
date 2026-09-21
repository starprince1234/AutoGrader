import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_C,
  evaluationQualitySummary,
} from "../../testing/contractFixtures";
import { QualitySummaryPage } from "./QualitySummaryPage";
import { SMALL_SAMPLE_THRESHOLD, toContractTimestamp } from "./useQualitySummary";
import type { EvaluationQualitySummary } from "../../api/types";

/**
 * 题目质量热力图（CHG-0015）。
 *
 * 断言的重点不是"数字显示出来了"，而是契约点名"很容易被不诚实地渲染"的两处：
 * - `security_not_checked` 与 `security_not_found` **分开**（含反向断言：不能出现合并值）；
 * - `unmeasured_runs` **单列**（"没有资源数据"不能被读成"没有占用"）。
 * 另外：`question_version_id` 缺失时不发请求、UUID 不合法时不发请求、样本太小时先提示。
 */

const QUALITY_PATH = "/api/v1/evaluation-runs/quality-summary";

function qualityRoutes(options: {
  summary?: EvaluationQualitySummary;
  respond?: () => Response;
}): StubbedRoute[] {
  return [
    {
      method: "GET",
      path: QUALITY_PATH,
      respond: () =>
        options.respond?.() ?? jsonResponse(options.summary ?? evaluationQualitySummary()),
    },
  ];
}

/** 页面是受控组件（筛选条件住在 URL 里），因此用一层壳提供 `onApply` 的真实行为。 */
function renderPage(args: {
  questionVersionId?: string | null;
  from?: string | null;
  to?: string | null;
  onApply?: (next: {
    questionVersionId: string | null;
    from: string | null;
    to: string | null;
  }) => void;
}) {
  return renderWithProviders(
    <QualitySummaryPage
      questionVersionId={args.questionVersionId ?? null}
      from={args.from ?? null}
      to={args.to ?? null}
      onApply={args.onApply ?? (() => {})}
    />,
  );
}

describe("题目质量聚合的读取条件（CHG-0015）", () => {
  it("没有 question_version_id 时不发任何请求，并说明原因", async () => {
    const stub = installFetchStub(qualityRoutes({}));
    renderPage({});

    expect(await screen.findByTestId("quality-no-request")).toHaveTextContent("不发任何请求");
    expect(stub.requests).toHaveLength(0);
    stub.restore();
  });

  it("UUID 不合法时不发请求（避免注定 422 的调用），并指出契约类型", async () => {
    const stub = installFetchStub(qualityRoutes({}));
    const onApply = vi.fn();
    const user = userEvent.setup();
    renderPage({ onApply });

    await user.type(screen.getByTestId("quality-version-input"), "不是-uuid");
    await user.click(screen.getByTestId("quality-apply"));

    expect(await screen.findByText(/必须是 UUID/)).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
    expect(stub.requests).toHaveLength(0);
    stub.restore();
  });

  it("应用筛选时把本地时间转成契约的 UTC `Timestamp`", async () => {
    const stub = installFetchStub(qualityRoutes({}));
    const onApply = vi.fn();
    const user = userEvent.setup();
    renderPage({ onApply });

    await user.type(screen.getByTestId("quality-version-input"), UUID_C);
    // `datetime-local` 的值用 change 直接设：它只接受本地无时区形式，逐字符敲没有意义。
    fireEvent.change(screen.getByTestId("quality-from-input"), {
      target: { value: "2026-09-15T08:00" },
    });
    await user.click(screen.getByTestId("quality-apply"));

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    const next = onApply.mock.calls[0][0];
    expect(next.questionVersionId).toBe(UUID_C);
    // `datetime-local` 是本地时间；契约要 UTC RFC 3339（毫秒精度）。
    expect(next.from).toBe(new Date("2026-09-15T08:00").toISOString());
    expect(next.to).toBeNull();
    expect(toContractTimestamp("")).toBeNull();
    stub.restore();
  });

  it("深链直接带 version 时立即查询（并把筛选值放进 URL 查询参数）", async () => {
    const stub = installFetchStub(qualityRoutes({}));
    renderPage({ questionVersionId: UUID_C, from: "2026-09-01T00:00:00.000Z", to: null });

    await screen.findByTestId("quality-summary");
    const request = stub.requests.find((item) => item.url.pathname === QUALITY_PATH);
    expect(request?.url.searchParams.get("question_version_id")).toBe(UUID_C);
    expect(request?.url.searchParams.get("from")).toBe("2026-09-01T00:00:00.000Z");
    // 空的可选参数不发（契约里它是可选时间范围）。
    expect(request?.url.searchParams.has("to")).toBe(false);
    stub.restore();
  });

  it("服务端拒绝（403）时原样展示失败", async () => {
    const stub = installFetchStub(
      qualityRoutes({
        respond: () =>
          problemResponse({ status: 403, title: "Forbidden", detail: "Not your course." }),
      }),
    );
    renderPage({ questionVersionId: UUID_C });

    expect(await screen.findByTestId("quality-error")).toBeInTheDocument();
    // 反向断言：界面上**不得**出现关于"对方实现到哪一步"的常驻断言。
    // 这类文案没有过期机制——端点一上线，它就会与旁边的实时数据直接矛盾。
    expect(screen.queryByTestId("declared-contract-quality")).toBeNull();
    stub.restore();
  });
});

describe("安全检查三态与未测量运行必须分开（CHG-0015 点名处）", () => {
  it("七条诚实性标注各就各位：样本在上、辅助标注明确、三态标签直写、空数据不给结论", async () => {
    // 这七条是坐标复核时逐条读到的性质，用断言钉住，避免改文案时丢失其中任何一条。
    const stub = installFetchStub(qualityRoutes({}));
    renderPage({ questionVersionId: UUID_C });

    await screen.findByTestId("quality-summary");

    // ① 去标识化写明是契约强制，并因此不提供下钻入口。
    expect(screen.getByTestId("quality-counts-only")).toHaveTextContent("去标识化由契约强制");
    expect(screen.getByTestId("quality-counts-only")).toHaveTextContent("没有源码");
    // ② 样本量在每一项计数**上方**（DOM 顺序），并转述 attempt_count 的定义。
    const attempts = screen.getByTestId("quality-attempt-count");
    const security = screen.getByTestId("quality-security");
    const caseRates = screen.getByTestId("quality-case-rates");
    expect(
      attempts.compareDocumentPosition(security) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      attempts.compareDocumentPosition(caseRates) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(attempts).toHaveTextContent("本时间范围内的评测运行数");
    // ③ `unmeasured_runs` 单独成列并写明理由（不是"没有占用"）。
    expect(screen.getByTestId("quality-unmeasured-runs")).toHaveTextContent("未测量");
    expect(screen.getByTestId("quality-unmeasured-separate")).toHaveTextContent("不并入");
    // ④ 三态安全各自成列，第三项直写"从未检查（不等于安全）"。
    expect(screen.getByTestId("quality-security-not-checked")).toHaveTextContent(
      "从未检查（不等于安全）",
    );
    expect(screen.getByTestId("quality-security-not-found")).toHaveTextContent("检查过、未发现");
    // ⑤ 系统原因从难度里单列。
    expect(screen.getByTestId("quality-categories-system")).toHaveTextContent("不是难度");
    // ⑥ 阅读辅助之一：逐用例色带写明只是阅读辅助（另一条"样本太少"的门槛见下面 TC-B1-086 的用例）。
    expect(screen.getByTestId("quality-case-rates")).toHaveTextContent("色带只是本页的阅读辅助");
    stub.restore();
  });

  it("security_found / not_found / not_checked 各自成块，且页面上不存在合并值", async () => {
    const stub = installFetchStub(qualityRoutes({}));
    renderPage({ questionVersionId: UUID_C });

    await screen.findByTestId("quality-summary");
    // 夹具：found=1 / not_found=27 / not_checked=14。三个数必须各自可见。
    expect(screen.getByTestId("quality-security-found-value")).toHaveTextContent("1");
    expect(screen.getByTestId("quality-security-not-found-value")).toHaveTextContent("27");
    expect(screen.getByTestId("quality-security-not-checked-value")).toHaveTextContent("14");
    expect(screen.getByTestId("quality-security-separate-note")).toHaveTextContent(
      "「我们没有检查」不是「我们检查了、没发现」",
    );

    // 反向断言：27+14=41 这个"合并后的安全检查数"在页面上**不存在**。
    // （把 not_checked 并进 not_found，会让"没检查过"看起来像"检查过没问题"。）
    expect(screen.queryByText("41")).toBeNull();
    // 每个数只出现在它自己那一块里。
    const security = screen.getByTestId("quality-security");
    expect(within(security).getAllByText("14")).toHaveLength(1);
    expect(within(screen.getByTestId("quality-security-not-found")).queryByText("14")).toBeNull();
    stub.restore();
  });

  it("unmeasured_runs 单列一块，不并入任何安全检查计数", async () => {
    const stub = installFetchStub(qualityRoutes({}));
    renderPage({ questionVersionId: UUID_C });

    await screen.findByTestId("quality-summary");
    const unmeasured = screen.getByTestId("quality-unmeasured-runs");
    expect(within(unmeasured).getByTestId("quality-unmeasured-runs-value")).toHaveTextContent("6");
    expect(unmeasured).toHaveTextContent("未测量");
    // 反向断言：这一块里不得出现安全检查的任何数字（"没测量到资源" ≠ "没发现问题"）。
    expect(unmeasured.textContent).not.toContain("27");
    expect(unmeasured.textContent).not.toContain("14");
    // 并且安全检查块里也不得出现 unmeasured 的数。
    expect(screen.getByTestId("quality-security").textContent).not.toContain("未测量资源峰值");
    expect(screen.getByTestId("quality-unmeasured-separate")).toHaveTextContent("不并入");
    stub.restore();
  });

  it("样本量很小（< 门槛）时先提示不足以判断区分度，并说明门槛是本页的阅读辅助", async () => {
    const stub = installFetchStub(qualityRoutes({
      summary: evaluationQualitySummary({ attempt_count: SMALL_SAMPLE_THRESHOLD - 1 }),
    }));
    renderPage({ questionVersionId: UUID_C });

    const banner = await screen.findByTestId("quality-small-sample");
    expect(banner).toHaveTextContent("样本太少");
    expect(banner).toHaveTextContent("不足以判断区分度");
    // 门槛不是契约规则：必须说明它是本页的阅读辅助，否则会被当成权威口径。
    expect(banner).toHaveTextContent(`门槛 ${SMALL_SAMPLE_THRESHOLD} 是本页的`);
    expect(banner).toHaveTextContent("阅读辅助");
    expect(banner).toHaveTextContent("不是契约规则");
    stub.restore();
  });

  it("样本量足够（= 门槛）时不出现样本不足提示", async () => {
    const stub = installFetchStub(qualityRoutes({
      summary: evaluationQualitySummary({ attempt_count: SMALL_SAMPLE_THRESHOLD }),
    }));
    renderPage({ questionVersionId: UUID_C });

    await screen.findByTestId("quality-summary");
    expect(screen.queryByTestId("quality-small-sample")).toBeNull();
    stub.restore();
  });

  it("attempt_count = 0 时明确说「没有可读的信号」，并且不编造比例", async () => {
    const stub = installFetchStub(qualityRoutes({
      summary: evaluationQualitySummary({
        attempt_count: 0,
        failure_categories: [],
        static_rule_hits: [],
        case_failure_rates: [],
        unmeasured_runs: 0,
        security_found: 0,
        security_not_found: 0,
        security_not_checked: 0,
      }),
    }));
    renderPage({ questionVersionId: UUID_C });

    expect(await screen.findByTestId("quality-empty")).toHaveTextContent("没有可读的信号");
    expect(await screen.findByTestId("quality-empty")).toHaveTextContent("attempt_count = 0");
    expect(screen.getByTestId("quality-attempt-count-value")).toHaveTextContent("0");
    stub.restore();
  });
});

describe("题目质量聚合的其余呈现（CHG-0015）", () => {
  it("系统原因（infrastructure_error）从题目难度里单列", async () => {
    const stub = installFetchStub(qualityRoutes({}));
    renderPage({ questionVersionId: UUID_C });

    await screen.findByTestId("quality-summary");
    // 夹具：infrastructure_error=5，其余类别 11+3=14。
    expect(screen.getByTestId("quality-categories-system-value")).toHaveTextContent("5");
    expect(screen.getByTestId("quality-categories-other-value")).toHaveTextContent("14");
    expect(screen.getByTestId("quality-categories-system")).toHaveTextContent("不是难度");
    stub.restore();
  });

  it("逐用例失败率按 ordinal 呈现；0 次尝试显示「无样例」而不是 0%", async () => {
    const stub = installFetchStub(qualityRoutes({}));
    renderPage({ questionVersionId: UUID_C });

    await screen.findByTestId("quality-case-rates-table");
    const table = screen.getByTestId("quality-case-rates-table");
    const rows = within(table).getAllByRole("row");
    // 表头 + 3 个 ordinal。
    expect(rows).toHaveLength(4);
    // ordinal 1：31/42 → 74%。
    expect(within(rows[2]).getByText("74%")).toBeInTheDocument();
    // ordinal 2：attempts=0 → "无样例"（0% 会被读成"这个用例大家都过得去"）。
    expect(within(rows[3]).getByText("无样例")).toBeInTheDocument();
    stub.restore();
  });

  it("静态规则按冻结目录逐条呈现：0 命中行不被过滤，且说明里写明 0 的含义", async () => {
    const stub = installFetchStub(qualityRoutes({}));
    renderPage({ questionVersionId: UUID_C });

    await screen.findByTestId("quality-rules-table");
    const rules = screen.getByTestId("quality-rules");
    expect(rules).toHaveTextContent("PY-UNUSED-IMPORT");
    expect(rules).toHaveTextContent("CMP-004");

    // 目录内、本范围内一次都没命中的规则**必须出现**（契约：never-firing 的规则正是要 surface 的东西），
    // 且它的计数就是 0，不是被过滤掉、也不是被写成"无数据"。
    const table = screen.getByTestId("quality-rules-table");
    const zeroRow = within(table).getByText("STAT-PY-013").closest("tr") as HTMLTableRowElement;
    expect(within(zeroRow).getAllByRole("cell")[2]).toHaveTextContent(/^0$/);

    // 说明必须与渲染出来的形状一致：曾写成"只含命中过的规则"+"拿不到规则全集"，而那 0 命中行正在表里。
    expect(rules).toHaveTextContent("冻结的规则目录");
    expect(rules).toHaveTextContent("一次都没有命中过");
    expect(rules).toHaveTextContent("不过滤");
    // 目录之外的 rule_id 会被追加在目录之后，这点也要如实说明。
    expect(rules).toHaveTextContent("ZZ-LEGACY-001");
    expect(rules).toHaveTextContent("追加在目录之后");
    // 契约的用意（never-firing 规则要么没用、要么写错）保留在页面上。
    expect(rules).toHaveTextContent("要么没用，要么写错了");

    // 去标识化说明必须留在页面上（不能让人以为这是完整口径）；但**没有**任何"尚未实现"式的常驻断言。
    expect(screen.getByTestId("quality-counts-only")).toHaveTextContent("没有学生 id");
    expect(screen.queryByTestId("declared-contract-quality")).toBeNull();
    stub.restore();
  });

  it("静态规则数组为空时给空态，不编造目录条目", async () => {
    const stub = installFetchStub(
      qualityRoutes({
        summary: evaluationQualitySummary({ static_rule_hits: [] }),
      }),
    );
    renderPage({ questionVersionId: UUID_C });

    const rules = await screen.findByTestId("quality-rules");
    expect(within(rules).getByText("暂无数据")).toBeInTheDocument();
    expect(screen.queryByTestId("quality-rules-table")).toBeNull();
    stub.restore();
  });
});
