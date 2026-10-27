import { useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { AuthoringAssistance, ComparisonFinding } from "../../api/types";
import { Banner, Panel, Spinner, StatusPill, Table } from "../../ui/components";
import { useAuthoringAssistance, useQuestionDiagnostics } from "./useAuthoring";

/**
 * 出题期的两个面板，刻意分开：
 *
 * - 「题目自查」：按固定规则检查判分设置，同一份内容永远得到同一份结论，据此改题即可；
 * - 「让 AI 看看这道题」：模型给的建议，仅供参考，采纳与否由教师决定。
 *
 * 两者都不改题，也不会替教师做决定。
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
        <Banner tone="error" testId="diagnostics-error">
          {t("authoring.diagnostics.failed")}
        </Banner>
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
                  <span className="muted">{t("authoring.diagnostics.wholeQuestion")}</span>
                ) : (
                  t("authoring.diagnostics.caseSpecific", { ordinal: row.ordinal + 1 })
                ),
            },
            { key: "message", header: t("authoring.diagnostics.finding"), render: (row) => row.message },
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
            { key: "message", header: t("authoring.assign.suggestion"), render: (row) => row.message },
          ]}
        />
      )}
      {assistance.provenance ? (
        <details data-testid="assistance-provenance">
          <summary>{t("authoring.assign.provenanceLabel")}</summary>
          <ul className="muted">
            <li>{t("authoring.assign.provenanceModel", { model: assistance.provenance.model })}</li>
          </ul>
        </details>
      ) : null}
      {/* 契约要求 `disclaimers` 非空并与建议一起展示：原文照录，不改写、不折叠。 */}
      <ul className="muted" data-testid="assistance-disclaimers">
        {assistance.disclaimers.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
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
      <p className="muted">{t("authoring.assign.warning")}</p>

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
          // 至少选一个方面才发请求（服务端也要求至少一项）。
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
        </Banner>
      ) : assistance.isError ? (
        <Banner tone="error" testId="assistance-error">
          {t("authoring.assign.requestFailed")}
        </Banner>
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
        </div>
      ) : null}
    </Panel>
  );
}
