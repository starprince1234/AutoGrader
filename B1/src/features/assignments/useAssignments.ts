import { useMemo } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import { useLoadAllPages } from "../../api/pagination";
import type {
  AssignmentPage,
  AssignmentPublic,
  CreateAssignmentRequest,
} from "../../api/types";
import { queryKeys } from "../../app/queryClient";

/**
 * 作业（作业集合）读取（`RQ-B-1-40/50` 的入口侧；契约 `AssignmentPublic`，CHG-0007）。
 *
 * 纪律：
 * - 单条读取走 `GET /api/v1/assignments/{assignment_id}`（0.19.0 起）：详情页不再取完列表
 *   再按 id 本地查找——那样一旦列表分页，"没找到"就变成错误结论。
 * - 创建走 `POST /api/v1/assignments`，响应体即存储后的作业，页面据此直接进入详情。
 * - 列表同样按游标分页，且作业是页面判定的输入（"这个作业多少人交了"），因此调用方
 *   需要**全部页**时用 `useAllAssignments`：它把 `next_cursor` 走完，未到齐时
 *   `complete === false`，调用方不得据此下结论。
 */

/** 契约的 `Limit` 上限（`common.yaml`）。 */
export const ASSIGNMENT_PAGE_SIZE = 200;

export type AssignmentListPage = { items: AssignmentPublic[]; nextCursor: string | null };

export type AssignmentFilter = { courseId: string | null; sectionId: string | null };

export function useAssignmentList(filter: AssignmentFilter) {
  return useInfiniteQuery({
    queryKey: queryKeys.assignments({
      course_id: filter.courseId ?? undefined,
      section_id: filter.sectionId ?? undefined,
    }),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<AssignmentListPage> => {
      const page = await apiRequest<AssignmentPage>("/api/v1/assignments", {
        query: {
          limit: ASSIGNMENT_PAGE_SIZE,
          course_id: filter.courseId ?? undefined,
          section_id: filter.sectionId ?? undefined,
          cursor: pageParam ?? undefined,
        },
      });
      return { items: page.items ?? [], nextCursor: page.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
}

export function loadedAssignments(
  pages: readonly AssignmentListPage[] | undefined,
): AssignmentPublic[] {
  return (pages ?? []).flatMap((page) => page.items);
}

/**
 * 作业列表 + 自动取完全部页。
 *
 * 与成绩/名单同理：作业数量不大，但"按 id 找作业"和"每个作业多少人交了"都要求列表完整，
 * 少了页就会得出错误结论。因此这里把 `next_cursor` 走完，并把 `complete` 交给调用方。
 *
 * `truncated` 单独暴露：到达 `MAX_AUTO_PAGES` 是**永久停止**，调用方必须显示"被截断"，
 * 不能对它渲染"加载中"（那个 spinner 永远不会消失）。
 */
export function useAllAssignments(filter: AssignmentFilter): {
  assignments: AssignmentPublic[];
  isPending: boolean;
  isError: boolean;
  error: unknown;
  /** 未取完（含被截断）：此时不得据此下结论。 */
  complete: boolean;
  /** 到达自动翻页上限而永久停止。 */
  truncated: boolean;
} {
  const query = useAssignmentList(filter);
  const { complete, capped } = useLoadAllPages({
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
    pageCount: query.data?.pages.length ?? 0,
  });

  const assignments = useMemo(() => loadedAssignments(query.data?.pages), [query.data]);

  return {
    assignments,
    isPending: query.isPending,
    isError: query.isError,
    error: query.error,
    // 到自动翻页上限也算"未取完"：调用方不得据此下结论。
    complete: complete && !capped,
    truncated: capped,
  };
}

/**
 * 单条作业（`GET /api/v1/assignments/{assignment_id}`）。
 *
 * 不重试：`404`（不存在）与 `403`（不在调用方的课程范围）都是**终局答案**，重试只会把
 * “没权限”拖成“加载中”，让用户等一个永远不会来的结果。
 */
export function useAssignment(assignmentId: string) {
  return useQuery({
    queryKey: queryKeys.assignment(assignmentId),
    retry: false,
    queryFn: async (): Promise<AssignmentPublic> =>
      apiRequest<AssignmentPublic>(
        `/api/v1/assignments/${encodeURIComponent(assignmentId)}`,
      ),
  });
}

/**
 * 创建作业。不做“先乐观插入再回滚”：作业是否真的建成必须以服务端 `201` 的正文为准，
 * 失败时表单里的选择必须原样保留（`409` 表示所选版本已不可用，换题是用户的决定）。
 */
export function useCreateAssignment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateAssignmentRequest): Promise<AssignmentPublic> =>
      apiRequest<AssignmentPublic>("/api/v1/assignments", { method: "POST", body }),
    onSuccess: async (created) => {
      queryClient.setQueryData(queryKeys.assignment(created.assignment_id), created);
      await queryClient.invalidateQueries({ queryKey: ["assignments"] });
    },
  });
}
