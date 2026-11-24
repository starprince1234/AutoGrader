import { apiRequest, apiUrl, newIdempotencyKey } from "../../api/client";
import { getAccessToken } from "../../auth/sessionStore";
import type { components } from "../../api/generated/ops";

export type OpsLogRecord = components["schemas"]["OpsLogRecord"];
export type OpsLogPage = components["schemas"]["OpsLogPage"];
export type OpsLogTrace = components["schemas"]["OpsLogTrace"];
export type OpsLogSummary = components["schemas"]["OpsLogSummary"];
export type OpsInsightLayer = components["schemas"]["OpsInsightLayer"];
export type OpsInsightFinding = components["schemas"]["OpsInsightFinding"];
export type OpsInsightReport = components["schemas"]["OpsInsightReport"];
export type OpsLogExportRequest = components["schemas"]["OpsLogExportRequest"];
export type LogProducer = components["schemas"]["LogProducer"];
export type LogLevel = components["schemas"]["LogLevel"];
export type AuditRecord = {
  audit_id: string;
  occurred_at: string;
  actor_user_id: string | null;
  action: string;
  outcome: string;
  target_type: string;
  target_id: string | null;
  course_id: string | null;
  section_id: string | null;
  request_id: string | null;
  correlation_id: string | null;
  change_digest: string | null;
  detail: string;
};
export type AuditRecordPage = {
  items: AuditRecord[];
  next_cursor: string | null;
};

export type OpsLogsSearchParams = {
  from?: string;
  to?: string;
  producer?: LogProducer;
  level?: LogLevel;
  event?: string;
  request_id?: string;
  correlation_id?: string;
  submission_id?: string;
  course_id?: string;
  io_type?: string;
  q?: string;
  cursor?: string;
  limit?: number;
};

export async function fetchOpsLogSummary(): Promise<OpsLogSummary> {
  return apiRequest<OpsLogSummary>("/api/v1/ops/logs/summary", {
    requestId: newIdempotencyKey(),
  });
}

/**
 * One button in the log center: the server reads the operational picture and asks the model for
 * a layered, plain-language report. POST because it spends money on a model call, not because it
 * changes anything -- the report is advice.
 */
export async function fetchOpsInsight(): Promise<OpsInsightReport> {
  return apiRequest<OpsInsightReport>("/api/v1/ops/insights", {
    method: "POST",
    body: {},
    requestId: newIdempotencyKey(),
  });
}

export async function fetchOpsLogs(params: OpsLogsSearchParams): Promise<OpsLogPage> {
  return apiRequest<OpsLogPage>("/api/v1/ops/logs", {
    query: params,
    requestId: newIdempotencyKey(),
  });
}

export async function fetchOpsLogTrace(traceId: string): Promise<OpsLogTrace> {
  return apiRequest<OpsLogTrace>(`/api/v1/ops/logs/trace/${encodeURIComponent(traceId)}`, {
    requestId: newIdempotencyKey(),
  });
}

export async function fetchAuditRecords(cursor?: string): Promise<AuditRecordPage> {
  return apiRequest<AuditRecordPage>("/api/v1/audit-records", {
    query: { cursor, limit: 50 },
    requestId: newIdempotencyKey(),
  });
}

export async function downloadOpsLogExport(body: OpsLogExportRequest): Promise<void> {
  const token = getAccessToken();
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "X-Request-ID": newIdempotencyKey(),
  };
  if (token) headers.authorization = `Bearer ${token}`;

  const response = await fetch(apiUrl("/api/v1/ops/logs/exports"), {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Export failed (${response.status})`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = body.format === "csv" ? "ops-logs.csv" : "ops-logs.jsonl";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
