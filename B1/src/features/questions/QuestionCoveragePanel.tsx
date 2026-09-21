import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { QuestionTagCoverage, TagCoverageRow } from "../../api/types";
import {
  Banner,
  ErrorState,
  Panel,
  Spinner,
  StatusPill,
  Table,
} from "../../ui/components";
import { useQuestionCoverage } from "./useQuestions";

/**
 * 题库标签覆盖度（`getQuestionTagCoverage`，CHG-0020）。
 *
 * 它回答的是"这批题覆盖了哪些知识点、每个知识点有多少题、有多少题还没打标签"。
 * 契约把这件事的三条读法写死在 schema 的 description 里，本组件逐条落实：
 *
 * 1. **可校验的恒等式只有 `tagged_questions + untagged_questions == total_questions`**。
 *    `sum(tags[].question_count)` **不等于** `total_questions`：一道题最多可带 20 个标签，
 *    逐标签计数按设计重叠。因此页面上**不存在**任何"标签合计"，也不拿它做校验——那会渲染出
 *    一个不成立的数，而它看起来完全正常。
 * 2. `untagged_questions` **单独一格**：没有它，"没有标签"会被静默读成"没有题目"
 *    （与质量热力图里 `unmeasured_runs` 同一条纪律）。
 * 3. 标签顺序**原样**渲染（服务端已按 `question_count` 降序、同数按 `tag` 升序给出）。
 *    本组件不再排一次：再排一次就可能悄悄改变契约承诺的顺序。
 *
 * 另外两处诚实性：条形长度只在标签之间相互比较（不是"占题目总数的比例"），
 * 以及"点标签 → 列表筛选"复用列表既有的 `tags` 参数。
 *
 * **标签形态（CHG-0022，契约 `0.15.0`）**：`QuestionTagCoverage.tags[].tag` 现在声明了
 * `pattern: '^[a-z0-9][a-z0-9-]*$'`（长度 1–40），与 `listQuestions.tags` / `QuestionDraftInput.tags`
 * 完全一致。本组件此前有一段"这个标签可能不是列表筛选取值"的防御分支（当时契约对该输出字段
 * 什么都没说），它**按其自身逻辑不可达**，已随本批删除：
 * - 删它的理由不是"更短"，而是**契约现在是唯一权威**：保留一个不信任刚拿到的契约的分支，
 *   等于把"字段定义只在 contracts 里"这条规则打折；
 * - 删它**不会**制造静默失败：万一服务端真的返回违反契约的标签，列表页既有的标签校验会把它
 *   归入 `invalid` 并显示"以下标签不符合契约形状"，那一条路径本来就在。
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
      <p className="muted" data-testid="coverage-tag-order">
        {t("questions.coverageTagOrder")}
      </p>
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
  const taggedAndUntagged = coverage.tagged_questions + coverage.untagged_questions;
  const identityHolds = taggedAndUntagged === coverage.total_questions;

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

      {/* 唯一可校验的恒等式。不自洽时**如实报告**，不挑一个数当真、也不悄悄改成一致。 */}
      <p
        className={identityHolds ? "muted" : "identity-broken"}
        data-testid="coverage-identity"
      >
        {identityHolds
          ? t("questions.coverageIdentityOk", {
              tagged: coverage.tagged_questions,
              untagged: coverage.untagged_questions,
              total: coverage.total_questions,
            })
          : t("questions.coverageIdentityBroken", {
              tagged: coverage.tagged_questions,
              untagged: coverage.untagged_questions,
              sum: taggedAndUntagged,
              total: coverage.total_questions,
            })}
      </p>

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

      {/* 这里**没有**"本页只是对着契约构建的"之类常驻横幅。
          它曾经有过一条（"B3 尚未实现该端点"），而那条**由静态文案断言另一个模块的实现进度**：
          端点一旦上线，横幅就会与它旁边的实时数字直接矛盾，而且没有任何机制会让它过期。
          "对方实现到哪一步"不是界面能知道、也不是界面该说的话；端点不可用时由下面的
          数据驱动分支按**服务端实际返回**说明本部署的响应。证据边界写在文档里（见 phase-05）。 */}

      {courseId === null ? (
        <Banner tone="info" testId="coverage-no-course">
          {t("questions.coverageNoCourse")}
        </Banner>
      ) : coverage.isPending ? (
        <Spinner />
      ) : isApiError(error) && error.isForbidden ? (
        // 403 是服务端的授权结论：原样呈现它的 detail，**不**渲染成空态。
        // 把它读成"这个课程没有题目"会让一次越权拒绝看起来像一份数据。
        <Banner tone="error" testId="coverage-forbidden">
          {t("questions.coverageForbidden", {
            detail: error.problem?.detail ?? t("common.forbiddenAction"),
          })}
        </Banner>
      ) : isApiError(error) && error.isNotFound ? (
        // 契约**没有**为这个端点声明 404（路径里也没有资源标识符），所以这里的 404 只描述
        // **本部署的响应**：这条路由取不到。它是"数据驱动"的分支——只有服务端真的这么说时才出现，
        // 且措辞不涉及任何模块的实现进度。
        <Banner tone="info" testId="coverage-endpoint-absent">
          {t("questions.coverageEndpointAbsent")}
        </Banner>
      ) : coverage.isError ? (
        <ErrorState error={error} testId="coverage-error" />
      ) : coverage.data ? (
        <CoverageBody coverage={coverage.data} onSelectTag={onSelectTag} />
      ) : null}
    </Panel>
  );
}
