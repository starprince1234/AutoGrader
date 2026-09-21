import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type {
  QuestionVersionPublic,
  ReferenceVerification,
  ReferenceVerificationCase,
} from "../../api/types";
import { formatDateTime, shortenId } from "../../ui/format";
import { Banner, ErrorState, Panel, Spinner, Table } from "../../ui/components";
import { usePublishQuestionVersion, useVerifyReferenceAnswer } from "./useAuthoring";

/**
 * 「验证并发布」面板（`CHG-0019` / `ADR-0015`）。
 *
 * 这条死结原本是 B1 上报的：`CHG-0008` 的发布门禁是真的（必须有与 `content_digest` 绑定的
 * 参考解答验证存证），而存证的前提是"执行者 ≠ 记录者"——B1 既不能自证，也不能持有 `serviceToken`。
 * `ADR-0015` 的裁决是 **B2 执行、B3 记录、教师一键触发**。因此这里的按钮语义是
 * "**请求平台验证这道题**"，不是"我确认这道题是对的"。
 *
 * 本面板刻意**不做任何自动推断与自动重试**，逐条对应契约：
 *
 * 1. **两步，不自动串起来**：`验证`与`发布`是两个显式动作。把"验证通过就顺手发布"合起来，
 *    会把"平台替我跑过参考解"与"我决定让它上线"压成一次点击，而后者是人的决定。
 * 2. **可发布 = 全通过 ∧ 存证已记录 ∧ 摘要未变**。契约写明 `attestation_id` 为 `null` 表示
 *    **B3 没有记录**（观察到的 `cases` 仍然返回）——此时即使全通过也不能发布，本面板不会把
 *    "跑通"渲染成"可以上线"。
 * 3. `infrastructure_error` / `503` 是**环境**结论（本机 Windows 上必然如此，`ADR-0004` 禁止降级
 *    为裸进程），必须显示成"验证需要 Linux 沙箱环境"，既不写成"验证失败"，也不伪装成功。
 * 4. 失败时只呈现契约给的粒度：逐用例的 `ordinal` / `passed` / `failure_category`。
 *    响应里没有期望输出、隐藏用例输入与参考解源码，本面板也不提供任何"看详情"的入口。
 */

/** 所有用例都没通过且失败类别全是基础设施错误 → 这是**环境**结论，不是题目结论。 */
function isEnvironmentOnly(verification: ReferenceVerification): boolean {
  const failed = verification.cases.filter((row) => !row.passed);
  if (failed.length === 0) return false;
  if (verification.cases_passed === verification.cases_total) return false;
  return failed.every((row) => row.failure_category === "infrastructure_error");
}

/** 摘要一致才谈得上"这份结论属于当前版本"。任一侧拿不到摘要时不猜（返回 `null`）。 */
function digestState(
  verification: ReferenceVerification,
  version: QuestionVersionPublic,
): "same" | "stale" | "unknown" {
  const current = version.content_digest;
  if (current === undefined || current === "") return "unknown";
  return current === verification.content_digest ? "same" : "stale";
}

function CaseTable({ cases }: { cases: ReferenceVerificationCase[] }) {
  return (
    <Table<ReferenceVerificationCase>
      testId="reference-verification-cases"
      label={t("authoring.verify.cases")}
      rows={cases}
      getRowKey={(row) => String(row.ordinal)}
      columns={[
        {
          key: "ordinal",
          header: t("authoring.verify.ordinal"),
          render: (row) => row.ordinal,
        },
        {
          key: "outcome",
          header: t("authoring.verify.outcome"),
          render: (row) => (
            <span data-testid={`reference-case-outcome-${row.ordinal}`}>
              {row.passed ? t("authoring.verify.passed") : t("authoring.verify.notPassed")}
            </span>
          ),
        },
        {
          key: "failure_category",
          header: t("authoring.verify.failureCategory"),
          render: (row) =>
            row.failure_category === null ? (
              // `null` 恰好等于 `passed: true`（契约原话）。这里仍显式渲染成"无"，不让空格被读成缺失。
              <span className="muted">{t("common.none")}</span>
            ) : (
              <span
                className="pill pill--warn"
                title={row.failure_category}
                data-testid={`reference-case-category-${row.ordinal}`}
              >
                {t(`grades.category.${row.failure_category}`)}
              </span>
            ),
        },
      ]}
    />
  );
}

export function ReferenceVerificationPanel({
  version,
  questionId,
}: {
  version: QuestionVersionPublic;
  /** 发布成功后要失效题目缓存（`versions[]` 里的状态也变了）。 */
  questionId: string | null;
}) {
  // 已发布的版本已经过了那道门：不再提供任何动作（这是门的语义，不是隐藏失败）。
  // 这一层刻意**不调用任何 hook**，因此这个提前返回不涉及 hooks 规则。
  if (version.status === "published") return null;

  // `key` 让"换一个版本"变成一次**重新挂载**：上一次的验证结论与发布结果随之消失。
  // 用 `key` 而不是在 effect 里 reset，是因为"结论只属于它验证的那个版本"是**结构**性质——
  // 靠 effect 清理会在两者之间留下一帧（那一帧里 v3 的"通过"看起来像这一版的结论）。
  return (
    <VerificationBody
      key={version.question_version_id}
      version={version}
      questionId={questionId}
    />
  );
}

function VerificationBody({
  version,
  questionId,
}: {
  version: QuestionVersionPublic;
  questionId: string | null;
}) {
  const verification = useVerifyReferenceAnswer();
  const publish = usePublishQuestionVersion();
  const versionId = version.question_version_id;

  const data = verification.data;
  const digest = data ? digestState(data, version) : null;
  const environmentOnly = data ? isEnvironmentOnly(data) : false;
  const allPassed = data ? data.cases_passed === data.cases_total : false;
  const attested = data ? data.attestation_id !== null : false;
  // 三个条件缺一不可；`digest === "unknown"` 时**不**当作通过（拿不到摘要就无法断言它属于当前版本）。
  const publishable = Boolean(data) && allPassed && attested && digest === "same";
  const verificationError = verification.error;

  return (
    <Panel
      title={t("authoring.verify.title")}
      testId="reference-verification"
      className="panel--gated"
    >
      <p className="muted">{t("authoring.verify.intro")}</p>

      {/* 证据边界与"这个端点不给什么"必须**总是**可见：它描述的正是"还没验证过"的时刻。 */}
      <p className="muted" data-testid="reference-no-protected-content">
        {t("authoring.verify.noProtected")}
      </p>
      <p className="muted" data-testid="reference-gate">
        {t("authoring.verify.gate")}
      </p>

      <p className="muted">
        {t("authoring.contentDigest", {
          digest: version.content_digest ?? t("common.none"),
        })}
      </p>
      {version.content_digest ? (
        <p className="muted mono-id" data-testid="reference-version-digest">
          {version.content_digest}
        </p>
      ) : null}

      <div className="inline">
        <button
          type="button"
          className="button"
          data-testid="reference-verify"
          disabled={verification.isPending}
          onClick={() => {
            // 每次点击都是一次新的沙箱作业：先清掉上一次的结论，页面不会同时显示两份。
            publish.reset();
            verification.mutate({ question_version_id: versionId });
          }}
        >
          {verification.isPending
            ? t("authoring.verify.verifying")
            : t("authoring.verify.request")}
        </button>
        {data ? (
          <button
            type="button"
            className="button button--ghost"
            data-testid="reference-verify-again"
            disabled={verification.isPending}
            onClick={() => {
              publish.reset();
              verification.mutate({ question_version_id: versionId });
            }}
          >
            {t("authoring.verify.again")}
          </button>
        ) : null}
      </div>
      <p className="muted">{t("authoring.verify.requestHint")}</p>

      {verification.isPending ? <Spinner label={t("authoring.verify.verifying")} /> : null}

      {verificationError !== null && verificationError !== undefined ? (
        isApiError(verificationError) && verificationError.isForbidden ? (
          // 403 是服务端的授权结论（该题目的课程写权限）：原样带出 detail，不当成"没验证过"。
          <Banner tone="error" testId="reference-verify-forbidden">
            {t("authoring.verify.forbidden", {
              detail: verificationError.problem?.detail ?? t("common.forbiddenAction"),
            })}
          </Banner>
        ) : isApiError(verificationError) && verificationError.status === 503 ? (
          // 契约：依赖不可用的平台返回 503 且不记录任何东西。
          // **不**从状态码推断是哪一类依赖（它同时覆盖"本平台没有沙箱"与"某个上游调用失败"，
          // 两者从 503 上无法区分）——标题保持中性，原因**照原样**由 `detail` 呈现。
          <Banner tone="warn" testId="reference-verify-environment">
            <p>{t("authoring.verify.environment")}</p>
            <p className="muted" data-testid="reference-verify-environment-detail">
              {t("authoring.verify.environmentDetail", {
                detail: verificationError.problem?.detail ?? t("common.none"),
              })}
            </p>
            <p className="muted">{t("authoring.verify.environmentCause")}</p>
          </Banner>
        ) : isApiError(verificationError) && verificationError.isNotFound ? (
          // 404 在这里有两种无法区分的含义（该版本不存在 / 本部署没有提供这条路由），
          // 因此如实列出两种读法，不替服务端选一个，也不断言任何模块的实现进度。
          <Banner tone="info" testId="reference-verify-notfound">
            {t("authoring.verify.notFound")}
          </Banner>
        ) : (
          <ErrorState error={verificationError} testId="reference-verify-error" />
        )
      ) : null}

      {data ? (
        <div className="stack" data-testid="reference-verification-result">
          <Banner
            tone={allPassed ? (attested ? "success" : "warn") : "info"}
            testId="reference-verification-summary"
          >
            <p data-testid="reference-summary-counts">
              {t("authoring.verify.counts", {
                passed: data.cases_passed,
                total: data.cases_total,
              })}
            </p>
            <p className="muted">
              {t("authoring.verify.verifiedAt", {
                time: formatDateTime(data.verified_at, t("common.none")),
              })}
              {" · "}
              {t("authoring.verify.adapter", {
                language: data.language,
                adapter: data.adapter_kind,
              })}
            </p>
          </Banner>

          {!allPassed && environmentOnly ? (
            // 全部失败都是基础设施错误：这是**平台**结论（依赖不可用），不是题目结论。
            // 标题同样保持中性：`infrastructure_error` 也不足以断定缺的是哪一类依赖。
            <Banner tone="warn" testId="reference-verify-environment">
              <p>{t("authoring.verify.environment")}</p>
              <p className="muted">{t("authoring.verify.environmentFromCases")}</p>
              <p className="muted">{t("authoring.verify.environmentCause")}</p>
            </Banner>
          ) : null}

          {!allPassed && !environmentOnly ? (
            <Banner tone="warn" testId="reference-verify-not-passed">
              <p>{t("authoring.verify.notPassedSummary", { cases: data.cases_total - data.cases_passed })}</p>
              <p className="muted">{t("authoring.verify.notPassedMeaning")}</p>
            </Banner>
          ) : null}

          {allPassed && !attested ? (
            <Banner tone="warn" testId="reference-verify-unrecorded">
              {t("authoring.verify.unrecorded")}
            </Banner>
          ) : null}

          {allPassed && attested && digest === "stale" ? (
            <Banner tone="warn" testId="reference-verify-stale">
              {t("authoring.verify.stale")}
            </Banner>
          ) : null}

          {digest === "unknown" ? (
            <Banner tone="info" testId="reference-verify-digest-unknown">
              {t("authoring.verify.digestUnknown")}
            </Banner>
          ) : null}

          {data.cases.length > 0 ? (
            <>
              <h3>{t("authoring.verify.cases")}</h3>
              <CaseTable cases={data.cases} />
              <p className="muted" data-testid="reference-cases-meaning">
                {t("authoring.verify.casesHint")}
              </p>
            </>
          ) : (
            <Banner tone="info" testId="reference-cases-empty">
              {t("authoring.verify.casesEmpty")}
            </Banner>
          )}

          <p className="muted" data-testid="reference-attestation">
            {attested
              ? t("authoring.verify.attestation", {
                  id: shortenId(data.attestation_id as string),
                })
              : t("authoring.verify.attestationMissing")}
          </p>

          {publishable ? (
            <div className="inline">
              <button
                type="button"
                className="button"
                data-testid="reference-publish"
                disabled={publish.isPending}
                onClick={() => {
                  if (questionId === null) return;
                  publish.mutate({ questionId, questionVersionId: versionId });
                }}
              >
                {publish.isPending ? t("authoring.verify.publishing") : t("authoring.verify.publish")}
              </button>
              <span className="muted">{t("authoring.verify.publishHint")}</span>
            </div>
          ) : (
            <p className="muted" data-testid="reference-publish-blocked">
              {t("authoring.verify.publishBlocked")}
            </p>
          )}
        </div>
      ) : null}

      {publish.isError ? (
        // 服务端拒绝（例如 422：门禁不认这份存证）→ 原样展示，不由前端改写成"成功"。
        <ErrorState error={publish.error} testId="reference-publish-error" />
      ) : null}
      {publish.data ? (
        <Banner tone="success" testId="reference-publish-done">
          {t("authoring.verify.publishDone", {
            version: publish.data.version,
            status: t(`questions.status.${publish.data.status}`),
          })}
        </Banner>
      ) : null}

      <p className="muted" data-testid="reference-no-auto">
        {t("authoring.verify.noAuto")}
      </p>
    </Panel>
  );
}
