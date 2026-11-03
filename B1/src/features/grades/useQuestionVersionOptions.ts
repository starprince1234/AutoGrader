import { useMemo } from "react";
import type { QuestionVersionPublic } from "../../api/types";
import { useQuestionList } from "../questions/useQuestions";

/**
 * 成绩页的题目选择器（P11）：显示"题目标题 · vN"，**不要求教师手抄 UUID**。
 *
 * 数据来自 B3 的公开题目列表（每一道题自带 `versions`）。这里只做**展平**，不推断任何字段：
 * 标签顺序、标题、版本号都按服务端返回的样子用。草稿版本也列出来（历史上可能有它的成绩），
 * 但显式标注状态——把草稿当成已发布题目会让教师误以为那道题已经发给学生。
 *
 * `courseId` 为空时不发请求（契约 `listQuestions` 允许不带课程，但成绩页需要一个课程作用域，
 * 空作用域下列出的题目与成绩无关）。
 */

export type QuestionVersionOption = {
  questionVersionId: string;
  questionId: string;
  title: string;
  version: number;
  status: QuestionVersionPublic["status"];
};

export function useQuestionVersionOptions(courseId: string | null, enabled = true) {
  const query = useQuestionList(
    courseId,
    { publishedOnly: false, tags: [] },
    { enabled: enabled && Boolean(courseId) },
  );

  const options = useMemo(() => {
    const flattened: QuestionVersionOption[] = [];
    for (const page of query.data?.pages ?? []) {
      for (const question of page.items) {
        for (const version of question.versions ?? []) {
          flattened.push({
            questionVersionId: version.question_version_id,
            questionId: question.question_id,
            title: question.title,
            version: version.version,
            status: version.status,
          });
        }
      }
    }
    // 稳定顺序：标题升序、版本降序（同一道题的最新版本在最上面），与"最新优先"的阅读习惯一致。
    return flattened.sort(
      (a, b) => a.title.localeCompare(b.title) || b.version - a.version,
    );
  }, [query.data]);

  return {
    options,
    isPending: query.isPending,
    isError: query.isError,
    error: query.error,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
  };
}
