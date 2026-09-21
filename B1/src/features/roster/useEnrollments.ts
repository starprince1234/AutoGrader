import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { EnrollmentPage, EnrollmentPublic, UpdateEnrollmentRequest } from "../../api/types";
import { log, newRequestId } from "../../logging/logger";
import { queryKeys } from "../../app/queryClient";
import { DIRECTORY_PAGE_SIZE } from "../../api/directory";

/**
 * 名单读取与编辑（RQ-B-1-20）。
 *
 * `GET /sections/{section_id}/enrollments` 的响应**已有 schema**（`EnrollmentPublic`，
 * CHG-0007），因此读路径不再需要不透明行模型；写路径用的是契约里成文的请求体
 * （`student_number` / `display_name` / `expected_updated_at`）。
 *
 * 读取同样走**游标分页**：`limit` 上限 200 且名单会更大，只取第一页会让名单后面的学生
 * 在页面上静默消失（教师会以为全班就这些人）。
 */

export type EnrollmentListPage = { items: EnrollmentPublic[]; nextCursor: string | null };

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
      return { items: page.items ?? [], nextCursor: page.next_cursor ?? null };
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

export function useDeleteEnrollment(sectionId: string) {
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
      await queryClient.invalidateQueries({ queryKey: queryKeys.enrollments(sectionId) });
    },
  });
}
