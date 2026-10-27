import { t } from "../../i18n";
import type { RevisionDiff } from "../../api/types";

/**
 * 版本说明与"和上一版比改了什么"。
 *
 * 只渲染服务端真的给了值的条目：缺项就留白，不用 0 或"无变化"填充。界面上不出现任何摘要、
 * 编号或内部字段名——教师要看的是用例多了几个、题干长度变了没有这类能判断的事。
 */

function Pair({ before, after }: { before: number | null | undefined; after: number | null | undefined }) {
  if (before === null || before === undefined || after === null || after === undefined) return null;
  return (
    <span>
      {before} → {after}
    </span>
  );
}

function Flag({ value }: { value: boolean | null | undefined }) {
  if (value === null || value === undefined) return null;
  return <span>{value ? t("questions.revisionDiff.yes") : t("questions.revisionDiff.no")}</span>;
}

export function RevisionSummary({
  revisionNote,
  revisionDiff,
  isFirstVersion,
}: {
  revisionNote: string | null | undefined;
  revisionDiff: RevisionDiff | null | undefined;
  /** 第一个版本没有可比对象，这不是缺数据。 */
  isFirstVersion: boolean;
}) {
  return (
    <div className="stack" data-testid="revision-summary">
      <div>
        <h3>{t("questions.revisionNote")}</h3>
        {revisionNote ? (
          <p data-testid="revision-note">{revisionNote}</p>
        ) : (
          <p className="muted">—</p>
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
