import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { PageTitle } from "../../ui/components";
import { MailRecordsTab } from "./MailRecordsTab";
import type { ReviewSearch } from "./search";

/**
 * `/review`：**邮件记录**页（只读）。
 *
 * 用户的要求：这个功能用于"查看邮件提交记录"，所以这里没有处置动作，也不再有第二个标签：
 * 打开就是三种分类（已处理 / 待处理 / 其他）的记录列表。页头只保留"返回作业列表"。
 *
 * 状态全部住在 URL（课程/课序与三态分类）：刷新、复制链接、前进/后退都能恢复；旧链接上的
 * `resolved`/`held`/`reason_code`/`cursor`/`tab` 等参数由 `parseReviewSearch` 丢弃。
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
          <Link
            className="button button--ghost"
            to="/ops/logs"
            search={{ tab: "search", producer: "B2" }}
            data-testid="review-view-related-logs"
          >
            {t("opsLogs.deepLink.viewLogs")}
          </Link>
          <Link className="button button--ghost" to="/assignments" data-testid="review-back">
            {t("review.backToAssignments")}
          </Link>
        </div>
      </div>
      <MailRecordsTab search={search} onChange={onChange} />
    </div>
  );
}
