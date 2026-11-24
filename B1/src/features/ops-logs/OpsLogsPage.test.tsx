import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OpsLogsPage } from "./OpsLogsPage";
import { mainNavItems } from "../../app/navigation";
import { canViewOpsLogs } from "../../auth/visibility";

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    to,
    children,
    ...rest
  }: {
    to: string;
    children: React.ReactNode;
    [key: string]: unknown;
  }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("./api", () => ({
  fetchOpsLogSummary: vi.fn().mockResolvedValue({
    generated_at: "2026-11-18T10:00:00.000Z",
    modules: [
      {
        producer: "B1",
        last_log_at: "2026-11-18T09:59:00.000Z",
        warn_1h: 0,
        error_1h: 0,
        warn_24h: 1,
        error_24h: 0,
        total_24h: 10,
        ready: true,
        silent_minutes: 1,
      },
      {
        producer: "B2",
        last_log_at: "2026-11-18T09:58:00.000Z",
        warn_1h: 1,
        error_1h: 2,
        warn_24h: 3,
        error_24h: 2,
        total_24h: 25,
        ready: true,
        silent_minutes: 2,
      },
      {
        producer: "B5",
        last_log_at: null,
        warn_1h: 0,
        error_1h: 0,
        warn_24h: 0,
        error_24h: 0,
        total_24h: 0,
        ready: true,
        silent_minutes: 120,
      },
    ],
    dropped_total: 0,
    db_degraded: false,
    storage_bytes: 2048,
    storage_limit_bytes: 134217728,
    retention_info_days: 7,
    retention_warn_error_days: 30,
  }),
  fetchOpsLogs: vi.fn().mockResolvedValue({
    items: [
      {
        log_id: 101,
        timestamp: "2026-11-18T09:59:00.000Z",
        level: "error",
        event: "evaluation.run.failed",
        producer: "B2",
        process: "worker",
        request_id: "11111111-1111-4111-8111-111111111111",
        correlation_id: "22222222-2222-4222-8222-222222222222",
        course_id: null,
        assignment_id: null,
        submission_id: null,
        evaluation_run_id: null,
        question_version_id: null,
        actor_id: null,
        stage: "dynamic",
        fields: { io_type: "stdin", error_category: "timeout" },
        truncated: false,
      },
    ],
    next_cursor: null,
  }),
  fetchOpsLogTrace: vi.fn().mockResolvedValue({
    trace_id: "22222222-2222-4222-8222-222222222222",
    items: [
      {
        log_id: 100,
        timestamp: "2026-11-18T09:58:50.000Z",
        level: "info",
        event: "submission.created",
        producer: "B1",
        process: "browser",
        fields: {},
        truncated: false,
      },
      {
        log_id: 101,
        timestamp: "2026-11-18T09:59:00.000Z",
        level: "error",
        event: "evaluation.run.failed",
        producer: "B2",
        process: "worker",
        fields: {},
        truncated: false,
      },
    ],
    truncated: false,
  }),
  fetchAuditRecords: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
  fetchOpsInsight: vi.fn().mockResolvedValue({
    status: "ready",
    generated_at: "2026-11-18T10:05:00.000Z",
    model: "mimo-v2.6-flash",
    prompt_version: "main-ops-insight-v1",
    window_hours: 24,
    summary: "整体正常，只有 B2 出现过一次可重试的评测失败。",
    layers: {
      critical: [],
      warning: [
        {
          layer: "warning",
          module: "B2",
          title: "有一条评测失败，可重试",
          detail: "最近 24 小时有 2 次评测失败，没有影响其他提交。",
          suggestion: "在日志检索里按 evaluation.run.failed 过滤，确认是否同一份提交。",
        },
      ],
      notice: [],
      healthy: [
        {
          layer: "healthy",
          module: "B1",
          title: "B1 未见异常",
          detail: "本次检查窗口内没有发现该模块的异常记录。",
          suggestion: null,
        },
      ],
    },
  }),
  downloadOpsLogExport: vi.fn().mockResolvedValue(undefined),
}));

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("OpsLogsPage and visibility", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("controls navigation visibility by role", () => {
    expect(
      canViewOpsLogs({
        status: "authenticated",
        roles: ["student"],
        rolesKnown: true,
        subject: "s1",
        username: "stu",
        scope: [],
        expiresAt: null,
      }),
    ).toBe(false);
    expect(
      canViewOpsLogs({
        status: "authenticated",
        roles: ["teacher"],
        rolesKnown: true,
        subject: "t1",
        username: "tch",
        scope: [],
        expiresAt: null,
      }),
    ).toBe(true);

    const studentNav = mainNavItems({ status: "authenticated", roles: ["student"], rolesKnown: true });
    expect(studentNav.some((i) => i.to === "/ops/logs")).toBe(false);
    const teacherNav = mainNavItems({ status: "authenticated", roles: ["teacher"], rolesKnown: true });
    expect(teacherNav.some((i) => i.to === "/ops/logs")).toBe(true);
  });

  it("renders overview, switches to search & opens redacted drawer, and runs trace", async () => {
    const user = userEvent.setup();
    renderWithQuery(<OpsLogsPage tab="overview" />);

    await waitFor(() => {
      expect(screen.getByTestId("ops-module-card-B1")).toBeInTheDocument();
      expect(screen.getByTestId("ops-module-card-B2")).toBeInTheDocument();
      const b5Card = screen.getByTestId("ops-module-card-B5");
      expect(b5Card).toHaveTextContent("按需触发");
      expect(b5Card).not.toHaveTextContent("静默 120 分钟");
    });

    await user.click(screen.getByTestId("ops-logs-tab-search"));
    await waitFor(() => {
      expect(screen.getByTestId("ops-log-row-101")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("ops-log-row-101"));
    expect(screen.getByTestId("ops-log-drawer")).toBeInTheDocument();
    expect(screen.getByText("已脱敏")).toBeInTheDocument();

    await user.click(screen.getByTestId("ops-logs-tab-trace"));
    await user.type(screen.getByTestId("ops-trace-input"), "22222222-2222-4222-8222-222222222222");
    await user.click(screen.getByTestId("ops-trace-submit"));
    await waitFor(() => {
      expect(screen.getByText("submission.created")).toBeInTheDocument();
      expect(screen.getByText("evaluation.run.failed")).toBeInTheDocument();
    });
  });

  it("renders the AI status report in four layers, with 无 for the empty ones", async () => {
    const user = userEvent.setup();
    renderWithQuery(<OpsLogsPage tab="overview" />);

    // Nothing is requested until the operator asks: the panel starts with just its button.
    expect(screen.queryByTestId("ops-insight-report")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("ops-insight-generate"));

    await waitFor(() => {
      expect(screen.getByTestId("ops-insight-report")).toBeInTheDocument();
    });

    expect(screen.getByTestId("ops-insight-summary")).toHaveTextContent(
      "整体正常，只有 B2 出现过一次可重试的评测失败。",
    );

    // The four layers are always present, in descending urgency, even when they are empty.
    const headers = screen.getAllByText(/^(严重告警|中度告警|轻度告警|完全健康)$/);
    expect(headers.map((node) => node.textContent)).toEqual([
      "严重告警",
      "中度告警",
      "轻度告警",
      "完全健康",
    ]);

    // An empty layer says 无 rather than rendering nothing.
    expect(screen.getByTestId("ops-insight-layer-critical-none")).toHaveTextContent("无");
    expect(screen.getByTestId("ops-insight-layer-notice-none")).toHaveTextContent("无");

    // A finding names its module, its plain-language title, and the suggested next step.
    const warning = screen.getByTestId("ops-insight-finding-warning-B2");
    expect(warning).toHaveTextContent("B2");
    expect(warning).toHaveTextContent("有一条评测失败，可重试");
    expect(warning).toHaveTextContent("建议：在日志检索里按 evaluation.run.failed 过滤");

    // A healthy module is listed too, so the report accounts for the whole platform.
    expect(screen.getByTestId("ops-insight-finding-healthy-B1")).toHaveTextContent("B1 未见异常");
  });
});
