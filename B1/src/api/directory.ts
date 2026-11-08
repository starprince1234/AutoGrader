import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { apiRequest } from "./client";
import type {
  ClassCreateRequest,
  ClassListBody,
  ClassPublic,
  ClassRenameRequest,
  CoursePage,
  CoursePublic,
  CreateCourseRequest,
  CreateSectionRequest,
  RosterSummaryBody,
  SectionListBody,
  SectionPublic,
} from "./types";
import { queryKeys } from "../app/queryClient";

/**
 * B4 的课程/课序/班级目录（P04/P05/P12/P13 共用）。
 *
 * v0.32.0 起课程结构是 **课程 → 课序（section）→ 班级（class）**：课序不再等同班级，
 * 一个课序可以挂多个班级。因此"新建班级 / 把已注册名单的班级导入某个课序 / 重命名或删除班级"
 * 都是班级端点，旧的一课序一个班的 `POST /courses/{id}/classes` 已从契约删除，本文件也不再
 * 暴露它（不允许给已删除的端点留别名）。
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
      return { items: page.items ?? [], nextCursor: page.next_cursor ?? null };
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

/** 一个课序下的班级列表（`GET /sections/{section_id}/classes`）。 */
export function useClasses(sectionId: string | null) {
  return useQuery({
    queryKey: queryKeys.classes(sectionId ?? ""),
    enabled: Boolean(sectionId),
    queryFn: async (): Promise<ClassPublic[]> => {
      const payload = await apiRequest<ClassListBody>(
        `/api/v1/sections/${encodeURIComponent(sectionId as string)}/classes`,
      );
      return payload.items ?? [];
    },
  });
}

/**
 * 课程受众的服务端聚合（`GET /courses/{course_id}/roster-summary`）。
 *
 * 这是"这门课多少人 / 已绑定 / 未绑定 / 多少课序 / 多少班级"的唯一权威答案：
 * 客户端不能用已加载的名单页去加总，那会把分页缺陷变成一句错误的规模结论。
 */
export function useRosterSummary(courseId: string | null) {
  return useQuery({
    queryKey: queryKeys.rosterSummary(courseId ?? ""),
    enabled: Boolean(courseId),
    queryFn: async (): Promise<RosterSummaryBody> =>
      apiRequest<RosterSummaryBody>(
        `/api/v1/courses/${encodeURIComponent(courseId as string)}/roster-summary`,
      ),
  });
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

/**
 * 课程显示名：有编号时"编号 · 名称"，没有编号时只显示名称。
 *
 * 编号是可选的标签（`updateCourse` 可以清空它），所以显示层必须容忍 null——否则清空之后
 * 每一屏都会出现一个孤零零的" · 软件工程"。
 */
export function courseLabel(course: { course_code?: string | null; title: string }): string {
  const code = (course.course_code ?? "").trim();
  return code === "" ? course.title : `${code} · ${course.title}`;
}

/** 删除课程：软删，课程从列表消失，但历史记录仍指向它。 */
export function useDeleteCourse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (courseId: string): Promise<void> =>
      apiRequest<void>(`/api/v1/courses/${encodeURIComponent(courseId)}`, { method: "DELETE" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.courses });
    },
  });
}

/** 删除课序（连带其班级与名单）。 */
export function useDeleteSection(courseId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (sectionId: string): Promise<void> =>
      apiRequest<void>(
        `/api/v1/courses/${encodeURIComponent(courseId ?? "")}/sections/${encodeURIComponent(sectionId)}`,
        { method: "DELETE" },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.sections(courseId ?? "") });
      await queryClient.invalidateQueries({ queryKey: queryKeys.courses });
      await queryClient.invalidateQueries({ queryKey: queryKeys.rosterSummary(courseId ?? "") });
      await queryClient.invalidateQueries({ queryKey: ["course-enrollments"] });
    },
  });
}

/** 重命名课程：只改 title；`expected_updated_at` 是乐观锁，避免两人同时改互相覆盖。 */
export function useRenameCourse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      courseId: string;
      title: string;
      /** `null` 表示清空序号；`undefined` 表示不改动（契约里两者语义不同）。 */
      courseCode?: string | null;
      expectedUpdatedAt: string;
    }): Promise<CoursePublic> =>
      apiRequest<CoursePublic>(`/api/v1/courses/${encodeURIComponent(input.courseId)}`, {
        method: "PATCH",
        body: {
          title: input.title,
          ...(input.courseCode === undefined ? {} : { course_code: input.courseCode }),
          expected_updated_at: input.expectedUpdatedAt,
        },
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.courses });
    },
  });
}

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
      await queryClient.invalidateQueries({ queryKey: queryKeys.courses });
      await queryClient.invalidateQueries({ queryKey: queryKeys.rosterSummary(courseId ?? "") });
    },
  });
}

/**
 * 在一个指定课序下新建班级（`POST /sections/{section_id}/classes`）。
 *
 * 带 `batch_id` 时班级初始成员复制自"已注册名单"里的那个班；不带时从空班开始。
 * 同一批学生若已在本课程的其他课序/班级有效在册，服务端整体回滚并返回 409
 * （含冲突计数与冲突班级/课序的 detail）；页面把这段 detail 原样展示。
 */
export function useCreateClass(input: { courseId: string | null; sectionId: string | null }) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ClassCreateRequest): Promise<ClassPublic> =>
      apiRequest<ClassPublic>(
        `/api/v1/sections/${encodeURIComponent(input.sectionId ?? "")}/classes`,
        { method: "POST", body },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.classes(input.sectionId ?? "") });
      await queryClient.invalidateQueries({ queryKey: queryKeys.sections(input.courseId ?? "") });
      await queryClient.invalidateQueries({ queryKey: queryKeys.courses });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.rosterSummary(input.courseId ?? ""),
      });
      await queryClient.invalidateQueries({ queryKey: ["course-enrollments"] });
    },
  });
}

/** 重命名班级（`PATCH /classes/{class_id}`）；重名由服务端 409 拒绝。 */
export function useRenameClass(input: { courseId: string | null; sectionId: string | null }) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (args: { classId: string; body: ClassRenameRequest }): Promise<ClassPublic> =>
      apiRequest<ClassPublic>(`/api/v1/classes/${encodeURIComponent(args.classId)}`, {
        method: "PATCH",
        body: args.body,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.classes(input.sectionId ?? "") });
      await queryClient.invalidateQueries({ queryKey: queryKeys.sections(input.courseId ?? "") });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.rosterSummary(input.courseId ?? ""),
      });
    },
  });
}

/** 删除班级（连带该班名单行；历史通知快照保留）。 */
export function useDeleteClass(input: { courseId: string | null; sectionId: string | null }) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (classId: string): Promise<void> =>
      apiRequest<void>(`/api/v1/classes/${encodeURIComponent(classId)}`, { method: "DELETE" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.classes(input.sectionId ?? "") });
      await queryClient.invalidateQueries({ queryKey: queryKeys.sections(input.courseId ?? "") });
      await queryClient.invalidateQueries({ queryKey: queryKeys.courses });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.rosterSummary(input.courseId ?? ""),
      });
      await queryClient.invalidateQueries({ queryKey: ["course-enrollments"] });
    },
  });
}
