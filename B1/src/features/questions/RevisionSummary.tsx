import { t } from "../../i18n";
import type { RevisionDiff } from "../../api/types";
import { shortenId } from "../../ui/format";

/**
 * 版本说明与结构差异（`QuestionVersionPublic.revision_note` / `revision_diff`，CHG-0013）。
 *
 * 契约对 `revision_diff` 的承诺是"只有计数、摘要与存在性标志"：它**不含任何受保护内容**，
 * 因此可以在版本面板上直接渲染。这里只渲染服务端真的给了值的条目——缺字段时留白，
 * 不用 0 或"无变化"填充：`null` 表示"没这一项"，不是"没有变化"。
 */

function Pair({ before, after }: { before: number | null | undefined; after: number | null | undefined }) {
  if (before === null || before === undefined || after === null || after === undefined) return null;
  return (
    <span>
      {t("questions.revisionDiff.before")} {before} → {t("questions.revisionDiff.after")} {after}
    </span>
  );
}

function Flag({ value }: { value: boolean | null | undefined }) {
  if (value === null || value === undefined) return null;
  return <span>{value ? t("questions.revisionDiff.yes") : t("questions.revisionDiff.no")}</span>;
}

function DigestPair({
  before,
  after,
}: {
  before: string | null | undefined;
  after: string | null | undefined;
}) {
  // 只是摘要，不是答案本身：契约明确它属于可公开的结构差异。
  if (!before && !after) return null;
  return (
    <span className="inline">
      {before ? (
        <span className="mono-id" title={before}>
          {shortenId(before)}
        </span>
      ) : (
        <span className="muted">{t("common.none")}</span>
      )}
      {" → "}
      {after ? (
        <span className="mono-id" title={after}>
          {shortenId(after)}
        </span>
      ) : (
        <span className="muted">{t("common.none")}</span>
      )}
    </span>
  );
}

export function RevisionSummary({
  revisionNote,
  revisionDiff,
  isFirstVersion,
}: {
  revisionNote: string | null | undefined;
  revisionDiff: RevisionDiff | null | undefined;
  /** 首个版本没有可比对象（`revision_diff` 为 null 是正常的，不是缺数据）。 */
  isFirstVersion: boolean;
}) {
  return (
    <div className="stack" data-testid="revision-summary">
      <div>
        <h3>{t("questions.revisionNote")}</h3>
        {revisionNote ? (
          <p data-testid="revision-note">{revisionNote}</p>
        ) : (
          <p className="muted">{t("common.none")}</p>
        )}
      </div>

      <div>
        <h3>{t("questions.revisionDiff")}</h3>
        {revisionDiff ? (
          <dl className="facts">
            <div className="facts__row">
              <dt>{t("questions.revisionDiff.caseCount")}</dt>
              <dd>
                <Pair before={revisionDiff.case_count_before} after={revisionDiff.case_count_after} />
              </dd>
            </div>
            <div className="facts__row">
              <dt>{t("questions.revisionDiff.stemLength")}</dt>
              <dd>
                <Pair
                  before={revisionDiff.stem_length_before}
                  after={revisionDiff.stem_length_after}
                />
              </dd>
            </div>
            <div className="facts__row">
              <dt>{t("questions.revisionDiff.caseSet")}</dt>
              <dd>
                <Flag value={revisionDiff.case_set_changed} />
              </dd>
            </div>
            <div className="facts__row">
              <dt>{t("questions.revisionDiff.weights")}</dt>
              <dd>
                <Flag value={revisionDiff.weights_changed} />
              </dd>
            </div>
            <div className="facts__row">
              <dt>{t("questions.revisionDiff.fixtures")}</dt>
              <dd>
                <Flag value={revisionDiff.fixtures_changed} />
              </dd>
            </div>
            <div className="facts__row">
              <dt>{t("questions.revisionDiff.adapterKind")}</dt>
              <dd>
                <Flag value={revisionDiff.adapter_kind_changed} />
              </dd>
            </div>
            <div className="facts__row">
              <dt>{t("questions.revisionDiff.sizeLimits")}</dt>
              <dd>
                <Flag value={revisionDiff.size_limits_changed} />
              </dd>
            </div>
            <div className="facts__row">
              <dt>{t("questions.revisionDiff.answerDigest")}</dt>
              <dd>
                <DigestPair
                  before={revisionDiff.reference_answer_digest_before}
                  after={revisionDiff.reference_answer_digest_after}
                />
              </dd>
            </div>
          </dl>
        ) : (
          <p className="muted">
            {isFirstVersion
              ? t("questions.revisionDiff.firstVersion")
              : t("questions.revisionDiff.absent")}
          </p>
        )}
      </div>
    </div>
  );
}
