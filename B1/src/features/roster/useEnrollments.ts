import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type {
  ClassEnrollmentPage,
  EnrollmentPage,
  EnrollmentPublic,
  UpdateEnrollmentRequest,
} from "../../api/types";
import { log, newRequestId } from "../../logging/logger";
import { queryKeys } from "../../app/queryClient";
import { DIRECTORY_PAGE_SIZE } from "../../api/directory";

/**
 * 名单读取与编辑（RQ-B-1-20）。
 *
 * v0.32.0 起名单挂在**班级**下：名单页按"课序 → 班级 → 学生"展开，逐班读取
 * `GET /classes/{class_id}/enrollments`；按课序读取的旧端点在契约里仍保留（成绩页/复核页
 * 仍按课序筛人），因此两个查询分开缓存、互不混用形状。
 *
 * 读取同样走**游标分页**：`limit` 上限 200 且名单会更大，只取第一页会让名单后面的学生
 * 在页面上静默消失（教师会以为全班就这些人）。
 */

export type EnrollmentListPage = { items: EnrollmentPublic[]; nextCursor: string | null };

function toEnrollmentPage(page: EnrollmentPage | ClassEnrollmentPage): EnrollmentListPage {
  return { items: page.items ?? [], nextCursor: page.next_cursor ?? null };
}

export function useEnrollments(sectionId: string | null) {
  return useInfiniteQuery({
    queryKey: queryKeys.enrollments(sectionId ?? ""),
    enabled: Boolean(sectionId),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<EnrollmentListPage> => {
      const page = await apiRequest<EnrollmentPage>(
        `/api/v1/sections/${encodeURIComponent(sectionId as string)}/enrollments`,
        { query: { limit: DIRECTORY_PAGE_SIZE, cursor: pageParam ?? undefined } },
      );
      return toEnrollmentPage(page);
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
}

/** 一个班级的名单（v0.32.0：班级是名单的归属单位）。 */
export function useClassEnrollments(classId: string | null) {
  return useInfiniteQuery({
    queryKey: queryKeys.classEnrollments(classId ?? ""),
    enabled: Boolean(classId),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<EnrollmentListPage> => {
      const page = await apiRequest<ClassEnrollmentPage>(
        `/api/v1/classes/${encodeURIComponent(classId as string)}/enrollments`,
        { query: { limit: DIRECTORY_PAGE_SIZE, cursor: pageParam ?? undefined } },
      );
      return toEnrollmentPage(page);
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
}

export function useUpdateEnrollment(sectionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      enrollmentId: string;
      body: UpdateEnrollmentRequest;
    }): Promise<void> => {
      const requestId = newRequestId();
      await apiRequest<void>(
        `/api/v1/enrollments/${encodeURIComponent(input.enrollmentId)}`,
        { method: "PATCH", body: input.body, requestId },
      );
      log({
        event: "roster_entry_updated",
        request_id: requestId,
        enrollment_id: input.enrollmentId,
        // 只记变更的字段名，不记值（避免 PII 进日志）
        changed_fields: Object.keys(input.body).filter((key) => key !== "expected_updated_at"),
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.enrollments(sectionId) });
    },
  });
}

/**
 * 删除一条名单行（退课）。
 *
 * 名单行现在挂在班级下，删除后会让两份计数都变化（班级人数、课程 `roster-summary`），
 * 因此失效范围按调用方给的班级/课序/课程 id 一起给；没给的维度不必失效。
 */
export function useDeleteEnrollment(scope: {
  classId?: string | null;
  sectionId?: string | null;
  courseId?: string | null;
}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (enrollmentId: string): Promise<void> => {
      const requestId = newRequestId();
      await apiRequest<void>(`/api/v1/enrollments/${encodeURIComponent(enrollmentId)}`, {
        method: "DELETE",
        requestId,
      });
      log({
        event: "roster_entry_removed",
        request_id: requestId,
        enrollment_id: enrollmentId,
      });
    },
    onSuccess: async () => {
      if (scope.classId) {
        await queryClient.invalidateQueries({
          queryKey: queryKeys.classEnrollments(scope.classId),
        });
      }
      if (scope.sectionId) {
        await queryClient.invalidateQueries({
          queryKey: queryKeys.enrollments(scope.sectionId),
        });
        await queryClient.invalidateQueries({ queryKey: queryKeys.classes(scope.sectionId) });
      }
      if (scope.courseId) {
        await queryClient.invalidateQueries({
          queryKey: queryKeys.rosterSummary(scope.courseId),
        });
        await queryClient.invalidateQueries({
          queryKey: queryKeys.sections(scope.courseId),
        });
        await queryClient.invalidateQueries({ queryKey: ["course-enrollments"] });
      }
    },
  });
}
