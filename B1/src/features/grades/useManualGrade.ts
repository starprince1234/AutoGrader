import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest, apiRequestRaw } from "../../api/client";
import type { components } from "../../api/generated/b2";
import { queryKeys } from "../../app/queryClient";

/** 人工评分与来信查看（契约 `setManualGrade` / `releaseManualGrade` / `getSubmissionIntakeMessage`）。 */

export type ManualGradeView = components["schemas"]["ManualGradeView"];
export type IntakeMessageView = components["schemas"]["IntakeMessageView"];

export type SetManualGradeInput = {
  submissionId: string;
  score: number;
  comment?: string;
  /** 改分必填：契约要求"改分必须能解释"，服务端在缺理由时返回 409。 */
  reason?: string;
  /** 乐观锁：调用方读到的那一版 `updated_at`，别人先改过就会 409。 */
  expectedUpdatedAt: string;
};

/** 当前人工评分；`null` = 还没评过（契约用 204 表达，不是错误）。 */
export function useManualGrade(submissionId: string | null) {
  return useQuery({
    queryKey: queryKeys.intakeMessage(`grade:${submissionId ?? ""}`),
    enabled: Boolean(submissionId),
    retry: false,
    queryFn: async (): Promise<ManualGradeView | null> => {
      const { response } = await apiRequestRaw(
        `/api/v1/submissions/${submissionId as string}/manual-grade`,
      );
      if (response.status === 204) {
        return null;
      }
      return (await response.json()) as ManualGradeView;
    },
  });
}

export function useSetManualGrade() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: SetManualGradeInput): Promise<ManualGradeView> =>
      apiRequest<ManualGradeView>(
        `/api/v1/submissions/${input.submissionId}/manual-grade`,
        {
          method: "POST",
          body: {
            score: input.score,
            comment: input.comment?.trim() ? input.comment.trim() : undefined,
            reason: input.reason?.trim() ? input.reason.trim() : undefined,
            expected_updated_at: input.expectedUpdatedAt,
          },
        },
      ),
    onSuccess: () => {
      // 成绩行的 `grade_status`/`score` 都会变，整族失效而不是局部改缓存：
      // 局部改一份列表会让"另一个筛选条件下的同一个提交"显示旧分数。
      void client.invalidateQueries({ queryKey: queryKeys.grades({}) });
    },
  });
}

export function useReleaseManualGrade() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (submissionId: string): Promise<{ grade_status: string }> =>
      apiRequest(`/api/v1/submissions/${submissionId}/manual-grade/release`, {
        method: "POST",
        body: {},
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.grades({}) });
    },
  });
}

/**
 * 来信内容。`null` = 这次提交不是邮件提交（契约对 API 提交返回 404，而不是空对象），
 * 因此调用方能把"没有邮件"和"读失败"区分开。
 */
export function useIntakeMessage(submissionId: string | null) {
  return useQuery({
    queryKey: queryKeys.intakeMessage(submissionId ?? ""),
    enabled: Boolean(submissionId),
    retry: false,
    queryFn: async (): Promise<IntakeMessageView | null> => {
      try {
        return await apiRequest<IntakeMessageView>(
          `/api/v1/submissions/${submissionId as string}/intake-message`,
        );
      } catch (error) {
        if (typeof error === "object" && error !== null && "status" in error) {
          const status = (error as { status?: number }).status;
          if (status === 404) {
            return null;
          }
        }
        throw error;
      }
    },
  });
}

/** 附件的下载地址：仍走带鉴权的请求，不用裸 `<a href>`（否则浏览器不会带 Bearer）。 */
export function submissionFileUrl(submissionId: string, path: string): string {
  const query = new URLSearchParams({ path });
  return `/api/v1/submissions/${submissionId}/files?${query.toString()}`;
}
