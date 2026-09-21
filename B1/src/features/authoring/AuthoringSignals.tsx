import { useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { AuthoringAssistance, ComparisonFinding } from "../../api/types";
import { shortenId } from "../../ui/format";
import {
  Banner,
  ErrorState,
  Panel,
  Spinner,
  StatusPill,
  Table,
} from "../../ui/components";
import { useAuthoringAssistance, useQuestionDiagnostics } from "./useAuthoring";

/**
 * 出题期的两类"信号"，**刻意做成两个外表不同的面板**：
 *
 * - `ComparisonDiagnosticsPanel`：确定性的 `CMP-*` 规则命中。同一份声明永远得到同一份结论，
 *   属于题目质量记录，可以直接据此改题。
 * - `AuthoringAssistancePanel`：模型生成的**建议**。契约把它标为 `confidence: advisory`，
 *   ADR-0009 明确要求它与确定性结论可区分——因此这里用不同标题、不同说明、不同外框，
 *   而不是把两种文字混进同一张表。
 *
 * 两种"空"也必须看起来不同：`findings` 为空是"检查过、无问题"；`409`/`status=unavailable`
 * 是"未启用"（provider 没配）。前者是结论，后者是没做这件事。
 */

const SEVERITY_TONE: Record<ComparisonFinding["severity"], "bad" | "warn" | "idle"> = {
  error: "bad",
  warning: "warn",
  info: "idle",
};

export function ComparisonDiagnosticsPanel({ questionId }: { questionId: string }) {
  const diagnostics = useQuestionDiagnostics(questionId);

  return (
    <Panel title={t("authoring.diagnostics.title")} testId="comparison-diagnostics">
      <p className="muted">{t("authoring.diagnostics.hint")}</p>
      {diagnostics.isPending ? (
        <Spinner />
      ) : diagnostics.isError ? (
        <ErrorState error={diagnostics.error} />
      ) : (diagnostics.data?.findings.length ?? 0) === 0 ? (
        <Banner tone="success" testId="diagnostics-clean">
          {t("authoring.diagnostics.clean")}
        </Banner>
      ) : (
        <Table<ComparisonFinding>
          testId="diagnostics-table"
          label={t("authoring.diagnostics.title")}
          rows={diagnostics.data?.findings ?? []}
          getRowKey={(row, index) => `${row.rule_id}-${index}`}
          columns={[
            {
              key: "rule_id",
              header: t("authoring.diagnostics.rule"),
              render: (row) => <span className="mono-id">{row.rule_id}</span>,
            },
            {
              key: "severity",
              header: t("authoring.diagnostics.severity"),
              render: (row) => (
                <StatusPill tone={SEVERITY_TONE[row.severity]}>
                  {t(`authoring.diagnostics.severity.${row.severity}`)}
                </StatusPill>
              ),
            },
            {
              key: "ordinal",
              header: t("authoring.diagnostics.case"),
              render: (row) =>
                row.ordinal === null || row.ordinal === undefined ? (
                  <span className="muted">{t("common.none")}</span>
                ) : (
                  t("authoring.diagnostics.caseSpecific", { ordinal: row.ordinal + 1 })
                ),
            },
            { key: "message", header: "message", render: (row) => row.message },
          ]}
        />
      )}
    </Panel>
  );
}

/** 契约 `AuthoringAssistanceRequest.topics` 的枚举。 */
const TOPICS = [
  "quality",
  "comparison_policy",
  "wording",
  "difficulty",
  "coverage",
] as const;

type Topic = (typeof TOPICS)[number];

function SuggestionList({ assistance }: { assistance: AuthoringAssistance }) {
  return (
    <>
      {assistance.suggestions.length === 0 ? (
        <p className="muted">{t("authoring.assign.empty")}</p>
      ) : (
        <Table
          testId="assistance-suggestions"
          label={t("authoring.assign.title")}
          rows={assistance.suggestions}
          getRowKey={(_row, index) => `suggestion-${index}`}
          columns={[
            {
              key: "topic",
              header: t("authoring.assign.topic"),
              render: (row) => t(`authoring.assign.topic.${row.topic}`),
            },
            { key: "message", header: "message", render: (row) => row.message },
            {
              key: "confidence",
              header: t("authoring.assign.confidence"),
              render: (row) => (
                <StatusPill tone="idle">{row.confidence ?? t("common.none")}</StatusPill>
              ),
            },
          ]}
        />
      )}
      {assistance.provenance ? (
        <p className="muted" data-testid="assistance-provenance">
          {t("authoring.assign.provenance", {
            provider: assistance.provenance.provider,
            model: assistance.provenance.model,
            promptVersion: assistance.provenance.prompt_version,
          })}
        </p>
      ) : null}
      {/* 契约要求：`disclaimers` 非空，且必须与建议一起展示。原文照录，不改写、不折叠。 */}
      <div data-testid="assistance-disclaimers">
        <p className="muted">{t("authoring.assign.disclaimers")}</p>
        <ul className="muted">
          {assistance.disclaimers.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
      </div>
    </>
  );
}

export function AuthoringAssistancePanel({ questionId }: { questionId: string }) {
  const [topics, setTopics] = useState<Topic[]>(["quality"]);
  const [notes, setNotes] = useState("");
  const assistance = useAuthoringAssistance(questionId);

  // 未启用有两种到达方式，都是契约声明的：`409`（没有配置 provider）与
  // `200 + status=unavailable`。两者都必须显示成"未启用"，而不是"没有建议"。
  const disabledByStatus = assistance.data?.status === "unavailable";
  const disabledByError = isApiError(assistance.error) && assistance.error.isConflict;
  const notEnabled = disabledByStatus || disabledByError;

  return (
    <Panel title={t("authoring.assign.title")} testId="authoring-assistance" className="panel--advisory">
      <Banner tone="warn" testId="assistance-advisory">
        {t("authoring.assign.warning")}
      </Banner>
      <p className="muted">{t("authoring.assign.neverWrites")}</p>

      <fieldset className="case-card">
        <legend className="case-card__legend">{t("authoring.assign.topics")}</legend>
        <div className="inline">
          {TOPICS.map((topic) => (
            <label className="inline" key={topic}>
              <input
                type="checkbox"
                checked={topics.includes(topic)}
                onChange={(event) =>
                  setTopics((previous) =>
                    event.target.checked
                      ? [...previous, topic]
                      : previous.filter((item) => item !== topic),
                  )
                }
              />
              {t(`authoring.assign.topic.${topic}`)}
            </label>
          ))}
        </div>
        <label className="field">
          <span>{t("authoring.assign.notes")}</span>
          <input
            aria-label={t("authoring.assign.notes")}
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
          />
        </label>
      </fieldset>

      <div className="inline">
        <button
          type="button"
          className="button"
          data-testid="request-assistance"
          // 契约 `topics` 是 `minItems: 1`：一个都不选就不该发请求。
          disabled={topics.length === 0 || assistance.isPending}
          onClick={() =>
            assistance.mutate({ topics, notes: notes.trim() === "" ? null : notes.trim() })
          }
        >
          {assistance.isPending ? t("authoring.assign.requesting") : t("authoring.assign.request")}
        </button>
      </div>

      {notEnabled ? (
        <Banner tone="info" testId="assistance-unavailable">
          {t("authoring.assign.status.unavailable")}
          {disabledByError && isApiError(assistance.error) && assistance.error.problem?.detail ? (
            <span className="muted"> {assistance.error.problem.detail}</span>
          ) : null}
        </Banner>
      ) : assistance.isError ? (
        <ErrorState error={assistance.error} />
      ) : null}

      {assistance.data ? (
        <div className="stack" data-testid="assistance-result">
          {assistance.data.status === "failed" ? (
            <Banner tone="warn" testId="assistance-failed">
              {t("authoring.assign.status.failed")}
            </Banner>
          ) : null}
          {assistance.data.status === "ready" ? (
            <p className="muted">
              {t("authoring.assign.status.ready", {
                count: assistance.data.suggestions.length,
              })}
            </p>
          ) : null}
          <SuggestionList assistance={assistance.data} />
          <p className="muted mono-id">question_id: {shortenId(assistance.data.question_id)}</p>
        </div>
      ) : null}
    </Panel>
  );
}
