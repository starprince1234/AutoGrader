import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type {
  QuestionDraftGeneration,
  QuestionDraftGenerationCapabilities,
  QuestionDraftGenerationRequest,
} from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { log, newRequestId } from "../../logging/logger";

/**
 * AI 结构化草稿生成（AI-FLOW-01A）的数据层。
 *
 * 三条刻意的设计：
 *
 * 1. **生成是 mutation，不是 query**：它会出网并计费，不能因为重渲染、窗口聚焦或缓存过期被自动
 *    重发。全局 `queryClient` 对 mutation 关闭重试，这里也不加任何自动重试——只有教师显式点
 *    "重新生成"才会再调用一次模型。
 * 2. **恢复是 query**：刷新后用 `generation_id` 读回**服务端原始候选**。教师尚未保存的字段编辑
 *    不持久化，所以调用方必须在离开前提示"这些编辑不会保留"，而不是假装恢复了它们。
 * 3. **候选只进内存**：不写 localStorage/sessionStorage，也不放 React Query 的持久缓存（本应用
 *    没有持久化缓存，这条是为了不让后来者加一个）。参考答案与隐藏用例只在当前会话的内存里。
 */

/** 服务端声明的上限/预算。只读，且必须在 teacher scope 内。 */
export function useGenerationCapabilities(courseId: string | null) {
  return useQuery({
    queryKey: queryKeys.generationCapabilities(courseId ?? ""),
    enabled: Boolean(courseId),
    // provider 状态与会话无关地很少变，但预算会变，所以不吃长缓存。
    staleTime: 5_000,
    queryFn: async (): Promise<QuestionDraftGenerationCapabilities> =>
      apiRequest<QuestionDraftGenerationCapabilities>(
        "/api/v1/question-draft-generations/capabilities",
        { query: { course_id: courseId as string } },
      ),
  });
}

export function useCreateGeneration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (args: {
      courseId: string;
      request: QuestionDraftGenerationRequest;
    }): Promise<QuestionDraftGeneration> => {
      const requestId = newRequestId();
      const generation = await apiRequest<QuestionDraftGeneration>(
        "/api/v1/question-draft-generations",
        {
          method: "POST",
          // `course_id` 是查询参数（契约），不放进 body：它是授权与归属，不是题目内容。
          query: { course_id: args.courseId },
          body: args.request,
          requestId,
        },
      );
      log({
        event: "question_draft_generated",
        request_id: requestId,
        course_id: args.courseId,
        generation_id: generation.generation_id ?? null,
        status: generation.status,
        // 只记计数与状态：候选内容（参考解、用例）绝不进日志。
        warning_count: generation.warnings.length,
        proposal_case_count: generation.proposal?.cases?.length ?? 0,
        provider: generation.provenance?.provider ?? null,
        model: generation.provenance?.model ?? null,
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.generationCapabilities(args.courseId),
      });
      return generation;
    },
  });
}

/**
 * 用 `generation_id` 读回服务端原始候选（刷新恢复）。
 *
 * `enabled` 由调用方控制：只有 URL 里带 `generation_id` 且当前课程已知时才请求。过期、已丢弃或
 * 属于他人时服务端会拒绝，调用方据此显示真实原因而不是空白表单。
 */
export function useGeneration(generationId: string | null) {
  return useQuery({
    queryKey: queryKeys.generation(generationId ?? ""),
    enabled: Boolean(generationId),
    // 候选是不变的：同一 id 读回的内容不会变，缓存它不会展示陈旧数据。
    staleTime: Infinity,
    retry: false,
    queryFn: async (): Promise<QuestionDraftGeneration> =>
      apiRequest<QuestionDraftGeneration>(
        `/api/v1/question-draft-generations/${encodeURIComponent(generationId as string)}`,
      ),
  });
}

/** 丢弃候选：只删 proposal 正文，服务端保留去内容化的用量记录。 */
export function useDiscardGeneration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (args: { courseId: string; generationId: string }): Promise<void> => {
      const requestId = newRequestId();
      await apiRequest<void>(
        `/api/v1/question-draft-generations/${encodeURIComponent(args.generationId)}`,
        { method: "DELETE", requestId },
      );
      log({
        event: "question_draft_discarded",
        request_id: requestId,
        generation_id: args.generationId,
      });
    },
    onSuccess: async (_result, args) => {
      queryClient.removeQueries({ queryKey: queryKeys.generation(args.generationId) });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.generationCapabilities(args.courseId),
      });
    },
  });
}
