import { useEffect } from "react";

/**
 * 自动把游标分页的**所有页**取完（作业中心、成绩/名单的连接判定用）。
 *
 * 为什么需要"取完"：只要还有未加载的页，任何"某人未提交 / 某人没交作业"的结论都可能是
 * 分页造成的假象。因此判定类视图必须等列表完整，`complete` 为假时调用方只能显示加载中。
 *
 * 为什么有上限：游标由服务端给出，一旦它异常（例如永远返回同一个 `next_cursor`），
 * 无上限的自动翻页会把浏览器拖进无限请求。到上限后**按"未加载完"处理**（调用方不判定），
 * 并用 `capped` 单独标出"这是终局"——调用方必须显示"被截断"，而不是"加载中"。
 *
 * `pageCount` **必须**在 effect 依赖里：只依赖 `hasNextPage`/`isFetchingNextPage` 时，
 * React 会把"这一页已到达"与"开始取下一页"合并成一次渲染，依赖项因此没有变化，
 * 自动翻页会在第 2 页停下——那比到达上限更糟，因为它连上限都没到就静默截断了
 * （实测：注入分页夹具时只发出 2 个请求）。`pageCount` 每成功取到一页都会严格增加，
 * 循环因此必然推进到"没有下一页"或"到达上限"这两个终点之一。
 */
export const MAX_AUTO_PAGES = 40;

export function useLoadAllPages(args: {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  /** 已加载的页数（`data?.pages.length ?? 0`）。 */
  pageCount: number;
}): { complete: boolean; capped: boolean } {
  const { hasNextPage, isFetchingNextPage, fetchNextPage, pageCount } = args;
  const capped = pageCount >= MAX_AUTO_PAGES;

  useEffect(() => {
    if (!hasNextPage || isFetchingNextPage || capped) return;
    void fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, pageCount, capped]);

  return { complete: !hasNextPage, capped };
}
