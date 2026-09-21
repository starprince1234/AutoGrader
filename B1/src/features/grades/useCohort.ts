import { useMemo } from "react";
import { useLoadAllPages } from "../../api/pagination";
import type { EnrollmentPublic, GradeRow } from "../../api/types";
import { useEnrollments } from "../roster/useEnrollments";
import { buildCohort, type CohortSummary } from "./cohort";
import { loadedGrades, useGrades, type GradeFilter } from "./useGrades";

/**
 * 名单 × 成绩的连接（成绩页「谁还没交」、作业中心「提交情况」共用）。
 *
 * **判定纪律**：先把名单与成绩的所有页取完，再交给 `buildCohort`。只加载第一页就说某人
 * "未提交"，等于把分页缺陷变成对学生的错误结论——`incomplete` 为真时调用方必须只显示
 * "正在加载"，不得渲染任何判定结果。
 */

export type CohortFilter = {
  courseId: string | null;
  sectionId: string | null;
  /**
   * 只统计这些题目版本的提交（`null` = 该课程/课序下的全部提交）。
   * 契约的 `listGrades` 只接受**单个** `question_version_id`，因此单元素时下推到服务端，
   * 多元素时在客户端取并集（作业有多个题目版本时属后者）。
   */
  questionVersionIds: string[] | null;
};

/** 下推到服务端的成绩过滤：只有"恰好一个版本"时契约才表达得出来。 */
export function gradeFilterOf(filter: CohortFilter): GradeFilter {
  const single = filter.questionVersionIds?.length === 1 ? filter.questionVersionIds[0] : null;
  return {
    courseId: filter.courseId,
    sectionId: filter.sectionId,
    questionVersionId: single,
  };
}

export type CohortState = CohortSummary & {
  /** 已加载的原始行：调用方要按作业再分组时用它们，不必重新取数。 */
  roster: EnrollmentPublic[];
  gradeRows: GradeRow[];
  isPending: boolean;
  error: unknown;
  /** 仍有未加载的页（或到自动翻页上限）：此时任何"未提交"判定都不可信。 */
  incomplete: boolean;
  /**
   * 名单或成绩到达自动翻页上限而**永久停止**（`MAX_AUTO_PAGES`）。
   * 与 `incomplete` 的区别是它会一直为真：调用方必须显示"被截断"的说明，
   * 而不是继续显示"加载中"——那会是一个永远不会结束的 spinner。
   */
  truncated: boolean;
};

export function useCohort(filter: CohortFilter): CohortState {
  const { sectionId, questionVersionIds } = filter;
  const enrollments = useEnrollments(sectionId);
  const grades = useGrades(gradeFilterOf(filter));

  const gradesLoad = useLoadAllPages({
    hasNextPage: grades.hasNextPage,
    isFetchingNextPage: grades.isFetchingNextPage,
    fetchNextPage: grades.fetchNextPage,
    pageCount: grades.data?.pages.length ?? 0,
  });
  const rosterLoad = useLoadAllPages({
    hasNextPage: enrollments.hasNextPage,
    isFetchingNextPage: enrollments.isFetchingNextPage,
    fetchNextPage: enrollments.fetchNextPage,
    pageCount: enrollments.data?.pages.length ?? 0,
  });

  const roster = useMemo(
    () => (enrollments.data?.pages ?? []).flatMap((page) => page.items),
    [enrollments.data],
  );
  const loaded = useMemo(() => loadedGrades(grades.data?.pages), [grades.data]);
  const gradeRows = useMemo(() => {
    // 多版本作业：契约下推不了集合，就在客户端取并集（单版本时这里不做任何过滤）。
    if (questionVersionIds === null || questionVersionIds.length <= 1) return loaded;
    const wanted = new Set(questionVersionIds);
    return loaded.filter((row) => wanted.has(row.question_version_id));
  }, [loaded, questionVersionIds]);

  const summary = useMemo(() => buildCohort(roster, gradeRows), [gradeRows, roster]);

  return {
    ...summary,
    roster,
    gradeRows,
    isPending: enrollments.isPending || grades.isPending,
    error: enrollments.error ?? grades.error,
    incomplete:
      !gradesLoad.complete || gradesLoad.capped || !rosterLoad.complete || rosterLoad.capped,
    // 名单与成绩任一到上限都算截断：判定需要两边都完整。
    truncated: gradesLoad.capped || rosterLoad.capped,
  };
}
