import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { PageTitle } from "../../ui/components";
import { MailReviewTab } from "./MailReviewTab";
import type { ReviewSearch } from "./search";

/**
 * P13 复核工作区（`/review`）：只做一件事——把待处理的隔离邮件归属到学生。
 *
 * 老师的要求：这里不要「题目质量」（题目详情页的入口也一并去掉），也不要「示例预览」。
 * 于是本页只剩一个视图；旧链接上的 `tab=…` 等参数由 `parseReviewSearch` 丢弃，
 * 不会渲染第二个页面，也不会留一个半可见的标签。
 *
 * 状态全部住在 URL：刷新、复制链接、前进/后退都能恢复。
 */
export function ReviewPage({
  search,
  onChange,
}: {
  search: ReviewSearch;
  onChange: (next: ReviewSearch) => void;
}) {
  return (
    <div className="stack">
      <div className="panel__head">
        <PageTitle>{t("review.title")}</PageTitle>
        <div className="row-actions">
          <Link className="button button--ghost" to="/assignments" data-testid="review-back">
            {t("review.backToAssignments")}
          </Link>
        </div>
      </div>
      <MailReviewTab search={search} onChange={onChange} />
    </div>
  );
}
