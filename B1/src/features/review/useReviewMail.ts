import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type {
  IntakeQuarantinePage,
  IntakeQuarantineView,
  ResolveIntakeQuarantineRequest,
} from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { log, newRequestId } from "../../logging/logger";

/**
 * 隔离邮件的读取与归属（P13-A）。
 *
 * 三条来自任务书与 B2 契约的硬约束：
 *
 * 1. **`resolved` 要么显式 `true/false`，要么省略（= 不过滤）**：本页的"未处理"是显式
 *    `?resolved=false`；"全部状态"才是省略。不发自造的 `all` 取值（契约里没有它）。
 * 2. **`course_id/section_id` 只是筛选**：服务端每次按令牌 scope 与记录行 scope 重新授权，
 *    越界返回 403（页面原样展示，不映射成空列表）。
 * 3. **游标是服务端签发的位置**：追加下一页只用服务端 `next_cursor`；切换筛选必然重置游标
 *    （查询键包含筛选，旧游标不会跨筛选复用）。
 *
 * 身份（`session.subject ?? username`）进入查询键：同一浏览器切换账号后不会读到上一个账号的
 * 隔离记录缓存（任务书要求"身份切换时清除缓存"，这里用键隔离 + 卸载后 `gcTime` 兜底）。
 */

/** 契约 `Limit` 的默认值；一页 50 行足够看清单页，又不会把整库拉成一次响应。 */
export const REVIEW_PAGE_SIZE = 50;

export type MailQueryFilter = {
  courseId: string | null;
  sectionId: string | null;
  /** `null` = 全部状态（请求省略该参数）；否则显式发送布尔值。 */
  resolved: boolean | null;
  reasonCode: string | null;
  /** 起始游标（URL 可承载）；后续翻页不写回 URL。 */
  cursor: string | null;
};

export type QuarantineListPage = {
  items: IntakeQuarantineView[];
  nextCursor: string | null;
};

export function useQuarantineMail(args: {
  identity: string;
  filter: MailQueryFilter;
  enabled: boolean;
}) {
  const { identity, filter, enabled } = args;
  return useInfiniteQuery({
    queryKey: queryKeys.reviewQuarantines(identity, filter),
    enabled,
    initialPageParam: filter.cursor,
    queryFn: async ({ pageParam }): Promise<QuarantineListPage> => {
      const page = await apiRequest<IntakeQuarantinePage>("/api/v1/intake/quarantines", {
        query: {
          course_id: filter.courseId ?? undefined,
          section_id: filter.sectionId ?? undefined,
          // "全部"必须省略；`false` 不能省略（省略 = 不过滤）。
          resolved: filter.resolved === null ? undefined : filter.resolved,
          reason_code: filter.reasonCode ?? undefined,
          limit: REVIEW_PAGE_SIZE,
          cursor: pageParam ?? undefined,
        },
      });
      return { items: page.items ?? [], nextCursor: page.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
}

export function loadedQuarantines(
  pages: readonly QuarantineListPage[] | undefined,
): IntakeQuarantineView[] {
  return (pages ?? []).flatMap((page) => page.items);
}

/**
 * 人工归属。请求体**只**含契约声明的 `student_id`/`note`：
 * 课程/课序是服务端从记录 scope 读出来的，不由客户端回写；备注可能含人写的上下文，
 * 只记录"有没有"，不记录正文。
 */
export function useResolveQuarantine() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      quarantineId: string;
      body: ResolveIntakeQuarantineRequest;
    }): Promise<IntakeQuarantineView> => {
      const requestId = newRequestId();
      const view = await apiRequest<IntakeQuarantineView>(
        `/api/v1/intake/quarantines/${encodeURIComponent(input.quarantineId)}/resolve`,
        { method: "POST", body: input.body, requestId },
      );
      log({
        event: "intake_quarantine_resolved",
        request_id: requestId,
        intake_quarantine_id: input.quarantineId,
        has_note: input.body.note !== null && input.body.note !== undefined,
        resolved: view.resolved,
      });
      return view;
    },
    onSuccess: async () => {
      // 该记录属于「已处理」，两个状态的列表都要失效（成功提示只能在真实响应后出现）。
      await queryClient.invalidateQueries({ queryKey: ["review-quarantines"] });
    },
  });
}
