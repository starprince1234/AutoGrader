/**
 * Complete submission history for one student's question, paged to the end.
 *
 * Why not reuse `progress.gradeRows`: that list comes from `useAllGrades`, which stops at an automatic
 * paging cap (`truncated`). A question with many resubmissions could therefore show fewer attempts
 * than exist, and the modal would imply that was all of them.
 *
 * This hook asks for the exact slice it needs (assignment + question + student) and
 * and follows `next_cursor` to the last page. The filtered set is small (one student, one question),
 * so "page until the end" costs a request or two rather than pulling the whole course.
 */


import { useInfiniteQuery } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { GradePage, GradeRow } from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { GRADE_PAGE_SIZE, type GradeListPage } from "./useGrades";

export type SubmissionHistoryInput = {
  courseId: string | null;
  sectionId?: string | null;
  assignmentId: string | null;
  questionVersionId: string | null;
  /** 学生看自己的历史时传本人 id；教师看某学生时传该学生 id；为空表示不按人过滤。 */
  studentId?: string | null;
};

export function useSubmissionHistory(input: SubmissionHistoryInput) {
  const enabled =
    Boolean(input.courseId) && Boolean(input.assignmentId) && Boolean(input.questionVersionId);

  const query = useInfiniteQuery({
    queryKey: queryKeys.grades({
      course_id: input.courseId ?? undefined,
      section_id: input.sectionId ?? undefined,
      assignment_id: input.assignmentId ?? undefined,
      question_version_id: input.questionVersionId ?? undefined,
      student_id: input.studentId ?? undefined,
      scope: "history",
    }),
    enabled,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<GradeListPage> => {
      const page = await apiRequest<GradePage>("/api/v1/grades", {
        query: {
          course_id: input.courseId as string,
          section_id: input.sectionId ?? undefined,
          assignment_id: input.assignmentId as string,
          question_version_id: input.questionVersionId as string,
          student_id: input.studentId ?? undefined,
          limit: GRADE_PAGE_SIZE,
          cursor: pageParam ?? undefined,
        },
      });
      return { items: page.items, nextCursor: page.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });

  const rows: GradeRow[] = (query.data?.pages ?? []).flatMap((page) => page.items);
  return {
    rows,
    /** 已翻到末页：此时 rows 就是全部历史，显示条数才是可信的。 */
    complete: enabled && !query.hasNextPage && !query.isFetchingNextPage,
    isPending: query.isPending,
    isError: query.isError,
    error: query.error,
  };
}
