import { describe, expect, it } from "vitest";
import type { EnrollmentPublic, GradeRow } from "../../api/types";
import {
  buildCounts,
  buildOwnProgress,
  buildStudentProgress,
  classifyQuestion,
  overallStateOf,
  perAssignmentCounts,
  type QuestionProgress,
} from "./useAssignmentProgress";

const ASSIGNMENT = "00000000-0000-4000-8000-0000000000a1";
const V1 = "00000000-0000-4000-8000-000000000101";
const V2 = "00000000-0000-4000-8000-000000000102";
const V3 = "00000000-0000-4000-8000-000000000103";
const S1 = "00000000-0000-4000-8000-000000000201";
const S2 = "00000000-0000-4000-8000-000000000202";

function enrollment(overrides: Partial<EnrollmentPublic>): EnrollmentPublic {
  return {
    enrollment_id: "00000000-0000-4000-8000-000000000301",
    section_id: "00000000-0000-4000-8000-000000000401",
    student_id: S1,
    student_number: "20260001",
    display_name: "张三",
    is_active: true,
    updated_at: "2026-09-15T00:00:00.000Z",
    ...overrides,
  } as EnrollmentPublic;
}

function grade(overrides: Partial<GradeRow>): GradeRow {
  return {
    submission_id: "00000000-0000-4000-8000-000000000501",
    student_id: S1,
    assignment_id: ASSIGNMENT,
    course_id: "00000000-0000-4000-8000-000000000601",
    section_id: "00000000-0000-4000-8000-000000000401",
    question_version_id: V1,
    state: "completed",
    score: 90,
    max_score: 100,
    is_pass: true,
    submitted_at: "2026-09-15T08:00:00.000Z",
    evaluation_run_id: null,
    ...overrides,
  } as GradeRow;
}

function question(state: QuestionProgress["state"]): QuestionProgress {
  return { questionVersionId: V1, ordinal: 1, submission: grade({}), state };
}

describe("作业逐人逐题聚合（CHG-0032 §7.1）", () => {
  it("每题状态四态：无提交 / 处理中 / 失败（含拒绝）/ 已完成", () => {
    expect(classifyQuestion(null)).toBe("not_submitted");
    expect(classifyQuestion(grade({ state: "queued" }))).toBe("evaluating");
    expect(classifyQuestion(grade({ state: "running" }))).toBe("evaluating");
    expect(classifyQuestion(grade({ state: "received" }))).toBe("evaluating");
    expect(classifyQuestion(grade({ state: "validated" }))).toBe("evaluating");
    expect(classifyQuestion(grade({ state: "failed" }))).toBe("failed");
    expect(classifyQuestion(grade({ state: "rejected" }))).toBe("failed");
    expect(classifyQuestion(grade({ state: "completed" }))).toBe("completed");
  });

  it("学生整体分类互斥：未交 / 部分 / 评测中 / 失败 / 已交", () => {
    expect(overallStateOf([])).toBe("not_submitted");
    expect(
      overallStateOf([
        question("not_submitted"),
        question("not_submitted"),
      ]),
    ).toBe("not_submitted");
    expect(
      overallStateOf([question("completed"), question("not_submitted")]),
    ).toBe("partial");
    expect(
      overallStateOf([question("completed"), question("evaluating")]),
    ).toBe("evaluating");
    expect(overallStateOf([question("completed"), question("failed")])).toBe("failed");
    expect(overallStateOf([question("completed"), question("completed")])).toBe(
      "submitted",
    );
  });

  it("同一学生重交同一道题时取最新提交（按 submitted_at）", () => {
    const roster = [enrollment({})];
    const rows = [
      grade({
        submission_id: "00000000-0000-4000-8000-000000000501",
        state: "failed",
        submitted_at: "2026-09-15T08:00:00.000Z",
        score: null,
      }),
      grade({
        submission_id: "00000000-0000-4000-8000-000000000502",
        state: "completed",
        submitted_at: "2026-09-15T09:00:00.000Z",
      }),
    ];
    const progress = buildStudentProgress({
      roster,
      grades: rows,
      questionVersionIds: [V1],
      assignmentId: ASSIGNMENT,
    });
    expect(progress[0].questions[0].submission?.submission_id).toBe(
      "00000000-0000-4000-8000-000000000502",
    );
    expect(progress[0].status).toBe("submitted");
  });

  it("不同学生互不影响：只按题目取全局最新会把别人的提交当成自己的", () => {
    const roster = [
      enrollment({ enrollment_id: "e1", student_id: S1 }),
      enrollment({ enrollment_id: "e2", student_id: S2, student_number: "20260002" }),
    ];
    const rows = [
      grade({ student_id: S1, submitted_at: "2026-09-15T08:00:00.000Z" }),
      grade({
        submission_id: "00000000-0000-4000-8000-000000000502",
        student_id: S2,
        submitted_at: "2026-09-15T09:00:00.000Z",
      }),
    ];
    const progress = buildStudentProgress({
      roster,
      grades: rows,
      questionVersionIds: [V1],
      assignmentId: ASSIGNMENT,
    });
    expect(progress[0].status).toBe("submitted");
    expect(progress[1].status).toBe("submitted");
    expect(progress[0].questions[0].submission?.student_id).toBe(S1);
  });

  it("历史行（无 assignment_id）与其它作业的行不进入作业视图", () => {
    const roster = [enrollment({})];
    const rows = [
      grade({ assignment_id: null, submitted_at: "2026-09-15T10:00:00.000Z" }),
      grade({
        assignment_id: "00000000-0000-4000-8000-0000000000ff",
        submitted_at: "2026-09-15T11:00:00.000Z",
      }),
    ];
    const progress = buildStudentProgress({
      roster,
      grades: rows,
      questionVersionIds: [V1],
      assignmentId: ASSIGNMENT,
    });
    expect(progress[0].status).toBe("not_submitted");
  });

  it("未绑定账号的名单行单独成类，不参与五个判定分类", () => {
    const roster = [enrollment({ student_id: null })];
    const progress = buildStudentProgress({
      roster,
      grades: [],
      questionVersionIds: [V1],
      assignmentId: ASSIGNMENT,
    });
    expect(progress[0].status).toBe("unbound");
    const counts = buildCounts(progress);
    expect(counts.unbound).toBe(1);
    expect(counts.notSubmitted).toBe(0);
    expect(counts.all).toBe(1);
  });

  it("汇总计数：人数 / 未交 / 部分 / 已交 / 评测中 / 失败互斥且合计正确", () => {
    const roster = [
      enrollment({ enrollment_id: "e1", student_id: S1 }),
      enrollment({ enrollment_id: "e2", student_id: S2 }),
      enrollment({ enrollment_id: "e3", student_id: "00000000-0000-4000-8000-000000000203" }),
      enrollment({ enrollment_id: "e4", student_id: "00000000-0000-4000-8000-000000000204" }),
      enrollment({ enrollment_id: "e5", student_id: "00000000-0000-4000-8000-000000000205" }),
    ];
    const versions = [V1, V2];
    const rows = [
      // S1：全部完成 → 已交
      grade({ student_id: S1, question_version_id: V1 }),
      grade({
        submission_id: "00000000-0000-4000-8000-000000000502",
        student_id: S1,
        question_version_id: V2,
      }),
      // S2：只交一题 → 部分
      grade({ student_id: S2, question_version_id: V1 }),
      // S3：全部提交且一题处理中 → 评测中
      grade({
        student_id: "00000000-0000-4000-8000-000000000203",
        question_version_id: V1,
        state: "running",
        score: null,
      }),
      grade({
        student_id: "00000000-0000-4000-8000-000000000203",
        question_version_id: V2,
        state: "completed",
      }),
      // S4：全部提交且一题失败 → 失败
      grade({
        student_id: "00000000-0000-4000-8000-000000000204",
        question_version_id: V1,
        state: "failed",
        score: null,
      }),
      grade({
        student_id: "00000000-0000-4000-8000-000000000204",
        question_version_id: V2,
        state: "completed",
      }),
      // S5：无提交 → 未交
    ];
    const progress = buildStudentProgress({
      roster,
      grades: rows,
      questionVersionIds: versions,
      assignmentId: ASSIGNMENT,
    });
    const counts = buildCounts(progress);
    expect(counts).toEqual({
      all: 5,
      notSubmitted: 1,
      partial: 1,
      submitted: 1,
      evaluating: 1,
      failed: 1,
      unbound: 0,
    });
    expect(
      counts.notSubmitted +
        counts.partial +
        counts.submitted +
        counts.evaluating +
        counts.failed,
    ).toBe(counts.all);
  });

  it("学生本人视角：completedCount 只数已完成，未完成不当成 0 分", () => {
    const rows = [
      grade({ question_version_id: V1, state: "completed" }),
      grade({
        submission_id: "00000000-0000-4000-8000-000000000502",
        question_version_id: V2,
        state: "failed",
        score: null,
      }),
      grade({
        submission_id: "00000000-0000-4000-8000-000000000503",
        question_version_id: V3,
        state: "queued",
        score: null,
      }),
    ];
    const own = buildOwnProgress({
      grades: rows,
      questionVersionIds: [V1, V2, V3],
      assignmentId: ASSIGNMENT,
    });
    expect(own.total).toBe(3);
    expect(own.submittedCount).toBe(3);
    expect(own.completedCount).toBe(1);
    expect(own.overall).toBe("evaluating");
  });

  it("作业列表的逐作业计数：按 assignment_id 分组，不混入其它作业", () => {
    const roster = [enrollment({})];
    const rows = [
      grade({ assignment_id: ASSIGNMENT, question_version_id: V1 }),
      grade({
        assignment_id: "00000000-0000-4000-8000-0000000000ff",
        question_version_id: V2,
      }),
    ];
    const counts = perAssignmentCounts(
      [
        {
          assignment_id: ASSIGNMENT,
          question_version_ids: [V1],
        } as never,
      ],
      roster,
      rows,
    );
    expect(counts?.get(ASSIGNMENT)?.submitted).toBe(1);
  });
});
