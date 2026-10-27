import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { EvaluationQualitySummary } from "../../api/types";
import { queryKeys } from "../../app/queryClient";

/**
 * 题目质量聚合（`GET /api/v1/evaluation-runs/quality-summary`，P13-B）。
 *
 * 响应只有计数：没有学生 id、没有提交 id、没有源码、没有期望输出。页面因此不提供任何
 * 下钻到个人的入口，也不调用模型做"总结"——它回答的是"这道题有没有区分度"。
 *
 * 查询键包含身份 + 版本 + 时间范围：同一版本的不同区间是两个不同的聚合，不同账号之间也
 * 不能复用缓存。
 */

export type QualityFilter = {
  questionVersionId: string | null;
  from: string | null;
  to: string | null;
};

export function useQualitySummary(args: {
  identity: string;
  filter: QualityFilter;
  /** 版本元信息通过后才会置为 true——B3 拒绝时不应再向 B2 发聚合请求。 */
  enabled: boolean;
}) {
  const { identity, filter, enabled } = args;
  return useQuery({
    queryKey: queryKeys.reviewQualitySummary(identity, filter),
    enabled: enabled && Boolean(filter.questionVersionId),
    queryFn: async (): Promise<EvaluationQualitySummary> =>
      apiRequest<EvaluationQualitySummary>("/api/v1/evaluation-runs/quality-summary", {
        query: {
          question_version_id: filter.questionVersionId as string,
          from: filter.from ?? undefined,
          to: filter.to ?? undefined,
        },
      }),
  });
}

/**
 * `datetime-local` 的本地值 → 契约 `Timestamp`（RFC 3339，UTC 偏移由 `toISOString` 给出）。
 * 空值返回 `null`（调用方据此省略参数）；不可解析同样返回 `null`，由调用方拦住。
 */
export function toContractTimestamp(local: string): string | null {
  if (local.trim() === "") return null;
  const date = new Date(local);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

/** 契约 `Timestamp` → `datetime-local` 需要的本地无时区值（`YYYY-MM-DDTHH:mm`）。 */
export function toLocalInputValue(iso: string | null): string {
  if (iso === null || iso === "") return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/**
 * "样本太少"的提示门槛。契约只说"很少时没有信号"，**没有**给数值；
 * 因此界面上必须声明这是本页的阅读辅助，不是契约规则。
 */
export const SMALL_SAMPLE_THRESHOLD = 5;

export function isSmallSample(attemptCount: number): boolean {
  return attemptCount < SMALL_SAMPLE_THRESHOLD;
}
