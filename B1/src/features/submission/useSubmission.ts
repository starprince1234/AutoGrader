import { useMutation, useQuery } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { SubmissionAccepted, SubmissionView } from "../../api/types";
import { log, newRequestId } from "../../logging/logger";
import { queryKeys } from "../../app/queryClient";
import type { SubmissionEnvelopeBody } from "./validation";

/** 提交到 B2（RQ-B-1-50）：请求体为契约的 `submission-envelope`，并携带幂等键。 */
export function useCreateSubmission() {
  return useMutation({
    mutationFn: async (envelope: SubmissionEnvelopeBody): Promise<SubmissionAccepted> => {
      const requestId = newRequestId();
      const accepted = await apiRequest<SubmissionAccepted>("/api/v1/submissions", {
        method: "POST",
        body: envelope,
        requestId,
        idempotencyKey: envelope.idempotency_key,
      });
      log({
        event: "submission_accepted",
        request_id: requestId,
        submission_id: accepted.submission_id,
        evaluation_run_id: accepted.evaluation_run_id ?? null,
        state: accepted.state,
        idempotent_replay: accepted.idempotent_replay,
        file_count: envelope.files.length,
        question_version_id: envelope.question_version_id,
      });
      return accepted;
    },
  });
}

/**
 * 解析一次提交对应的评测运行 id。
 *
 * 为什么需要轮询：运行行是**受理之后**由 B2 的编排器创建的，因此 `202` 里的
 * `evaluation_run_id` 合法地为 `null`，而进度流是按运行 id 寻址的。契约因此在
 * `SubmissionView` 上暴露 `evaluation_run_id`，客户端据此把提交推进到进度流。
 *
 * 只在拿到 id 或进入终态后停止：终态但无 id 意味着这次提交不会产生运行（例如被静态拒绝），
 * 此时继续轮询只是浪费请求。
 */
export function useEvaluationRunId(
  submissionId: string | null,
  acceptedRunId: string | null | undefined,
  terminal: boolean,
) {
  return useQuery({
    queryKey: queryKeys.submission(submissionId ?? "none"),
    enabled: Boolean(submissionId) && !acceptedRunId && !terminal,
    refetchInterval: (query) => {
      const data = query.state.data as SubmissionView | undefined;
      return data?.evaluation_run_id ? false : 1000;
    },
    queryFn: async (): Promise<SubmissionView> =>
      apiRequest<SubmissionView>(`/api/v1/submissions/${submissionId}`),
  });
}
