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
  /** 一个课序下的班级列表（v0.32.0：课序 ≠ 班级）。 */
  classes: (sectionId: string) => ["classes", sectionId] as const,
  /** 一个班级的名单（v0.32.0）。 */
  classEnrollments: (classId: string) => ["class-enrollments", classId] as const,
  /**
   * 全课程名单（教师作业详情 / 名单统计用）。
   *
   * 与 `enrollments`（按课序）分开缓存：同一个键下两种数据形状会让一个页面把另一个页面的
   * 缓存读成错误结构；这里把"取完全部页"封装在单次 queryFn 里，结果就是完整名单 +
   * 是否被翻页上限截断。
   */
  courseEnrollments: (courseId: string) => ["course-enrollments", courseId] as const,
  /** 服务端聚合的课程受众计数（`roster-summary`）。 */
  rosterSummary: (courseId: string) => ["roster-summary", courseId] as const,
  rosterImport: (id: string) => ["roster-import", id] as const,
  users: (role: string | null) => ["users", role] as const,
  /** 当前生效的注册名单批次（P14）。只有一个对象，没有筛选维度可进键。 */
  registrationAllowlist: ["registration-allowlist"] as const,
  allowlistBatches: ["allowlist-batches"] as const,
  allowlistEntries: (batchId: string) => ["allowlist-entries", batchId] as const,
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
  /**
   * 作业发布通知（B4 拥有投递状态）。键含课程与作业：两个 id 共同确定唯一记录，
   * 同一作业在不同课程下不是同一条通知。
   */
  assignmentNotifications: (courseId: string, assignmentId: string) =>
    ["assignment-notifications", courseId, assignmentId] as const,
  question: (questionId: string) => ["question", questionId] as const,
  questionVersion: (versionId: string) => ["question-version", versionId] as const,
  questionDiagnostics: (questionId: string) =>
    ["question-diagnostics", questionId] as const,
  grades: (filter: Record<string, string | undefined>) => ["grades", filter] as const,
  /** 来信正文：一次提交一封，按 submission_id 缓存即可。 */
  intakeMessage: (submissionId: string) => ["intake-message", submissionId] as const,
  manualGrade: (submissionId: string) => ["manual-grade", submissionId] as const,
  submission: (submissionId: string) => ["submission", submissionId] as const,
  /**
   * 提交答案文件内容（受控读取，见 `apiRequestText`）。按 `(submission_id, path)` 缓存：
   * 同一个提交的同一道题内容不会变（提交是不可变快照），因此不必反复下载。
   */
  submissionFile: (submissionId: string, path: string) =>
    ["submission-file", submissionId, path] as const,
  /** 调用者本人资料（`GET /api/v1/me`）。一个账号只有一份，没有筛选维度。 */
  currentUser: ["current-user"] as const,
  /** 邮件核验码挑战状态（轮询）：按 challenge_id 缓存。 */
  emailVerificationChallenge: (challengeId: string) =>
    ["email-verification-challenge", challengeId] as const,
  evaluationReport: (runId: string, view: string) =>
    ["evaluation-report", runId, view] as const,
  /**
   * 教学评语（RQ-T-20）。按运行取，且**不带 `view`**：契约的这个端点在服务端按调用者的
   * 真实权限收窄，没有 `view` 查询参数——把 `view` 放进键会造出一个契约里不存在的维度。
   */
  commentary: (runId: string) => ["evaluation-commentary", runId] as const,
  /**
   * 复核工作区（P13）。身份 + 筛选一起进键：
   * - 同一浏览器切换账号后不得读到上一个账号的隔离记录/质量聚合缓存；
   * - `resolved`/课程/课序/原因/时间范围不同即为**不同的列表或聚合**，不能共用缓存。
   */
  reviewQuarantines: (
    identity: string,
    filter: {
      courseId: string | null;
      sectionId: string | null;
      resolved: boolean | null;
      reasonCode: string | null;
      cursor: string | null;
    },
  ) => ["review-quarantines", identity, filter] as const,
  /** 题目质量聚合：身份 + 版本 + 时间范围。 */
  reviewQualitySummary: (
    identity: string,
    filter: {
      questionVersionId: string | null;
      from: string | null;
      to: string | null;
    },
  ) => ["review-quality-summary", identity, filter] as const,
  /** 申诉证据包。`view` 进键：学生视图与教师视图是两份不同的内容。 */
  appealPack: (runId: string, view: string) => ["appeal-pack", runId, view] as const,
  /**
   * 学生分层学习提示（AI-FLOW-01B）。按 run 取，**不带 view**：契约没有教师视图，
   * 这条路径只对 run owner 学生开放。
   */
  learningHints: (runId: string) => ["learning-hints", runId] as const,
  /** 课序名单（成绩页身份关联用）。与 `enrollments` 分开：那个是分页列表，这里是一次取完的映射。 */
  gradeRoster: (sectionId: string) => ["grade-roster", sectionId] as const,
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
