import { describe, expect, it } from "vitest";
import { scoreDistribution, sortRowsForDisplay, summarizeLoadedRows } from "./aggregate";
import { gradeRow } from "../../testing/contractFixtures";

describe("成绩汇总口径（RQ-B-1-30）", () => {
  it("未完成的行不参与平均分与通过率，也绝不被当成 0 分", () => {
    const summary = summarizeLoadedRows([
      gradeRow({ score: 80, max_score: 100, is_pass: true, state: "completed" }),
      gradeRow({ score: 40, max_score: 100, is_pass: false, state: "completed" }),
      gradeRow({ score: null, max_score: null, is_pass: null, state: "running" }),
      gradeRow({ score: null, max_score: null, is_pass: null, state: "failed" }),
    ]);

    expect(summary.rowCount).toBe(4);
    expect(summary.scoredCount).toBe(2);
    expect(summary.unfinishedCount).toBe(2);
    expect(summary.averageScore).toBe(60);
    expect(summary.passRate).toBe(0.5);
    expect(summary.maxScore).toBe(100);
  });

  it("全部未评分时平均分与通过率为 null（页面显示状态而不是 0）", () => {
    const summary = summarizeLoadedRows([
      gradeRow({ score: null, max_score: null, is_pass: null, state: "queued" }),
    ]);
    expect(summary.averageScore).toBeNull();
    expect(summary.passRate).toBeNull();
    expect(summary.unfinishedCount).toBe(1);
  });

  it("空集合不产生 NaN", () => {
    const summary = summarizeLoadedRows([]);
    expect(summary).toMatchObject({
      rowCount: 0,
      averageScore: null,
      passRate: null,
      maxScore: null,
    });
  });

  it("分布按满分百分比分桶，未评分单列一桶", () => {
    const buckets = scoreDistribution([
      gradeRow({ score: 50, max_score: 100 }),
      gradeRow({ score: 65, max_score: 100 }),
      gradeRow({ score: 95, max_score: 100 }),
      gradeRow({ score: null, max_score: null }),
    ]);
    expect(buckets).toEqual([
      { label: "0-59%", count: 1 },
      { label: "60-69%", count: 1 },
      { label: "70-79%", count: 0 },
      { label: "80-89%", count: 0 },
      { label: "90-100%", count: 1 },
      { label: "未评分", count: 1 },
    ]);
  });

  it("同名同姓以学号稳定排序，不合并记录（验收 5）", () => {
    const rows = sortRowsForDisplay([
      gradeRow({ student_id: "id-2", student_number: "20260002", display_name: "同名" }),
      gradeRow({ student_id: "id-1", student_number: "20260001", display_name: "同名" }),
    ]);
    expect(rows.map((row) => row.student_id)).toEqual(["id-1", "id-2"]);
    expect(rows).toHaveLength(2);
  });
});
