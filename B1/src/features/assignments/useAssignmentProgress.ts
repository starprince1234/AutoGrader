import { useMemo } from "react";
import type { AssignmentPublic, EnrollmentPublic, GradeRow } from "../../api/types";
import { gradesAvailability } from "../grades/useGrades";
import { useAllGrades } from "../grades/useGrades";
import { useAssignmentRoster } from "./useAssignmentRoster";

/**
 * 作业 × 课程名单 × 成绩的逐人逐题聚合（CHG-0032 §7.1 / v0.32.0 课程级受众）。
 *
 * 两条硬规则：
 * 1. **一题一次提交**：一道题取该生该题**最新**的提交（按 `submitted_at`），它代表现状。
 * 2. **取数未完成不得下结论**：名单或成绩任一未取全（或到达翻页上限）时，
 *    调用方不得渲染"未交"之类判定——只允许显示加载/截断。因此 `counts` 只有在
 *    `complete === true` 时才是非 `null`。
 *
 * 名单来自**全课程**（作业对该课程全部课序/班级生效），未绑定账号的行以 `unbound` 保留。
 * 历史行（`assignment_id === null`）不属于任何作业，**不**进入作业级视图：这里按
 * `assignment_id` 精确过滤，不按题目版本"猜"归属。
 */

/** 单题状态（门户展示用四态）。 */
export type QuestionState = "not_submitted" | "evaluating" | "failed" | "completed";

/** 学生整体分类（互斥，用于汇总计数）。`unbound` = 名单行未绑定账号，不参与判定。 */
export type OverallState =
  | "unbound"
  | "not_submitted"
  | "partial"
  | "submitted"
  | "evaluating"
  | "failed";

export type QuestionProgress = {
  questionVersionId: string;
  /** 1 基题号（作业顺序），答案文件名即 `${ordinal}.txt`。 */
  ordinal: number;
  submission: GradeRow | null;
  state: QuestionState;
};

export type StudentProgress = {
  enrollmentId: string;
  studentId: string | null;
  studentNumber: string;
  displayName: string;
  /** 课序（`EnrollmentPublic.section_id`）。 */
  sectionId: string;
  /** 班级（v0.32.0：课序之下还有班级）。 */
  classId: string;
  className: string;
  status: OverallState;
  questions: QuestionProgress[];
  /** 已完成的题目数。 */
  completedCount: number;
  /** 有提交的题目数（含失败/处理中）。 */
  submittedCount: number;
  total: number;
  lastSubmittedAt: string | null;
};

export type AssignmentProgressCounts = {
  /** 受众人数（含未绑定账号的行）。 */
  all: number;
  notSubmitted: number;
  partial: number;
  /** 已交（全部题已完成）。 */
  submitted: number;
  evaluating: number;
  failed: number;
  unbound: number;
};

/** 成绩行的 `state` → 单题四态。与契约枚举保持一致，不手写新的状态机。 */
export function classifyQuestion(grade: GradeRow | null): QuestionState {
  if (grade === null) return "not_submitted";
  if (grade.state === "completed") return "completed";
  if (grade.state === "failed" || grade.state === "rejected") return "failed";
  return "evaluating";
}

/**
 * 一名学生在**一道题**上的最新提交（一个学生可能重交同一道题）。
 * 键必须同时包含学生与题目：只按题目取"全局最新"会把别的学生更晚的行当成这名学生的。
 */
function latestByStudentQuestion(
  grades: readonly GradeRow[],
  assignmentId: string,
): Map<string, GradeRow> {
  const latest = new Map<string, GradeRow>();
  for (const row of grades) {
    // 历史行（无 assignment_id）不进作业视图；其它作业的行也被排除。
    if (row.assignment_id !== assignmentId) continue;
    const key = `${row.student_id}:${row.question_version_id}`;
    const previous = latest.get(key);
    if (!previous || (row.submitted_at ?? "") > (previous.submitted_at ?? "")) {
      latest.set(key, row);
    }
  }
  return latest;
}

/** 学生本人视角：成绩行全部属于本人，因此按题目取最新即可。 */
function latestOwnByVersion(
  grades: readonly GradeRow[],
  assignmentId: string,
): Map<string, GradeRow> {
  const latest = new Map<string, GradeRow>();
  for (const row of grades) {
    if (row.assignment_id !== assignmentId) continue;
    const previous = latest.get(row.question_version_id);
    if (!previous || (row.submitted_at ?? "") > (previous.submitted_at ?? "")) {
      latest.set(row.question_version_id, row);
    }
  }
  return latest;
}

/** 学生本人的逐题进度（作业列表与工作区共用）。 */
export function buildOwnProgress(args: {
  grades: readonly GradeRow[];
  questionVersionIds: readonly string[];
  assignmentId: string;
}): {
  questions: QuestionProgress[];
  overall: OverallState;
  completedCount: number;
  submittedCount: number;
  total: number;
  lastSubmittedAt: string | null;
} {
  const questions = questionsOf(
    args.questionVersionIds,
    latestOwnByVersion(args.grades, args.assignmentId),
  );
  return {
    questions,
    overall: overallStateOf(questions),
    completedCount: questions.filter((question) => question.state === "completed").length,
    submittedCount: questions.filter((question) => question.state !== "not_submitted").length,
    total: args.questionVersionIds.length,
    lastSubmittedAt: lastSubmittedAtOf(questions),
  };
}

function questionsOf(
  questionVersionIds: readonly string[],
  latest: Map<string, GradeRow>,
): QuestionProgress[] {
  return questionVersionIds.map((questionVersionId, index) => {
    const submission = latest.get(questionVersionId) ?? null;
    return {
      questionVersionId,
      ordinal: index + 1,
      submission,
      state: classifyQuestion(submission),
    };
  });
}

/**
 * 学生整体分类（互斥）：
 * 1. 无任何提交 → 未交；
 * 2. 有提交但题数不全 → 部分；
 * 3. 全部题已提交且存在处理中 → 评测中；
 * 4. 全部题已提交、无处理中、存在失败/拒绝 → 失败；
 * 5. 全部题已提交且全部已完成 → 已交。
 */
export function overallStateOf(questions: readonly QuestionProgress[]): OverallState {
  if (questions.length === 0) return "not_submitted";
  const submitted = questions.filter((question) => question.state !== "not_submitted");
  if (submitted.length === 0) return "not_submitted";
  if (submitted.length < questions.length) return "partial";
  if (questions.some((question) => question.state === "evaluating")) return "evaluating";
  if (questions.some((question) => question.state === "failed")) return "failed";
  return "submitted";
}

function lastSubmittedAtOf(questions: readonly QuestionProgress[]): string | null {
  let latest: string | null = null;
  for (const question of questions) {
    const value = question.submission?.submitted_at ?? null;
    if (value !== null && (latest === null || value > latest)) latest = value;
  }
  return latest;
}

/**
 * 把名单与成绩行聚合成逐人逐题结果（纯函数，直接单测）。
 *
 * `grades` 必须是**同一作业**的成绩行；这里仍按 `assignment_id` 再过滤一次，确保历史行
 * 不会因为调用方把课程级成绩直接传进来而混入作业视图。
 */
export function buildStudentProgress(args: {
  roster: readonly EnrollmentPublic[];
  grades: readonly GradeRow[];
  questionVersionIds: readonly string[];
  assignmentId: string;
}): StudentProgress[] {
  const latest = latestByStudentQuestion(args.grades, args.assignmentId);
  return args.roster.map((enrollment) => {
    const studentId = enrollment.student_id ?? null;
    if (studentId === null) {
      return {
        enrollmentId: enrollment.enrollment_id,
        studentId: null,
        studentNumber: enrollment.student_number,
        displayName: enrollment.display_name,
        sectionId: enrollment.section_id,
        classId: enrollment.class_id,
        className: enrollment.class_name ?? "",
        status: "unbound" as const,
        questions: [],
        completedCount: 0,
        submittedCount: 0,
        total: args.questionVersionIds.length,
        lastSubmittedAt: null,
      };
    }
    const mine = new Map<string, GradeRow>();
    for (const questionVersionId of args.questionVersionIds) {
      const row = latest.get(`${studentId}:${questionVersionId}`);
      if (row !== undefined) mine.set(questionVersionId, row);
    }
    const questions = questionsOf(args.questionVersionIds, mine);
    const submittedCount = questions.filter(
      (question) => question.state !== "not_submitted",
    ).length;
    const completedCount = questions.filter(
      (question) => question.state === "completed",
    ).length;
    return {
      enrollmentId: enrollment.enrollment_id,
      studentId,
      studentNumber: enrollment.student_number,
      displayName: enrollment.display_name,
      sectionId: enrollment.section_id,
      classId: enrollment.class_id,
      className: enrollment.class_name ?? "",
      status: overallStateOf(questions),
      questions,
      completedCount,
      submittedCount,
      total: args.questionVersionIds.length,
      lastSubmittedAt: lastSubmittedAtOf(questions),
    };
  });
}

/** 汇总计数：`all` = 受众人数；五个分类互斥；未绑定账号单独计数，不参与判定。 */
export function buildCounts(rows: readonly StudentProgress[]): AssignmentProgressCounts {
  const counts: AssignmentProgressCounts = {
    all: rows.length,
    notSubmitted: 0,
    partial: 0,
    submitted: 0,
    evaluating: 0,
    failed: 0,
    unbound: 0,
  };
  for (const row of rows) {
    switch (row.status) {
      case "not_submitted":
        counts.notSubmitted += 1;
        break;
      case "partial":
        counts.partial += 1;
        break;
      case "submitted":
        counts.submitted += 1;
        break;
      case "evaluating":
        counts.evaluating += 1;
        break;
      case "failed":
        counts.failed += 1;
        break;
      case "unbound":
        counts.unbound += 1;
        break;
    }
  }
  return counts;
}

/**
 * 教师作业详情：先取受众名单与作业级成绩，再聚合。
 *
 * `counts` 为 `null` 表示取数未完成/被截断：此时**不得**渲染"未交"。
 */
export function useAssignmentProgress(assignment: AssignmentPublic | null) {
  const roster = useAssignmentRoster(assignment);
  const grades = useAllGrades(
    assignment?.course_id ?? null,
    null,
    assignment?.assignment_id ?? null,
  );
  const assignmentId = assignment?.assignment_id ?? "";
  const questionVersionIds = useMemo(
    () => assignment?.question_version_ids ?? [],
    [assignment?.question_version_ids],
  );

  const rows = useMemo(
    () =>
      buildStudentProgress({
        roster: roster.students,
        grades: grades.rows,
        questionVersionIds,
        assignmentId,
      }),
    [roster.students, grades.rows, questionVersionIds, assignmentId],
  );

  const availability = gradesAvailability(grades);
  const complete =
    assignment !== null &&
    roster.complete &&
    !roster.truncated &&
    availability === "ready" &&
    !grades.isPending &&
    !grades.isError;

  return {
    rows,
    counts: complete ? buildCounts(rows) : null,
    complete,
    isPending: roster.isPending || grades.isPending,
    isError: roster.isError || grades.isError,
    error: roster.error ?? grades.error,
    truncated: roster.truncated || availability === "truncated",
    roster: roster.students,
    gradeRows: grades.rows,
  };
}

/**
 * 学生视角：本人在这个作业里的逐题状态与整体分类。
 *
 * 学生成绩端点只返回本人记录（B1 从不发送 `student_id`），因此这里不需要名单；
 * 但"未完成/被拒绝的系统故障"不能显示成 0 分或未交——那只影响分数列的渲染，
 * 不影响状态分类。
 */
export function useStudentAssignmentProgress(
  assignment: AssignmentPublic | null,
  courseId: string | null,
  sectionId: string | null,
) {
  const grades = useAllGrades(courseId, sectionId, assignment?.assignment_id ?? null);
  const assignmentId = assignment?.assignment_id ?? "";
  const questionVersionIds = useMemo(
    () => assignment?.question_version_ids ?? [],
    [assignment?.question_version_ids],
  );
  const own = useMemo(
    () =>
      buildOwnProgress({
        grades: grades.rows,
        questionVersionIds,
        assignmentId,
      }),
    [grades.rows, questionVersionIds, assignmentId],
  );
  const availability = gradesAvailability(grades);

  return {
    ...own,
    complete: availability === "ready" && !grades.isPending && !grades.isError,
    isPending: grades.isPending,
    isError: grades.isError,
    error: grades.error,
    truncated: availability === "truncated",
    gradeRows: grades.rows,
  };
}

/**
 * 作业列表页的逐作业计数：复用同一份"名单 × 课程级成绩行"，按 `assignment_id` 分组。
 * 名单不完整时返回 `null`——列表页不得据此显示"未交"。
 */
export function perAssignmentCounts(
  assignments: readonly AssignmentPublic[],
  roster: readonly EnrollmentPublic[],
  gradeRows: readonly GradeRow[],
): Map<string, AssignmentProgressCounts> | null {
  const map = new Map<string, AssignmentProgressCounts>();
  const byAssignment = new Map<string, GradeRow[]>();
  for (const row of gradeRows) {
    if (row.assignment_id === null || row.assignment_id === undefined) continue;
    const bucket = byAssignment.get(row.assignment_id) ?? [];
    bucket.push(row);
    byAssignment.set(row.assignment_id, bucket);
  }
  for (const assignment of assignments) {
    const rows = buildStudentProgress({
      roster,
      grades: byAssignment.get(assignment.assignment_id) ?? [],
      questionVersionIds: assignment.question_version_ids,
      assignmentId: assignment.assignment_id,
    });
    map.set(assignment.assignment_id, buildCounts(rows));
  }
  return map;
}
