/**
 * 契约形状的测试夹具（**只在测试中使用**，边界说明见 `httpBoundary.ts`）。
 *
 * 这里的每个对象都用**生成的契约类型**标注，因此夹具本身会被 `tsc` 校验：
 * 一旦 MAIN 契约变化，夹具会先编译失败，而不是让测试继续通过一个过期的形状。
 */
import type {
  AdvisoryScore,
  AppealPack,
  AssignmentPublic,
  AuthoringAssistance,
  ComparisonDiagnostics,
  CourseSummary,
  EnrollmentPublic,
  EvaluationCommentary,
  EvaluationQualitySummary,
  EvaluationReport,
  EvaluationStreamFrame,
  GradeRow,
  IntakeQuarantineView,
  ProblemDetails,
  QuestionDraftGeneration,
  QuestionDraftGenerationCapabilities,
  QuestionPublic,
  QuestionTagCoverage,
  QuestionVersionPublic,
  ReferenceVerification,
  RegistrationAllowlistReplacement,
  RegistrationAllowlistState,
  RosterImportPreview,
  Role,
  SessionCreated,
  SubmissionAccepted,
  UserSummary,
} from "../api/types";

export const UUID_A = "00000000-0000-4000-8000-0000000000a1";
export const UUID_B = "00000000-0000-4000-8000-0000000000b2";
export const UUID_C = "00000000-0000-4000-8000-0000000000c3";
export const UUID_D = "00000000-0000-4000-8000-0000000000d4";
export const UUID_RUN = "00000000-0000-4000-8000-000000000004";
export const SHA_A = "a".repeat(64);

export function sessionCreated(overrides?: Partial<SessionCreated>): SessionCreated {
  return {
    access_token: "test.token.value",
    token_type: "Bearer",
    expires_in: 900,
    roles: ["teacher"] as Role[],
    scope: [{ course_id: UUID_A, section_id: UUID_B }],
    ...overrides,
  } as SessionCreated;
}

export function problem(overrides?: Partial<ProblemDetails>): ProblemDetails {
  return {
    type: "https://autograder.local/problems/forbidden",
    title: "Forbidden",
    status: 403,
    detail: "Role or course scope does not permit this operation.",
    instance: "/api/v1/enrollments",
    ...overrides,
  };
}

export function courseSummary(overrides?: Partial<CourseSummary>): CourseSummary {
  return { course_id: UUID_A, course_code: "OS-2026", title: "操作系统", ...overrides };
}

export function coursePage(courses: CourseSummary[], nextCursor: string | null = null) {
  return { items: courses, next_cursor: nextCursor };
}

/**
 * 作业（`AssignmentPublic`，CHG-0007）。B1 只拿到 `assignment_id → question_version_ids`，
 * 没有别的字段可用；`status` 只有 `open`/`archived`。
 */
export function assignmentPublic(overrides?: Partial<AssignmentPublic>): AssignmentPublic {
  return {
    assignment_id: UUID_A,
    course_id: UUID_B,
    section_id: UUID_C,
    title: "第一次作业",
    status: "open",
    due_at: "2026-10-01T15:59:00.000Z",
    question_version_ids: [UUID_C],
    created_at: "2026-09-15T07:00:00.000Z",
    updated_at: "2026-09-15T07:00:00.000Z",
    ...overrides,
  } as AssignmentPublic;
}

export function assignmentPage(
  items: AssignmentPublic[],
  nextCursor: string | null = null,
) {
  return { items, next_cursor: nextCursor };
}

/**
 * 名单行（`EnrollmentPublic`，CHG-0007 起契约有响应 schema）。
 * `student_id` 是连接名单与成绩的**唯一**可用键，且可空：名单里的学生可能还没绑定账号。
 */
export function enrollmentPublic(overrides?: Partial<EnrollmentPublic>): EnrollmentPublic {
  return {
    enrollment_id: UUID_C,
    section_id: UUID_B,
    student_id: UUID_D,
    student_number: "20260001",
    display_name: "张三",
    is_active: true,
    updated_at: "2026-09-15T07:00:00.000Z",
    ...overrides,
  } as EnrollmentPublic;
}

export function enrollmentPage(
  items: EnrollmentPublic[],
  nextCursor: string | null = null,
) {
  return { items, next_cursor: nextCursor };
}

/**
 * 用户行（`UserSummary`，P14）。
 *
 * `is_active` 与 `updated_at` 都是必需字段：前者让"停用了没有"可读，后者是
 * `PATCH /users/{id}` 的 `expected_updated_at` 的唯一来源。夹具按"可登录、刚读到的行"给默认值。
 */
export function userSummary(overrides?: Partial<UserSummary>): UserSummary {
  return {
    user_id: UUID_C,
    username: "zhangsan",
    display_name: "张三",
    student_number: "20260001",
    roles: ["student"],
    is_active: true,
    updated_at: "2026-09-15T07:00:00.000Z",
    created_at: "2026-09-15T07:00:00.000Z",
    ...overrides,
  } as UserSummary;
}

/** 用户页（`listUsers` 的 200）：游标与行组合，没有权威总数。 */
export function userPage(items: UserSummary[], nextCursor: string | null = null) {
  return { items, next_cursor: nextCursor };
}

/** 当前生效的注册名单批次（`getRegistrationAllowlist`）：默认"已导入过一份 36 人的名单"。 */
export function allowlistState(
  overrides?: Partial<RegistrationAllowlistState>,
): RegistrationAllowlistState {
  return {
    batch_id: UUID_A,
    filename: "allowlist-2026.csv",
    row_count: 36,
    uploaded_at: "2026-09-15T07:00:00.000Z",
    ...overrides,
  } as RegistrationAllowlistState;
}

/** 全量替换的结果（`replaceRegistrationAllowlist`）：三个数字都来自服务端事务。 */
export function allowlistReplacement(
  overrides?: Partial<RegistrationAllowlistReplacement>,
): RegistrationAllowlistReplacement {
  return {
    batch_id: UUID_B,
    row_count: 38,
    replaced_row_count: 36,
    content_sha256: SHA_A,
    ...overrides,
  } as RegistrationAllowlistReplacement;
}

export function gradeRow(overrides?: Partial<GradeRow>): GradeRow {
  return {
    submission_id: UUID_C,
    student_id: UUID_D,
    student_number: "20260001",
    display_name: "张三",
    course_id: UUID_A,
    section_id: UUID_B,
    question_version_id: UUID_C,
    state: "completed",
    score: 86,
    max_score: 100,
    is_pass: true,
    submitted_at: "2026-09-15T08:00:00.000Z",
    evaluation_run_id: UUID_RUN,
    ...overrides,
  } as GradeRow;
}

export function gradePage(rows: GradeRow[], nextCursor: string | null = null) {
  return { items: rows, next_cursor: nextCursor };
}

export function rosterImportPreview(
  overrides?: Partial<RosterImportPreview>,
): RosterImportPreview {
  return {
    roster_import_id: UUID_A,
    status: "previewed",
    course_id: UUID_B,
    section_id: UUID_C,
    detected_columns: ["姓名", "学号", "课程编号", "课序号", "邮箱"],
    proposed_mapping: {
      display_name: "姓名",
      student_number: "学号",
      course_code: "课程编号",
      section_code: "课序号",
    },
    accepted_rows: [
      {
        row_number: 2,
        values: {
          display_name: "张三",
          student_number: "20260001",
          course_code: "OS-2026",
          section_code: "01",
          email: "zhangsan@example.edu",
        },
        reason: null,
      },
    ],
    rejected_rows: [
      {
        row_number: 3,
        values: { display_name: "李四", student_number: "20260001", course_code: "OS-2026" },
        reason: "重复学号",
      },
      {
        row_number: 4,
        values: { display_name: "王五", student_number: "", course_code: "OS-2026" },
        reason: "缺少必填字段：学号",
      },
    ],
    created_at: "2026-09-15T07:30:00.000Z",
    ...overrides,
  } as RosterImportPreview;
}

export function submissionAccepted(overrides?: Partial<SubmissionAccepted>): SubmissionAccepted {
  return {
    submission_id: UUID_C,
    state: "received",
    evaluation_run_id: UUID_RUN,
    idempotent_replay: false,
    accepted_at: "2026-09-15T08:00:05.000Z",
    ...overrides,
  } as SubmissionAccepted;
}

export function streamFrame(
  overrides?: Partial<EvaluationStreamFrame>,
): EvaluationStreamFrame {
  return {
    evaluation_run_id: UUID_RUN,
    event: "progress",
    state: "running",
    stage: "dynamic_execution",
    sequence: 1,
    cases_total: 3,
    cases_done: 1,
    score: null,
    is_pass: null,
    terminal_reason: null,
    message: null,
    occurred_at: "2026-09-15T08:01:00.000Z",
    ...overrides,
  } as EvaluationStreamFrame;
}

export function questionPublic(overrides?: Partial<QuestionPublic>): QuestionPublic {
  return {
    question_id: UUID_A,
    course_id: UUID_B,
    title: "统计文件行数",
    stem_markdown: "# 统计文件行数\n\n请实现 `wc -l` 的最小版本。",
    stem_assets: [
      { asset_id: UUID_C, content_type: "image/png", alt_text: "示例输出" },
    ],
    language: "bash",
    status: "published",
    tags: ["unix"],
    // Version history is part of the public DTO: publishing needs a
    // question_version_id, and RQ-B-1-40 acceptance 5 renders historical versions.
    versions: [
      {
        question_version_id: UUID_A,
        version: 3,
        status: "published",
        published_at: "2026-09-15T07:20:00.000Z",
      },
      {
        question_version_id: "00000000-0000-4000-8000-0000000000b9",
        version: 2,
        status: "published",
        published_at: "2026-09-14T07:20:00.000Z",
      },
    ],
    created_at: "2026-09-15T07:00:00.000Z",
    updated_at: "2026-09-15T07:10:00.000Z",
    ...overrides,
  } as QuestionPublic;
}

/**
 * 题库标签覆盖度（`QuestionTagCoverage`，CHG-0020）。
 *
 * 夹具刻意让 `sum(tags[].question_count) = 21` **大于** `total_questions = 16`：一道题可以带
 * 多个标签，逐标签计数按设计重叠。任何把逐标签计数当成"题目数之和"去渲染或校验的实现，
 * 在这个夹具上立刻对不上。可校验的恒等式只有 `12 + 4 == 16`。
 *
 * 标签顺序就是契约承诺的顺序（`question_count` 降序、同数按 `tag` 升序），因此它**不是**
 * 字母序：`process-scheduling` 与 `unix` 同为 9 题，前者按 tag 升序在前。客户端若再排一次
 * （按 tag 排、或按数量升序），渲染顺序就会与这里不同。
 */
export function questionTagCoverage(
  overrides?: Partial<QuestionTagCoverage>,
): QuestionTagCoverage {
  return {
    course_id: UUID_A,
    total_questions: 16,
    tagged_questions: 12,
    untagged_questions: 4,
    tags: [
      { tag: "process-scheduling", question_count: 9 },
      { tag: "unix", question_count: 9 },
      { tag: "scheduling", question_count: 3 },
    ],
    ...overrides,
  } as QuestionTagCoverage;
}

export function questionVersionPublic(
  overrides?: Partial<QuestionVersionPublic>,
): QuestionVersionPublic {
  return {
    question_version_id: UUID_C,
    question_id: UUID_A,
    version: 3,
    status: "published",
    language: "bash",
    adapter_kind: "stdin",
    stem_markdown: "# 统计文件行数\n\n请实现 `wc -l` 的最小版本。",
    stem_assets: [],
    content_digest: SHA_A,
    published_at: "2026-09-15T07:20:00.000Z",
    max_files: 5,
    max_total_bytes: 1048576,
    ...overrides,
  } as QuestionVersionPublic;
}

/** 确定性比较策略检查（CHG-0013）：同输入同输出的规则命中。 */
export function comparisonDiagnostics(
  overrides?: Partial<ComparisonDiagnostics>,
): ComparisonDiagnostics {
  return {
    question_id: UUID_A,
    findings: [
      {
        rule_id: "CMP-001",
        severity: "warning",
        message: "期望输出含 CRLF 行尾，比较策略为 exact：在 Linux runner 上会判为不一致。",
        ordinal: 1,
      },
    ],
    ...overrides,
  } as ComparisonDiagnostics;
}

/** 出题辅助（RQ-T-10）：模型生成的**建议**，`confidence` 只有 `advisory`。 */
export function authoringAssistance(
  overrides?: Partial<AuthoringAssistance>,
): AuthoringAssistance {
  return {
    question_id: UUID_A,
    status: "ready",
    suggestions: [
      {
        topic: "wording",
        message: "题干第 2 段同时要求“按行数”和“按字节”，建议明确其中一个。",
        confidence: "advisory",
      },
    ],
    provenance: {
      provider: "example-provider",
      model: "example-model-v1",
      prompt_version: "authoring-v3",
    },
    disclaimers: [
      "由模型生成，非确定性结论。",
      "不参与判分，不作为申诉依据。",
    ],
    ...overrides,
  } as AuthoringAssistance;
}

/**
 * AI 结构化草稿生成（AI-FLOW-01A）：一份可编辑候选。
 *
 * 刻意带上 `needs_review` 才会有的**字段级**问题与一个权重为 `null` 的用例：
 * 这两处正是"候选不是成品"的体现，测试要能证明页面把它们当成待补项而不是错误结果。
 */
export function questionDraftGeneration(
  overrides?: Partial<QuestionDraftGeneration>,
): QuestionDraftGeneration {
  return {
    generation_id: UUID_C,
    course_id: UUID_A,
    status: "needs_review",
    proposal: {
      title: "统计标准输入的行数",
      stem_markdown: "读取标准输入，输出它的行数。",
      language: "python",
      adapter_kind: "stdin",
      tags: ["unix", "text-processing"],
      reference_answer: "import sys\nprint(len(sys.stdin.read().splitlines()))\n",
      adapter: {
        expect_exit_code: 0,
        comparison: { stdout: "ignore_trailing_whitespace", stderr: "ignored" },
      },
      cases: [
        {
          name: "三行",
          ordinal: 0,
          weight: 1,
          visible_to_student: false,
          stdin: "a\nb\nc\n",
          expected_stdout: "3\n",
          expected_exit_code: 0,
        },
        // 服务端判定权重不可用 → null + 一条字段问题；页面必须留空让教师填。
        { name: "空输入", ordinal: 1, weight: null, visible_to_student: false },
      ],
    },
    warnings: [
      { field: "cases[1].weight", message: "权重必须是大于 0 的有限数字；已留空。", severity: "warning" },
    ],
    provenance: {
      provider: "example-provider",
      model: "example-model-v1",
      prompt_version: "b3-draft-generation-v1",
    },
    disclaimers: [
      "模型生成的是候选草稿，不是正确答案；每个字段都由你审阅并确认。",
      "生成不会创建题目、版本或用例，只有你点击“保存草稿”才会写入题库。",
    ],
    created_at: "2026-10-24T08:00:00.000Z",
    expires_at: "2026-10-25T08:00:00.000Z",
    accepted_question_version_id: null,
    ...overrides,
  } as QuestionDraftGeneration;
}

/** 生成能力（`getQuestionDraftGenerationCapabilities`）：上限来自部署配置。 */
export function generationCapabilities(
  overrides?: Partial<QuestionDraftGenerationCapabilities>,
): QuestionDraftGenerationCapabilities {
  return {
    course_id: UUID_A,
    provider_status: "enabled",
    provider: "example-provider",
    model: "example-model-v1",
    learning_objective_max_chars: 1000,
    constraints_max_chars: 500,
    desired_case_count_max: 10,
    proposal_max_cases: 20,
    retention_seconds: 86400,
    requests_remaining: 7,
    retry_after: null,
    disclaimers: ["模型生成的是候选草稿，不是正确答案。"],
    ...overrides,
  } as QuestionDraftGenerationCapabilities;
}

/**
 * 与 `MAIN/contracts/examples/evaluation-report.json` 同形（含隐藏用例不给期望输出）。
 *
 * 第二个用例刻意带有 CHG-0012 的三个新块：`resource` 有值（含 `limit_hit`）、
 * `artifacts` 有期望/实际摘要与文件改动、`explanation` 是确定性文本。
 * 第一个用例只有 `resource: null`：**未测量**，不是 0。
 */
export function evaluationReport(overrides?: Partial<EvaluationReport>): EvaluationReport {  return {
    evaluation_run_id: UUID_RUN,
    submission_id: UUID_C,
    question_version_id: UUID_D,
    state: "completed",
    overall: {
      score: 86,
      max_score: 100,
      is_pass: true,
      summary: "Static checks clean; 2 of 3 weighted cases passed.",
    },
    static_findings: [
      {
        rule_id: "PY-UNUSED-IMPORT",
        severity: "info",
        file: "solution.py",
        line: 2,
        column: 1,
        message: "Unused import 'sys'.",
        blocking: false,
      },
    ],
    security_conclusion: { status: "not_found", findings: [] },
    case_evidence: [
      {
        case_id: UUID_A,
        ordinal: 0,
        weight: 1,
        awarded: 86,
        passed: true,
        failure_category: null,
        actual_output_digest: SHA_A,
        actual_output_excerpt: "3\n",
        expected_output_excerpt: "3\n",
        duration_ms: 41,
        message: null,
        resource: null,
        artifacts: null,
        explanation: "Case 1 passed: exit code 0 and stdout matched the declared policy.",
      },
      {
        case_id: UUID_B,
        ordinal: 1,
        weight: 2,
        awarded: 0,
        passed: false,
        failure_category: "resource_limit",
        actual_output_digest: SHA_A,
        actual_output_excerpt: "1\n",
        expected_output_excerpt: null,
        duration_ms: 12,
        message: "Output differs (hidden case: expected output withheld from the student view).",
        resource: {
          memory_peak_bytes: 1048576,
          cpu_user_seconds: 0.25,
          cpu_system_seconds: null,
          pids_peak: 3,
          wall_seconds: 1.5,
          limit_hit: "memory",
        },
        artifacts: {
          expected_sha256: "b".repeat(64),
          actual_sha256: SHA_A,
          created: [
            { path: "out/count.txt", content_sha256: SHA_A, size_bytes: 4 },
            { path: "out/empty.txt", content_sha256: null, size_bytes: null },
          ],
          modified: [{ path: "input/data.txt", content_sha256: SHA_A, size_bytes: 12 }],
          deleted: [],
        },
        explanation:
          "Case 2 failed: the memory limit was hit before the program produced its output file.",
      },
    ],
    binding: {
      submission_digest: SHA_A,
      question_version_binding: SHA_A,
      package_digest: SHA_A,
      runner_policy_digest: SHA_A,
      environment_digest: SHA_A,
      interpreter_version: "CPython 3.12.3",
    },
    total_check: { sum_of_awards: 86, matches_total: true },
    result_digest: "4".repeat(64),
    generated_at: "2026-09-15T08:01:30.000Z",
    ...overrides,
  } as EvaluationReport;
}

/**
 * AI 评分参考（`AdvisoryScore`，RQ-T-30 / `ADR-0014`）。
 *
 * 夹具的 `score` 刻意与 `evaluationReport()` 的权威分（86）**不同**（这里 62）：
 * 只要页面上出现任何"两者相等/被合并"的迹象，测试就会看出来。
 * `max_score` 按契约是服务端按用例权重填的，不是模型产的。
 */
export function advisoryScore(overrides?: Partial<AdvisoryScore>): AdvisoryScore {
  return {
    score: 62,
    max_score: 100,
    rationale: "第二个用例未通过，其余用例符合期望策略；因此建议接近部分分。",
    confidence: "medium",
    ...overrides,
  } as AdvisoryScore;
}

/**
 * 教学评语（`EvaluationCommentary`，RQ-T-20）。默认是 `ready` 且带参考分——夹具取最"满"的形状，
 * 三态由各测试显式覆盖。`disclaimers` 是契约的规范标注，测试按**逐字**断言引用它们。
 */
export function evaluationCommentary(
  overrides?: Partial<EvaluationCommentary>,
): EvaluationCommentary {
  return {
    evaluation_run_id: UUID_RUN,
    status: "ready",
    text: "本题主要失分在第二个用例：程序在内存上限到达前没有产出输出文件。",
    advisory_score: advisoryScore(),
    provenance: {
      provider: "example-provider",
      model: "example-model-v1",
      prompt_version: "commentary-v2",
      input_scope: [
        "case_outcomes",
        "failure_categories",
        "static_rule_hits",
        "security_conclusion",
        "score_breakdown",
        "question_public_metadata",
      ],
    },
    disclaimers: [
      "由模型生成，非确定性内容。",
      "不参与判分：不进入成绩、导出与结果摘要。",
      "不作为申诉依据。",
    ],
    ...overrides,
  } as EvaluationCommentary;
}

/**
 * 隔离记录（`IntakeQuarantineView`，CHG-0014；词表与可归属集合见 CHG-0021）。刻意**只有识别性元数据与已脱敏的原因**：
 * 夹具里没有附件内容、没有源码、没有提交的任何部分——页面若去渲染契约之外的字段，
 * 就没有任何东西可渲染。
 *
 * 默认 `reason_code` 取 `not_uniquely_matched`：它是 `CHG-0021` §2.1 词表里**可归属**的两类之一，
 * 因此默认夹具代表"教师真的能归属"的那种记录。需要"不可归属"的用例请显式传
 * （例如 `archive_bomb`、`attachment_too_large`）。
 */
export function intakeQuarantineView(
  overrides?: Partial<IntakeQuarantineView>,
): IntakeQuarantineView {
  return {
    intake_quarantine_id: UUID_C,
    intake: "email",
    reason_code: "not_uniquely_matched",
    detail: "邮件主题与正文都未能解析出作业标识；已按策略记录并脱敏。",
    mailbox: "submissions@example.edu",
    message_id: "<20260915.abc123@example.edu>",
    uid: 4211,
    recorded_at: "2026-09-15T08:05:00.000Z",
    resolved: false,
    resolved_student_id: null,
    resolution_note: null,
    ...overrides,
  } as IntakeQuarantineView;
}

export function intakeQuarantinePage(
  items: IntakeQuarantineView[],
  nextCursor: string | null = null,
) {
  return { items, next_cursor: nextCursor };
}

/**
 * 可移动的申诉证据包（`AppealPack`，CHG-0014）。
 *
 * `redactions` 非空是契约要求（`minItems: 1`）；夹具里的 `runner_policy_summary` 只放
 * 隔离设置，**不放**宿主机路径——契约规定绝对路径按构造被排除，夹具必须与之一致。
 */
export function appealPack(overrides?: Partial<AppealPack>): AppealPack {
  return {
    evaluation_run_id: UUID_RUN,
    result_digest: "4".repeat(64),
    report: evaluationReport(),
    binding: {
      submission_digest: SHA_A,
      question_version_binding: SHA_A,
      package_digest: "b".repeat(64),
      runner_policy_digest: "c".repeat(64),
      environment_digest: "d".repeat(64),
    },
    runner_policy_summary: {
      network: "disabled",
      filesystem: "read_only_root",
      pids_limit: 64,
      memory_limit_bytes: 268435456,
    },
    replay:
      "在相同的 runner 策略下用同一份提交重新评测，并比较 result_digest 与五个绑定摘要。",
    redactions: [
      "已移除宿主机文件系统绝对路径（描述内部布局）。",
      "未包含学生视图之外的受保护内容（隐藏用例与期望输出）。",
    ],
    generated_at: "2026-09-15T09:00:00.000Z",
    ...overrides,
  } as AppealPack;
}

/**
 * 题目质量聚合（`EvaluationQualitySummary`，CHG-0015）。每一项都是计数：
 * 夹具刻意包含 `infrastructure_error`（系统原因）与 `security_not_checked > 0`，
 * 因为这两处正是"最容易被不诚实地渲染"的地方。
 *
 * `static_rule_hits` 按**服务端真实形状**给：B2 以冻结的规则目录逐条为准
 * （`B2/src/autograder_b2/evaluation/quality.py` 的 `_rule_hits`），**未命中即 0**，
 * 目录之外的 `rule_id` 按字典序追加在后。因此夹具里必须有一条 `count = 0` 的目录内规则，
 * 否则"0 也要显示并解释"这条断言会变成永远成立的空检查。
 */
export function evaluationQualitySummary(
  overrides?: Partial<EvaluationQualitySummary>,
): EvaluationQualitySummary {
  return {
    question_version_id: UUID_C,
    attempt_count: 42,
    failure_categories: [
      { category: "output_mismatch", count: 11 },
      { category: "timeout", count: 3 },
      { category: "infrastructure_error", count: 5 },
    ],
    static_rule_hits: [
      { rule_id: "PY-UNUSED-IMPORT", severity: "info", count: 9 },
      { rule_id: "CMP-004", severity: "warning", count: 2 },
      // 目录内、本范围内一次都没命中：必须出现且必须是 0（不是被过滤掉）。
      { rule_id: "STAT-PY-013", severity: "info", count: 0 },
      // 目录之外的历史 rule_id：服务端按字典序追加在目录之后。
      { rule_id: "ZZ-LEGACY-001", severity: "warning", count: 4 },
    ],
    case_failure_rates: [
      { ordinal: 0, attempts: 42, failures: 2 },
      { ordinal: 1, attempts: 42, failures: 31 },
      { ordinal: 2, attempts: 0, failures: 0 },
    ],
    unmeasured_runs: 6,
    security_found: 1,
    security_not_found: 27,
    security_not_checked: 14,
    ...overrides,
  } as EvaluationQualitySummary;
}

/**
 * 参考解答验证的结论（`ReferenceVerification`，CHG-0019 / ADR-0015）。
 *
 * 默认是最"满"的形状：全部用例通过 + 存证已记录（`attestation_id` 非空）——即**可发布**的那一种。
 * 三态由各测试显式覆盖，另外两个必须在测试里被区分开的变体是：
 * - `attestation_id: null`（B3 没有记录存证 → 即使全通过也**不能**发布）；
 * - 全部用例的 `failure_category: "infrastructure_error"`（本机 Windows 的常态：
 *   验证需要 Linux 沙箱环境，**不是**"验证失败"）。
 *
 * `content_digest` 与 `questionVersionPublic()` 的默认摘要同为 `SHA_A`：两者不同就意味着
 * 这份结论属于另一个版本的摘要（内容被改过），旧存证不可复用。
 */
export function referenceVerification(
  overrides?: Partial<ReferenceVerification>,
): ReferenceVerification {
  return {
    question_version_id: UUID_C,
    content_digest: SHA_A,
    language: "bash",
    adapter_kind: "stdin",
    cases_total: 2,
    cases_passed: 2,
    cases: [
      { ordinal: 0, passed: true, failure_category: null },
      { ordinal: 1, passed: true, failure_category: null },
    ],
    verified_at: "2026-09-15T09:30:00.000Z",
    attestation_id: "00000000-0000-4000-8000-0000000000e5",
    ...overrides,
  } as ReferenceVerification;
}

