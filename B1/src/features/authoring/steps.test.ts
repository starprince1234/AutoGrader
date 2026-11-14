import { describe, expect, it } from "vitest";
import { emptyCase, emptyDraftForm, type DraftForm } from "./draft";
import { STEPS, caseHasContent, hasReferenceContent, isStepComplete } from "./steps";

/**
 * 出题向导的三步定义（统一 AI 逻辑评分模式）：
 * 基本信息 → 题干 → 参考答案与 AI 评分细则。
 */

function form(overrides: Partial<DraftForm> = {}): DraftForm {
  return { ...emptyDraftForm(), ...overrides };
}

describe("出题向导的步骤定义", () => {
  it("顺序固定：基本信息 → 题干 → 参考答案", () => {
    expect(STEPS.map((step) => step.id)).toEqual(["basic", "stem", "reference"]);
  });

  it("每一步都有面向教师的中文标题", () => {
    expect(STEPS.map((step) => step.label())).toEqual(["基本信息", "题干", "参考答案"]);
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

  it("参考答案：主参考答案或多档解法之一非空即算完成；编辑既有题目时也视为已完成", () => {
    expect(isStepComplete("reference", form(), "", { editing: false })).toBe(false);
    expect(
      isStepComplete("reference", form({ referenceAnswer: "print(1)" }), "", { editing: false }),
    ).toBe(true);
    expect(
      isStepComplete(
        "reference",
        form({ referenceAnswers: [{ label: "高阶解法", body: "print(sum(a))" }] }),
        "",
        { editing: false },
      ),
    ).toBe(true);
    expect(
      hasReferenceContent(form({ referenceAnswers: [{ label: "空", body: "   " }] })),
    ).toBe(false);
    expect(isStepComplete("reference", form(), "", { editing: true })).toBe(true);
  });

  it("兼容导出：caseHasContent 仅在填了内容时为 true", () => {
    expect(caseHasContent(emptyCase())).toBe(false);
    expect(caseHasContent({ ...emptyCase(), weight: "2" })).toBe(false);
    expect(caseHasContent({ ...emptyCase(), name: "边界" })).toBe(true);
  });
});
