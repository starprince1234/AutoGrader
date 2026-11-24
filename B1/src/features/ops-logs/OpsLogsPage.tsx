import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { t, type MessageKey } from "../../i18n";
import {
  Banner,
  EmptyState,
  ErrorState,
  InlineLoader,
  PageTitle,
  Panel,
  StatusPill,
} from "../../ui/components";
import { formatDateTime } from "../../ui/format";
import {
  downloadOpsLogExport,
  fetchAuditRecords,
  fetchOpsInsight,
  fetchOpsLogs,
  fetchOpsLogSummary,
  fetchOpsLogTrace,
  type AuditRecord,
  type LogLevel,
  type LogProducer,
  type OpsInsightLayer,
  type OpsLogRecord,
  type OpsLogsSearchParams,
} from "./api";
import { matchesFuzzy, redactSensitive } from "./redact";

export type OpsLogsTab = "overview" | "search" | "trace" | "audit";

export type OpsLogsPageProps = {
  tab?: OpsLogsTab;
  initialTraceId?: string;
  initialProducer?: LogProducer;
  /** 深链：`?submission_id=` —— 只留这条提交的日志（成绩详情「查看相关日志」）。 */
  initialSubmissionId?: string;
  onTabChange?: (tab: OpsLogsTab) => void;
  onTraceChange?: (traceId: string) => void;
};

const LEVEL_TONE: Record<LogLevel, "idle" | "warn" | "bad"> = {
  info: "idle",
  warn: "warn",
  error: "bad",
};

/** The four layers, most urgent first. The report itself never reorders them. */
const INSIGHT_LAYERS: { key: OpsInsightLayer; labelKey: MessageKey; tone: "bad" | "warn" | "idle" | "ok" }[] = [
  { key: "critical", labelKey: "opsLogs.insight.critical", tone: "bad" },
  { key: "warning", labelKey: "opsLogs.insight.warning", tone: "warn" },
  { key: "notice", labelKey: "opsLogs.insight.notice", tone: "idle" },
  { key: "healthy", labelKey: "opsLogs.insight.healthy", tone: "ok" },
];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function OpsLogsPage({
  tab = "overview",
  initialTraceId = "",
  initialProducer,
  initialSubmissionId = "",
  onTabChange,
  onTraceChange,
}: OpsLogsPageProps) {
  const [activeTab, setActiveTab] = useState<OpsLogsTab>(tab);
  const [selectedRecord, setSelectedRecord] = useState<OpsLogRecord | null>(null);
  const drawerRef = useRef<HTMLElement | null>(null);
  // 深链带来的提交 id：作为检索条件之一（服务端没有这个筛选维度，所以在已加载的行上过滤）。
  const [submissionFilter, setSubmissionFilter] = useState(initialSubmissionId);
  // 事件名输入做防抖，避免每敲一个字母就重查一次；匹配本身是"包含"。
  const [eventDebounced, setEventDebounced] = useState("");

  // Search filters
  const [producer, setProducer] = useState<LogProducer | "">(initialProducer ?? "");
  const [level, setLevel] = useState<LogLevel | "">("");
  const [eventFilter, setEventFilter] = useState("");
  const [traceFilter, setTraceFilter] = useState("");
  const [ioTypeFilter, setIoTypeFilter] = useState("");
  const [keyword, setKeyword] = useState("");
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [accumulated, setAccumulated] = useState<OpsLogRecord[]>([]);
  const [exporting, setExporting] = useState(false);

  // Trace tab state
  const [traceInput, setTraceInput] = useState(initialTraceId);
  const [activeTraceId, setActiveTraceId] = useState(initialTraceId);

  useEffect(() => {
    setActiveTab(tab);
  }, [tab]);

  useEffect(() => {
    if (initialTraceId) {
      setTraceInput(initialTraceId);
      setActiveTraceId(initialTraceId);
    }
  }, [initialTraceId]);

  useEffect(() => {
    setSubmissionFilter(initialSubmissionId);
  }, [initialSubmissionId]);

  useEffect(() => {
    const timer = window.setTimeout(() => setEventDebounced(eventFilter.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [eventFilter]);

  // 抽屉打开时把焦点移进去（它现在挂在 body 上，不移动焦点键盘用户会被留在页面里）；Esc 收起。
  useEffect(() => {
    if (!selectedRecord) return undefined;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    drawerRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedRecord(null);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [selectedRecord]);

  const switchTab = (next: OpsLogsTab) => {
    setActiveTab(next);
    onTabChange?.(next);
  };

  const summaryQuery = useQuery({
    queryKey: ["ops-logs", "summary"],
    queryFn: fetchOpsLogSummary,
    enabled: activeTab === "overview",
  });

  // The AI report is a deliberate, paid action: nothing is requested until the operator asks for
  // it, and asking again replaces the previous answer rather than accumulating stale reports.
  const insightMutation = useMutation({
    mutationFn: fetchOpsInsight,
  });

  // `event` 不再作为服务端条件：契约里的 event 是精确匹配，输入片段会一条都查不到。
  // 片段匹配在前端对已加载的行做（见下面的 `visibleRows`），这样"打前几个字母就能命中"。
  const searchParams: OpsLogsSearchParams = {
    producer: producer || undefined,
    level: level || undefined,
    correlation_id: traceFilter.trim() || undefined,
    io_type: ioTypeFilter.trim() || undefined,
    q: keyword.trim() || undefined,
    cursor,
    limit: 50,
  };

  const searchQuery = useQuery({
    queryKey: ["ops-logs", "search", searchParams],
    queryFn: () => fetchOpsLogs(searchParams),
    enabled: activeTab === "search",
  });

  useEffect(() => {
    if (!searchQuery.data) return;
    if (!cursor) {
      setAccumulated(searchQuery.data.items);
    } else {
      setAccumulated((prev) => {
        const seen = new Set(prev.map((r) => r.log_id));
        return [...prev, ...searchQuery.data.items.filter((r) => !seen.has(r.log_id))];
      });
    }
  }, [searchQuery.data, cursor]);

  // 事件名是**模糊匹配**（片段、大小写不敏感）；提交 id 深链在整条记录里找。
  // 两者都只作用于已加载的行，和分页语义不冲突。
  const submissionNeedle = submissionFilter.trim().toLocaleLowerCase();
  const visibleRows = accumulated.filter((row) => {
    if (!matchesFuzzy(row.event, eventDebounced)) return false;
    if (submissionNeedle === "") return true;
    return JSON.stringify(row).toLocaleLowerCase().includes(submissionNeedle);
  });

  const traceQuery = useQuery({
    queryKey: ["ops-logs", "trace", activeTraceId],
    queryFn: () => fetchOpsLogTrace(activeTraceId),
    enabled: activeTab === "trace" && activeTraceId.trim().length > 0,
  });

  const auditQuery = useQuery({
    queryKey: ["ops-logs", "audit"],
    queryFn: () => fetchAuditRecords(),
    enabled: activeTab === "audit",
  });

  const handleExport = async (format: "jsonl" | "csv") => {
    setExporting(true);
    try {
      await downloadOpsLogExport({
        format,
        producer: producer || undefined,
        level: level || undefined,
        event: eventFilter.trim() || undefined,
        correlation_id: traceFilter.trim() || undefined,
        io_type: ioTypeFilter.trim() || undefined,
        q: keyword.trim() || undefined,
        limit: 2000,
      });
    } finally {
      setExporting(false);
    }
  };

  const jumpToTrace = (id: string) => {
    setTraceInput(id);
    setActiveTraceId(id);
    setSelectedRecord(null);
    onTraceChange?.(id);
    switchTab("trace");
  };

  return (
    <div className="stack" data-testid="ops-logs-page">
      <div className="panel__head">
        <div>
          <PageTitle>{t("opsLogs.title")}</PageTitle>
        </div>
      </div>

      {/*
        分段控件（与 `ui/components.tsx` 的 Tabs 同形）：外层 `.tabs` 是轨道，选中项是白面。
        这里保留页面自己的 state 与深链，只把外观换成本项目的按钮词汇。
      */}
      <div
        className="tabs"
        role="tablist"
        aria-label={t("opsLogs.title")}
        onKeyDown={(event) => {
          if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
          event.preventDefault();
          const order: OpsLogsTab[] = ["overview", "search", "trace", "audit"];
          const index = order.indexOf(activeTab);
          const delta = event.key === "ArrowRight" ? 1 : -1;
          const next = order[(index + delta + order.length) % order.length];
          switchTab(next);
          document.querySelector<HTMLElement>(`[data-testid="ops-logs-tab-${next}"]`)?.focus();
        }}
      >
        {(["overview", "search", "trace", "audit"] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={activeTab === key}
            tabIndex={activeTab === key ? 0 : -1}
            className={activeTab === key ? "button" : "button button--ghost"}
            onClick={() => switchTab(key)}
            data-testid={`ops-logs-tab-${key}`}
          >
            {t(`opsLogs.tab.${key}`)}
          </button>
        ))}
      </div>

      {activeTab === "overview" && (
        <div className="stack" data-testid="ops-logs-overview">
          {summaryQuery.isPending && <InlineLoader />}
          {summaryQuery.error && <ErrorState error={summaryQuery.error} />}
          <Panel title={t("opsLogs.insight.title")} testId="ops-insight-panel">
            <p className="muted">{t("opsLogs.insight.hint")}</p>
            <div className="inline ops-log-actions">
              <button
                type="button"
                className="button"
                data-testid="ops-insight-generate"
                disabled={insightMutation.isPending}
                onClick={() => insightMutation.mutate()}
              >
                {insightMutation.isPending
                  ? t("opsLogs.insight.generating")
                  : t("opsLogs.insight.generate")}
              </button>
              {insightMutation.isPending && <InlineLoader />}
              {insightMutation.data && (
                <span className="muted">
                  {t("opsLogs.insight.generatedAt", {
                    at: formatDateTime(insightMutation.data.generated_at, "-"),
                    model: insightMutation.data.model ?? "-",
                    hours: insightMutation.data.window_hours,
                  })}
                </span>
              )}
            </div>

            {insightMutation.error && (
              <Banner tone="error">
                {t("opsLogs.insight.failed", { detail: String(insightMutation.error) })}
              </Banner>
            )}

            {insightMutation.data?.status === "unavailable" && (
              <Banner tone="warn">{t("opsLogs.insight.unavailable")}</Banner>
            )}

            {insightMutation.data?.status === "ready" && (
              <div className="stack" data-testid="ops-insight-report">
                <p data-testid="ops-insight-summary">{insightMutation.data.summary}</p>
                {INSIGHT_LAYERS.map(({ key, labelKey, tone }) => {
                  const findings = insightMutation.data?.layers[key] ?? [];
                  return (
                    <section key={key} className="stack" data-testid={`ops-insight-layer-${key}`}>
                      <div className="inline" style={{ gap: "0.5rem" }}>
                        <StatusPill tone={tone}>{t(labelKey)}</StatusPill>
                        {findings.length === 0 && (
                          <span className="muted" data-testid={`ops-insight-layer-${key}-none`}>
                            {t("opsLogs.insight.none")}
                          </span>
                        )}
                      </div>
                      {findings.length > 0 && (
                        <ul className="stack" style={{ margin: 0, paddingLeft: "1.2rem" }}>
                          {findings.map((finding) => (
                            <li key={`${key}-${finding.module}`} data-testid={`ops-insight-finding-${key}-${finding.module}`}>
                              <strong>{finding.module}</strong> · {finding.title}
                              <div className="muted">{finding.detail}</div>
                              {finding.suggestion && (
                                <div className="muted">
                                  {t("opsLogs.insight.suggestion")}：{finding.suggestion}
                                </div>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </section>
                  );
                })}
              </div>
            )}
          </Panel>
          {summaryQuery.data && (
            <>
              {summaryQuery.data.db_degraded && (
                <Banner tone="warn">{t("opsLogs.overview.dbDegraded")}</Banner>
              )}
              <div className="inline" style={{ flexWrap: "wrap", gap: "1rem" }}>
                <StatusPill tone={summaryQuery.data.dropped_total > 0 ? "warn" : "idle"}>
                  {t("opsLogs.overview.droppedTotal")}: {summaryQuery.data.dropped_total}
                </StatusPill>
                <StatusPill tone="idle">
                  {t("opsLogs.overview.storageUsed")}: {formatBytes(summaryQuery.data.storage_bytes)} / {formatBytes(summaryQuery.data.storage_limit_bytes)}
                </StatusPill>
                <span className="muted">
                  {t("opsLogs.overview.retentionPolicy", {
                    infoDays: summaryQuery.data.retention_info_days,
                    warnDays: summaryQuery.data.retention_warn_error_days,
                  })}
                </span>
              </div>

              <div className="grid-2">
                {summaryQuery.data.modules.map((mod) => {
                  const isEventDriven = mod.producer === "B5";
                  const isSilent = !isEventDriven && mod.silent_minutes !== null && mod.silent_minutes >= 60;
                  return (
                    <Panel key={mod.producer} title={mod.producer} testId={`ops-module-card-${mod.producer}`}>
                      <div className="inline" style={{ justifyContent: "space-between", marginBottom: "0.5rem" }}>
                        <StatusPill tone={mod.ready ? "ok" : "bad"}>
                          {mod.ready ? "ready" : "not-ready"}
                        </StatusPill>
                        {isEventDriven ? (
                          <StatusPill tone="idle">
                            {t("opsLogs.overview.eventDriven")}
                          </StatusPill>
                        ) : (
                          isSilent && (
                            <StatusPill tone="bad">
                              {t("opsLogs.overview.silentAlert", { minutes: mod.silent_minutes ?? 0 })}
                            </StatusPill>
                          )
                        )}
                      </div>
                      <p className="muted">
                        {t("opsLogs.overview.lastLog")}:{" "}
                        {mod.last_log_at ? formatDateTime(mod.last_log_at, "-") : t("opsLogs.overview.noLogs")}
                      </p>
                      {isEventDriven && !mod.last_log_at && (
                        <p className="muted">{t("opsLogs.overview.eventDrivenHint")}</p>
                      )}
                      <p className="muted">
                        {t("opsLogs.overview.warn1h")}: {mod.warn_1h} / {t("opsLogs.overview.error1h")}: {mod.error_1h}
                      </p>
                      <p className="muted">
                        {t("opsLogs.overview.warn24h")}: {mod.warn_24h} / {t("opsLogs.overview.error24h")}: {mod.error_24h}
                      </p>
                    </Panel>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}

      {activeTab === "search" && (
        <Panel title={t("opsLogs.tab.search")} testId="ops-logs-search">
          <div className="ops-log-filters">
            <select
              aria-label={t("opsLogs.search.producer")}
              value={producer}
              onChange={(e) => {
                setCursor(undefined);
                setProducer(e.target.value as LogProducer | "");
              }}
            >
              <option value="">{t("opsLogs.search.producer")}: 全部</option>
              {(["B1", "B2", "B3", "B4", "B5", "MAIN"] as const).map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            <select
              aria-label={t("opsLogs.search.level")}
              value={level}
              onChange={(e) => {
                setCursor(undefined);
                setLevel(e.target.value as LogLevel | "");
              }}
            >
              <option value="">{t("opsLogs.search.level")}: 全部</option>
              <option value="info">info</option>
              <option value="warn">warn</option>
              <option value="error">error</option>
            </select>
            <input
              type="text"
              aria-label={t("opsLogs.search.event")}
              placeholder={t("opsLogs.search.event")}
              value={eventFilter}
              onChange={(e) => {
                setCursor(undefined);
                setEventFilter(e.target.value);
              }}
            />
            <input
              type="text"
              aria-label={t("opsLogs.search.correlationId")}
              placeholder={t("opsLogs.search.correlationId")}
              value={traceFilter}
              onChange={(e) => {
                setCursor(undefined);
                setTraceFilter(e.target.value);
              }}
            />
            <input
              type="text"
              aria-label={t("opsLogs.search.ioType")}
              placeholder={t("opsLogs.search.ioType")}
              value={ioTypeFilter}
              onChange={(e) => {
                setCursor(undefined);
                setIoTypeFilter(e.target.value);
              }}
            />
            <input
              type="text"
              aria-label={t("opsLogs.search.keyword")}
              placeholder={t("opsLogs.search.keyword")}
              value={keyword}
              onChange={(e) => {
                setCursor(undefined);
                setKeyword(e.target.value);
              }}
            />
            <button
              type="button"
              className="button button--ghost"
              disabled={exporting}
              onClick={() => void handleExport("jsonl")}
              data-testid="ops-export-jsonl"
            >
              {exporting ? t("opsLogs.search.exporting") : t("opsLogs.search.exportJsonl")}
            </button>
            <button
              type="button"
              className="button button--ghost"
              disabled={exporting}
              onClick={() => void handleExport("csv")}
              data-testid="ops-export-csv"
            >
              {exporting ? t("opsLogs.search.exporting") : t("opsLogs.search.exportCsv")}
            </button>
          </div>

          {searchQuery.isPending && !cursor && <InlineLoader />}
          {searchQuery.error && <ErrorState error={searchQuery.error} />}
          {submissionFilter ? (
            <div className="inline" data-testid="ops-submission-filter">
              <span className="muted">
                {t("opsLogs.search.submissionFilter")}
                <code>{submissionFilter}</code>
              </span>
              <button
                type="button"
                className="button button--ghost"
                data-testid="ops-submission-clear"
                onClick={() => setSubmissionFilter("")}
              >
                {t("opsLogs.search.submissionClear")}
              </button>
            </div>
          ) : null}

          {!searchQuery.isPending && visibleRows.length === 0 && (
            <EmptyState>{t("opsLogs.search.empty")}</EmptyState>
          )}

          {visibleRows.length > 0 && (
            /* 表格必须待在 `.table__wrap` 里：长 UUID 与长事件名在窄屏上靠它横向滚动，
               而不是把面板和整页撑出视口（这正是这一页此前的表现）。 */
            <div className="table__wrap ops-log-scroll">
              <table className="table ops-log-table ops-log-table--search">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>模块</th>
                    <th>进程</th>
                    <th>级别</th>
                    <th>事件</th>
                    <th>关联 ID</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row) => {
                    const correlation = row.correlation_id ?? row.request_id ?? "-";
                    return (
                      <tr
                        key={row.log_id}
                        onClick={() => setSelectedRecord(row)}
                        data-testid={`ops-log-row-${row.log_id}`}
                      >
                        <td className="ops-log-time">{formatDateTime(row.timestamp, "-")}</td>
                        <td>{row.producer}</td>
                        <td>{row.process}</td>
                        <td>
                          <StatusPill tone={LEVEL_TONE[row.level]}>{row.level}</StatusPill>
                        </td>
                        <td>
                          <code className="ops-log-event" title={row.event}>{row.event}</code>
                        </td>
                        <td>
                          {/* 完整值留在 DOM 与 title 里；视觉上截断，点行可在抽屉里看全量 JSON。 */}
                          <code className="ops-log-id" title={correlation}>{correlation}</code>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {searchQuery.data?.next_cursor && (
            <div style={{ marginTop: "1rem" }}>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setCursor(searchQuery.data.next_cursor ?? undefined)}
              >
                {t("opsLogs.search.loadMore")}
              </button>
            </div>
          )}
        </Panel>
      )}

      {activeTab === "trace" && (
        <Panel title={t("opsLogs.tab.trace")} testId="ops-logs-trace">
          <form
            className="ops-log-filters"
            onSubmit={(e) => {
              e.preventDefault();
              const next = traceInput.trim();
              setActiveTraceId(next);
              onTraceChange?.(next);
            }}
          >
            <input
              type="text"
              className="ops-log-trace-input"
              placeholder={t("opsLogs.trace.placeholder")}
              value={traceInput}
              onChange={(e) => setTraceInput(e.target.value)}
              data-testid="ops-trace-input"
            />
            <button type="submit" className="button" data-testid="ops-trace-submit">
              {t("opsLogs.trace.submit")}
            </button>
          </form>

          {traceQuery.isPending && activeTraceId && <InlineLoader />}
          {traceQuery.error && <ErrorState error={traceQuery.error} />}
          {traceQuery.data && traceQuery.data.items.length === 0 && (
            <EmptyState>{t("opsLogs.trace.empty")}</EmptyState>
          )}
          {traceQuery.data && traceQuery.data.items.length > 0 && (
            <div className="stack">
              {traceQuery.data.truncated && (
                <p className="muted">{t("opsLogs.trace.truncated")}</p>
              )}
              <ul className="stack" style={{ listStyle: "none", padding: 0 }}>
                {traceQuery.data.items.map((item) => (
                  <li
                    key={item.log_id}
                    className={`ops-log-item${item.level === "error" ? " ops-log-item--bad" : item.level === "warn" ? " ops-log-item--warn" : ""}`}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedRecord(item)}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      setSelectedRecord(item);
                    }}
                  >
                    <div className="inline" style={{ justifyContent: "space-between" }}>
                      <span className="ops-log-item__title">
                        <strong>{item.producer}</strong> ({item.process}) · <code>{item.event}</code>
                      </span>
                      <StatusPill tone={LEVEL_TONE[item.level]}>{item.level}</StatusPill>
                    </div>
                    <p className="muted" style={{ margin: "0.25rem 0 0" }}>
                      {formatDateTime(item.timestamp, "-")}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Panel>
      )}

      {activeTab === "audit" && (
        <Panel title={t("opsLogs.tab.audit")} testId="ops-logs-audit">
          <p style={{ marginBottom: "0.75rem" }}>
            <Link to="/review">{t("opsLogs.audit.mailRecordsLink")}</Link>
          </p>
          {auditQuery.isPending && <InlineLoader />}
          {auditQuery.error && <ErrorState error={auditQuery.error} />}
          {auditQuery.data && auditQuery.data.items.length === 0 && (
            <EmptyState>{t("opsLogs.audit.empty")}</EmptyState>
          )}
          {auditQuery.data && auditQuery.data.items.length > 0 && (
            <div className="table__wrap ops-log-scroll">
              <table className="table ops-log-table ops-log-table--audit">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>动作</th>
                    <th>结果</th>
                    <th>目标</th>
                    <th>详情</th>
                  </tr>
                </thead>
                <tbody>
                  {auditQuery.data.items.map((rec: AuditRecord) => (
                    <tr key={rec.audit_id}>
                      <td className="ops-log-time">{formatDateTime(rec.occurred_at, "-")}</td>
                      <td><code className="ops-log-event" title={rec.action}>{rec.action}</code></td>
                      <td>{rec.outcome}</td>
                      <td>
                        <span className="ops-log-id" title={rec.target_id ?? undefined}>
                          {rec.target_type} {rec.target_id ? `(${rec.target_id.slice(0, 8)})` : ""}
                        </span>
                      </td>
                      <td><span className="ops-log-detail" title={rec.detail ?? undefined}>{rec.detail}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}

      {/*
        抽屉挂在 `document.body` 上（而不是页面 tree 里）：页头的 `backdrop-filter` 会形成自己的
        层叠上下文，页面内的 fixed 元素会被它压在下面——遮罩盖不住页头是这么来的。portal 之后
        「遮罩 + 抽屉」永远在最上层，且不参与页面布局。
      */}
      {selectedRecord &&
        createPortal(
          <>
            <div
              className="ops-log-backdrop"
              data-testid="ops-log-drawer-backdrop"
              onMouseDown={(event) => {
                // 点抽屉外部（遮罩）收起；抽屉内部的点击不会冒泡到这里。
                if (event.target === event.currentTarget) setSelectedRecord(null);
              }}
            />
            <aside
              className="ops-log-drawer"
              role="dialog"
              aria-modal="true"
              aria-label={t("opsLogs.drawer.title")}
              tabIndex={-1}
              ref={drawerRef}
              data-testid="ops-log-drawer"
            >
              <div className="ops-log-drawer__head">
                <div className="inline" style={{ gap: "0.5rem" }}>
                  <strong>{t("opsLogs.drawer.title")}</strong>
                  <StatusPill tone="ok">{t("opsLogs.drawer.redactedBadge")}</StatusPill>
                </div>
                <button
                  type="button"
                  className="button button--ghost"
                  data-testid="ops-log-drawer-close"
                  onClick={() => setSelectedRecord(null)}
                >
                  {t("opsLogs.drawer.close")}
                </button>
              </div>
              {(selectedRecord.correlation_id || selectedRecord.request_id) && (
                <button
                  type="button"
                  className="button button--ghost"
                  onClick={() =>
                    jumpToTrace(selectedRecord.correlation_id ?? selectedRecord.request_id ?? "")
                  }
                >
                  {t("opsLogs.drawer.traceAction")}
                </button>
              )}
              {/* 敏感字段（密码、令牌、参考答案、用例内容…）统一显示 [REDACTED]。 */}
              <pre className="ops-log-json" data-testid="ops-log-drawer-json">
                {JSON.stringify(redactSensitive(selectedRecord), null, 2)}
              </pre>
            </aside>
          </>,
          document.body,
        )}
    </div>
  );
}
