import { describe, expect, it } from "vitest";
import { ProtectedContentError, assertNoProtectedFields, findProtectedFields } from "./protectedFields";
import { questionPublic } from "../../testing/contractFixtures";

describe("公开题目 DTO 的受保护字段扫描（RQ-B-1-40 验收 4）", () => {
  it("干净的公开 DTO 通过", () => {
    expect(findProtectedFields(questionPublic())).toEqual([]);
    expect(() => assertNoProtectedFields(questionPublic())).not.toThrow();
  });

  it("契约违规（响应里出现参考答案/隐藏用例/期望输出）会被定位并抛出", () => {
    const leaked = {
      ...questionPublic(),
      reference_answer: "print(3)",
      cases: [{ expected_output: "3\n" }],
    };
    const paths = findProtectedFields(leaked);
    expect(paths).toContain("$.reference_answer");
    // 命中禁用键后不再深入其内部（整块受保护内容都不该被解析）
    expect(paths).toContain("$.cases");
    expect(paths).not.toContain("$.cases[0].expected_output");

    expect(() => assertNoProtectedFields(leaked)).toThrow(ProtectedContentError);
  });

  it("嵌套结构里的受保护字段同样被找出", () => {
    const nested = { items: [{ stem_markdown: "x", hidden_cases: [] }] };
    expect(findProtectedFields(nested)).toEqual(["$.items[0].hidden_cases"]);
  });
});
