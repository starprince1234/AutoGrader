import type { StudentProgress } from "./useAssignmentProgress";

/**
 * 批改界面（`/assignments/{id}/grading`）的纯计算部分。
 *
 * PDF 的「作业情况（子界面）」只有两个板块：**已完成**与**未完成**。因此这里只做两件
 * 可验证的事：
 * 1. 把 `useAssignmentProgress` 的逐人分类映射成两个板块（`unbound` 单列，它既不是
 *    "已交"也不是"未交"——不能把它算进任何一边，否则界面就在说一个它不知道的结论）；
 * 2. 按百分制换算一名学生的作业得分（缺交的题计 0，未出分的题不猜分）。
 */

/** 已完成 = 至少提交过一题（含评测中与评测失败：那些是"交了但没出结果"）。 */
export function isSubmittedStudent(row: StudentProgress): boolean {
  return (
    row.status === "partial" ||
    row.status === "submitted" ||
    row.status === "evaluating" ||
    row.status === "failed"
  );
}

export type CompletionGroups = {
  /** 已完成：至少提交过一题。 */
  completed: StudentProgress[];
  /** 未完成：一题都没交。 */
  pending: StudentProgress[];
  /** 名单行尚未绑定账号：不参与"已交/未交"判定。 */
  unbound: StudentProgress[];
};

function byStudentNumber(left: StudentProgress, right: StudentProgress): number {
  return left.studentNumber.localeCompare(right.studentNumber, "zh-Hans-CN", {
    numeric: true,
  });
}

export function splitByCompletion(
  rows: readonly StudentProgress[],
): CompletionGroups {
  const groups: CompletionGroups = { completed: [], pending: [], unbound: [] };
  for (const row of rows) {
    if (row.status === "unbound") groups.unbound.push(row);
    else if (isSubmittedStudent(row)) groups.completed.push(row);
    else groups.pending.push(row);
  }
  groups.completed.sort(byStudentNumber);
  groups.pending.sort(byStudentNumber);
  groups.unbound.sort(byStudentNumber);
  return groups;
}

/** 已完成 / 未完成 的分母：只数**可判定**的名单行（未绑定账号不进分母）。 */
export function judgedTotal(groups: CompletionGroups): number {
  return groups.completed.length + groups.pending.length;
}

/**
 * 作业得分（百分制）。
 *
 * 口径：`Σ(本题得分 / 本题满分) / 作业题数 × 100`。缺交的题按 0 计（分母始终是作业题数），
 * 未出分的题同样不计入分子——这样"只交了一题且满分"不会被显示成 100 分。
 * 一题都没有完成分数时返回 `null`（不显示 0 分，那是另一件事）。
 */
export function assignmentScorePercent(row: StudentProgress): number | null {
  if (row.total <= 0) return null;
  let ratioSum = 0;
  let scored = 0;
  for (const question of row.questions) {
    const grade = question.submission;
    if (grade === null || question.state !== "completed") continue;
    if (typeof grade.score !== "number") continue;
    if (typeof grade.max_score !== "number" || grade.max_score <= 0) continue;
    ratioSum += grade.score / grade.max_score;
    scored += 1;
  }
  if (scored === 0) return null;
  const percent = (ratioSum / row.total) * 100;
  // 一位小数足够阅读，也避免 66.666666 这类噪声。不使用 toFixed 以免出现字符串分数。
  return Math.round(percent * 10) / 10;
}
