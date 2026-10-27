/**
 * 契约类型的**唯一出口**（便于全局替换生成来源），不手写任何字段。
 *
 * 所有类型都由 `pnpm api:generate` 从 `MAIN/contracts/openapi/*.yaml` 生成
 * （`src/api/generated/*.ts`，见 ADR-0002）。本文件只做别名与少量结构性派生。
 */
import type { components as CommonComponents } from "./generated/common";
import type { components as B2Components, operations as B2Operations } from "./generated/b2";
import type { components as B3Components, operations as B3Operations } from "./generated/b3";
import type { components as B4Components, operations as B4Operations } from "./generated/b4";

type CommonSchemas = CommonComponents["schemas"];
type B2Schemas = B2Components["schemas"];
type B3Schemas = B3Components["schemas"];
type B4Schemas = B4Components["schemas"];

/** JSON 响应体类型（`operations[x].responses[status].content["application/json"]`）。 */
type JsonBody<O, S extends number> = O extends {
  responses: Record<S, { content: { "application/json": infer Body } }>;
}
  ? Body
  : never;

/* ---------------------------------------------------------------- 公共（common.yaml） */

export type ProblemDetails = CommonSchemas["ProblemDetails"];
export type Role = CommonSchemas["Role"];
export type CourseScope = CommonSchemas["CourseScope"];
export type Language = CommonSchemas["Language"];

/* ------------------------------------------------------------------------ B4（身份） */

export type SessionCreated = JsonBody<B4Operations["createSession"], 200>;
export type SessionRefreshed = JsonBody<B4Operations["refreshSession"], 200>;
export type RegistrationCodeRequest =
  B4Operations["requestRegistrationCode"]["requestBody"]["content"]["application/json"];
export type RegistrationCodeAccepted = JsonBody<B4Operations["requestRegistrationCode"], 202>;
export type RegistrationCompleteRequest =
  B4Operations["createRegistration"]["requestBody"]["content"]["application/json"];
export type RegistrationCreated = JsonBody<B4Operations["createRegistration"], 201>;
export type PasswordResetCodeRequest =
  B4Operations["requestPasswordResetCode"]["requestBody"]["content"]["application/json"];
export type PasswordResetCodeAccepted = JsonBody<
  B4Operations["requestPasswordResetCode"],
  202
>;
export type PasswordResetCompleteRequest =
  B4Operations["completePasswordReset"]["requestBody"]["content"]["application/json"];
export type UserSummary = B4Schemas["UserSummary"];
export type CoursePublic = B4Schemas["CoursePublic"];
export type SectionPublic = B4Schemas["SectionPublic"];
export type EnrollmentPublic = B4Schemas["EnrollmentPublic"];
export type RosterImportPreview = B4Schemas["RosterImportPreview"];
export type RosterRowPreview = B4Schemas["RosterRowPreview"];
export type UpdateEnrollmentRequest =
  B4Operations["updateEnrollment"]["requestBody"]["content"]["application/json"];
export type CreateUserRequest =
  B4Operations["createUser"]["requestBody"]["content"]["application/json"];
export type UpdateUserRequest =
  B4Operations["updateUser"]["requestBody"]["content"]["application/json"];
export type SetRosterMappingRequest =
  B4Operations["setRosterColumnMapping"]["requestBody"]["content"]["application/json"];
export type CreateCourseSessionRequest =
  B4Operations["createSession"]["requestBody"]["content"]["application/json"];

/** 游标页（`common.yaml` 的 `CursorPage`）与具体行的组合，来自契约的 `allOf`。 */
export type UserPage = JsonBody<B4Operations["listUsers"], 200>;
export type CoursePage = JsonBody<B4Operations["listCourses"], 200>;
export type SectionListBody = JsonBody<B4Operations["listSections"], 200>;
export type CreateCourseRequest =
  B4Operations["createCourse"]["requestBody"]["content"]["application/json"];
export type CreateSectionRequest =
  B4Operations["createSection"]["requestBody"]["content"]["application/json"];
export type EnrollmentPage = JsonBody<B4Operations["listEnrollments"], 200>;

/**
 * 创建用户的响应（`createUser` 的 `201`）：只有新账号的 `user_id`。
 *
 * 它**不是**一份用户记录：要拿整行（角色、状态、`updated_at`）仍由列表读回。P14 用它把
 * "创建成功"说清楚成"创建了哪个账号"，而不是只弹一句成功。
 */
export type CreateUserResponse = JsonBody<B4Operations["createUser"], 201>;

/**
 * 当前生效的注册名单批次（`getRegistrationAllowlist`）。
 *
 * 四门字段 `batch_id`/`filename`/`uploaded_at` 都可为 `null`，且**零批次时仍是这四门字段**
 * （而不是空对象）：页面因此能区分"从没导入过"与"读取失败"。
 */
export type RegistrationAllowlistState = JsonBody<
  B4Operations["getRegistrationAllowlist"],
  200
>;

/**
 * 一次全量替换的结果（`replaceRegistrationAllowlist`）。
 *
 * `row_count` 是本次文件的有效行数，`replaced_row_count` 是被它替换掉的旧行数；两者都来自
 * 服务端事务，页面不自己数。
 */
export type RegistrationAllowlistReplacement = JsonBody<
  B4Operations["replaceRegistrationAllowlist"],
  200
>;


/* ------------------------------------------------------------------- B3（题目/作业） */

export type QuestionPublic = B3Schemas["QuestionPublic"];
export type QuestionVersionPublic = B3Schemas["QuestionVersionPublic"];
export type AssignmentPublic = B3Schemas["AssignmentPublic"];
export type QuestionVersionSummary = B3Schemas["QuestionVersionSummary"];
export type QuestionPage = JsonBody<B3Operations["listQuestions"], 200>;
export type AssignmentPage = JsonBody<B3Operations["listAssignments"], 200>;
export type CreateAssignmentRequest =
  B3Operations["createAssignment"]["requestBody"]["content"]["application/json"];
export type StemAsset = NonNullable<QuestionPublic["stem_assets"]>[number];

/**
 * 题库标签覆盖度（`getQuestionTagCoverage`，CHG-0020）。
 *
 * 它是**分布**，不是名单：契约刻意不给任何 `question_id`，`tags[]` 里只有标签与计数。
 *
 * 一处必须照着契约读的地方：`sum(tags[].question_count)` **不等于** `total_questions`——
 * 一道题最多可带 20 个标签（`QuestionDraftInput.tags`），逐标签计数**按设计重叠**。
 * 可校验的恒等式是 `tagged_questions + untagged_questions == total_questions`。
 */
export type QuestionTagCoverage = B3Schemas["QuestionTagCoverage"];
export type TagCoverageRow = QuestionTagCoverage["tags"][number];

/* --------------------------------------------------- B3（出题写路径，CHG-0008/0013） */

/**
 * 出题写路径的类型。`reference_answer` 与 `cases[]` **只在这条路径上存在**：
 * 它们被写进去，但从不出现在任何公开 DTO 里（`QuestionPublic`/`QuestionVersionPublic`
 * 都是 `additionalProperties: false` 且没有这些字段）。
 */
export type QuestionDraftInput =
  B3Operations["createQuestion"]["requestBody"]["content"]["application/json"];
export type AdapterManifest = B3Schemas["AdapterManifest"];
export type TestCaseInput = B3Schemas["TestCaseInput"];
export type RevisionDiff = B3Schemas["RevisionDiff"];

/** 出题辅助（RQ-T-10）：模型生成的**建议**，与确定性的 `ComparisonDiagnostics` 分属两个端点。 */
export type AuthoringAssistance = B3Schemas["AuthoringAssistance"];
export type AuthoringAssistanceRequest =
  B3Operations["requestAuthoringAssistance"]["requestBody"]["content"]["application/json"];

/** 确定性比较策略检查（CHG-0013）：规则命中，同输入同输出。 */
export type ComparisonDiagnostics = B3Schemas["ComparisonDiagnostics"];
export type ComparisonFinding = ComparisonDiagnostics["findings"][number];

/* ------------------------------ B3（AI 结构化草稿生成，AI-FLOW-01A） */

/**
 * 一次生成请求（`createQuestionDraftGeneration`）。
 *
 * `learning_objective` 与 `constraints` **在契约里没有写死上限**：上限是部署配置（provider
 * 上下文/成本），由 `getQuestionDraftGenerationCapabilities` 报告当前值。因此本模块不在客户端
 * 猜一个数字去校验，而是照服务端返回的上限展示与拦截。
 *
 * `language` 与 `adapter_kind` 是**教师的约束**，不是模型的建议：候选必须与之相同，否则那道题
 * 在平台里跑不起来。
 */
export type QuestionDraftGenerationRequest =
  B3Operations["createQuestionDraftGeneration"]["requestBody"]["content"]["application/json"];

/** 服务端声明的生成约束（上限、预算、provider 状态）。只读，不含凭据或他人用量。 */
export type QuestionDraftGenerationCapabilities =
  B3Schemas["QuestionDraftGenerationCapabilities"];

/**
 * 一份 AI 候选（`QuestionDraftGeneration`）。
 *
 * 这是**教师专用** DTO：`proposal` 里可以带参考答案与候选隐藏用例，因此它既不是
 * `QuestionPublic` 也不是 `QuestionVersionPublic`，也不得被学生端读取。
 *
 * `status` 是唯一的结论位：`ready`/`needs_review` 都表示"可编辑但必须人工审核"，
 * `failed` 表示没能形成编辑器（`proposal` 为空），`unavailable` 表示本部署没有配 provider
 * （没有出网、没有计费、也没有记录可恢复）。
 */
export type QuestionDraftGeneration = B3Schemas["QuestionDraftGeneration"];
export type QuestionDraftProposal = NonNullable<QuestionDraftGeneration["proposal"]>;
export type QuestionDraftProposalCase = NonNullable<QuestionDraftProposal["cases"]>[number];
export type QuestionDraftProposalFixture = NonNullable<QuestionDraftProposal["fixtures"]>[number];
export type DraftGenerationWarning = QuestionDraftGeneration["warnings"][number];

/** 教师上传夹具（`uploadVersionFixture`）：摘要由服务端计算，请求里没有摘要字段。 */
export type FixtureUploadInput =
  B3Operations["uploadVersionFixture"]["requestBody"]["content"]["application/json"];
export type FixtureUploadResult = JsonBody<B3Operations["uploadVersionFixture"], 201>;

/** 题干图片上传（`uploadQuestionAsset`）：同样不信任客户端摘要。 */
export type AssetUploadInput =
  B3Operations["uploadQuestionAsset"]["requestBody"]["content"]["application/json"];
export type StemAssetUploadResult = JsonBody<B3Operations["uploadQuestionAsset"], 201>;

/* ------------------------------------------------------------------- B2（提交/成绩） */

export type SubmissionAccepted = B2Schemas["SubmissionAccepted"];
export type SubmissionView = B2Schemas["SubmissionView"];
export type EvaluationRunView = B2Schemas["EvaluationRunView"];
export type EvaluationStreamFrame = B2Schemas["EvaluationStreamFrame"];
export type GradeRow = B2Schemas["GradeRow"];
export type GradePage = JsonBody<B2Operations["listGrades"], 200>;
export type EvaluationReport = B2Schemas["evaluation-report.schema"];
export type SubmissionEnvelope = B2Schemas["submission-envelope.schema"];

/**
 * 逐用例证据（RQ-B-2-50 + CHG-0012）。
 * `resource` 为 `null` 表示**未测量**（不是 0）；`artifacts.expected_sha256` 与
 * `expected_output_excerpt` 是受保护字段，只在教师视图下发。
 */
export type CaseEvidence = EvaluationReport["case_evidence"][number];
export type CaseResource = NonNullable<CaseEvidence["resource"]>;
export type CaseArtifacts = NonNullable<CaseEvidence["artifacts"]>;
export type FileChange = NonNullable<CaseArtifacts["created"]>[number];

/* ------------------------------------------------- B2（教学评语与 AI 参考分，RQ-T-20/T-30） */

/**
 * 教学评语（`getEvaluationCommentary` / `requestEvaluationCommentary`，`ADR-0009`）。
 *
 * 它是**非确定性轨**：`status` 四态（`unavailable`/`pending`/`ready`/`failed`），
 * `disclaimers` 契约要求至少一项、永不为空，且必须**逐字**显示在文本与参考分旁边。
 * `text` 只在 `ready` 时非空。
 */
export type EvaluationCommentary = B2Schemas["EvaluationCommentary"];
export type CommentaryStatus = EvaluationCommentary["status"];
export type CommentaryProvenance = NonNullable<EvaluationCommentary["provenance"]>;

/**
 * AI 评分参考（`AdvisoryScore`，RQ-T-30 / `ADR-0014`）。
 *
 * **它不是分数**：契约明确它不进入 `evaluation_results`、`case_evidence`、
 * `result_digest`、五个绑定摘要、评测报告、成绩端点与任何事件。因此 B1 只把它当**展示对象**
 * 读取——不参与任何本地计算（不求和、不排序、不导出）。
 *
 * 路径本身也是约束：只能经 `commentary.advisory_score.score` 取到（多一层字段名的意义
 * 就在于此），所以这里**不**把它拍平成 `advisory_score_number` 之类的别名。
 */
export type AdvisoryScore = B2Schemas["AdvisoryScore"];

/* ------------------------------------------- B2（隔离区 / 证据包 / 质量聚合，CHG-0014/0015） */

/**
 * 隔离区复核（`listIntakeQuarantines` / `resolveIntakeQuarantine`）。
 * `IntakeQuarantineView` 只含识别性元数据与**已脱敏**的原因说明：它让教师能在自己的邮箱里
 * 重新找到那封信，因此**不含**附件内容、学生源码或提交的任何部分。
 */
export type IntakeQuarantineView = B2Schemas["IntakeQuarantineView"];
export type IntakeQuarantinePage = JsonBody<B2Operations["listIntakeQuarantines"], 200>;
export type ResolveIntakeQuarantineRequest =
  B2Operations["resolveIntakeQuarantine"]["requestBody"]["content"]["application/json"];

/**
 * 可移动的申诉证据包（`getAppealPack`）。`report` 受调用方 `view` 约束——
 * 导出永不扩大可见范围，因此这个类型同时是"学生视图"与"教师视图"的形状。
 */
export type AppealPack = B2Schemas["AppealPack"];

/**
 * `view` 的取值直接取自契约的 enum（`student` / `teacher`），不手写字符串字面量。
 * 契约给它声明了 `default: teacher`，因此调用方必须**显式**决定发哪个值
 * （见 `src/auth/visibility.ts` 的 `appealPackView`）。
 */
export type AppealPackView = NonNullable<
  NonNullable<B2Operations["getAppealPack"]["parameters"]["query"]>["view"]
>;

/** 题目质量聚合（`getEvaluationQualitySummary`）：只有计数，没有任何可回溯到个人的字段。 */
export type EvaluationQualitySummary = B2Schemas["EvaluationQualitySummary"];
export type CaseFailureRate = EvaluationQualitySummary["case_failure_rates"][number];
export type StaticRuleHit = EvaluationQualitySummary["static_rule_hits"][number];
export type FailureCategoryCount = EvaluationQualitySummary["failure_categories"][number];

/* ------------------------------------------- B2（参考解答验证，CHG-0019 / ADR-0015） */

/**
 * 参考解答验证的结论（`verifyReferenceAnswer`，CHG-0019）。
 *
 * 这个端点解的是 `CHG-0008` 留下的死结：发布要求一份与 `content_digest` 绑定的
 * 参考解答验证存证，而存证的设计前提是"执行者 ≠ 记录者"——B2 执行、B3 记录、教师一键触发。
 * 因此按钮的语义是"**请求平台验证这道题**"，不是"我确认这道题是对的"（ADR-0015）。
 *
 * 三处必须照着契约读的地方：
 * 1. `cases_passed == cases_total` 才意味着可发布；`attestation_id` **为 `null` 表示 B3 没有记录**
 *    （观察到的 `cases` 仍然返回，因为那是实际发生的事）——此时即使全通过也**不能**发布。
 * 2. 只有计数、逐用例结局与失败类别。**没有**期望输出、隐藏用例输入与参考解源码，本模块也不得暗示存在。
 * 3. `content_digest` 是验证那一刻的摘要：内容一改即失效（旧存证不可复用）。
 */
export type ReferenceVerification = B2Schemas["ReferenceVerification"];
export type ReferenceVerificationCase = ReferenceVerification["cases"][number];
export type ReferenceVerificationFailure = NonNullable<
  ReferenceVerificationCase["failure_category"]
>;
export type ReferenceVerificationRequest =
  B2Operations["verifyReferenceAnswer"]["requestBody"]["content"]["application/json"];

/** 状态枚举直接取自契约，避免在组件里手写字符串字面量。 */
export type SubmissionState = GradeRow["state"];
export type EvaluationStage = EvaluationStreamFrame["stage"];
export type TerminalReason = EvaluationStreamFrame["terminal_reason"];
export type StreamEvent = EvaluationStreamFrame["event"];

/**
 * 契约里 `200` 响应**没有 schema** 的端点（见
 * `docs/collaboration/interfaces.md` 的"已知契约缺口"第 3 条：`listSections`、
 * `listAuditRecords`、`listModuleHealth`；`listEnrollments`/`listAssignments`
 * 已由 CHG-0007 补上 schema，不再属于缺口）。
 * 这里刻意只暴露 `unknown`：调用方必须经 `src/api/opaque.ts` 处理，
 * 不允许在类型层"补"一个形状出来。
 */
export type OpaqueResponse = unknown;
