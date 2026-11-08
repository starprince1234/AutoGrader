import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import { DIRECTORY_PAGE_SIZE } from "../../api/directory";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import type {
  AssignmentPublic,
  CourseEnrollmentPage,
  EnrollmentPublic,
} from "../../api/types";
import { queryKeys } from "../../app/queryClient";

/**
 * 作业受众名单（v0.32.0）。
 *
 * 作业是**课程级**的：它对该课程当前全部课序与全部班级的学生生效，没有手选课序或指定学生。
 * 因此教师详情的名单来源是 `GET /courses/{course_id}/enrollments` 的**全课程名单**，
 * 一次取完全部页；未绑定的名单行（`student_id` 为 `null`）也在其中，必须保留并注明原因。
 *
 * 判定纪律（与 `useCohort` 一致）：**必须把全部页取完**才算完整。只加载第一页就渲染
 * "某学生未交"，等于把分页缺陷变成对学生的错误结论；到达自动翻页上限即 `truncated`
 * （终局，调用方必须如实说明而不是继续转圈）。
 */

export type AssignmentRosterState = {
  /** 全课程名单行（服务端顺序：课序 → 班级 → 学号）。 */
  students: EnrollmentPublic[];
  isPending: boolean;
  isError: boolean;
  error: unknown;
  /** 名单已全部取完，且没有因翻页上限被截断。 */
  complete: boolean;
  /** 到达自动翻页上限而永久停止。 */
  truncated: boolean;
};

type CourseRosterResult = {
  items: EnrollmentPublic[];
  /** 还有下一页但已到自动翻页上限——终局，不是"还在加载"。 */
  capped: boolean;
};

/**
 * 顺序走完 `next_cursor`。
 *
 * 放在单个 `queryFn` 里而不是渲染期的 `useLoadAllPages`：这里只有一份数据、没有
 * "每个课序一个查询"的动态 hook 数量问题，单次取完也让缓存里就是完整名单。
 */
async function fetchAllCourseEnrollments(courseId: string): Promise<CourseRosterResult> {
  const items: EnrollmentPublic[] = [];
  let cursor: string | null = null;
  let pages = 0;
  for (;;) {
    const page: CourseEnrollmentPage = await apiRequest<CourseEnrollmentPage>(
      `/api/v1/courses/${encodeURIComponent(courseId)}/enrollments`,
      { query: { limit: DIRECTORY_PAGE_SIZE, cursor: cursor ?? undefined } },
    );
    items.push(...(page.items ?? []));
    cursor = page.next_cursor ?? null;
    pages += 1;
    if (cursor === null) return { items, capped: false };
    if (pages >= MAX_AUTO_PAGES) return { items, capped: true };
  }
}

export function useCourseRoster(courseId: string | null): AssignmentRosterState {
  const query = useQuery({
    queryKey: queryKeys.courseEnrollments(courseId ?? ""),
    enabled: Boolean(courseId),
    queryFn: async (): Promise<CourseRosterResult> =>
      fetchAllCourseEnrollments(courseId as string),
  });

  const students = query.data?.items ?? [];
  const truncated = query.data?.capped === true;
  return {
    students,
    isPending: Boolean(courseId) && query.isPending,
    isError: query.isError,
    error: query.error,
    complete: Boolean(courseId) && query.isSuccess && !truncated,
    truncated,
  };
}

/** 作业的受众名单 = 该作业所属课程的全课程名单。 */
export function useAssignmentRoster(
  assignment: AssignmentPublic | null,
): AssignmentRosterState {
  return useCourseRoster(assignment?.course_id ?? null);
}
