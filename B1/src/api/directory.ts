import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiRequest } from "./client";
import { readCollection, readIdentifier, type OpaqueRow } from "./opaque";
import type { CoursePage, CourseSummary, OpaqueResponse } from "./types";
import { queryKeys } from "../app/queryClient";

/**
 * B4 的课程/课序目录（WP-A/B/C 共用；共享文件）。
 *
 * 课序端点（`GET /api/v1/courses/{course_id}/sections`）在契约中**没有响应 schema**
 * （见 `docs/collaboration/interfaces.md` 缺口第 3 条），因此这里只能用不透明行模型：
 * 列来自服务端返回的键，标识符只用契约路径参数名 `section_id`。
 */

/** 契约的 `Limit` 上限（`common.yaml`）。一页拿满，减少翻页次数。 */
export const DIRECTORY_PAGE_SIZE = 200;

export type SectionOption = {
  sectionId: string;
  label: string;
  row: OpaqueRow;
};

export type CourseListPage = { items: CourseSummary[]; nextCursor: string | null };

/**
 * 课程目录按**游标分页**加载。
 *
 * 不能只取一页：`limit` 上限是 200，而全校课程数可以更多，只取第一页且不给"还有更多"
 * 的信号会让后面的课程在筛选下拉里**静默消失**（教师找不到自己那门课，却看不出少了什么）。
 * 因此用 `useInfiniteQuery` 走完 `next_cursor`，由调用方渲染「加载更多 / 已到末页」。
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

/** 展平课程分页结果（页面只关心"已经加载了哪些课程"）。 */
export function loadedCourses(
  pages: readonly CourseListPage[] | undefined,
): CourseSummary[] {
  return (pages ?? []).flatMap((page) => page.items);
}

export function useSections(courseId: string | null) {
  return useQuery({
    queryKey: queryKeys.sections(courseId ?? ""),
    enabled: Boolean(courseId),
    queryFn: async (): Promise<SectionOption[]> => {
      const payload = await apiRequest<OpaqueResponse>(
        `/api/v1/courses/${encodeURIComponent(courseId as string)}/sections`,
      );
      const { items } = readCollection(payload, "listSections");
      return items.map((row) => {
        const sectionId = readIdentifier(row, "section_id");
        if (!sectionId) {
          throw new Error(
            "课序列表返回的行缺少 section_id：契约没有该响应的 schema，B1 不能猜测标识字段（见 docs/collaboration/interfaces.md）。",
          );
        }
        const label = sectionLabel(row);
        return { sectionId, label, row };
      });
    },
  });
}

/** 课序的展示文案：只使用服务端确实返回的键，缺什么就不显示什么。 */
export function sectionLabel(row: OpaqueRow): string {
  const candidates = ["section_code", "code", "name", "title", "term"];
  const parts = candidates
    .map((key) => row[key])
    .filter((value): value is string => typeof value === "string" && value !== "");
  return parts.length > 0 ? parts.join(" · ") : (readIdentifier(row, "section_id") ?? "—");
}
