import { useMemo } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiRequest, apiRequestRaw } from "../../api/client";
import { useLoadAllPages } from "../../api/pagination";
import type { EvaluationReport, GradePage, GradeRow } from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { log, newRequestId } from "../../logging/logger";

/** 成绩查询、证据报告与导出（RQ-B-1-30 / RQ-B-1-60）。 */

export type GradeFilter = {
  courseId: string | null;
  sectionId: string | null;
  /**
   * 只看某个作业的提交/成绩行（`?assignment_id=`，CHG-0032）。历史提交没有 `assignment_id`，
   * 因此不会被任何作业级过滤返回——它们仍可按题目版本查询，但不进作业视图。
   */
  assignmentId?: string | null;
  questionVersionId: string | null;
  /** UTC RFC 3339（页面把本地输入转成它）；`null` = 不限制。 */
  fromUtc?: string | null;
  toUtc?: string | null;
  /** P10 → P11 深链：只看这一次运行对应的提交。授权作用域仍由服务端判定。 */
  evaluationRunId?: string | null;
};

/** 契约的 `Limit` 上限（`common.yaml`）。一页拿满，减少翻页次数。 */
export const GRADE_PAGE_SIZE = 200;

export type GradeListPage = { items: GradeRow[]; nextCursor: string | null };

/**
 * 成绩按**游标分页**加载。
 *
 * 曾经这里写死 `limit: 200` 且只取第一页：一个 300 人的课序会**静默丢掉后 100 人的成绩**，
 * 教师看到 200 行会以为那就是全班（导出却包含全部——页面上看不出的不一致）。现在跟随
 * `next_cursor`，由页面提供"已加载 N 行 / 加载更多 / 已到末页"。
 * 汇总口径仍只覆盖**已加载行**并如实标注（契约没有汇总端点，缺口第 4 条）。
 */
export function useGrades(filter: GradeFilter, options?: { enabled?: boolean }) {
  return useInfiniteQuery({
    queryKey: queryKeys.grades({
      course_id: filter.courseId ?? undefined,
      section_id: filter.sectionId ?? undefined,
      assignment_id: filter.assignmentId ?? undefined,
      question_version_id: filter.questionVersionId ?? undefined,
      from: filter.fromUtc ?? undefined,
      to: filter.toUtc ?? undefined,
      evaluation_run_id: filter.evaluationRunId ?? undefined,
    }),
    enabled: Boolean(filter.courseId) && (options?.enabled ?? true),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<GradeListPage> => {
      const page = await apiRequest<GradePage>("/api/v1/grades", {
        query: {
          course_id: filter.courseId as string,
          section_id: filter.sectionId ?? undefined,
          assignment_id: filter.assignmentId ?? undefined,
          question_version_id: filter.questionVersionId ?? undefined,
          from: filter.fromUtc ?? undefined,
          to: filter.toUtc ?? undefined,
          evaluation_run_id: filter.evaluationRunId ?? undefined,
          limit: GRADE_PAGE_SIZE,
          cursor: pageParam ?? undefined,
        },
      });
      return { items: page.items, nextCursor: page.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
}

/** 展平成绩分页结果（页面只关心"已经加载了哪些行"）。 */
export function loadedGrades(pages: readonly GradeListPage[] | undefined): GradeRow[] {
  return (pages ?? []).flatMap((page) => page.items);
}

/**
 * 成绩列表 + 自动取完全部页（作业中心的学生进度用）。
 *
 * 学生看到的进度是"这个作业我做完了没有"，而那是**判定**：只加载第一页就渲染"未提交"，
 * 会把分页缺陷变成对学生说错话。因此未到末页时 `incomplete === true`，调用方必须只显示
 * 加载中。学生视角下返回的是本人记录（`listGrades` 对学生只返回本人）。
 *
 * `incomplete` 与 `truncated` **必须分开**：
 * - `incomplete` = "不要下结论"（可能是暂时的）；
 * - `truncated` = 到达自动翻页上限而**永久停止**（重试不会继续）。
 * 把两者合并成一个标志，调用方只能对合并态渲染"加载中"，于是真的被截断时用户看到的是
 * 一个永远不会结束的 spinner——数据被静默丢弃换了个说法继续存在。
 */
export function useAllGrades(
  courseId: string | null,
  sectionId: string | null,
  assignmentId: string | null = null,
): {
  rows: GradeRow[];
  isPending: boolean;
  isError: boolean;
  error: unknown;
  incomplete: boolean;
  truncated: boolean;
} {
  const query = useGrades({
    courseId,
    sectionId,
    assignmentId,
    questionVersionId: null,
    fromUtc: null,
    toUtc: null,
    evaluationRunId: null,
  });
  const { complete, capped } = useLoadAllPages({
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
    pageCount: query.data?.pages.length ?? 0,
  });

  const rows = useMemo(() => loadedGrades(query.data?.pages), [query.data]);

  return {
    rows,
    isPending: query.isPending,
    isError: query.isError,
    error: query.error,
    incomplete: !complete || capped,
    truncated: capped,
  };
}

export function useEvaluationReport(runId: string | null, view: "student" | "teacher") {
  return useQuery({
    queryKey: queryKeys.evaluationReport(runId ?? "", view),
    enabled: Boolean(runId),
    queryFn: async (): Promise<EvaluationReport> =>
      apiRequest<EvaluationReport>(
        `/api/v1/evaluation-runs/${encodeURIComponent(runId as string)}/report`,
        { query: { view } },
      ),
  });
}

/**
 * 成绩行的三种可用性，供"我交了没有"这类判定视图使用。
 *
 * `loading` 与 `truncated` **必须分开渲染**：前者是暂时的（等一下会好），后者是终局
 * （到自动翻页上限，重试也不会继续）。合并成"未就绪"会让被截断的视图永远显示"加载中"。
 */
export type GradesAvailability = "ready" | "loading" | "truncated";

export function gradesAvailability(state: {
  isPending: boolean;
  truncated: boolean;
  incomplete: boolean;
}): GradesAvailability {
  if (state.isPending) return "loading";
  if (state.truncated) return "truncated";
  if (state.incomplete) return "loading";
  return "ready";
}

export type ExportFormat = "xlsx" | "csv";
/** 从 `Content-Disposition` 取文件名（优先 RFC 5987 的 `filename*`），不做兜底猜测以外的推断。 */
export function filenameFromDisposition(
  disposition: string | null,
  format: ExportFormat,
): string {
  if (disposition) {
    const extended = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    if (extended) {
      try {
        return decodeURIComponent(extended[1]);
      } catch {
        /* 回落到普通 filename */
      }
    }
    const plain = /filename="?([^";]+)"?/i.exec(disposition);
    if (plain) return plain[1];
  }
  return `grades.${format}`;
}

/**
 * 触发 B2 的导出并下载。
 *
 * **B1 不重新拼接成绩数据、不自行拼列**：文件内容、列顺序、公式注入转义全部由 B2 的
 * `exportGrades` 决定；这里只负责发请求与把响应体交给浏览器下载。
 */
export async function downloadGradesExport(
  filter: GradeFilter,
  format: ExportFormat,
  columns: readonly string[] = [],
): Promise<{ filename: string; requestId: string; bytes: number }> {
  const requestId = newRequestId();
  const { response } = await apiRequestRaw("/api/v1/grades/export", {
    query: {
      course_id: filter.courseId as string,
      section_id: filter.sectionId ?? undefined,
      assignment_id: filter.assignmentId ?? undefined,
      question_version_id: filter.questionVersionId ?? undefined,
      from: filter.fromUtc ?? undefined,
      to: filter.toUtc ?? undefined,
      evaluation_run_id: filter.evaluationRunId ?? undefined,
      columns: columns.length === 0 ? undefined : columns.join(","),
      format,
    },
    accept:
      format === "csv"
        ? "text/csv"
        : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    requestId,
  });

  const blob = await response.blob();
  const filename = filenameFromDisposition(
    response.headers.get("content-disposition"),
    format,
  );

  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }

  log({
    event: "grades_export_downloaded",
    request_id: requestId,
    format,
    filename,
    bytes: blob.size,
    course_id: filter.courseId,
    section_id: filter.sectionId,
    assignment_id: filter.assignmentId ?? null,
    question_version_id: filter.questionVersionId,
    from: filter.fromUtc ?? null,
    to: filter.toUtc ?? null,
    evaluation_run_id: filter.evaluationRunId ?? null,
    columns: [...columns],
  });

  return { filename, requestId, bytes: blob.size };
}
