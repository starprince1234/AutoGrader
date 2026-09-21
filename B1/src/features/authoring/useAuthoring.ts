import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type {
  AuthoringAssistance,
  AuthoringAssistanceRequest,
  ComparisonDiagnostics,
  QuestionDraftInput,
  QuestionPublic,
  QuestionVersionPublic,
  ReferenceVerification,
  ReferenceVerificationRequest,
} from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { log, newRequestId } from "../../logging/logger";

/**
 * 出题写路径与出题期辅助（`b3.yaml` 的 `createQuestion`/`updateQuestionDraft`/
 * `getQuestionDiagnostics`/`requestAuthoringAssistance`）。
 *
 * 日志只记录标识与状态：参考答案、用例与适配器内容一律不进日志（`redaction.ts` 也会
 * 按 `answer`/`content`/`expected` 等键名整体脱敏，但这里根本不传）。
 */

/**
 * 新建题目。`course_id` 是**查询参数**：`QuestionDraftInput` 是闭合对象
 * （`additionalProperties: false`），课程作用域只能与请求体并列传递。
 */
export function useCreateQuestion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (args: {
      courseId: string;
      input: QuestionDraftInput;
    }): Promise<QuestionPublic> => {
      const requestId = newRequestId();
      const question = await apiRequest<QuestionPublic>("/api/v1/questions", {
        method: "POST",
        query: { course_id: args.courseId },
        body: args.input,
        requestId,
      });
      log({
        event: "question_created",
        request_id: requestId,
        question_id: question.question_id,
        course_id: args.courseId,
        status: question.status,
        version_count: question.versions?.length ?? 0,
      });
      return question;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["questions"] });
    },
  });
}

/**
 * 修改草稿。`as_new_version=true` 只在最新版本已发布时发送：契约规定已发布版本不可就地
 * 修改，就地修改草稿则不需要这个参数（它的默认值就是 false）。
 *
 * 响应是 **`QuestionPublic`**（CHG-0016）：`QuestionDraftInput` 能改 `title`/`tags`，而
 * `QuestionVersionPublic` 是 `additionalProperties: false` 且没有这两个字段——把响应声明成
 * 版本 DTO 就无法表达刚刚被改动的字段。因此新版本的标识只能从 `versions[0]`
 * （契约规定版本历史新→旧）里取：这正是该字段存在的原因之一。
 */
export function useUpdateQuestionDraft() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (args: {
      questionId: string;
      input: QuestionDraftInput;
      asNewVersion: boolean;
    }): Promise<QuestionPublic> => {
      const requestId = newRequestId();
      const question = await apiRequest<QuestionPublic>(
        `/api/v1/questions/${encodeURIComponent(args.questionId)}`,
        {
          method: "PATCH",
          query: args.asNewVersion ? { as_new_version: true } : undefined,
          body: args.input,
          requestId,
        },
      );
      const newest = question.versions?.[0] ?? null;
      log({
        event: "question_draft_updated",
        request_id: requestId,
        question_id: question.question_id,
        question_version_id: newest?.question_version_id ?? null,
        version: newest?.version ?? null,
        status: question.status,
        as_new_version: args.asNewVersion,
      });
      return question;
    },
    onSuccess: async (question, args) => {
      await queryClient.invalidateQueries({ queryKey: ["questions"] });
      await queryClient.invalidateQueries({ queryKey: queryKeys.question(args.questionId) });
      const newest = question.versions?.[0]?.question_version_id;
      if (newest) {
        await queryClient.invalidateQueries({ queryKey: queryKeys.questionVersion(newest) });
      }
      await queryClient.invalidateQueries({
        queryKey: queryKeys.questionDiagnostics(args.questionId),
      });
    },
  });
}

/** 确定性比较策略检查：规则命中，同输入同输出。 */
export function useQuestionDiagnostics(questionId: string | null) {
  return useQuery({
    queryKey: queryKeys.questionDiagnostics(questionId ?? ""),
    enabled: Boolean(questionId),
    queryFn: async (): Promise<ComparisonDiagnostics> =>
      apiRequest<ComparisonDiagnostics>(
        `/api/v1/questions/${encodeURIComponent(questionId as string)}/diagnostics`,
      ),
  });
}

/**
 * 出题辅助（RQ-T-10）。刻意是 **mutation 而不是 query**：
 * 它调用模型并有副作用（出网、计费），不能因为重渲染或窗口聚焦被自动重发。
 * 未配置 provider 时服务端返回 `409`，调用方据此显示"未启用"而不是"没有建议"。
 */
export function useAuthoringAssistance(questionId: string | null) {
  return useMutation({
    mutationFn: async (body: AuthoringAssistanceRequest): Promise<AuthoringAssistance> => {
      const requestId = newRequestId();
      const result = await apiRequest<AuthoringAssistance>(
        `/api/v1/questions/${encodeURIComponent(questionId as string)}/authoring-assistance`,
        { method: "POST", body, requestId },
      );
      log({
        event: "authoring_assistance_requested",
        request_id: requestId,
        question_id: questionId,
        topics: body.topics,
        status: result.status,
        suggestion_count: result.suggestions.length,
        provider: result.provenance?.provider ?? null,
        model: result.provenance?.model ?? null,
      });
      return result;
    },
  });
}

/**
 * 请求平台验证参考解答（`verifyReferenceAnswer`，CHG-0019 / ADR-0015）。
 *
 * 刻意是 **mutation 而不是 query**：每一次调用都会**起一次沙箱作业**（ADR-0015 把"未加显式
 * 速率限制"记为已知限制，单并发沙箱是唯一的天然限流）。查询会在重渲染、窗口聚焦或缓存过期时
 * 自动重发——那在这里不是"刷新数据"，而是**又跑一次别人的代码**。mutation 没有这些自动重发路径，
 * 且全局 `queryClient` 对 mutation 关闭重试（`retry: false`）。
 *
 * 请求体只有 `question_version_id`（契约 `required: [question_version_id]` +
 * `additionalProperties: false`）。日志只记标识与计数：本端点按契约**不返回**受保护内容，
 * 因此这里也没有受保护内容可记。
 */
export function useVerifyReferenceAnswer() {
  return useMutation({
    mutationFn: async (
      body: ReferenceVerificationRequest,
    ): Promise<ReferenceVerification> => {
      const requestId = newRequestId();
      const verification = await apiRequest<ReferenceVerification>(
        "/api/v1/reference-verifications",
        { method: "POST", body, requestId },
      );
      log({
        event: "reference_verification_requested",
        request_id: requestId,
        question_version_id: verification.question_version_id,
        cases_total: verification.cases_total,
        cases_passed: verification.cases_passed,
        // 只记"存证有没有被 B3 记下来"，不记 id（id 对前端没有可用处）。
        attested: verification.attestation_id !== null,
      });
      return verification;
    },
  });
}

/**
 * 发布并冻结一个版本（`publishQuestionVersion`，`CHG-0008` 的门禁在服务端）。
 *
 * 客户端**不**判断自己是否有资格发布：门禁是 B3 交叉核对摘要后给出的结论。本页只在
 * "验证通过 + 存证已记录 + 摘要未变"时**提供入口**，而 422 之类的拒绝一律原样展示服务端 detail。
 */
export function usePublishQuestionVersion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (args: {
      questionId: string;
      questionVersionId: string;
    }): Promise<QuestionVersionPublic> => {
      const requestId = newRequestId();
      const version = await apiRequest<QuestionVersionPublic>(
        `/api/v1/question-versions/${encodeURIComponent(args.questionVersionId)}/publish`,
        { method: "POST", requestId },
      );
      log({
        event: "question_version_published",
        request_id: requestId,
        question_id: args.questionId,
        question_version_id: args.questionVersionId,
        status: version.status,
      });
      return version;
    },
    onSuccess: async (_version, args) => {
      // 发布改变版本状态与题目状态（`versions[]` 里的 status、题目的 status），两处都要重取。
      await queryClient.invalidateQueries({
        queryKey: queryKeys.questionVersion(args.questionVersionId),
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.question(args.questionId) });
      await queryClient.invalidateQueries({ queryKey: ["questions"] });
    },
  });
}
