import { useInfiniteQuery } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { MailRecordCategory, MailRecordPage, MailRecordView } from "../../api/types";
import { queryKeys } from "../../app/queryClient";

/**
 * 邮件记录的**只读**列表（`GET /api/v1/intake/mail-records`）。
 *
 * 这一页的定位就是"查看邮件提交记录"：页面只读，不提供归属/暂存/作废/打回等处置动作，
 * 因此这里只有无限查询，没有 mutation（后端处置端点保留，只是本页不再使用）。
 *
 * 三条来自契约的硬约束：
 *
 * 1. **`category` 是必填的**（`processed|pending|other`）：缺省会 `422`，三个标签就是
 *    这个视图的分类本体，因此请求永远显式发送当前分类。
 * 2. **`course_id/section_id` 只是筛选**：服务端每次按令牌 scope 与记录行 scope 重新授权，
 *    越界返回 `403`（页面原样展示，不映射成空列表）。
 * 3. **游标是服务端签发的位置**：追加下一页只用 `next_cursor`；切换分类/课程必然改变查询键，
 *    旧游标不会跨筛选复用。
 *
 * 身份（`session.subject ?? username`）进入查询键：同一浏览器切换账号后不会读到上一个账号的
 * 邮件记录缓存。
 */

/** 契约 `Limit` 的默认值；一页 50 行足够看清单页，又不会把整库拉成一次响应。 */
export const MAIL_RECORD_PAGE_SIZE = 50;

export type MailRecordFilter = {
  category: MailRecordCategory;
  courseId: string | null;
  sectionId: string | null;
  /** 页面大小；作业列表的入口徽标只需要 1 行。 */
  limit?: number;
};

export type MailRecordListPage = {
  items: MailRecordView[];
  nextCursor: string | null;
};

export function useMailRecords(args: {
  identity: string;
  filter: MailRecordFilter;
  enabled: boolean;
}) {
  const { identity, filter, enabled } = args;
  return useInfiniteQuery({
    queryKey: queryKeys.mailRecords(identity, {
      category: filter.category,
      courseId: filter.courseId,
      sectionId: filter.sectionId,
    }),
    enabled,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<MailRecordListPage> => {
      const page = await apiRequest<MailRecordPage>("/api/v1/intake/mail-records", {
        query: {
          category: filter.category,
          course_id: filter.courseId ?? undefined,
          section_id: filter.sectionId ?? undefined,
          limit: filter.limit ?? MAIL_RECORD_PAGE_SIZE,
          cursor: pageParam ?? undefined,
        },
      });
      return { items: page.items ?? [], nextCursor: page.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
}

/** 展平分页结果（页面只关心已经加载了哪些记录）。 */
export function loadedMailRecords(
  pages: readonly MailRecordListPage[] | undefined,
): MailRecordView[] {
  return (pages ?? []).flatMap((page) => page.items);
}
