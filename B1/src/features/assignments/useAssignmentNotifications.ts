import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import { useLoadAllPages } from "../../api/pagination";
import { isApiError } from "../../api/problem";
import type {
  AssignmentNotificationDelivery,
  AssignmentNotificationDeliveryPage,
  AssignmentNotificationSummary,
  AssignmentPublic,
  ScheduleAssignmentNotificationsResult,
} from "../../api/types";
import { queryKeys } from "../../app/queryClient";

/**
 * 作业发布通知（v0.32.0）。
 *
 * 数据来源被刻意分成两层，页面不得把两层混成一句"已通知"：
 * - **投递状态**：B4 `GET /courses/{cid}/assignments/{aid}/notifications` 的 summary 与
 *   逐人 delivery。`sent` 才表示 SMTP 已接受；`skipped_*` 是"没有可用的通知渠道"，
 *   不能显示成已送达；该端点 `404` 表示 B4 没有这条通知记录（前端显示"通知未安排"）。
 * - **调度状态**：B3 `POST /assignments/{aid}/notifications` 只是把受众交给 B4
 *   （幂等）。它返回的 `notification_state=scheduled` 表示"事件已写入 B4"，
 *   不代表任何一封邮件已经送出；`unavailable` 表示没交给 B4，可以重试。
 *
 * 因此重试按钮的文案是"安排/重试通知"，成功提示只说调度结果，不说"已送达"。
 */

/** 契约 `Limit` 的默认值。 */
export const NOTIFICATION_PAGE_SIZE = 100;

export type AssignmentNotificationPage = {
  summary: AssignmentNotificationSummary;
  items: AssignmentNotificationDelivery[];
  nextCursor: string | null;
};

export type AssignmentNotificationsState = {
  /** 投递汇总；`null` 表示还没有拿到（加载中/未安排/读取失败）。 */
  summary: AssignmentNotificationSummary | null;
  deliveries: AssignmentNotificationDelivery[];
  isPending: boolean;
  isError: boolean;
  error: unknown;
  /** B4 明确 404：本部署没有这个作业的通知记录。 */
  notScheduled: boolean;
  /** 投递记录已全部取完（未到翻页上限）。 */
  complete: boolean;
  /** 投递记录到达自动翻页上限（终局）。 */
  truncated: boolean;
};

async function fetchNotificationPage(input: {
  courseId: string;
  assignmentId: string;
  cursor: string | null;
}): Promise<AssignmentNotificationPage> {
  const body = await apiRequest<{
    summary: AssignmentNotificationSummary;
    deliveries: AssignmentNotificationDeliveryPage;
  }>(
    `/api/v1/courses/${encodeURIComponent(input.courseId)}/assignments/${encodeURIComponent(
      input.assignmentId,
    )}/notifications`,
    { query: { limit: NOTIFICATION_PAGE_SIZE, cursor: input.cursor ?? undefined } },
  );
  return {
    summary: body.summary,
    items: body.deliveries.items ?? [],
    nextCursor: body.deliveries.next_cursor ?? null,
  };
}

/**
 * 作业通知的 summary + 逐人投递记录。
 *
 * `404` 是"B4 没有这条通知"的**结论**而不是错误：因此不重试，并由 `notScheduled` 单独标出，
 * 页面显示"通知未安排"并提供调度入口。投递记录是游标页，这里自动取完全部页；
 * 到达自动翻页上限时 `truncated` 为真（终局，不得把它渲染成"还在加载"）。
 */
export function useAssignmentNotifications(
  courseId: string | null,
  assignmentId: string | null,
  options?: { enabled?: boolean },
): AssignmentNotificationsState {
  const enabled = Boolean(courseId) && Boolean(assignmentId) && (options?.enabled ?? true);
  const query = useInfiniteQuery({
    queryKey: queryKeys.assignmentNotifications(
      courseId ?? "",
      assignmentId ?? "",
    ),
    enabled,
    retry: false,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) =>
      fetchNotificationPage({
        courseId: courseId as string,
        assignmentId: assignmentId as string,
        cursor: pageParam,
      }),
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });

  const { complete, capped } = useLoadAllPages({
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
    pageCount: query.data?.pages.length ?? 0,
  });

  const pages = query.data?.pages ?? [];
  return {
    summary: pages[0]?.summary ?? null,
    deliveries: pages.flatMap((page) => page.items),
    isPending: enabled && query.isPending,
    isError: query.isError,
    error: query.error,
    notScheduled: query.isError && isApiError(query.error) && query.error.status === 404,
    // 被截断也算"未完整"：调用方不得据此下"某人没收到"的结论。
    complete: enabled && !query.isError && complete && !capped,
    truncated: capped,
  };
}

/**
 * 安排（或重试）作业通知：B3 的幂等调度端点。
 *
 * 成功后必须同时失效两处缓存：B4 的投递记录（可能刚刚被材料化）与作业本身
 * （`notification_state` 变了）。失败不改任何本地状态：调度是否成功只能由服务端回答。
 */
export function useScheduleAssignmentNotifications(
  courseId: string | null,
  assignmentId: string | null,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<ScheduleAssignmentNotificationsResult> =>
      apiRequest<AssignmentPublic>(
        `/api/v1/assignments/${encodeURIComponent(assignmentId as string)}/notifications`,
        { method: "POST" },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.assignmentNotifications(courseId ?? "", assignmentId ?? ""),
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.assignment(assignmentId ?? ""),
      });
    },
  });
}
