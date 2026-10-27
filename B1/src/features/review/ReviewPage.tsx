import { t } from "../../i18n";
import { PageTitle, Tabs } from "../../ui/components";
import { MailReviewTab } from "./MailReviewTab";
import { QualityReviewTab } from "./QualityReviewTab";
import {
  normalizeReviewTab,
  withTab,
  type ReviewSearch,
  type ReviewTab,
} from "./search";

/**
 * P13 复核工作区（唯一入口：`/review?tab=mail|quality`）。
 *
 * 一个页面、两个标签：邮件隔离归属（P13-A）与题目质量（P13-B）。它们共享"低频 staff 复核"
 * 这一个场景，因此不各占一条导航；P04 的"待处理邮件"把课程/课序上下文带进 mail 标签，
 * P07 的"查看题目质量"把 `question_version_id` 带进 quality 标签。
 *
 * 状态全部住在 URL（由路由 `validateSearch` 解析）：刷新、复制链接、前进/后退都能恢复，
 * 切换标签会移除另一标签的专属参数；切换本身不触发任何写操作或跨标签请求。
 */
export function ReviewPage({
  search,
  onChange,
}: {
  search: ReviewSearch;
  onChange: (next: ReviewSearch) => void;
}) {
  const tab: ReviewTab = normalizeReviewTab(search.tab);
  return (
    <div className="stack">
      <PageTitle>{t("review.title")}</PageTitle>
      <Tabs
        label={t("review.title")}
        testId="review-tabs"
        activeId={tab}
        onChange={(id) => onChange(withTab(search, normalizeReviewTab(id)))}
        tabs={[
          {
            id: "mail",
            label: t("review.tabMail"),
            content: <MailReviewTab search={search} onChange={onChange} />,
          },
          {
            id: "quality",
            label: t("review.tabQuality"),
            content: <QualityReviewTab search={search} onChange={onChange} />,
          },
        ]}
      />
    </div>
  );
}
