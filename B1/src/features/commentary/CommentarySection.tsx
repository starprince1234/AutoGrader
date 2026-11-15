import { t, type MessageKey } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { CommentaryStatus } from "../../api/types";
import { useSession } from "../../auth/sessionContext";
import { canGenerateCommentary } from "../../auth/visibility";
import { Banner, ErrorState, Panel, Spinner } from "../../ui/components";
import { useEvaluationCommentary, useGenerateCommentary } from "./useCommentary";

const STATUS_BANNER: Record<
  CommentaryStatus,
  { tone: "info" | "warn"; testId: string; message: MessageKey } | null
> = {
  unavailable: {
    tone: "info",
    testId: "commentary-unavailable",
    message: "commentary.statusUnavailable",
  },
  pending: { tone: "info", testId: "commentary-pending", message: "commentary.statusPending" },
  failed: { tone: "warn", testId: "commentary-failed", message: "commentary.statusFailed" },
  ready: null,
};

function parseCommentarySections(text: string): { title: string; body: string }[] {
  const sections: { title: string; body: string }[] = [];
  const pattern = /【([^】]+)】([\s\S]*?)(?=【[^】]+】|$)/g;
  for (const match of text.matchAll(pattern)) {
    const title = match[1]?.trim() ?? "";
    const body = match[2]?.trim() ?? "";
    if (title !== "" || body !== "") {
      sections.push({ title, body });
    }
  }
  return sections;
}

function splitBullets(body: string): string[] | null {
  const trimmed = body.trim();
  if (!trimmed.startsWith("- ") && !/\n\s*-\s+/.test(trimmed)) {
    return null;
  }
  const items = trimmed
    .split(/(?:^|\n\s*|\s+)-\s+/)
    .map((item) => item.trim())
    .filter((item) => item !== "");
  return items.length > 0 ? items : null;
}

function renderCommentaryContent(text: string) {
  const sections = parseCommentarySections(text);
  if (sections.length === 0) {
    return (
      <p className="ai-commentary-plain" data-testid="commentary-text">
        {text}
      </p>
    );
  }
  const firstMatchIndex = text.indexOf("【");
  const preamble = firstMatchIndex > 0 ? text.slice(0, firstMatchIndex).trim() : "";
  return (
    <div className="ai-commentary-sections" data-testid="commentary-text">
      {preamble !== "" ? <p className="ai-commentary-plain">{preamble}</p> : null}
      {sections.map((section, index) => {
        const bullets = splitBullets(section.body);
        return (
          <section className="ai-commentary-block" key={`${index}-${section.title}`}>
            {section.title !== "" ? (
              <h4 className="ai-commentary-block__title">【{section.title}】</h4>
            ) : null}
            {bullets ? (
              <ul className="ai-commentary-block__list">
                {bullets.map((bullet, bulletIdx) => (
                  <li key={bulletIdx}>{bullet}</li>
                ))}
              </ul>
            ) : section.body !== "" ? (
              <p className="ai-commentary-block__body">{section.body}</p>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

/** AI 评价展示区块：学生端与教师端共用，结构化呈现评价正文与重新生成入口。 */
export function CommentarySection({
  runId,
}: {
  runId: string;
  /** 保留可选属性以兼容已有调用方；精简后不再展示单独的建议参考分块。 */
  showAdvisory?: boolean;
}) {
  const session = useSession();
  const commentary = useEvaluationCommentary(runId);
  const generate = useGenerateCommentary(runId);

  const canRegenerate = canGenerateCommentary(session);
  const data = commentary.data;
  const forbidden = isApiError(commentary.error) && commentary.error.isForbidden;
  const notGenerated =
    isApiError(commentary.error) && commentary.error.isNotFound ? commentary.error : null;
  const notEnabled = isApiError(generate.error) && generate.error.isConflict;
  const statusBanner = data ? STATUS_BANNER[data.status] : null;

  return (
    <div className="stack" data-testid="commentary-section">
      <Panel
        title={t("commentary.title")}
        testId="commentary"
        actions={
          canRegenerate ? (
            <button
              type="button"
              className="button button--ghost"
              data-testid="regenerate-commentary"
              disabled={generate.isPending}
              onClick={() => generate.mutate()}
            >
              {generate.isPending ? t("commentary.regenerating") : t("commentary.regenerate")}
            </button>
          ) : undefined
        }
      >
        {notEnabled ? (
          <Banner tone="info" testId="commentary-not-enabled">
            {t("commentary.notEnabled")}
          </Banner>
        ) : generate.isError ? (
          <ErrorState error={generate.error} testId="commentary-generate-error" />
        ) : null}

        {commentary.isPending ? <Spinner /> : null}

        {forbidden ? (
          <Banner tone="info" testId="commentary-forbidden">
            {t("commentary.forbidden")}
          </Banner>
        ) : notGenerated ? (
          <Banner tone="info" testId="commentary-not-generated">
            {t("commentary.notGenerated")}
          </Banner>
        ) : commentary.isError ? (
          <ErrorState error={commentary.error} testId="commentary-error" />
        ) : null}

        {statusBanner ? (
          <Banner tone={statusBanner.tone} testId={statusBanner.testId}>
            {t(statusBanner.message)}
          </Banner>
        ) : null}

        {data?.text ? renderCommentaryContent(data.text) : null}
      </Panel>
    </div>
  );
}
