import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { EvaluationCommentary } from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { log, newRequestId } from "../../logging/logger";

/**
 * 教学评语（`GET`/`POST /api/v1/evaluation-runs/{evaluation_run_id}/commentary`，RQ-T-20）。
 *
 * 两条契约性质决定了这里的形状：
 *
 * 1. **读取是普通查询**：`unavailable`（本部署没有配置模型 provider）是一个**正常答案**
 *    （`ADR-0009` 决策 4），不是错误，因此不能用 `isError` 来表达它。
 * 2. **生成是 mutation，不是 query**：它调用模型并有副作用（出网、计费），不能因为重渲染或
 *    窗口聚焦被自动重发。没有 provider 时服务端返回 `409`（detail 里点名缺哪项配置），
 *    调用方据此显示"未启用"而不是通用失败。
 *
 * 日志只记标识、状态与"参考分是否存在"这个布尔：评语正文是模型输出，不进日志。
 */

export function commentaryPath(runId: string): string {
  return `/api/v1/evaluation-runs/${encodeURIComponent(runId)}/commentary`;
}

export function useEvaluationCommentary(runId: string | null) {
  return useQuery({
    queryKey: queryKeys.commentary(runId ?? ""),
    enabled: Boolean(runId),
    queryFn: async (): Promise<EvaluationCommentary> =>
      apiRequest<EvaluationCommentary>(commentaryPath(runId as string)),
  });
}

export function useGenerateCommentary(runId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<EvaluationCommentary> => {
      const requestId = newRequestId();
      const commentary = await apiRequest<EvaluationCommentary>(
        commentaryPath(runId as string),
        { method: "POST", requestId },
      );
      log({
        event: "evaluation_commentary_requested",
        request_id: requestId,
        evaluation_run_id: runId,
        status: commentary.status,
        // 布尔而不是分数：参考分不进日志（它不是判分结果，日志里留一个数字只会被误读）。
        advisory_score_present: commentary.advisory_score !== null &&
          commentary.advisory_score !== undefined,
      });
      return commentary;
    },
    onSuccess: (commentary) => {
      // `POST` 返回 `202` 且响应体就是该端点的 schema：它是对这次请求的权威回答
      // （通常是 `pending`），直接写入缓存，避免多打一次 `GET` 却读回同一个状态。
      queryClient.setQueryData(queryKeys.commentary(runId ?? ""), commentary);
    },
  });
}
