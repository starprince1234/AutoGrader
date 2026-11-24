/**
 * AUTO-GENERATED FROM openapi/ops.yaml — DO NOT EDIT BY HAND.
 *
 * Regenerate with: pnpm api:generate
 * Source of truth: MAIN/contracts/** (see docs/adr/ADR-0002-contract-generated-api-client.md).
 * Any hand edit is reported as contract drift by `pnpm api:check`.
 */

export interface paths {
    "/api/v1/ops/logs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Search normalized operational logs with cursor pagination */
        get: operations["listOpsLogs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/ops/logs/trace/{trace_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Fetch chronological cross-module trace by request_id, correlation_id, or submission_id */
        get: operations["getOpsLogTrace"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/ops/logs/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Operational log overview per module, storage budget, and drop counters */
        get: operations["getOpsLogSummary"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/ops/logs/exports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Stream bounded operational logs as JSONL or CSV (audited) */
        post: operations["exportOpsLogs"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/logs/client": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Ingest bounded browser structured logs for authenticated callers */
        post: operations["ingestClientLogs"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/ops/insights": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Turn the current operational picture into a layered, plain-language status report
         * @description The operator presses one button; the server assembles the operational picture -- per-module
         *     readiness and health text, warn/error counts for the last hour and day, the log pipeline's
         *     own storage and drop counters, and the most frequent recent warn/error events -- and asks
         *     the configured model for a report a non-engineer can act on.
         *
         *     **The shape is contract, the wording is not.** Findings are sorted into four layers in
         *     descending urgency (`critical` > `warning` > `notice` > `healthy`) and every finding names
         *     exactly one module (`B1`..`B5`, `MAIN`). Each module appears **exactly once across all four
         *     layers**, so the report accounts for the whole platform: an empty `critical` layer means
         *     "nothing is critical", not "nobody looked", and a module with no evidence of a problem is
         *     returned in `healthy` rather than omitted.
         *
         *     Severity is about **impact on users**, not about the log level of the evidence: a module
         *     that cannot serve requests, loses data, or is out of storage is `critical`; an error that a
         *     user would notice but that a retry can absorb is `warning`; a trend, an approaching limit,
         *     or a single transient failure is `notice`.
         *
         *     The report is **advisory**: nothing it says changes routing, retries, or published state.
         *     Facts are redacted with the same sanitizer the log store uses before they leave the
         *     process, and a failed provider call never echoes its response body.
         */
        post: operations["generateOpsInsight"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** @enum {string} */
        LogLevel: "info" | "warn" | "error";
        /** @enum {string} */
        LogProducer: "B1" | "B2" | "B3" | "B4" | "B5" | "MAIN";
        /** @enum {string} */
        LogProcess: "host" | "worker" | "intake" | "notifier" | "browser";
        OpsLogRecord: {
            /** Format: int64 */
            log_id: number;
            timestamp: components["schemas"]["Timestamp"];
            level: components["schemas"]["LogLevel"];
            event: string;
            producer: components["schemas"]["LogProducer"];
            process: components["schemas"]["LogProcess"];
            /** Format: uuid */
            request_id?: string | null;
            /** Format: uuid */
            correlation_id?: string | null;
            /** Format: uuid */
            course_id?: string | null;
            /** Format: uuid */
            assignment_id?: string | null;
            /** Format: uuid */
            submission_id?: string | null;
            /** Format: uuid */
            evaluation_run_id?: string | null;
            /** Format: uuid */
            question_version_id?: string | null;
            /** Format: uuid */
            actor_id?: string | null;
            stage?: string | null;
            fields?: {
                [key: string]: unknown;
            };
            truncated?: boolean;
        };
        OpsLogPage: {
            items: components["schemas"]["OpsLogRecord"][];
            next_cursor: string | null;
        };
        OpsLogTrace: {
            trace_id: components["schemas"]["Uuid"];
            items: components["schemas"]["OpsLogRecord"][];
            truncated: boolean;
        };
        OpsModuleLogHealth: {
            producer: components["schemas"]["LogProducer"];
            last_log_at: components["schemas"]["Timestamp"] | null;
            warn_1h: number;
            error_1h: number;
            warn_24h: number;
            error_24h: number;
            total_24h: number;
            ready: boolean;
            silent_minutes: number | null;
        };
        OpsLogSummary: {
            generated_at: components["schemas"]["Timestamp"];
            modules: components["schemas"]["OpsModuleLogHealth"][];
            dropped_total: number;
            db_degraded: boolean;
            storage_bytes: number;
            storage_limit_bytes: number;
            retention_info_days: number;
            retention_warn_error_days: number;
        };
        OpsLogExportRequest: {
            /** @enum {string} */
            format: "jsonl" | "csv";
            from?: components["schemas"]["Timestamp"];
            to?: components["schemas"]["Timestamp"];
            producer?: components["schemas"]["LogProducer"];
            level?: components["schemas"]["LogLevel"];
            event?: string;
            request_id?: components["schemas"]["Uuid"];
            correlation_id?: components["schemas"]["Uuid"];
            submission_id?: components["schemas"]["Uuid"];
            course_id?: components["schemas"]["Uuid"];
            io_type?: string;
            q?: string;
            /** @default 2000 */
            limit: number;
        };
        ClientLogItem: {
            timestamp: components["schemas"]["Timestamp"];
            /** @enum {string} */
            level: "debug" | "info" | "warn" | "error";
            event: string;
            request_id?: string | null;
            correlation_id?: string | null;
            course_id?: string | null;
            assignment_id?: string | null;
            submission_id?: string | null;
            stage?: string | null;
        } & {
            [key: string]: unknown;
        };
        ClientLogBatchRequest: {
            lines: components["schemas"]["ClientLogItem"][];
        };
        ClientLogIngestResponse: {
            accepted: number;
            dropped: number;
        };
        /** @enum {string} */
        OpsInsightLayer: "critical" | "warning" | "notice" | "healthy";
        /**
         * @description One statement about one module. `title` and `detail` are for a non-engineer: no log ids,
         *     no stack traces, no unexplained jargon. `suggestion` is what to do next, and it is absent
         *     exactly when there is nothing to do (a healthy module has no next step).
         */
        OpsInsightFinding: {
            layer: components["schemas"]["OpsInsightLayer"];
            module: components["schemas"]["LogProducer"];
            title: string;
            detail: string;
            suggestion?: string | null;
        };
        /**
         * @description The four layers. A layer with nothing to report is an **empty array**, never `null` and
         *     never absent: the client renders "无" for it without guessing.
         */
        OpsInsightLayers: {
            critical: components["schemas"]["OpsInsightFinding"][];
            warning: components["schemas"]["OpsInsightFinding"][];
            notice: components["schemas"]["OpsInsightFinding"][];
            healthy: components["schemas"]["OpsInsightFinding"][];
        };
        OpsInsightReport: {
            /** @enum {string} */
            status: "ready" | "unavailable";
            generated_at: components["schemas"]["Timestamp"];
            model?: string | null;
            prompt_version: string;
            window_hours: number;
            summary: string;
            layers: components["schemas"]["OpsInsightLayers"];
        };
        /**
         * Format: date-time
         * @description UTC RFC 3339 with millisecond precision, e.g. 2026-09-15T08:30:00.000Z.
         */
        Timestamp: string;
        /**
         * Format: uuid
         * @description UUID v4 (RFC 9562), generated by the owning module. See MAIN/docs/api/identifiers.md.
         */
        Uuid: string;
        /**
         * @description RFC 9457 problem details. Never carries stack traces, SQL or internal paths.
         *
         *     **The status code's meaning is a hard constraint; the order between competing codes is
         *     not.** Each code keeps exactly one meaning (`governance.md` section 2.2): `401` is
         *     "not authenticated", `403` is "authenticated but not permitted", `404` is "permitted but
         *     absent", `422` is "well-formed but semantically invalid". That part is guaranteed.
         *
         *     When more than one code applies to the same request, **their relative order is not
         *     guaranteed and consumers must handle each independently.** This is stated rather than
         *     left implicit because modules genuinely differ here and both ways are defensible: B2 and
         *     B4 authenticate before validating (B2 defers parameter parsing to its service layer so
         *     that it has a single Problem Details producer), while B3 lets the framework validate
         *     first. See `CHG-0021` section 2.6 for the measurements. A client that assumed one order
         *     would break against the other module.
         *
         *     Two things *are* ordered and may be relied on:
         *
         *     * **Route existence and method matching (`404` for an unknown path, `405` for a known
         *       path with the wrong method) precede both `401` and `422`.** They are routing-layer
         *       outcomes and depend on no request content. This leaks nothing new: which methods exist
         *       on which paths is written in this contract.
         *     * **`403`, `404` (resource absent) and `409` never compete with `401`** -- they are the
         *       conclusions of an *authenticated* request.
         */
        ProblemDetails: {
            /**
             * Format: uri-reference
             * @example https://autograder.local/problems/forbidden
             */
            type: string;
            /** @example Forbidden */
            title: string;
            status: number;
            detail: string;
            /** @description Request path or trace identifier */
            instance: string;
        };
    };
    responses: {
        /** @description Missing, malformed or expired credentials. Does not reveal whether the principal exists. */
        Unauthorized: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
        /** @description Authenticated, but the role or course scope does not permit the operation. */
        Forbidden: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
        /** @description Schema-valid request rejected by a business rule. */
        UnprocessableEntity: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
    };
    parameters: {
        /** @description Correlation identifier. Generated at the entry point when absent. */
        RequestId: string;
    };
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    listOpsLogs: {
        parameters: {
            query?: {
                from?: components["schemas"]["Timestamp"];
                to?: components["schemas"]["Timestamp"];
                producer?: components["schemas"]["LogProducer"];
                level?: components["schemas"]["LogLevel"];
                event?: string;
                request_id?: components["schemas"]["Uuid"];
                correlation_id?: components["schemas"]["Uuid"];
                submission_id?: components["schemas"]["Uuid"];
                course_id?: components["schemas"]["Uuid"];
                io_type?: string;
                q?: string;
                cursor?: string;
                limit?: number;
            };
            header?: {
                /** @description Correlation identifier. Generated at the entry point when absent. */
                "X-Request-ID"?: components["parameters"]["RequestId"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Cursor page of normalized log records. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OpsLogPage"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    getOpsLogTrace: {
        parameters: {
            query?: never;
            header?: {
                /** @description Correlation identifier. Generated at the entry point when absent. */
                "X-Request-ID"?: components["parameters"]["RequestId"];
            };
            path: {
                trace_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Chronological trace timeline across modules. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OpsLogTrace"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    getOpsLogSummary: {
        parameters: {
            query?: never;
            header?: {
                /** @description Correlation identifier. Generated at the entry point when absent. */
                "X-Request-ID"?: components["parameters"]["RequestId"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Module log health and storage summary. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OpsLogSummary"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
        };
    };
    exportOpsLogs: {
        parameters: {
            query?: never;
            header?: {
                /** @description Correlation identifier. Generated at the entry point when absent. */
                "X-Request-ID"?: components["parameters"]["RequestId"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OpsLogExportRequest"];
            };
        };
        responses: {
            /** @description Exported log stream (JSONL or CSV) with filter header metadata. */
            200: {
                headers: {
                    "Content-Disposition"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/x-ndjson": string;
                    "text/csv": string;
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    ingestClientLogs: {
        parameters: {
            query?: never;
            header?: {
                /** @description Correlation identifier. Generated at the entry point when absent. */
                "X-Request-ID"?: components["parameters"]["RequestId"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ClientLogBatchRequest"];
            };
        };
        responses: {
            /** @description Accepted for asynchronous persistence. */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClientLogIngestResponse"];
                };
            };
            401: components["responses"]["Unauthorized"];
            422: components["responses"]["UnprocessableEntity"];
            /** @description Rate limit exceeded for client log ingestion. */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    generateOpsInsight: {
        parameters: {
            query?: never;
            header?: {
                /** @description Correlation identifier. Generated at the entry point when absent. */
                "X-Request-ID"?: components["parameters"]["RequestId"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /**
             * @description The report. `status: unavailable` means this deployment has no model configured, which
             *     is a deployment fact rather than an error: the layers are empty and `summary` says so.
             */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OpsInsightReport"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            /**
             * @description The configured model failed or did not finish inside the server's time budget. The
             *     report body is never echoed, so the message is a classification only.
             */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
}
