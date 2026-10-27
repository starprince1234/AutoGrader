import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "./client";
import type {
  CoursePage,
  CoursePublic,
  CreateCourseRequest,
  CreateSectionRequest,
  SectionListBody,
  SectionPublic,
} from "./types";
import { queryKeys } from "../app/queryClient";

/**
 * B4 的课程/课序目录（P04/P05/P12 共用）。
 *
 * 课序端点原先在契约里**没有响应 schema**，客户端只能用候选键猜 `section_code`/`term`；
 * 现在 `SectionPublic` 成文，读写都用生成类型，猜字段的分支已删除（不能再加回来：
 * 那会把接口变化变成静默的字段缺失）。
 */

/** 契约的 `Limit` 上限（`common.yaml`）。一页拿满，减少翻页次数。 */
export const DIRECTORY_PAGE_SIZE = 200;

export type CourseListPage = { items: CoursePublic[]; nextCursor: string | null };

export type SectionOption = { sectionId: string; label: string; row: SectionPublic };

/**
 * 课程目录按**游标分页**加载。
 *
 * 不能只取一页：`limit` 上限是 200，而全校课程数可以更多，只取第一页且不给“还有更多”的信号
 * 会让后面的课程在筛选下拉里静默消失。因此走完 `next_cursor`，由调用方决定何时显示“更多”。
 */
export function useCourses(options?: { enabled?: boolean }) {
  return useInfiniteQuery({
    queryKey: queryKeys.courses,
    enabled: options?.enabled ?? true,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<CourseListPage> => {
      const page = await apiRequest<CoursePage>("/api/v1/courses", {
        query: { limit: DIRECTORY_PAGE_SIZE, cursor: pageParam ?? undefined },
      });
      return { items: page.items, nextCursor: page.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
}

/** 展平课程分页结果（页面只关心“已经加载了哪些课程”）。 */
export function loadedCourses(pages: readonly CourseListPage[] | undefined): CoursePublic[] {
  return (pages ?? []).flatMap((page) => page.items);
}

export function useSections(courseId: string | null) {
  return useQuery({
    queryKey: queryKeys.sections(courseId ?? ""),
    enabled: Boolean(courseId),
    queryFn: async (): Promise<SectionOption[]> => {
      const payload = await apiRequest<SectionListBody>(
        `/api/v1/courses/${encodeURIComponent(courseId as string)}/sections`,
      );
      return (payload.items ?? []).map((row) => ({
        sectionId: row.section_id,
        label: sectionLabel(row),
        row,
      }));
    },
  });
}

/** 课序的展示文案：`课序号 · 学期`，学期为空就只显示课序号。 */
export function sectionLabel(row: SectionPublic): string {
  const code = row.section_code;
  return row.term ? `${code} · ${row.term}` : code;
}

/** 创建课程：响应体即存储后的课程，页面据此选中新 `course_id`。 */
export function useCreateCourse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateCourseRequest): Promise<CoursePublic> =>
      apiRequest<CoursePublic>("/api/v1/courses", { method: "POST", body }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.courses });
    },
  });
}

/** 创建课序：响应体即存储后的课序，直接加入当前课程的课序列表。 */
export function useCreateSection(courseId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateSectionRequest): Promise<SectionPublic> =>
      apiRequest<SectionPublic>(
        `/api/v1/courses/${encodeURIComponent(courseId as string)}/sections`,
        { method: "POST", body },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.sections(courseId ?? "") });
    },
  });
}
