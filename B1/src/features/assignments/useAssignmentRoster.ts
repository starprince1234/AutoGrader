import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import { DIRECTORY_PAGE_SIZE } from "../../api/directory";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import type { AssignmentPublic, EnrollmentPage, EnrollmentPublic } from "../../api/types";
import { queryKeys } from "../../app/queryClient";

/**
 * 作业受众名单（CHG-0032）。
 *
 * 作业可以选中**多个课序**（`section_ids`），再可选收窄到若干学生（`student_ids`）。
 * 因此"这个作业该有多少人"不是一次名单查询能回答的：必须按每个目标课序分别取名单，
 * 再按 `student_ids` 过滤。
 *
 * 判定纪律（与 `useCohort` 一致）：**每个课序都要把全部页取完**才算完整。
 * 只加载第一页就渲染"某学生未交"，等于把分页缺陷变成对学生的错误结论；
 * 任一课序到自动翻页上限即 `truncated`（终局，调用方必须如实说明而不是继续转圈）。
 *
 * 分页方式：每个课序一个查询，在 `queryFn` 里顺序走完 `next_cursor`（见 `MAX_AUTO_PAGES`）。
 * 不在渲染期按查询数量调用 hook，也就没有 hook 数量随 `section_ids` 变化的问题。
 */

export type AssignmentRosterState = {
  /** 目标课序的学生（收窄后）。顺序：按 `section_ids` 顺序、课序内保持服务端顺序。 */
  students: EnrollmentPublic[];
  isPending: boolean;
  isError: boolean;
  error: unknown;
  /** 所有课序的名单都已取完，且没有因翻页上限被截断。 */
  complete: boolean;
  /** 任一课序到达自动翻页上限而永久停止。 */
  truncated: boolean;
};

type SectionRosterPage = {
  items: EnrollmentPublic[];
  /** 还有下一页但已到自动翻页上限——终局，不是"还在加载"。 */
  capped: boolean;
};

async function fetchAllEnrollments(sectionId: string): Promise<SectionRosterPage> {
  const items: EnrollmentPublic[] = [];
  let cursor: string | null = null;
  let pages = 0;
  for (;;) {
    const page: EnrollmentPage = await apiRequest<EnrollmentPage>(
      `/api/v1/sections/${encodeURIComponent(sectionId)}/enrollments`,
      { query: { limit: DIRECTORY_PAGE_SIZE, cursor: cursor ?? undefined } },
    );
    items.push(...(page.items ?? []));
    cursor = page.next_cursor ?? null;
    pages += 1;
    if (cursor === null) return { items, capped: false };
    if (pages >= MAX_AUTO_PAGES) return { items, capped: true };
  }
}

/** 并行拉取若干课序的完整名单（收窄逻辑见 `useAssignmentRoster`）。 */
export function useSectionsRoster(sectionIds: readonly string[]): AssignmentRosterState {
  const effectiveIds = useMemo(
    () => sectionIds.filter((sectionId) => sectionId !== ""),
    [sectionIds],
  );
  const queries = useQueries({
    queries: effectiveIds.map((sectionId) => ({
      queryKey: queryKeys.sectionRoster(sectionId),
      queryFn: async (): Promise<SectionRosterPage> => fetchAllEnrollments(sectionId),
    })),
  });

  return useMemo(() => {
    const students: EnrollmentPublic[] = [];
    const seen = new Set<string>();
    for (const query of queries) {
      for (const row of query.data?.items ?? []) {
        // 同一课序不会出现重复行；多课序合并时按 enrollment_id 防御性去重。
        if (seen.has(row.enrollment_id)) continue;
        seen.add(row.enrollment_id);
        students.push(row);
      }
    }

    const truncated = queries.some((query) => query.data?.capped === true);
    const complete =
      queries.length > 0 &&
      !truncated &&
      queries.every((query) => query.isSuccess);

    return {
      students,
      isPending: queries.some((query) => query.isPending),
      isError: queries.some((query) => query.isError),
      error: queries.find((query) => query.error)?.error ?? null,
      complete,
      truncated,
    };
  }, [queries]);
}

/**
 * 作业的受众名单：按 `section_ids` 并行取名单，`student_ids` 非空时收窄到指定学生。
 * `student_ids` 为空数组表示"所选课序全体学生"（契约语义）。
 */
export function useAssignmentRoster(
  assignment: AssignmentPublic | null,
): AssignmentRosterState {
  const sectionIds = useMemo(
    () => assignment?.section_ids ?? [],
    [assignment?.section_ids],
  );
  const roster = useSectionsRoster(sectionIds);
  const narrowed = assignment?.student_ids ?? null;

  const students = useMemo(() => {
    if (narrowed === null || narrowed.length === 0) return roster.students;
    const wanted = new Set(narrowed);
    // 只有已绑定账号的学生能出现在 `student_ids` 里（服务端创建作业时已校验），
    // 这里仍按 `student_id` 是否存在过滤：名单行未绑定时不能拿学号冒充用户 ID。
    return roster.students.filter(
      (row) => typeof row.student_id === "string" && wanted.has(row.student_id),
    );
  }, [roster.students, narrowed]);

  return { ...roster, students };
}
