import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { EvaluationQualitySummary } from "../../api/types";
import { queryKeys } from "../../app/queryClient";

/**
 * 题目质量聚合（`GET /api/v1/evaluation-runs/quality-summary`，CHG-0015）。
 *
 * `question_version_id` 是契约的**必需**查询参数：填不出来就不发请求（本页因此不会用空参数
 * 换来一个 422，也不会显示一个"没有数据"的假空态）。
 *
 * 响应只有计数：没有学生 id、没有提交 id、没有源码、没有期望输出。这一点由契约强制，
 * 因此本模块不提供任何下钻到个人的入口。
 */

/** 已应用的筛选条件。三项一起进查询键：同一版本的不同时间区间是两个不同的聚合。 */
export type QualityFilter = {
  questionVersionId: string | null;
  from: string | null;
  to: string | null;
};

export function useQualitySummary(filter: QualityFilter) {
  return useQuery({
    queryKey: queryKeys.qualitySummary(filter),
    enabled: Boolean(filter.questionVersionId),
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
 * `datetime-local` 的本地值 → 契约 `Timestamp`（UTC RFC 3339，毫秒精度）。
 * 空值返回 `null`（调用方据此**省略**该查询参数）；不可解析的输入同样返回 `null`，
 * 由调用方拦住而不发送一个必然 422 的值。
 */
export function toContractTimestamp(local: string): string | null {
  if (local.trim() === "") return null;
  const date = new Date(local);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

/**
 * 契约 `Timestamp`（UTC ISO）→ `datetime-local` 输入框的值（本地时间 `YYYY-MM-DDTHH:mm`）。
 *
 * 为什么必须来回转换：筛选条件住在 URL 里，而 `datetime-local` 的 `value` 只接受**本地**的
 * 无时区形式。把 `...Z` 直接塞进输入框会得到一个浏览器视为无效的值——用户看到的是空框，
 * 而请求里却带着时间范围，这种不一致比缺少输入框更难发现。
 */
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
 * "样本太少"的提示门槛。
 *
 * 契约只说"尝试次数很少时这道题还没有信号，不应从噪声里下结论"，**没有**给出数值门槛
 * （这是本模块自己的阅读辅助，界面上必须这么说明，不能让它看起来像契约规则）。
 */
export const SMALL_SAMPLE_THRESHOLD = 5;

export function isSmallSample(attemptCount: number): boolean {
  return attemptCount < SMALL_SAMPLE_THRESHOLD;
}
