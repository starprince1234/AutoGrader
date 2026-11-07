import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "./client";
import type { CurrentUser } from "./types";
import { queryKeys } from "../app/queryClient";

/**
 * 调用者本人的账号资料（`GET /api/v1/me`，CHG-0032）。
 *
 * 邮件提交的正文必须写明 `姓名`/`学号`，B2 会拿它们与 B4 的 `display_name`/`student_number`
 * 精确比较（不符即 `email_body_mismatch` 拒绝）。因此界面上必须显示**服务端的权威值**，
 * 不能让学生凭记忆手输。
 *
 * 这是"本人读自己"的只读端点：任何已认证调用者只能读到自己，响应不含邮箱。
 */
export function useCurrentUser(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.currentUser,
    enabled: options?.enabled ?? true,
    // 资料是稳定事实；读取失败是终局（401/403 重试也不会变），不做无意义重试。
    retry: false,
    staleTime: 5 * 60_000,
    queryFn: async (): Promise<CurrentUser> => apiRequest<CurrentUser>("/api/v1/me"),
  });
}
