import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import { DIRECTORY_PAGE_SIZE } from "../../api/directory";
import { MAX_AUTO_PAGES, useLoadAllPages } from "../../api/pagination";
import type { EnrollmentPage, EnrollmentPublic, GradeRow } from "../../api/types";
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
  /** 当未指定单个 `sectionId` 时，可并行拉取所属全部课序名单。 */
  sectionIds?: readonly string[];
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

type SectionRosterPageResult = {
  rows: EnrollmentPublic[];
  complete: boolean;
  truncated: boolean;
};

async function fetchSectionRoster(sectionId: string): Promise<SectionRosterPageResult> {
  const rows: EnrollmentPublic[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_AUTO_PAGES; page += 1) {
    const body: EnrollmentPage = await apiRequest<EnrollmentPage>(
      `/api/v1/sections/${encodeURIComponent(sectionId)}/enrollments`,
      { query: { limit: DIRECTORY_PAGE_SIZE, cursor: cursor ?? undefined } },
    );
    for (const item of body.items ?? []) {
      rows.push(item);
    }
    cursor = body.next_cursor ?? null;
    if (!cursor) {
      return { rows, complete: true, truncated: false };
    }
  }
  return { rows, complete: false, truncated: true };
}

export function useCohort(filter: CohortFilter): CohortState {
  const { sectionId, sectionIds, questionVersionIds } = filter;
  const targetSectionIds = useMemo(() => {
    if (sectionId) return [sectionId];
    if (!sectionIds || sectionIds.length === 0) return [];
    return Array.from(new Set(sectionIds.filter(Boolean))).sort();
  }, [sectionId, sectionIds]);

  const rosterQueries = useQueries({
    queries: targetSectionIds.map((id) => ({
      queryKey: ["cohort-roster", id] as const,
      queryFn: () => fetchSectionRoster(id),
    })),
  });
  const grades = useGrades(gradeFilterOf(filter));

  const gradesLoad = useLoadAllPages({
    hasNextPage: grades.hasNextPage,
    isFetchingNextPage: grades.isFetchingNextPage,
    fetchNextPage: grades.fetchNextPage,
    pageCount: grades.data?.pages.length ?? 0,
  });

  const rosterPending = targetSectionIds.length > 0 && rosterQueries.some((query) => query.isPending);
  const rosterError = rosterQueries.find((query) => query.error)?.error ?? null;
  const rosterTruncated = rosterQueries.some((query) => query.data?.truncated);
  const rosterComplete =
    targetSectionIds.length === 0 ||
    rosterQueries.every((query) => query.isSuccess && query.data.complete && !query.data.truncated);

  const roster = useMemo(() => {
    const deduped = new Map<string, EnrollmentPublic>();
    for (const query of rosterQueries) {
      for (const row of query.data?.rows ?? []) {
        if (!deduped.has(row.enrollment_id)) {
          deduped.set(row.enrollment_id, row);
        }
      }
    }
    return Array.from(deduped.values());
  }, [rosterQueries]);

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
    isPending: rosterPending || grades.isPending,
    error: rosterError ?? grades.error,
    incomplete:
      !gradesLoad.complete || gradesLoad.capped || !rosterComplete || rosterTruncated,
    // 名单与成绩任一到上限都算截断：判定需要两边都完整。
    truncated: gradesLoad.capped || rosterTruncated,
  };
}
