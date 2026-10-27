import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type {
  QuestionPage,
  QuestionPublic,
  QuestionTagCoverage,
  QuestionVersionPublic,
} from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { assertNoProtectedFields } from "./protectedFields";

/** 题目读取（B3 公开 DTO）。所有响应在返回前都过一遍受保护字段扫描。 */

/** 契约的 `Limit` 上限（`common.yaml`）。一页拿满，减少翻页次数。 */
export const QUESTION_PAGE_SIZE = 100;

export type QuestionListPage = { items: QuestionPublic[]; nextCursor: string | null };

export type QuestionListFilter = {
  /** 契约 `published_only` 默认为 `true`：新建的草稿默认不在列表里。 */
  publishedOnly: boolean;
  /** 契约 `tags`（`style: form, explode: true`）：**all-of** 匹配，不是 any-of。 */
  tags: string[];
};

/**
 * 题目列表按**游标分页**逐页加载。
 *
 * 为什么不能只取一页：契约的 `limit` 上限是 200，而题库可以更大（本环境导入了 100+ 道）。
 * 只取一页且不给出"还有更多"的信号，会让最后一页之后的题目**静默消失**——教师看到 100 行
 * 会以为那就是全部。因此这里用 `useInfiniteQuery` 把 `next_cursor` 一路走完，页面提供
 * "加载更多"，并显式显示是否已到末页。
 *
 * `options.enabled` 存在的理由只有一个：**学生不浏览全量题库**。P06 对非出题角色不渲染列表，
 * 但"不渲染"不等于"不请求"——一个仍在后台发出的题库请求既没有必要，也让"学生能不能拿到全量
 * 题目"这个问题在客户端就已经含糊了。因此由调用方显式关掉它。
 */
export function useQuestionList(
  courseId: string | null,
  filter: QuestionListFilter,
  options: { enabled?: boolean } = {},
) {
  return useInfiniteQuery({
    queryKey: [...queryKeys.questions(courseId), filter.publishedOnly, filter.tags] as const,
    enabled: options.enabled ?? true,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<QuestionListPage> => {
      const page = await apiRequest<QuestionPage>("/api/v1/questions", {
        query: {
          limit: QUESTION_PAGE_SIZE,
          course_id: courseId ?? undefined,
          published_only: filter.publishedOnly,
          tags: filter.tags,
          cursor: pageParam ?? undefined,
        },
      });
      assertNoProtectedFields(page.items);
      return { items: page.items, nextCursor: page.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
}

export function useQuestion(questionId: string) {
  return useQuery({
    queryKey: queryKeys.question(questionId),
    enabled: questionId !== "",
    queryFn: async (): Promise<QuestionPublic> => {
      const question = await apiRequest<QuestionPublic>(
        `/api/v1/questions/${encodeURIComponent(questionId)}`,
      );
      assertNoProtectedFields(question);
      return question;
    },
  });
}

export function useQuestionVersion(versionId: string | null) {
  return useQuery({
    queryKey: queryKeys.questionVersion(versionId ?? ""),
    enabled: Boolean(versionId),
    queryFn: async (): Promise<QuestionVersionPublic> => {
      const version = await apiRequest<QuestionVersionPublic>(
        `/api/v1/question-versions/${encodeURIComponent(versionId as string)}`,
      );
      assertNoProtectedFields(version);
      return version;
    },
  });
}

/**
 * 题库标签覆盖度（`GET /api/v1/questions/coverage`，CHG-0020）。
 *
 * `course_id` 是契约的**必需**查询参数：拿不到课程作用域就不发请求（本页因此不会用一个空参数
 * 换来一个 422，也不会显示一个"没有数据"的假空态）。
 *
 * `published_only` 由调用方显式传入，而且必须与列表用的是**同一个值**：契约给它声明了
 * `default: true`，但覆盖度与列表若落在不同作用域，两个并排的数字会互相矛盾——那种矛盾
 * 看起来像"数据不对"，而不是"参数不对"。
 *
 * 这里**不**对响应做任何本地加工：顺序、计数都按服务端返回的样子用（见 `QuestionTagCoverage` 的读法）。
 */
export function useQuestionCoverage(courseId: string | null, publishedOnly: boolean) {
  return useQuery({
    queryKey: queryKeys.questionCoverage(courseId ?? "", publishedOnly),
    enabled: Boolean(courseId),
    queryFn: async (): Promise<QuestionTagCoverage> =>
      apiRequest<QuestionTagCoverage>("/api/v1/questions/coverage", {
        query: { course_id: courseId as string, published_only: publishedOnly },
      }),
  });
}
