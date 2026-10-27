import { QueryClient } from "@tanstack/react-query";
import { isApiError } from "../api/problem";

/**
 * 全局查询客户端。
 * 重试策略刻意保守：`4xx` 是**确定性结论**（403/404/409/422），重试只会放大噪声；
 * 只对网络错误与 `5xx` 重试一次。
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (isApiError(error) && error.status < 500) return false;
        return failureCount < 1;
      },
      staleTime: 15_000,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
});

/** 统一查询键，避免各页面各写一套字符串（共享文件）。 */
export const queryKeys = {
  courses: ["courses"] as const,
  sections: (courseId: string) => ["sections", courseId] as const,
  enrollments: (sectionId: string) => ["enrollments", sectionId] as const,
  rosterImport: (id: string) => ["roster-import", id] as const,
  users: (role: string | null) => ["users", role] as const,
  /** 当前生效的注册名单批次（P14）。只有一个对象，没有筛选维度可进键。 */
  registrationAllowlist: ["registration-allowlist"] as const,
  questions: (courseId: string | null) => ["questions", courseId] as const,
  /**
   * 题库标签覆盖度（CHG-0020）。作用域（课程 + `published_only`）进键：同一门课在
   * 「只看已发布」与「含草稿」下是两个不同的分布，共用缓存就会让两个数字互相矛盾。
   * 没有"全部课程"这个键：契约把 `course_id` 声明为必需查询参数。
   */
  questionCoverage: (courseId: string, publishedOnly: boolean) =>
    ["question-tag-coverage", courseId, publishedOnly] as const,
  assignments: (filter: Record<string, string | undefined>) =>
    ["assignments", filter] as const,
  assignment: (assignmentId: string) => ["assignment", assignmentId] as const,
  question: (questionId: string) => ["question", questionId] as const,
  questionVersion: (versionId: string) => ["question-version", versionId] as const,
  questionDiagnostics: (questionId: string) =>
    ["question-diagnostics", questionId] as const,
  grades: (filter: Record<string, string | undefined>) => ["grades", filter] as const,
  submission: (submissionId: string) => ["submission", submissionId] as const,
  evaluationReport: (runId: string, view: string) =>
    ["evaluation-report", runId, view] as const,
  /**
   * 教学评语（RQ-T-20）。按运行取，且**不带 `view`**：契约的这个端点在服务端按调用者的
   * 真实权限收窄，没有 `view` 查询参数——把 `view` 放进键会造出一个契约里不存在的维度。
   */
  commentary: (runId: string) => ["evaluation-commentary", runId] as const,
  /**
   * 隔离区列表。筛选条件进键：`resolved` 是本页始终显式发送的参数（见
   * `features/intake/useQuarantines.ts`），不同取值是**不同的列表**，不能共用缓存。
   */
  quarantines: (filter: { resolved: boolean; reasonCode: string | null }) =>
    ["intake-quarantines", filter] as const,
  /** 题目质量聚合。键里带时间范围：同一版本的不同区间是两个不同的聚合。 */
  qualitySummary: (filter: {
    questionVersionId: string | null;
    from: string | null;
    to: string | null;
  }) => ["evaluation-quality-summary", filter] as const,
  /** 申诉证据包。`view` 进键：学生视图与教师视图是两份不同的内容。 */
  appealPack: (runId: string, view: string) => ["appeal-pack", runId, view] as const,
  /**
   * AI 草稿生成（AI-FLOW-01A）。按课程进键：provider 状态与请求预算都是部署配置的结果，
   * 而"另一门课的剩余次数"对当前页面没有意义。
   */
  generationCapabilities: (courseId: string) =>
    ["question-draft-generation-capabilities", courseId] as const,
  /** 单份候选。候选不可变，按 id 缓存不会展示陈旧内容。 */
  generation: (generationId: string) =>
    ["question-draft-generation", generationId] as const,
};
