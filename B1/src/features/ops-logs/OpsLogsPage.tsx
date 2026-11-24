import { useEffect, useState } from "react";
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

export type OpsLogsTab = "overview" | "search" | "trace" | "audit";

export type OpsLogsPageProps = {
  tab?: OpsLogsTab;
  initialTraceId?: string;
  initialProducer?: LogProducer;
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
  onTabChange,
  onTraceChange,
}: OpsLogsPageProps) {
  const [activeTab, setActiveTab] = useState<OpsLogsTab>(tab);
  const [selectedRecord, setSelectedRecord] = useState<OpsLogRecord | null>(null);

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

  const searchParams: OpsLogsSearchParams = {
    producer: producer || undefined,
    level: level || undefined,
    event: eventFilter.trim() || undefined,
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
          <p className="muted">{t("opsLogs.subtitle")}</p>
        </div>
      </div>

      <div className="tabs" role="tablist">
        {(["overview", "search", "trace", "audit"] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={activeTab === key}
            className={`tab ${activeTab === key ? "is-active" : ""}`}
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
            <div className="inline" style={{ flexWrap: "wrap", gap: "0.75rem", marginBottom: "0.75rem" }}>
              <button
                type="button"
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

              <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "1rem" }}>
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
          <div className="inline" style={{ flexWrap: "wrap", gap: "0.5rem", marginBottom: "1rem" }}>
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
              placeholder={t("opsLogs.search.event")}
              value={eventFilter}
              onChange={(e) => {
                setCursor(undefined);
                setEventFilter(e.target.value);
              }}
            />
            <input
              type="text"
              placeholder={t("opsLogs.search.correlationId")}
              value={traceFilter}
              onChange={(e) => {
                setCursor(undefined);
                setTraceFilter(e.target.value);
              }}
            />
            <input
              type="text"
              placeholder={t("opsLogs.search.ioType")}
              value={ioTypeFilter}
              onChange={(e) => {
                setCursor(undefined);
                setIoTypeFilter(e.target.value);
              }}
            />
            <input
              type="text"
              placeholder={t("opsLogs.search.keyword")}
              value={keyword}
              onChange={(e) => {
                setCursor(undefined);
                setKeyword(e.target.value);
              }}
            />
            <button
              type="button"
              className="button button-secondary"
              disabled={exporting}
              onClick={() => void handleExport("jsonl")}
              data-testid="ops-export-jsonl"
            >
              {exporting ? t("opsLogs.search.exporting") : t("opsLogs.search.exportJsonl")}
            </button>
            <button
              type="button"
              className="button button-secondary"
              disabled={exporting}
              onClick={() => void handleExport("csv")}
              data-testid="ops-export-csv"
            >
              {exporting ? t("opsLogs.search.exporting") : t("opsLogs.search.exportCsv")}
            </button>
          </div>

          {searchQuery.isPending && !cursor && <InlineLoader />}
          {searchQuery.error && <ErrorState error={searchQuery.error} />}
          {!searchQuery.isPending && accumulated.length === 0 && (
            <EmptyState>{t("opsLogs.search.empty")}</EmptyState>
          )}

          {accumulated.length > 0 && (
            <table className="table">
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
                {accumulated.map((row) => (
                  <tr
                    key={row.log_id}
                    onClick={() => setSelectedRecord(row)}
                    style={{ cursor: "pointer" }}
                    data-testid={`ops-log-row-${row.log_id}`}
                  >
                    <td>{formatDateTime(row.timestamp, "-")}</td>
                    <td>{row.producer}</td>
                    <td>{row.process}</td>
                    <td>
                      <StatusPill tone={LEVEL_TONE[row.level]}>{row.level}</StatusPill>
                    </td>
                    <td><code>{row.event}</code></td>
                    <td><code>{row.correlation_id ?? row.request_id ?? "-"}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {searchQuery.data?.next_cursor && (
            <div style={{ marginTop: "1rem" }}>
              <button
                type="button"
                className="button button-secondary"
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
            className="inline"
            style={{ gap: "0.5rem", marginBottom: "1rem" }}
            onSubmit={(e) => {
              e.preventDefault();
              const next = traceInput.trim();
              setActiveTraceId(next);
              onTraceChange?.(next);
            }}
          >
            <input
              type="text"
              style={{ minWidth: "22rem" }}
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
                    className="card"
                    style={{
                      padding: "0.75rem",
                      borderLeft: item.level === "error" ? "4px solid var(--bad, #d32f2f)" : item.level === "warn" ? "4px solid var(--warn, #ed6c02)" : undefined,
                      cursor: "pointer",
                    }}
                    onClick={() => setSelectedRecord(item)}
                  >
                    <div className="inline" style={{ justifyContent: "space-between" }}>
                      <span>
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
            <table className="table">
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
                    <td>{formatDateTime(rec.occurred_at, "-")}</td>
                    <td><code>{rec.action}</code></td>
                    <td>{rec.outcome}</td>
                    <td>{rec.target_type} {rec.target_id ? `(${rec.target_id.slice(0, 8)})` : ""}</td>
                    <td>{rec.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      )}

      {selectedRecord && (
        <div
          className="card"
          role="dialog"
          aria-label={t("opsLogs.drawer.title")}
          data-testid="ops-log-drawer"
          style={{
            position: "fixed",
            right: "1rem",
            bottom: "1rem",
            width: "min(560px, 92vw)",
            maxHeight: "75vh",
            overflow: "auto",
            zIndex: 50,
            padding: "1rem",
            boxShadow: "0 8px 30px rgba(0,0,0,0.2)",
          }}
        >
          <div className="inline" style={{ justifyContent: "space-between", marginBottom: "0.5rem" }}>
            <div className="inline" style={{ gap: "0.5rem" }}>
              <strong>{t("opsLogs.drawer.title")}</strong>
              <StatusPill tone="ok">{t("opsLogs.drawer.redactedBadge")}</StatusPill>
            </div>
            <button
              type="button"
              className="button button-secondary"
              onClick={() => setSelectedRecord(null)}
            >
              {t("opsLogs.drawer.close")}
            </button>
          </div>
          {(selectedRecord.correlation_id || selectedRecord.request_id) && (
            <button
              type="button"
              className="button button-secondary"
              style={{ marginBottom: "0.75rem" }}
              onClick={() =>
                jumpToTrace(selectedRecord.correlation_id ?? selectedRecord.request_id ?? "")
              }
            >
              {t("opsLogs.drawer.traceAction")}
            </button>
          )}
          <pre style={{ fontSize: "0.8rem", overflowX: "auto" }}>
            {JSON.stringify(selectedRecord, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
