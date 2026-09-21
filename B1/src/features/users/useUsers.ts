import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type {
  CreateUserRequest,
  Role,
  UpdateUserRequest,
  UserPage,
  UserSummary,
} from "../../api/types";
import { log, newRequestId } from "../../logging/logger";
import { queryKeys } from "../../app/queryClient";

/** 用户与角色管理（`RQ-B-4-50`；B1 只呈现，授权由 B4 判定）。 */

/** 契约的 `Limit` 上限（`common.yaml`）。一页拿满，减少翻页次数。 */
export const USER_PAGE_SIZE = 200;

export type UserListPage = { items: UserSummary[]; nextCursor: string | null };

/**
 * 用户列表按**游标分页**加载。
 *
 * 与成绩/名单/课程属同一缺陷类：写死 `limit: 200` 且只取第一页，会让第 200 个之后的账号
 * 在管理页上**静默消失**（管理员据此判断"这个账号不存在"）。因此跟随 `next_cursor`，
 * 由页面提供"已加载 N 行 / 加载更多 / 已到末页"。
 */
export function useUsers(role: Role | null) {
  return useInfiniteQuery({
    queryKey: queryKeys.users(role),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<UserListPage> => {
      const page = await apiRequest<UserPage>("/api/v1/users", {
        query: { limit: USER_PAGE_SIZE, role: role ?? undefined, cursor: pageParam ?? undefined },
      });
      return { items: page.items, nextCursor: page.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
}

/** 展平用户分页结果。 */
export function loadedUsers(pages: readonly UserListPage[] | undefined): UserSummary[] {
  return (pages ?? []).flatMap((page) => page.items);
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateUserRequest): Promise<void> => {
      const requestId = newRequestId();
      await apiRequest<void>("/api/v1/users", { method: "POST", body, requestId });
      log({
        event: "user_created",
        request_id: requestId,
        roles: body.roles,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { userId: string; body: UpdateUserRequest }): Promise<void> => {
      const requestId = newRequestId();
      await apiRequest<void>(`/api/v1/users/${encodeURIComponent(input.userId)}`, {
        method: "PATCH",
        body: input.body,
        requestId,
      });
      log({
        event: "user_roles_updated",
        request_id: requestId,
        user_id: input.userId,
        roles: input.body.roles ?? null,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });
}

export const ASSIGNABLE_ROLES: readonly Role[] = [
  "student",
  "teacher",
  "assistant",
  "admin",
] as const;
