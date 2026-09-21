import type { EnrollmentPublic, GradeRow } from "../../api/types";
import { t } from "../../i18n";

/**
 * 名单 × 成绩的**外连接**（把"应该交的人"与"真的交了的人"接起来）。
 *
 * 这是成绩页「谁还没交」与作业中心「提交情况」共用的**唯一**一处连接逻辑：
 * 连接键只有 `student_id`（成绩侧是平台用户 ID；名单侧的 `student_id` 可空）。
 * 两边都取全量后才调用——见 `useCohort`，只加载第一页就判定"未提交"会把分页缺陷
 * 变成对学生的错误结论。
 */

export type CohortStatus =
  | "not_submitted"
  | "failed"
  | "running"
  | "rejected"
  | "graded"
  | "unbound";

/** 筛选下拉与统计的固定顺序（先看"没交的"，再看交了的）。 */
export const COHORT_STATUS_ORDER: CohortStatus[] = [
  "not_submitted",
  "failed",
  "running",
  "rejected",
  "graded",
  "unbound",
];

/** 名单行 × 该学生最新的提交行。`grade` 为 null 表示给定成绩行里没有任何提交。 */
export type CohortRow = {
  enrollmentId: string;
  studentId: string | null;
  studentNumber: string;
  displayName: string;
  status: CohortStatus;
  grade: GradeRow | null;
};

export type CohortSummary = {
  rows: CohortRow[];
  counts: Record<CohortStatus, number>;
  /** 提交了但不在名单里的人（转课序、退课，或筛选范围没对上）。 */
  outsideRoster: number;
};

function statusOf(grade: GradeRow | null, studentId: string | null): CohortStatus {
  if (studentId === null) return "unbound";
  if (grade === null) return "not_submitted";
  switch (grade.state) {
    case "completed":
      return "graded";
    case "failed":
      return "failed";
    case "rejected":
      return "rejected";
    default:
      // received / validated / queued / running：评测尚未到达终态。
      return "running";
  }
}

/** 按 `submitted_at` 取最新一行：一个学生可能有多次提交，最新一次代表他的当前状态。 */
function latestByStudent(rows: GradeRow[]): Map<string, GradeRow> {
  const latest = new Map<string, GradeRow>();
  for (const row of rows) {
    const previous = latest.get(row.student_id);
    if (!previous || (row.submitted_at ?? "") > (previous.submitted_at ?? "")) {
      latest.set(row.student_id, row);
    }
  }
  return latest;
}

export function buildCohort(roster: EnrollmentPublic[], grades: GradeRow[]): CohortSummary {
  const latest = latestByStudent(grades);
  const rosterStudentIds = new Set<string>();
  const rows: CohortRow[] = roster.map((enrollment) => {
    const studentId = enrollment.student_id ?? null;
    if (studentId !== null) rosterStudentIds.add(studentId);
    const grade = studentId === null ? null : (latest.get(studentId) ?? null);
    return {
      enrollmentId: enrollment.enrollment_id,
      studentId,
      studentNumber: enrollment.student_number,
      displayName: enrollment.display_name,
      status: statusOf(grade, studentId),
      grade,
    };
  });

  const counts: Record<CohortStatus, number> = {
    not_submitted: 0,
    failed: 0,
    running: 0,
    rejected: 0,
    graded: 0,
    unbound: 0,
  };
  for (const row of rows) counts[row.status] += 1;

  let outsideRoster = 0;
  for (const studentId of latest.keys()) {
    if (!rosterStudentIds.has(studentId)) outsideRoster += 1;
  }

  return { rows, counts, outsideRoster };
}

/**
 * "已提交 / 未提交"的判定口径（作业中心的提交情况用）。
 *
 * **未绑定账号**的行不参与判定：它们既不是"已提交"也不是"未提交"，把 `unbound` 算进
 * 已提交会把"系统还没把这个学生接到账号上"说成"他交了"。因此分母是"可判定人数"。
 */
export function cohortJudged(summary: CohortSummary): {
  submitted: number;
  notSubmitted: number;
  unbound: number;
  judged: number;
} {
  const { counts, rows } = summary;
  return {
    submitted: counts.graded + counts.failed + counts.running + counts.rejected,
    notSubmitted: counts.not_submitted,
    unbound: counts.unbound,
    judged: rows.length - counts.unbound,
  };
}

export function cohortStatusLabel(status: CohortStatus): string {
  if (status === "not_submitted") return t("grades.missing.statusNotSubmitted");
  if (status === "unbound") return t("grades.missing.statusUnbound");
  if (status === "running") return t("grades.missing.statusRunning");
  // graded 只是一个聚合桶：契约里对应的终态是 `completed`。
  if (status === "graded") return t("grades.state.completed");
  return t(`grades.state.${status}`);
}

export function cohortStatusTone(status: CohortStatus): "ok" | "warn" | "bad" | "idle" {
  if (status === "graded") return "ok";
  if (status === "failed") return "bad";
  if (status === "running" || status === "rejected") return "warn";
  return "idle";
}
