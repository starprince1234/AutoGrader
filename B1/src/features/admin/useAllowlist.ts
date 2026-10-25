import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type {
  RegistrationAllowlistReplacement,
  RegistrationAllowlistState,
} from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { log, newRequestId } from "../../logging/logger";

/** 注册名单（`GET|POST /api/v1/registration-allowlists`，仅管理员）。 */

/**
 * 当前生效的批次。
 *
 * 读取失败**不是**"没有批次"：`GET` 的零批次是 `batch_id: null` 且四门字段都在，而请求失败是
 * 一个错误对象。两者在页面上必须分开呈现，否则一次 5xx 会被读成"名单被清空了"。
 */
export function useRegistrationAllowlist() {
  return useQuery({
    queryKey: queryKeys.registrationAllowlist,
    queryFn: () =>
      apiRequest<RegistrationAllowlistState>("/api/v1/registration-allowlists", { method: "GET" }),
  });
}

/**
 * 全量替换（multipart）。
 *
 * 写操作不自动重试：`mutations.retry` 全局为 `false`，这里也不加 `onError` 重试。一次上传就是
 * 一次名单替换，浏览器没收到响应并不等于服务端没执行——页面因此只提供"刷新当前名单"，
 * 由管理员看过事实再决定是否重新确认。
 */
export function useReplaceRegistrationAllowlist() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File): Promise<RegistrationAllowlistReplacement> => {
      const requestId = newRequestId();
      const formData = new FormData();
      formData.append("file", file);
      const result = await apiRequest<RegistrationAllowlistReplacement>(
        "/api/v1/registration-allowlists",
        { method: "POST", formData, requestId },
      );
      // 只记录聚合数字：名单内容（姓名、学号）不进前端日志。
      log({
        event: "registration_allowlist_replaced",
        request_id: requestId,
        row_count: result.row_count,
        replaced_row_count: result.replaced_row_count,
      });
      return result;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.registrationAllowlist });
    },
  });
}
