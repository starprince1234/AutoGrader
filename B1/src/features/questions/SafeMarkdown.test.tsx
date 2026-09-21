import { describe, expect, it } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { questionPublic } from "../../testing/contractFixtures";
import { SafeMarkdown } from "./SafeMarkdown";

function renderStem(source: string) {
  return renderWithProviders(<SafeMarkdown source={source} />);
}

describe("富文本渲染（RQ-B-1-40）", () => {
  it("渲染加粗、列表、行内代码、代码块、表格与受控图片", () => {
    const { container } = renderStem(
      [
        "# 题目",
        "",
        "含 **粗体** 与 `code`。",
        "",
        "- 项目一",
        "",
        "```python",
        "print(1)",
        "```",
        "",
        "| 列 | 值 |",
        "| --- | --- |",
        "| a | b |",
        "",
        "![图示](/api/v1/questions/1/stem.png)",
      ].join("\n"),
    );

    expect(screen.getByText("粗体")).toBeInTheDocument();
    expect(container.querySelector("strong")).not.toBeNull();
    expect(container.querySelector("ul li")?.textContent).toBe("项目一");
    expect(container.querySelector("pre code")?.textContent).toBe("print(1)");
    expect(container.querySelectorAll("table th")).toHaveLength(2);
    const image = container.querySelector("img");
    expect(image).not.toBeNull();
    expect(image?.getAttribute("src")).toBe("/api/v1/questions/1/stem.png");
    expect(image?.getAttribute("referrerpolicy")).toBe("no-referrer");
  });

  it("题干里的脚本与内联事件不会变成可执行内容", () => {
    const payload =
      '<script>window.__xss = 1; alert(1)</script><img src=x onerror="window.__xss=2">';
    const { container } = renderStem(payload);

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelectorAll("[onerror]")).toHaveLength(0);
    expect((globalThis as { __xss?: number }).__xss).toBeUndefined();
    // 原始文本被当作文字保留下来（可审计），而不是被静默吞掉
    expect(container.textContent).toContain("<script>");
  });

  it("javascript: 链接被拦截，渲染为不可点击的文本", () => {
    const { container } = renderStem("[点我](javascript:alert(1))");
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByTestId("blocked-link")).toHaveTextContent("点我");
  });

  it("外部主机图片被拦截，只展示明确的阻塞提示", () => {
    renderStem("![外链](https://evil.example/x.png)");
    expect(screen.getByTestId("blocked-image")).toBeInTheDocument();
  });

  it("受控 URL 下图片加载失败时给出契约缺口提示（不伪装成正常图片）", () => {
    renderStem("![缺图](/api/v1/questions/1/missing.png)");
    fireEvent.error(screen.getByRole("img"));
    expect(screen.getByTestId("unavailable-image")).toBeInTheDocument();
  });

  it("公开 DTO 的整洁性不依赖渲染层：渲染器只接受 stem_markdown 字符串", () => {
    const question = questionPublic();
    const { container } = renderStem(question.stem_markdown);
    // 夹具里的题干含行内代码与标题，且不含任何受保护内容
    expect(container.querySelector("h1")?.textContent).toBe("统计文件行数");
    expect(container.textContent).not.toContain("print(3)");
  });
});
