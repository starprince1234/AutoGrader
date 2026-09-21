import { useState } from "react";
import { t } from "../../i18n";
import { useSession } from "../../auth/sessionContext";
import { appealPackView } from "../../auth/visibility";
import { Banner, ErrorState, Panel, Spinner } from "../../ui/components";
import { formatDateTime, shortenId } from "../../ui/format";
import { downloadAppealPack, useAppealPack } from "./useAppealPack";

/**
 * 申诉证据包（CHG-0014）。
 *
 * 为什么它必须是一个独立文档而不是"报告端点加个下载头"：成绩争议要由**没登录本部署**的
 * 复核者仅凭文件回答，因此证据包自带报告、五个绑定摘要、运行策略摘要与重现说明，并由
 * `result_digest` 钉住。两处强制删减（宿主机路径、学生视图不得因导出而变宽）正是它需要
 * 逐条列出的原因，所以 `redactions` 在这里**原文列出**，不做归纳。
 *
 * 加载是**按需**的：证据包内嵌完整报告，进页面就拉会把每次下钻都变成一次大响应。
 */

function KeyValueList({ record }: { record: Record<string, unknown> }) {
  const entries = Object.entries(record);
  if (entries.length === 0) return <p className="muted">{t("common.none")}</p>;
  return (
    <ul className="muted">
      {entries.map(([key, value]) => (
        <li key={key}>
          {key}: {typeof value === "string" ? value : JSON.stringify(value)}
        </li>
      ))}
    </ul>
  );
}

export function AppealPackPanel({ runId }: { runId: string }) {
  const session = useSession();
  const [requested, setRequested] = useState(false);
  const [downloaded, setDownloaded] = useState<string | null>(null);
  // 契约的 `view` 默认值是 `teacher`：角色未知时不猜，而是不发送任何请求。
  const view = appealPackView(session);
  const appeal = useAppealPack(runId, view, requested);
  const data = appeal.data;

  return (
    <Panel title={t("appeal.title")} testId="appeal-pack">
      <p className="muted">{t("appeal.intro")}</p>

      {view === null ? (
        <Banner tone="warn" testId="appeal-view-unknown">
          {t("appeal.viewUnknown")}
        </Banner>
      ) : (
        <p className="muted" data-testid="appeal-view">
          {t("appeal.viewExplicit", { view })}
          {view === "student" ? t("appeal.viewStudent") : t("appeal.viewTeacher")}
        </p>
      )}

      <div className="inline">
        <button
          type="button"
          className="button"
          data-testid="load-appeal-pack"
          disabled={view === null || appeal.isFetching}
          onClick={() => setRequested(true)}
        >
          {appeal.isFetching ? t("common.loading") : t("appeal.open")}
        </button>
      </div>

      {appeal.isPending && requested && view !== null ? <Spinner /> : null}
      {appeal.isError ? <ErrorState error={appeal.error} testId="appeal-error" /> : null}

      {data ? (
        <div className="stack" data-testid="appeal-pack-content">
          <ul className="muted">
            <li>
              {t("appeal.runId")}: {data.pack.evaluation_run_id}
            </li>
            <li>
              {t("appeal.generatedAt")}:
              {formatDateTime(data.pack.generated_at, t("common.none"))}
            </li>
            <li data-testid="appeal-result-digest">
              {t("appeal.resultDigest")}: <span className="mono-id">{data.pack.result_digest}</span>
            </li>
            <li>
              {t("appeal.reportCases")}: {data.pack.report.case_evidence.length}
            </li>
          </ul>
          <p className="muted">{t("appeal.digestHint")}</p>

          <div className="inline">
            <button
              type="button"
              className="button"
              data-testid="download-appeal-pack"
              onClick={() => {
                const result = downloadAppealPack({
                  runId,
                  blob: data.blob,
                  requestId: data.requestId,
                });
                setDownloaded(
                  t("appeal.downloaded", {
                    filename: result.filename,
                    bytes: result.bytes,
                  }),
                );
              }}
            >
              {t("appeal.download")}
            </button>
            {downloaded ? (
              <span className="muted" data-testid="appeal-downloaded">
                {downloaded}
              </span>
            ) : null}
          </div>
          <p className="muted">{t("appeal.downloadVerbatim")}</p>

          <div data-testid="appeal-redactions">
            <h3>{t("appeal.redactions")}</h3>
            <p className="muted">{t("appeal.redactionsHint")}</p>
            <ul>
              {data.pack.redactions.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <p className="muted">{t("appeal.hostPathNote")}</p>
          </div>

          <div data-testid="appeal-binding">
            <h3>{t("appeal.binding")}</h3>
            <p className="muted">{t("appeal.bindingHint")}</p>
            <ul className="muted">
              <li>submission_digest: {data.pack.binding.submission_digest}</li>
              <li>
                {t("appeal.questionVersionBinding")}:{" "}
                {data.pack.binding.question_version_binding ?? t("common.none")}
              </li>
              <li>package_digest: {data.pack.binding.package_digest}</li>
              <li>runner_policy_digest: {data.pack.binding.runner_policy_digest}</li>
              <li>environment_digest: {data.pack.binding.environment_digest}</li>
            </ul>
          </div>

          <div data-testid="appeal-replay">
            <h3>{t("appeal.replay")}</h3>
            <p>{data.pack.replay}</p>
          </div>

          <div data-testid="appeal-policy-summary">
            <h3>{t("appeal.policySummary")}</h3>
            <p className="muted">{t("appeal.policySummaryHint")}</p>
            <KeyValueList record={data.pack.runner_policy_summary ?? {}} />
          </div>

          <p className="muted" data-testid="appeal-report-hint">
            {t("appeal.reportHint")}
            {t("appeal.report")}：{shortenId(data.pack.report.evaluation_run_id)}
          </p>
        </div>
      ) : null}
    </Panel>
  );
}
