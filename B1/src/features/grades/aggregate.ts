import type { GradeRow } from "../../api/types";

/**
 * 成绩汇总（RQ-B-1-30 验收 1）。
 *
 * **口径必须说清楚**：契约没有成绩汇总端点（`listGrades` 只返回分页行，见
 * `docs/collaboration/interfaces.md` 缺口第 4 条），所以这里的统计只覆盖**当前已加载的行**。
 * 因此函数名、返回结构与 UI 文案都统一标注 "loaded"，绝不冒充权威汇总。
 * 未完成的行（`score === null`）不参与平均分与通过率，也**不会**被当成 0 分。
 */

export type LoadedScopeSummary = {
  rowCount: number;
  scoredCount: number;
  unfinishedCount: number;
  averageScore: number | null;
  passRate: number | null;
  maxScore: number | null;
};

export function summarizeLoadedRows(rows: GradeRow[]): LoadedScopeSummary {
  const scored = rows.filter((row) => typeof row.score === "number");
  const totalScore = scored.reduce((sum, row) => sum + (row.score ?? 0), 0);
  const passed = scored.filter((row) => row.is_pass === true).length;
  const maxScores = rows
    .map((row) => row.max_score)
    .filter((value): value is number => typeof value === "number");

  return {
    rowCount: rows.length,
    scoredCount: scored.length,
    unfinishedCount: rows.length - scored.length,
    averageScore: scored.length === 0 ? null : round2(totalScore / scored.length),
    passRate: scored.length === 0 ? null : round2(passed / scored.length),
    maxScore: maxScores.length === 0 ? null : Math.max(...maxScores),
  };
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

export type ScoreBucket = { label: string; count: number };

/**
 * 已加载行的分数分布（用于 ECharts 直方图）。
 * 以满分百分比分桶，避免不同题目满分不同导致的分桶歧义；无满分行归入"未评分"。
 */
export function scoreDistribution(rows: GradeRow[]): ScoreBucket[] {
  const buckets: ScoreBucket[] = [
    { label: "0-59%", count: 0 },
    { label: "60-69%", count: 0 },
    { label: "70-79%", count: 0 },
    { label: "80-89%", count: 0 },
    { label: "90-100%", count: 0 },
    { label: "未评分", count: 0 },
  ];

  for (const row of rows) {
    if (typeof row.score !== "number" || typeof row.max_score !== "number" || row.max_score <= 0) {
      buckets[5].count += 1;
      continue;
    }
    const percent = (row.score / row.max_score) * 100;
    if (percent < 60) buckets[0].count += 1;
    else if (percent < 70) buckets[1].count += 1;
    else if (percent < 80) buckets[2].count += 1;
    else if (percent < 90) buckets[3].count += 1;
    else buckets[4].count += 1;
  }

  return buckets;
}

/** 学生视图下同名同姓必须以学号区分：列表的稳定排序键包含学号（RQ-B-1-30 验收 5）。 */
export function sortRowsForDisplay(rows: GradeRow[]): GradeRow[] {
  return [...rows].sort((a, b) => {
    const numberA = a.student_number ?? "";
    const numberB = b.student_number ?? "";
    if (numberA !== numberB) return numberA.localeCompare(numberB);
    return a.student_id.localeCompare(b.student_id);
  });
}
