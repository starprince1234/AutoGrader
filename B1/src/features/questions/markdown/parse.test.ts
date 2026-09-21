import { describe, expect, it } from "vitest";
import { parseMarkdown, parseInline } from "./parse";

describe("安全 Markdown 解析（RQ-B-1-40 验收 1）", () => {
  it("解析标题、列表、行内代码、代码块、表格与图片", () => {
    const source = [
      "# 标题",
      "",
      "正文含 **粗体**、*斜体* 与 `code`。",
      "",
      "- 第一项",
      "- 第二项",
      "",
      "```bash",
      "wc -l < file.txt",
      "```",
      "",
      "| 列一 | 列二 |",
      "| --- | --- |",
      "| a | b |",
      "",
      "![示例](/api/v1/question-assets/abc)",
    ].join("\n");

    const blocks = parseMarkdown(source);
    const types = blocks.map((block) => block.type);
    expect(types).toEqual([
      "heading",
      "paragraph",
      "list",
      "code_block",
      "table",
      "paragraph",
    ]);

    const codeBlock = blocks[3];
    expect(codeBlock).toMatchObject({ type: "code_block", language: "bash" });
    if (codeBlock.type === "code_block") {
      expect(codeBlock.value).toBe("wc -l < file.txt");
    }

    const table = blocks[4];
    if (table.type === "table") {
      expect(table.header).toHaveLength(2);
      expect(table.rows).toHaveLength(1);
    }

    const last = blocks[5];
    if (last.type === "paragraph") {
      expect(last.children[0]).toMatchObject({
        type: "image",
        src: "/api/v1/question-assets/abc",
      });
    }
  });

  it("HTML 标签只作为文字节点存在，不产生任何元素节点（XSS 前置条件）", () => {
    const nodes = parseInline('<script>alert(1)</script> <img src=x onerror=alert(2)>');
    expect(nodes.every((node) => node.type === "text")).toBe(true);
    expect(nodes.map((node) => (node.type === "text" ? node.value : "")).join("")).toContain(
      "<script>",
    );
  });

  it("代码片段中的语法不会被解释为行内标记", () => {
    const nodes = parseInline("`**not bold**` 之后是 **bold**");
    expect(nodes[0]).toEqual({ type: "code", value: "**not bold**" });
    expect(nodes[1]).toMatchObject({ type: "text" });
    expect(nodes[2]).toMatchObject({ type: "strong" });
  });

  it("无结束围栏的代码块也能安全收尾", () => {
    const blocks = parseMarkdown("```python\nprint(1)");
    expect(blocks).toEqual([{ type: "code_block", language: "python", value: "print(1)" }]);
  });
});
