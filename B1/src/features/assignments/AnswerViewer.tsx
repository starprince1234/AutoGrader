import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import { Banner, ErrorState, InlineLoader } from "../../ui/components";
import {
  MAX_ANSWER_PREVIEW_CHARS,
  useSubmissionFileText,
} from "../submission/useSubmissionFile";

/**
 * 答案预览：带鉴权读取 + 转义文本展示 + 字符上限。
 *
 * 作业详情矩阵与批改界面共用同一份实现——两处都必须用 `Authorization` 请求读取
 * （裸 `<a href>` 不带 Bearer），把内容以 `<pre>` 转义呈现，并遵守字符上限，
 * 且**不把答案内容写进日志**。
 */
export function AnswerViewer({
  submissionId,
  path,
}: {
  submissionId: string;
  path: string;
}) {
  const query = useSubmissionFileText(submissionId, path);
  if (query.isPending) {
    return (
      <p className="muted" data-testid="answer-loading">
        <InlineLoader />
      </p>
    );
  }
  if (query.isError) {
    if (isApiError(query.error) && query.error.isForbidden) {
      return (
        <Banner tone="error" testId="answer-forbidden">
          {t("assignments.answerForbidden")}
        </Banner>
      );
    }
    if (isApiError(query.error) && query.error.isNotFound) {
      return (
        <Banner tone="warn" testId="answer-missing">
          {t("assignments.answerMissing")}
        </Banner>
      );
    }
    return <ErrorState error={query.error} testId="answer-error" />;
  }
  const text = query.data ?? "";
  const truncated = text.length > MAX_ANSWER_PREVIEW_CHARS;
  const shown = truncated ? text.slice(0, MAX_ANSWER_PREVIEW_CHARS) : text;
  return (
    <div className="stack" data-testid="answer-content">
      <pre>{shown}</pre>
      {truncated ? (
        <p className="muted" data-testid="answer-truncated">
          {t("assignments.answerTruncated", { max: MAX_ANSWER_PREVIEW_CHARS })}
        </p>
      ) : null}
    </div>
  );
}
