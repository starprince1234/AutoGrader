import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest, newIdempotencyKey } from "../../api/client";
import type { LearningHintsResponse } from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { log, newRequestId } from "../../logging/logger";

/**
 * 学生分层学习提示（AI-FLOW-01B）。
 *
 * 两个调用都只针对**本人**的 run：服务端用 submission 的 owner 判定，前端不传 student_id。
 * 生成期间**不自动重试**（写请求），失败后由学生显式点"重试"；重试会带新的 `Idempotency-Key`，
 * 而服务端对"已 ready 的同一 run"始终返回同一行，不会重复计费。
 */

export function useLearningHints(runId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.learningHints(runId ?? ""),
    enabled: Boolean(runId) && enabled,
    queryFn: async (): Promise<LearningHintsResponse> =>
      apiRequest<LearningHintsResponse>(
        `/api/v1/evaluation-runs/${encodeURIComponent(runId as string)}/learning-hints`,
      ),
  });
}

export function useGenerateLearningHints(runId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<LearningHintsResponse> => {
      const requestId = newRequestId();
      const idempotencyKey = newIdempotencyKey();
      const result = await apiRequest<LearningHintsResponse>(
        `/api/v1/evaluation-runs/${encodeURIComponent(runId as string)}/learning-hints`,
        { method: "POST", requestId, idempotencyKey },
      );
      log({
        event: "learning_hints_requested",
        request_id: requestId,
        evaluation_run_id: runId,
        status: result.status,
        step_count: result.hint_steps.length,
      });
      return result;
    },
    onSuccess: (data) => {
      if (runId) queryClient.setQueryData(queryKeys.learningHints(runId), data);
    },
  });
}
