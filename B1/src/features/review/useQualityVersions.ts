import { useMemo } from "react";
import type { QuestionPublic } from "../../api/types";
import { useQuestion, useQuestionList, useQuestionVersion } from "../questions/useQuestions";

/**
 * 题目质量的版本入口（P13-B）。
 *
 * 教师**不需要**知道 UUID：版本选项来自 B3 的已发布题目列表
 * （`GET /api/v1/questions?course_id=…&published_only=true` 的 `versions`），
 * 只包含当前用户有权查看的课程（B4 的课程列表本身已按 scope 过滤）。
 *
 * 深链进入（P07 带 `question_version_id`）时，先用 B3 的 `GET /question-versions/{id}`
 * 取真实标题/版本号：这一步同时是**权限判定**——B3 对无权版本返回 403，页面据此显示拒绝，
 * 而不是去 B2 拉一个可能存在的聚合。这也解释了为什么这里不显示"手填 ID"输入框。
 */

type VersionSummary = NonNullable<QuestionPublic["versions"]>[number];

export type VersionOption = {
  questionVersionId: string;
  label: string;
  status: VersionSummary["status"];
  questionTitle: string;
  version: number;
};

/** 课程 → 题目（含版本历史）→ 版本选项。走游标分页，不静默截断题库。 */
export function useVersionOptions(courseId: string | null) {
  // 没有选中课程时**不能**退化成"全量题库请求"：版本选择器的课程是输入，不是授权依据，
  // 但一次未选课程就发出的 `/questions` 请求既无意义，也会把教师的题库范围暴露给页面。
  const query = useQuestionList(
    courseId,
    { publishedOnly: true, tags: [] },
    { enabled: courseId !== null },
  );
  const options = useMemo<VersionOption[]>(() => {
    const pages = query.data?.pages ?? [];
    return pages.flatMap((page) =>
      page.items.flatMap((question) =>
        (question.versions ?? []).map((version) => ({
          questionVersionId: version.question_version_id,
          label: `${question.title} · v${version.version}`,
          status: version.status,
          questionTitle: question.title,
          version: version.version,
        })),
      ),
    );
  }, [query.data]);
  return { query, options };
}

/**
 * 深链版本的元信息（标题、版本号、所属题目）。B3 拒绝时**不**继续请求 B2 聚合。
 */
export function useVersionContext(questionVersionId: string | null) {
  const version = useQuestionVersion(questionVersionId);
  const questionId = version.data?.question_id ?? null;
  const question = useQuestion(questionId ?? "");
  const title =
    question.data?.title ?? null;
  return {
    version,
    question,
    questionId,
    label:
      title !== null && version.data
        ? `${title} · v${version.data.version}`
        : version.data
          ? `v${version.data.version}`
          : null,
  };
}
