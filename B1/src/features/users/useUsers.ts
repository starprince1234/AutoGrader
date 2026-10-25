import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type {
  CreateUserRequest,
  CreateUserResponse,
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
 * 由页面提供"已加载 N 位 / 加载更多 / 已到末页"。
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

/**
 * 创建用户。
 *
 * 密码只活在这个 mutation 的调用参数里：不写日志、不进 query cache、不在成功回调里回显。
 * 日志只记录新账号的非秘密标识与角色。
 */
export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateUserRequest): Promise<CreateUserResponse> => {
      const requestId = newRequestId();
      const created = await apiRequest<CreateUserResponse>("/api/v1/users", {
        method: "POST",
        body,
        requestId,
      });
      log({
        event: "user_created",
        request_id: requestId,
        user_id: created.user_id,
        roles: body.roles,
      });
      return created;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });
}

/**
 * 改角色（`PATCH /api/v1/users/{user_id}`）。
 *
 * 必须带 `expected_updated_at`：B4 用它做乐观并发检查，两个管理员同时改同一账号时后来者得到
 * `409` 而不是静默覆盖。`updated_at` 来自列表行的真实值——读不到就**不发送**这个写请求。
 */
export function useUpdateUserRoles() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      userId: string;
      roles: Role[];
      expectedUpdatedAt: string;
    }): Promise<void> => {
      const requestId = newRequestId();
      const body: UpdateUserRequest = {
        roles: input.roles,
        expected_updated_at: input.expectedUpdatedAt,
      };
      await apiRequest<void>(`/api/v1/users/${encodeURIComponent(input.userId)}`, {
        method: "PATCH",
        body,
        requestId,
      });
      log({
        event: "user_roles_updated",
        request_id: requestId,
        user_id: input.userId,
        roles: input.roles,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });
}

/**
 * 停用账号（`DELETE /api/v1/users/{user_id}`）。
 *
 * 契约里这个动词的**实际语义是软停用**：行与历史保留，`is_active` 变为 `false`，该用户的
 * 会话被撤销。页面文案因此写"停用账号"，不写"删除"。
 */
export function useDeactivateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { userId: string }): Promise<void> => {
      const requestId = newRequestId();
      await apiRequest<void>(`/api/v1/users/${encodeURIComponent(input.userId)}`, {
        method: "DELETE",
        requestId,
      });
      log({ event: "user_deactivated", request_id: requestId, user_id: input.userId });
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

/**
 * 角色在列表里的展示顺序：按 `ASSIGNABLE_ROLES` 排，而不是按服务端返回的顺序
 * （B4 按字典序返回，助教会排在教师前面，同一列读起来像是随机顺序）。
 */
export function orderedRoles(roles: readonly Role[]): Role[] {
  return ASSIGNABLE_ROLES.filter((role) => roles.includes(role));
}
