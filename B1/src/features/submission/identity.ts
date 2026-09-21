import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { GradePage } from "../../api/types";
import { useSession } from "../../auth/sessionContext";
import { queryKeys } from "../../app/queryClient";

/**
 * 提交信封必填 `student_id`，但**契约没有"当前用户资料"端点**：
 * `GET /api/v1/users/{user_id}` 是管理员专用，`POST /api/v1/auth/session` 的响应只有
 * `roles`/`scope`（见 `docs/collaboration/interfaces.md` 缺口第 5 条）。
 *
 * 因此按以下**契约内的**顺序解析，且每一步都可核对、都不猜：
 * 1. 访问令牌的 `sub`（会话状态里的 `subject`，`RQ-B-4-50` 要求令牌可本地验证身份）；
 * 2. 学生本人的成绩行（`listGrades` 对学生只返回本人记录，行内含 `student_id`）。
 * 两者都拿不到时**不提交**，而是显式报阻塞——绝不用输入框让用户"自己填一个身份"。
 *
 * 主体来自**会话状态**而不是渲染期读令牌：令牌在 `restoreSession()` 完成前是 `null`，
 * 读模块变量不会在它变成非空时重算，于是 `token_subject` 这条路径在真实使用中不可达
 * （只有"本人成绩行"回退会被触发，而从未提交过的学生因此拿不到身份）。
 */

export type IdentityResolution = {
  studentId: string | null;
  source: "token_subject" | "own_grade_row" | "unavailable";
};

export function useStudentIdentity(courseId: string | null, sectionId: string | null) {
  const session = useSession();
  const fromToken = session.subject;

  const ownGrades = useQuery({
    queryKey: queryKeys.grades({
      scope: "identity",
      course_id: courseId ?? undefined,
      section_id: sectionId ?? undefined,
    }),
    enabled: fromToken === null && Boolean(courseId),
    queryFn: async (): Promise<GradePage> =>
      apiRequest<GradePage>("/api/v1/grades", {
        query: {
          course_id: courseId as string,
          section_id: sectionId ?? undefined,
          limit: 1,
        },
      }),
  });

  const fallback = ownGrades.data?.items?.[0]?.student_id ?? null;
  const resolution: IdentityResolution =
    fromToken !== null
      ? { studentId: fromToken, source: "token_subject" }
      : fallback !== null
        ? { studentId: fallback, source: "own_grade_row" }
        : { studentId: null, source: "unavailable" };

  return { ...resolution, isResolving: fromToken === null && ownGrades.isPending };
}
