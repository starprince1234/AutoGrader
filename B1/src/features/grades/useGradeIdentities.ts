import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { EnrollmentPage, EnrollmentPublic } from "../../api/types";
import { queryKeys } from "../../app/queryClient";

/**
 * 成绩行的**姓名/学号关联**（RQ-B-1-30 验收 5、`TC-P11-054`/`055`）。
 *
 * 为什么在 B1 关联而不是让 B2 返回：姓名/学号属 B4（`b4.enrollments`），B2 只拥有 `b2` 且
 * **不读 `b4` schema**。B1 已经持有调用者的访问令牌，用的就是教师能读的授权名单接口
 * `GET /sections/{section_id}/enrollments`——授权判定仍只有 B4 一处，不需要"由前端重建导出"
 * （导出文件仍完全由 B2 生成）。
 *
 * 映射键是 `(section_id, student_id)`：`student_id` 可空（名单行未绑定账号），因此**不**用学号或
 * 姓名做键。匹配不到时调用方显示"名单中未找到"，绝不把平台 ID 当成学号。
 */

export type GradeIdentity = { studentNumber: string; displayName: string };

/** 一页拿满（契约 `Limit` 上限 200）；名单可能多页，这里一次取完用于关联。 */
const ROSTER_PAGE_LIMIT = 200;
/** 防御性页数上限：一个课序的名单不该超过它；到达即视为异常并保留已加载部分。 */
const MAX_ROSTER_PAGES = 20;

export type IdentityMaps = {
  /** `${section_id}:${student_id}` → 身份。 */
  bySectionAndStudent: Map<string, GradeIdentity>;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  retry: () => void;
};

export function identityKey(sectionId: string, studentId: string): string {
  return `${sectionId}:${studentId}`;
}

async function fetchRoster(sectionId: string): Promise<Map<string, GradeIdentity>> {
  const identities = new Map<string, GradeIdentity>();
  let cursor: string | null = null;
  for (let page = 0; page < MAX_ROSTER_PAGES; page += 1) {
    const body: EnrollmentPage = await apiRequest<EnrollmentPage>(
      `/api/v1/sections/${encodeURIComponent(sectionId)}/enrollments`,
      {
        query: { limit: ROSTER_PAGE_LIMIT, cursor: cursor ?? undefined },
      },
    );
    for (const item of body.items ?? []) {
      const identity = identityOf(item);
      if (identity) identities.set(item.student_id as string, identity);
    }
    cursor = body.next_cursor ?? null;
    if (!cursor) break;
  }
  return identities;
}

function identityOf(item: EnrollmentPublic): GradeIdentity | null {
  if (!item.student_id) return null;
  return { studentNumber: item.student_number, displayName: item.display_name };
}

/**
 * 按当前已加载成绩行中出现的课序，批量读取授权名单并建立映射。
 *
 * `enabled` 为假（学生视图）时**不发任何名单请求**：学生表格没有姓名/学号列，读取全班名单
 * 既无用处、又是超出学生权限的读取。
 */
export function useGradeIdentities(sectionIds: readonly string[], enabled: boolean): IdentityMaps {
  const unique = useMemo(
    () => [...new Set(sectionIds.filter((id) => id !== ""))].sort(),
    [sectionIds],
  );
  const queries = useQueries({
    queries: unique.map((sectionId) => ({
      queryKey: queryKeys.gradeRoster(sectionId),
      enabled: enabled && sectionId !== "",
      queryFn: () => fetchRoster(sectionId),
      staleTime: 60_000,
    })),
  });

  const bySectionAndStudent = useMemo(() => {
    const merged = new Map<string, GradeIdentity>();
    queries.forEach((query, index) => {
      const sectionId = unique[index];
      if (!sectionId || !query.data) return;
      for (const [studentId, identity] of query.data) {
        merged.set(identityKey(sectionId, studentId), identity);
      }
    });
    return merged;
  }, [queries, unique]);

  return {
    bySectionAndStudent,
    isLoading: queries.some((query) => query.isPending),
    isError: queries.some((query) => query.isError),
    error: queries.find((query) => query.isError)?.error ?? null,
    retry: () => {
      for (const query of queries) {
        if (query.isError) void query.refetch();
      }
    },
  };
}
