import { t } from "../../i18n";
import type { CaseArtifacts, CaseEvidence, CaseResource, FileChange } from "../../api/types";
import { StatusPill } from "../../ui/components";
import { shortenId } from "../../ui/format";

/**
 * 逐用例证据（`evaluation-report.schema.json` 的 `case_evidence[]`，含 CHG-0012 的三个新块）。
 *
 * 目标是让教师**不读 JSON** 就能回答"这个用例为什么失败"，因此每个用例是一张卡片，
 * 而不是被压成一行单元格的表格。
 *
 * 三条硬约束：
 * 1. `resource` 为 `null`（或整个字段缺失）表示**未测量**，不是 0——渲染成 "0 MB"
 *    会把"沙箱没报告峰值"说成"用了零内存"。字段级同样：每个未测的峰值单独显示"未测量"。
 * 2. `expected_sha256` 与 `expected_output_excerpt` 是受保护字段：**只有教师视图**读取与渲染。
 *    学生视图既不显示，也不由前端推导（"两者是否一致"本身就是受保护信息）。
 * 3. `explanation` 是确定性规则推导的句子（契约明确它**不是**模型输出），因此在这里标注来源，
 *    避免与 ADR-0009 的模型评语轨混为一谈。
 */

/** 字节数按人读的单位显示；`null` 与 `undefined` 一律走"未测量"，绝不当 0。 */
function formatBytes(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function seconds(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  return `${value.toFixed(2)} s`;
}

/** 度量值：有值就显示，没有就显示"未测量"（区别于 0）。 */
function Metric({ value }: { value: string | null }) {
  if (value === null) {
    return (
      <span className="muted" data-testid="metric-unmeasured">
        {t("grades.resourceFieldNotMeasured")}
      </span>
    );
  }
  return <span>{value}</span>;
}

function ResourceBlock({ resource, measured }: { resource: CaseResource | null; measured: boolean }) {
  if (!measured || resource === null) {
    return (
      <div data-testid="case-resource">
        <h4>{t("grades.resource")}</h4>
        <p className="muted" data-testid="resource-unmeasured">
          {t("grades.resourceNotMeasured")}
        </p>
        <p className="muted">{t("grades.resourceNotMeasuredHint")}</p>
      </div>
    );
  }

  const rows: { label: string; value: string | null }[] = [
    { label: t("grades.resource.memory"), value: formatBytes(resource.memory_peak_bytes) },
    { label: t("grades.resource.cpuUser"), value: seconds(resource.cpu_user_seconds) },
    { label: t("grades.resource.cpuSystem"), value: seconds(resource.cpu_system_seconds) },
    {
      label: t("grades.resource.pids"),
      value: resource.pids_peak === null || resource.pids_peak === undefined
        ? null
        : String(resource.pids_peak),
    },
    { label: t("grades.resource.wall"), value: seconds(resource.wall_seconds) },
  ];

  return (
    <div data-testid="case-resource">
      <h4>{t("grades.resource")}</h4>
      <dl className="facts">
        {rows.map((row) => (
          <div className="facts__row" key={row.label}>
            <dt>{row.label}</dt>
            <dd>
              <Metric value={row.value} />
            </dd>
          </div>
        ))}
        <div className="facts__row">
          <dt>{t("grades.resource.limitHit")}</dt>
          <dd>
            {/* 契约的 `limit_hit` 是枚举，不是给人读的文本：显示"哪个上限被触发"。 */}
            {resource.limit_hit ? (
              <StatusPill tone="bad" testId="limit-hit">
                {t(`grades.limit.${resource.limit_hit}`)}
              </StatusPill>
            ) : (
              <span className="muted" data-testid="limit-none">
                {t("grades.resource.limitNone")}
              </span>
            )}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function ChangeList({ label, files }: { label: string; files: FileChange[] | undefined }) {
  if (!files || files.length === 0) return null;
  return (
    <div>
      <h5>
        {label}（{files.length}）
      </h5>      <ul className="muted">
        {files.map((file) => (
          <li key={file.path}>
            <code>{file.path}</code>
            {file.size_bytes === null || file.size_bytes === undefined ? (
              <span className="muted"> · {t("grades.artifacts.sizeUnknown")}</span>
            ) : (
              <span className="muted">
                {" "}
                · {t("grades.artifacts.size", { bytes: file.size_bytes })}
              </span>
            )}
            {file.content_sha256 ? (
              <span className="mono-id" title={file.content_sha256}>
                {" "}
                {shortenId(file.content_sha256)}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ArtifactsBlock({
  artifacts,
  present,
  teacherView,
}: {
  artifacts: CaseArtifacts | null;
  present: boolean;
  teacherView: boolean;
}) {
  if (!present || artifacts === null) {
    return (
      <div data-testid="case-artifacts">
        <h4>{t("grades.artifacts")}</h4>
        <p className="muted" data-testid="artifacts-missing">
          {t("grades.artifactsMissing")}
        </p>
      </div>
    );
  }

  const expected = artifacts.expected_sha256 ?? null;
  const actual = artifacts.actual_sha256 ?? null;
  const nothingChanged =
    (artifacts.created?.length ?? 0) === 0 &&
    (artifacts.modified?.length ?? 0) === 0 &&
    (artifacts.deleted?.length ?? 0) === 0;

  return (
    <div data-testid="case-artifacts">
      <h4>{t("grades.artifacts")}</h4>
      <dl className="facts">
        <div className="facts__row">
          <dt>{t("grades.artifacts.actualDigest")}</dt>
          <dd>
            {actual ? (
              <span className="mono-id" data-testid="artifact-actual" title={actual}>
                {shortenId(actual)}
              </span>
            ) : (
              <span className="muted">{t("grades.resourceFieldNotMeasured")}</span>
            )}
          </dd>
        </div>
        {/* 受保护字段：学生视图既不下发也不显示，更不由前端拿实际摘要去反推"是否一致"。 */}
        {teacherView ? (
          <div className="facts__row">
            <dt>{t("grades.artifacts.expectedDigest")}</dt>
            <dd>
              {expected ? (
                <span className="inline">
                  <span className="mono-id" data-testid="artifact-expected" title={expected}>
                    {shortenId(expected)}
                  </span>
                  {actual ? (
                    <StatusPill
                      tone={expected === actual ? "ok" : "bad"}
                      testId="artifact-digest-verdict"
                    >
                      {expected === actual
                        ? t("grades.artifacts.digestMatch")
                        : t("grades.artifacts.digestMismatch")}
                    </StatusPill>
                  ) : null}
                </span>
              ) : (
                <span className="muted" data-testid="artifact-expected-absent">
                  {t("common.none")}
                </span>
              )}
            </dd>
          </div>
        ) : null}
      </dl>
      {nothingChanged ? (
        <p className="muted">{t("grades.artifacts.noChanges")}</p>
      ) : (
        <>
          <ChangeList label={t("grades.artifacts.created")} files={artifacts.created} />
          <ChangeList label={t("grades.artifacts.modified")} files={artifacts.modified} />
          <ChangeList label={t("grades.artifacts.deleted")} files={artifacts.deleted} />
        </>
      )}
    </div>
  );
}

export function CaseEvidenceCard({
  evidence,
  teacherView,
}: {
  evidence: CaseEvidence;
  teacherView: boolean;
}) {
  return (
    <article className="case-evidence" data-testid={`case-${evidence.case_id}`}>
      <header className="inline">
        <strong>
          {t("grades.caseOrdinal", { ordinal: evidence.ordinal + 1 })}
        </strong>
        <StatusPill tone={evidence.passed ? "ok" : "bad"}>
          {evidence.passed ? t("grades.casePassed") : t("grades.caseFailed")}
        </StatusPill>
        <span className="muted">
          {t("grades.caseWeight")}：{evidence.weight ?? 1}
        </span>
        <span className="muted">
          {t("grades.caseAwarded")}：{evidence.awarded ?? t("common.none")}
        </span>
        {evidence.duration_ms === null || evidence.duration_ms === undefined ? null : (
          <span className="muted">
            {t("grades.duration")}：{evidence.duration_ms}
          </span>
        )}
        {evidence.failure_category ? (
          // 枚举值放进 title：界面读中文，契约取值仍可核对。
          <StatusPill tone="warn">
            <span title={evidence.failure_category}>
              {t(`grades.category.${evidence.failure_category}`)}
            </span>
          </StatusPill>
        ) : null}
      </header>

      {/* 确定性说明：契约规定它是固定规则推导的文本，因此可以当作报告的一部分。 */}
      <div data-testid="case-explanation">
        <h4>{t("grades.explanation")}</h4>
        {evidence.explanation ? (
          <p>{evidence.explanation}</p>
        ) : (
          <p className="muted" data-testid="explanation-missing">
            {t("grades.explanationMissing")}
          </p>
        )}
        <p className="muted">{t("grades.explanationHint")}</p>
      </div>

      {evidence.message ? <p className="muted">{evidence.message}</p> : null}

      <ResourceBlock resource={evidence.resource ?? null} measured={evidence.resource !== undefined} />
      <ArtifactsBlock
        artifacts={evidence.artifacts ?? null}
        present={evidence.artifacts !== undefined}
        teacherView={teacherView}
      />

      <div>
        <h4>{t("grades.actualOutput")}</h4>
        <pre>
          <code>{evidence.actual_output_excerpt ?? ""}</code>
        </pre>
        {teacherView ? (
          <div data-testid="expected-output-block">
            <h5>{t("grades.expectedOutput")}</h5>
            {evidence.expected_output_excerpt === null ||
            evidence.expected_output_excerpt === undefined ? (
              <span className="muted">{t("common.none")}</span>
            ) : (
              <pre>
                <code data-testid="expected-output">{evidence.expected_output_excerpt}</code>
              </pre>
            )}
          </div>
        ) : null}
      </div>
    </article>
  );
}
