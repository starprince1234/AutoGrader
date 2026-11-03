import { describe, expect, it } from "vitest";
import { emptyCase, emptyDraftForm, type DraftForm } from "./draft";
import { STEPS, caseHasContent, isStepComplete } from "./steps";

/**
 * 向导的五步只由这一份定义驱动：左侧导航的圆点、每一步的标题，以及"哪一步算完成"。
 * 这里固定的是**完成判据**——它决定教师看到的是蓝色实心对勾还是空心圆圈。
 */

function form(overrides: Partial<DraftForm> = {}): DraftForm {
  return { ...emptyDraftForm(), ...overrides };
}

describe("出题向导的步骤定义", () => {
  it("顺序固定：基本信息 → 题干 → 参考答案 → 测试用例 → 判分比对规则与运行配置", () => {
    expect(STEPS.map((step) => step.id)).toEqual([
      "basic",
      "stem",
      "reference",
      "cases",
      "comparison",
    ]);
  });

  it("每一步都有面向教师的中文标题", () => {
    expect(STEPS.map((step) => step.label())).toEqual([
      "基本信息",
      "题干",
      "参考答案",
      "测试用例",
      "判分比对规则与运行配置",
    ]);
  });
});

describe("步骤完成判据", () => {
  it("基本信息：选了课程并写了标题才算完成", () => {
    expect(isStepComplete("basic", form(), "", { editing: false })).toBe(false);
    expect(isStepComplete("basic", form({ title: "统计行数" }), "", { editing: false })).toBe(false);
    expect(isStepComplete("basic", form(), "course-1", { editing: false })).toBe(false);
    expect(isStepComplete("basic", form({ title: "统计行数" }), "course-1", { editing: false })).toBe(
      true,
    );
  });

  it("题干：题干非空即完成（只有空白不算）", () => {
    expect(isStepComplete("stem", form({ stemMarkdown: "   " }), "", { editing: false })).toBe(
      false,
    );
    expect(isStepComplete("stem", form({ stemMarkdown: "# 题目" }), "", { editing: false })).toBe(
      true,
    );
  });

  it("参考答案：写了答案算完成；编辑既有题目时页面上读不到它，因此按已完成处理", () => {
    expect(isStepComplete("reference", form(), "", { editing: false })).toBe(false);
    expect(isStepComplete("reference", form({ referenceAnswer: "print(1)" }), "", { editing: false })).toBe(
      true,
    );
    expect(isStepComplete("reference", form(), "", { editing: true })).toBe(true);
  });

  it("测试用例：默认那个空用例不算填过，写了内容才算", () => {
    // 新建时默认就有一个空用例；它不构成"已完成"（否则一进页面第四步就是蓝勾）。
    expect(isStepComplete("cases", form(), "", { editing: false })).toBe(false);
    expect(isStepComplete("cases", form({ cases: [] }), "", { editing: false })).toBe(false);
    expect(
      isStepComplete("cases", form({ cases: [{ ...emptyCase(), stdin: "a\n" }] }), "", {
        editing: false,
      }),
    ).toBe(true);
  });

  it("「填过了」只看教师写进去的内容，权值与预置退出码不算", () => {
    expect(caseHasContent(emptyCase())).toBe(false);
    // 只填了一个分值：仍然不算填过。
    expect(caseHasContent({ ...emptyCase(), weight: "2" })).toBe(false);
    expect(caseHasContent({ ...emptyCase(), name: "边界" })).toBe(true);
    expect(caseHasContent({ ...emptyCase(), args: "-n" })).toBe(true);
    expect(caseHasContent({ ...emptyCase(), expectedStdout: "2\n" })).toBe(true);
    expect(caseHasContent({ ...emptyCase(), expectedStderr: "boom" })).toBe(true);
  });

  it("判分比对规则：选了标准输出比对方式才算完成", () => {
    expect(isStepComplete("comparison", form(), "", { editing: false })).toBe(false);
    expect(
      isStepComplete("comparison", form({ comparisonStdout: "exact" }), "", { editing: false }),
    ).toBe(true);
  });
});
