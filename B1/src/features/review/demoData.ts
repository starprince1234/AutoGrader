import type {
  AssignmentPublic,
  CoursePublic,
  EnrollmentPublic,
  EvaluationQualitySummary,
  IntakeQuarantineView,
  SectionPublic,
} from "../../api/types";
import { sectionLabel, type SectionOption } from "../../api/directory";
import type { VersionOption } from "./useQualityVersions";

/**
 * 示例预览数据（`/review?demo=1`）。
 *
 * 它不是后端数据，也**不会**写库：只用于在没有真实提交/隔离邮件时检查页面结构。
 * 示例模式下的所有数字都由本文件的静态记录算出（时间范围真的会过滤），而不是写死的
 * 一份"看起来很像"的响应——否则界面上的筛选就成了摆设。
 *
 * 三条边界与真实页面一致：
 *
 * 1. 邮件记录只有隔离视图的字段：没有邮件正文、没有附件、没有学生姓名；
 * 2. 质量聚合只有计数：没有学生、提交、代码；
 * 3. 处置按钮只改本文件的副本（组件内 state），绝不调用写接口。
 */

export const DEMO_COURSE_ID = "00000000-0000-4000-8000-00000000d007";
export const DEMO_SECTION_ID = "00000000-0000-4000-8000-00000000d004";
export const DEMO_ASSIGNMENT_ID = "00000000-0000-4000-8000-00000000d005";
export const DEMO_SUBMISSION_ID = "00000000-0000-4000-8000-00000000d006";
export const DEMO_VERSION_ID = "00000000-0000-4000-8000-00000000d001";
export const DEMO_VERSION_V2_ID = "00000000-0000-4000-8000-00000000d008";
/** 示例教师的平台用户 ID：处置记录里的"处理人"用它，界面显示为短 id。 */
export const DEMO_TEACHER_ID = "00000000-0000-4000-8000-00000000d009";
const DEMO_STUDENT_ID = "00000000-0000-4000-8000-00000000d002";
const DEMO_STUDENT_2 = "00000000-0000-4000-8000-00000000d003";
const DEMO_STUDENT_3 = "00000000-0000-4000-8000-00000000d00a";
const DEMO_STUDENT_4 = "00000000-0000-4000-8000-00000000d00b";

export const DEMO_COURSE: CoursePublic = {
  course_id: DEMO_COURSE_ID,
  course_code: "05",
  title: "Python",
  updated_at: "2026-10-28T03:12:00.000Z",
  section_count: 1,
};

const DEMO_SECTION: SectionPublic = {
  section_id: DEMO_SECTION_ID,
  course_id: DEMO_COURSE_ID,
  section_code: "01",
  term: "2026 秋",
  class_name: "2026 秋 软件工程 1 班",
  enrollment_count: 24,
  created_at: "2026-09-02T02:00:00.000Z",
};

export const DEMO_SECTION_OPTION: SectionOption = {
  sectionId: DEMO_SECTION_ID,
  label: sectionLabel(DEMO_SECTION),
  row: DEMO_SECTION,
};

/* ------------------------------------------------------------------ 邮件提交 */

type DemoQuarantineSeed = {
  id: string;
  reason: IntakeQuarantineView["reason_code"];
  subject: string;
  mailbox: string;
  detail: string;
  uid: number;
  recordedAt: string;
  resolution: "completed" | "dismissed" | null;
  resolvedAt?: string;
  note?: string;
  heldAt?: string;
};

const QUARANTINE_SEEDS: readonly DemoQuarantineSeed[] = [
  {
    id: "00000000-0000-4000-8000-00000000e001",
    reason: "not_uniquely_matched",
    subject: "【作业】统计文件行数 - 王一",
    mailbox: "wangyi@example.edu",
    detail: "同一个发件地址对应名单中的两个学生。",
    uid: 41211,
    recordedAt: "2026-11-07T06:42:00.000Z",
    resolution: null,
  },
  {
    id: "00000000-0000-4000-8000-00000000e002",
    reason: "no_attachments",
    subject: "请问作业提交格式",
    mailbox: "liming@example.edu",
    detail: "邮件正文只有一句话，没有附件。",
    uid: 41210,
    recordedAt: "2026-11-07T06:15:00.000Z",
    resolution: null,
  },
  {
    id: "00000000-0000-4000-8000-00000000e003",
    reason: "malformed_message",
    subject: "（无主题）",
    mailbox: "zhangwei@example.edu",
    detail: "邮件头损坏，无法解析发件人与附件。",
    uid: 41209,
    recordedAt: "2026-11-07T05:58:00.000Z",
    resolution: null,
  },
  {
    id: "00000000-0000-4000-8000-00000000e004",
    reason: "ambiguous_match",
    subject: "统计文件行数 第三次提交",
    mailbox: "zhaolei@example.edu",
    detail: "两个班的同名附件指向不同学生，老师已暂存。",
    uid: 41208,
    recordedAt: "2026-11-07T05:30:00.000Z",
    resolution: null,
    heldAt: "2026-11-07T05:35:00.000Z",
  },
  {
    id: "00000000-0000-4000-8000-00000000e005",
    reason: "not_uniquely_matched",
    subject: "作业提交 - 李四",
    mailbox: "lisi@example.edu",
    detail: "发件地址在名单里不唯一，人工确认后归属。",
    uid: 41207,
    recordedAt: "2026-11-07T04:20:00.000Z",
    resolution: "completed",
    resolvedAt: "2026-11-07T05:01:00.000Z",
    note: "已确认是李四（20260002）的提交。",
  },
  {
    id: "00000000-0000-4000-8000-00000000e006",
    reason: "archive_bomb",
    subject: "附件.zip（解压后 2.4 GB）",
    mailbox: "unknown@example.edu",
    detail: "压缩包解压后体积异常，安全策略拒绝。",
    uid: 41206,
    recordedAt: "2026-11-06T15:40:00.000Z",
    resolution: "dismissed",
    resolvedAt: "2026-11-06T16:02:00.000Z",
    note: "垃圾邮件，作废。",
  },
];

/**
 * 隔离记录的示例副本。返回**新数组**：调用方在示例模式下修改行状态，不能改到模块常量。
 * 默认视图（待处理）会命中前三条，其余靠筛选器切换查看。
 */
export function demoQuarantines(): IntakeQuarantineView[] {
  return QUARANTINE_SEEDS.map((seed) => {
    const resolved = seed.resolution !== null;
    const held = seed.heldAt !== undefined;
    return {
      intake_quarantine_id: seed.id,
      intake: "email",
      reason_code: seed.reason,
      detail: seed.detail,
      mailbox: seed.mailbox,
      message_id: `<${seed.uid}@example.edu>`,
      uid: seed.uid,
      uidvalidity: 20260901,
      recorded_at: seed.recordedAt,
      resolved,
      resolved_student_id:
        seed.resolution === "completed" ? DEMO_STUDENT_2 : null,
      resolution_note: seed.note ?? null,
      subject: seed.subject,
      resolution: seed.resolution,
      resolved_at: seed.resolvedAt ?? null,
      resolved_by: resolved ? DEMO_TEACHER_ID : null,
      resolved_submission_id:
        seed.resolution === "completed" ? DEMO_SUBMISSION_ID : null,
      held,
      held_at: seed.heldAt ?? null,
      held_by: held ? DEMO_TEACHER_ID : null,
      scope_course_id: DEMO_COURSE_ID,
      scope_section_id: DEMO_SECTION_ID,
    };
  });
}

/* ------------------------------------------------------------------ 题目质量 */

type DemoRun = {
  at: string;
  student: number;
  /** 每个用例是否失败：下标就是契约里的 `ordinal`。 */
  caseFailed: boolean[];
  category: string | null;
  unmeasured: boolean;
  security: "found" | "not_found" | "not_checked";
};

type DemoQualityProfile = {
  questionVersionId: string;
  version: number;
  questionTitle: string;
  /** 评测次数（= 契约的 `attempt_count`）。 */
  total: number;
  /** 独立作答人数（= 契约的 `answerer_count`）。 */
  students: number;
  /** 每个用例的失败次数（按 ordinal 排列），不得超过 `total`。 */
  caseFailures: readonly number[];
  categories: readonly { category: string; count: number }[];
  unmeasured: number;
  security: { found: number; notFound: number; notChecked: number };
  startAt: string;
  stepMinutes: number;
};

/**
 * 示例运行的时间线：v1 是"已经跑熟的题目"（42 次、18 人，含系统故障与安全告警）；
 * v2 是"刚发布的新版本"（3 次、2 人，样本不足、安全扫描一次都没跑）。
 * 两种状态都要能看到，否则示例只能证明"正常路径"。
 */
const QUALITY_PROFILES: readonly DemoQualityProfile[] = [
  {
    questionVersionId: DEMO_VERSION_ID,
    version: 1,
    questionTitle: "平均分判定",
    total: 42,
    students: 18,
    caseFailures: [2, 31, 0],
    categories: [
      { category: "output_mismatch", count: 11 },
      { category: "timeout", count: 2 },
      { category: "infrastructure_error", count: 5 },
    ],
    unmeasured: 6,
    security: { found: 1, notFound: 27, notChecked: 14 },
    startAt: "2026-10-21T01:00:00.000Z",
    stepMinutes: 480,
  },
  {
    questionVersionId: DEMO_VERSION_V2_ID,
    version: 2,
    questionTitle: "平均分判定",
    total: 3,
    students: 2,
    caseFailures: [1, 1, 0],
    categories: [{ category: "output_mismatch", count: 1 }],
    unmeasured: 0,
    security: { found: 0, notFound: 0, notChecked: 3 },
    startAt: "2026-11-05T02:00:00.000Z",
    stepMinutes: 360,
  },
];

/** 把 profile 展开成逐条运行记录：同一份数据既喂聚合，也保证时间筛选真的生效。 */
function buildRuns(profile: DemoQualityProfile): DemoRun[] {
  const categoryQueue = profile.categories.flatMap(({ category, count }) =>
    Array.from({ length: count }, () => category),
  );
  const start = Date.parse(profile.startAt);
  return Array.from({ length: profile.total }, (_, index) => {
    const security: DemoRun["security"] =
      index < profile.security.found
        ? "found"
        : index < profile.security.found + profile.security.notFound
          ? "not_found"
          : "not_checked";
    return {
      at: new Date(start + index * profile.stepMinutes * 60_000).toISOString(),
      student: index % profile.students,
      caseFailed: profile.caseFailures.map((failures) => index < failures),
      category: categoryQueue[index] ?? null,
      // 资源用量没测到的是**最近几次**运行：时间范围一变，这个数也跟着变。
      unmeasured: index >= profile.total - profile.unmeasured,
      security,
    };
  });
}

const RUNS_BY_VERSION = new Map<string, DemoRun[]>(
  QUALITY_PROFILES.map((profile) => [profile.questionVersionId, buildRuns(profile)]),
);

/** 版本下拉的示例选项（标题与版本号手写，不请求 B3）。 */
export const DEMO_VERSION_OPTIONS: VersionOption[] = QUALITY_PROFILES.map((profile) => ({
  questionVersionId: profile.questionVersionId,
  label: `${profile.questionTitle} · v${profile.version}`,
  status: "published",
  questionTitle: profile.questionTitle,
  version: profile.version,
}));

function emptySummary(questionVersionId: string): EvaluationQualitySummary {
  return {
    question_version_id: questionVersionId,
    answerer_count: 0,
    attempt_count: 0,
    failure_categories: [],
    static_rule_hits: [],
    case_failure_rates: [],
    unmeasured_runs: 0,
    security_found: 0,
    security_not_found: 0,
    security_not_checked: 0,
  };
}

/**
 * 按示例运行记录重算聚合。`from`/`to` 是 RFC 3339；`to` 是**开区间**（与页面的
 * "结束时间必须晚于开始时间"一致，也不会把一分钟内的事件算两次）。
 */
export function demoQualitySummary(
  questionVersionId: string,
  from: string | null = null,
  to: string | null = null,
): EvaluationQualitySummary {
  const profile = QUALITY_PROFILES.find(
    (item) => item.questionVersionId === questionVersionId,
  );
  const runs = RUNS_BY_VERSION.get(questionVersionId);
  if (!profile || !runs) return emptySummary(questionVersionId);

  const fromMs = from === null ? null : Date.parse(from);
  const toMs = to === null ? null : Date.parse(to);
  const inRange = runs.filter((run) => {
    const at = Date.parse(run.at);
    if (Number.isNaN(at)) return false;
    if (fromMs !== null && at < fromMs) return false;
    if (toMs !== null && at >= toMs) return false;
    return true;
  });

  const categoryCounts = new Map<string, number>();
  for (const run of inRange) {
    if (run.category === null) continue;
    categoryCounts.set(run.category, (categoryCounts.get(run.category) ?? 0) + 1);
  }

  const countSecurity = (status: DemoRun["security"]) =>
    inRange.filter((run) => run.security === status).length;

  return {
    question_version_id: questionVersionId,
    answerer_count: new Set(inRange.map((run) => run.student)).size,
    attempt_count: inRange.length,
    failure_categories: [...categoryCounts.entries()]
      .map(([category, count]) => ({ category, count }))
      .sort((left, right) => right.count - left.count),
    static_rule_hits: [],
    case_failure_rates: profile.caseFailures.map((_, ordinal) => ({
      ordinal,
      attempts: inRange.length,
      failures: inRange.filter((run) => run.caseFailed[ordinal]).length,
    })),
    unmeasured_runs: inRange.filter((run) => run.unmeasured).length,
    security_found: countSecurity("found"),
    security_not_found: countSecurity("not_found"),
    security_not_checked: countSecurity("not_checked"),
  };
}

/* ------------------------------------------------------------------ 匹配用 */

export function demoAssignment(courseId: string | null): AssignmentPublic {
  return {
    assignment_id: DEMO_ASSIGNMENT_ID,
    course_id: courseId ?? DEMO_COURSE_ID,
    section_ids: [DEMO_SECTION_ID],
    student_ids: [],
    title: "示例作业：统计文件行数",
    status: "open",
    due_at: "2026-11-20T15:59:00.000Z",
    question_version_ids: [DEMO_VERSION_ID],
    created_at: "2026-11-01T07:00:00.000Z",
    updated_at: "2026-11-01T07:00:00.000Z",
  };
}

export const DEMO_STUDENTS: EnrollmentPublic[] = [
  {
    enrollment_id: "00000000-0000-4000-8000-00000000e101",
    section_id: DEMO_SECTION_ID,
    student_id: DEMO_STUDENT_ID,
    student_number: "20260001",
    display_name: "张三",
    is_active: true,
    updated_at: "2026-11-01T07:00:00.000Z",
  },
  {
    enrollment_id: "00000000-0000-4000-8000-00000000e102",
    section_id: DEMO_SECTION_ID,
    student_id: DEMO_STUDENT_2,
    student_number: "20260002",
    display_name: "李四",
    is_active: true,
    updated_at: "2026-11-01T07:00:00.000Z",
  },
  {
    enrollment_id: "00000000-0000-4000-8000-00000000e103",
    section_id: DEMO_SECTION_ID,
    student_id: DEMO_STUDENT_3,
    student_number: "20260003",
    display_name: "王五",
    is_active: true,
    updated_at: "2026-11-01T07:00:00.000Z",
  },
  {
    enrollment_id: "00000000-0000-4000-8000-00000000e104",
    section_id: DEMO_SECTION_ID,
    student_id: DEMO_STUDENT_4,
    student_number: "20260004",
    display_name: "赵六",
    is_active: true,
    updated_at: "2026-11-01T07:00:00.000Z",
  },
];

