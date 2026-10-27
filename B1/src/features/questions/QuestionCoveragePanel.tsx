import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { QuestionTagCoverage, TagCoverageRow } from "../../api/types";
import { Banner, Panel, Spinner, StatusPill, Table } from "../../ui/components";
import { useQuestionCoverage } from "./useQuestions";

/**
 * 标签覆盖度：这门课有哪些知识点、各有多少道题、多少道还没打标签。
 *
 * 只给教师看（题库管理视角）；计数按设计可重叠——一道题可以有多个标签，所以各标签题数不能相加。
 */

/** 单个计数的展示件：值用 `data-testid` 固定，避免"哪一格显示了哪个数"只靠排版判断。 */
function CoverageCount({
  label,
  value,
  hint,
  testId,
  tone = "idle",
}: {
  label: string;
  value: number;
  hint: string;
  testId: string;
  tone?: "ok" | "warn" | "bad" | "idle";
}) {
  return (
    <div className="count-card" data-testid={testId}>
      <StatusPill tone={tone}>{label}</StatusPill>
      <p className="count-card__value" data-testid={`${testId}-value`}>
        {value}
      </p>
      <p className="muted">{hint}</p>
    </div>
  );
}

function CoverageTags({
  coverage,
  onSelectTag,
}: {
  coverage: QuestionTagCoverage;
  onSelectTag: (tag: string) => void;
}) {
  // 条形只相对**最多的那个标签**：它不承载任何"占题目总数多少"的含义。
  const maxCount = coverage.tags.reduce(
    (max, row) => Math.max(max, row.question_count),
    0,
  );

  if (coverage.tags.length === 0) {
    return (
      <Banner tone="info" testId="coverage-no-tags">
        {t("questions.coverageEmptyTags", { total: coverage.total_questions })}
      </Banner>
    );
  }

  return (
    <div data-testid="coverage-tags">
      <h3>{t("questions.coverageTags")}</h3>
      <Table<TagCoverageRow>
        testId="coverage-tag-table"
        label={t("questions.coverageTags")}
        rows={coverage.tags}
        getRowKey={(row) => row.tag}
        columns={[
          {
            key: "tag",
            header: t("questions.tags"),
            render: (row) => (
              // 每个标签都可点：契约（CHG-0022）保证 `tag` 的形状与 `listQuestions.tags` 一致。
              <button
                type="button"
                className="button button--ghost"
                data-testid={`coverage-tag-${row.tag}`}
                // 可访问名称必须说出"点了会发生什么"：光读到"unix 按钮"的读屏用户
                // 无从知道它会填筛选条件。
                aria-label={t("questions.coverageTagFilterLabel", { tag: row.tag })}
                title={t("questions.coverageTagFilter")}
                onClick={() => onSelectTag(row.tag)}
              >
                {row.tag}
              </button>
            ),
          },
          {
            key: "question_count",
            header: t("questions.coverageQuestionCount"),
            align: "right",
            render: (row) => (
              <span data-testid={`coverage-count-${row.tag}`}>{row.question_count}</span>
            ),
          },
          {
            key: "bar",
            header: t("questions.coverageBar"),
            render: (row) => (
              <span className="coverage-bar__track">
                <span
                  className="coverage-bar"
                  data-testid={`coverage-bar-${row.tag}`}
                  style={{
                    width: `${maxCount === 0 ? 0 : Math.round((row.question_count / maxCount) * 100)}%`,
                  }}
                />
              </span>
            ),
          },
        ]}
      />
      <p className="muted" data-testid="coverage-bar-note">
        {t("questions.coverageBarNote")}
      </p>
      <p className="muted" data-testid="coverage-tag-filter-note">
        {t("questions.coverageTagFilterHint")}
      </p>
    </div>
  );
}

function CoverageBody({
  coverage,
  onSelectTag,
}: {
  coverage: QuestionTagCoverage;
  onSelectTag: (tag: string) => void;
}) {
  return (
    <div className="stack" data-testid="coverage-body">
      <div className="count-grid">
        <CoverageCount
          label={t("questions.coverageTotal")}
          value={coverage.total_questions}
          hint={t("questions.coverageTotalHint")}
          testId="coverage-total"
        />
        <CoverageCount
          label={t("questions.coverageTagged")}
          value={coverage.tagged_questions}
          hint={t("questions.coverageTaggedHint")}
          testId="coverage-tagged"
        />
        <CoverageCount
          label={t("questions.coverageUntagged")}
          value={coverage.untagged_questions}
          hint={t("questions.coverageUntaggedHint")}
          testId="coverage-untagged"
          tone="warn"
        />
      </div>

      <p className="muted" data-testid="coverage-overlap-note">
        {t("questions.coverageOverlapNote")}
      </p>

      <CoverageTags coverage={coverage} onSelectTag={onSelectTag} />
    </div>
  );
}

export function QuestionCoveragePanel({
  courseId,
  publishedOnly,
  onSelectTag,
}: {
  /** 已选课程；`null` = 还没有作用域（契约的 `course_id` 是必需参数）。 */
  courseId: string | null;
  /** 与列表共用同一个 `published_only`：两个数字必须来自同一个作用域。 */
  publishedOnly: boolean;
  /** 点标签 → 复用列表既有的 `tags` 筛选（本页不另造一套筛选机制）。 */
  onSelectTag: (tag: string) => void;
}) {
  const coverage = useQuestionCoverage(courseId, publishedOnly);
  const error = coverage.error;

  return (
    <Panel title={t("questions.coverageTitle")} testId="question-coverage">
      <p className="muted">{t("questions.coverageIntro")}</p>

      {courseId === null ? (
        <Banner tone="info" testId="coverage-no-course">
          {t("questions.coverageNoCourse")}
        </Banner>
      ) : coverage.isPending ? (
        <Spinner />
      ) : isApiError(error) && error.isForbidden ? (
        <Banner tone="error" testId="coverage-forbidden">
          {t("questions.coverageForbidden")}
        </Banner>
      ) : coverage.isError ? (
        <Banner tone="error" testId="coverage-error">
          {t("questions.coverageFailed")}
        </Banner>
      ) : coverage.data ? (
        <CoverageBody coverage={coverage.data} onSelectTag={onSelectTag} />
      ) : null}
    </Panel>
  );
}
