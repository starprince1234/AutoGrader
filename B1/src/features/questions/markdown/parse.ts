/**
 * 安全 Markdown 子集解析器（RQ-B-1-40）。
 *
 * 三条不可协商的规则：
 * 1. **不做 HTML 透传**：源文本里的 `<script>`、`<img onerror=...>` 一律当作普通文字
 *    （由 React 文本节点转义），不存在把字符串塞进 `innerHTML` 的路径。
 * 2. 只产出**结构化节点**，由渲染层决定元素的属性；解析器不产生任何"原始属性串"。
 * 3. 链接/图片的 URL 另经 `urlPolicy` 白名单判定，解析器本身不做网络判断。
 */

export type InlineNode =
  | { type: "text"; value: string }
  | { type: "strong"; children: InlineNode[] }
  | { type: "em"; children: InlineNode[] }
  | { type: "code"; value: string }
  | { type: "link"; href: string; children: InlineNode[] }
  | { type: "image"; src: string; alt: string };

export type BlockNode =
  | { type: "heading"; level: 1 | 2 | 3 | 4 | 5 | 6; children: InlineNode[] }
  | { type: "paragraph"; children: InlineNode[] }
  | { type: "code_block"; language: string | null; value: string }
  | { type: "list"; ordered: boolean; items: InlineNode[][] }
  | { type: "blockquote"; children: BlockNode[] }
  | { type: "table"; header: InlineNode[][]; rows: InlineNode[][][] };

const FENCE = /^\s*(```|~~~)\s*([A-Za-z0-9_+-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const UNORDERED = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const TABLE_DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

/**
 * 行内解析。刻意不支持嵌套链接、原始 HTML、自动链接（避免把不可控字符串变成可点击/可执行内容）。
 * 解析顺序即安全边界：代码片段先被"吃掉"，其中的 `*`/`[]`/`<>` 不再被解释。
 */
export function parseInline(input: string): InlineNode[] {
  const nodes: InlineNode[] = [];
  let buffer = "";
  let index = 0;

  const flushText = () => {
    if (buffer !== "") {
      nodes.push({ type: "text", value: buffer });
      buffer = "";
    }
  };

  while (index < input.length) {
    const rest = input.slice(index);

    const code = /^`([^`]+)`/.exec(rest);
    if (code) {
      flushText();
      nodes.push({ type: "code", value: code[1] });
      index += code[0].length;
      continue;
    }

    const image = /^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/.exec(rest);
    if (image) {
      flushText();
      nodes.push({ type: "image", alt: image[1], src: image[2] });
      index += image[0].length;
      continue;
    }

    const link = /^\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/.exec(rest);
    if (link) {
      flushText();
      nodes.push({ type: "link", href: link[2], children: parseInline(link[1]) });
      index += link[0].length;
      continue;
    }

    const strong = /^(\*\*|__)(.+?)\1/.exec(rest);
    if (strong) {
      flushText();
      nodes.push({ type: "strong", children: parseInline(strong[2]) });
      index += strong[0].length;
      continue;
    }

    const em = /^(\*|_)([^*_]+?)\1/.exec(rest);
    if (em) {
      flushText();
      nodes.push({ type: "em", children: parseInline(em[2]) });
      index += em[0].length;
      continue;
    }

    buffer += input[index];
    index += 1;
  }

  flushText();
  return nodes;
}

/** 块级解析：只按"整行"判定，不做 HTML 块级透传。 */
export function parseMarkdown(source: string): BlockNode[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: BlockNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (line.trim() === "") {
      index += 1;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1];
      const language = fence[2] === "" ? null : fence[2];
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith(marker)) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1; // 跳过结束围栏（缺失时也安全收尾）
      blocks.push({ type: "code_block", language, value: body.join("\n") });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      const level = Math.min(heading[1].length, 6) as 1 | 2 | 3 | 4 | 5 | 6;
      blocks.push({ type: "heading", level, children: parseInline(heading[2]) });
      index += 1;
      continue;
    }

    if (QUOTE.test(line)) {
      const quoted: string[] = [];
      while (index < lines.length && QUOTE.test(lines[index])) {
        quoted.push(QUOTE.exec(lines[index])?.[1] ?? "");
        index += 1;
      }
      blocks.push({ type: "blockquote", children: parseMarkdown(quoted.join("\n")) });
      continue;
    }

    // 表格：首行含 `|`，次行是分隔行。
    if (line.includes("|") && index + 1 < lines.length && TABLE_DIVIDER.test(lines[index + 1])) {
      const header = splitTableRow(line).map(parseInline);
      index += 2;
      const rows: InlineNode[][][] = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim() !== "") {
        rows.push(splitTableRow(lines[index]).map(parseInline));
        index += 1;
      }
      blocks.push({ type: "table", header, rows });
      continue;
    }

    if (UNORDERED.test(line) || ORDERED.test(line)) {
      const ordered = ORDERED.test(line);
      const items: InlineNode[][] = [];
      while (index < lines.length) {
        const current = lines[index];
        const match = ordered ? ORDERED.exec(current) : UNORDERED.exec(current);
        if (!match) break;
        items.push(parseInline(match[1]));
        index += 1;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }

    const paragraph: string[] = [];
    while (index < lines.length) {
      const current = lines[index];
      if (
        current.trim() === "" ||
        FENCE.test(current) ||
        HEADING.test(current) ||
        QUOTE.test(current) ||
        UNORDERED.test(current) ||
        ORDERED.test(current)
      ) {
        break;
      }
      paragraph.push(current);
      index += 1;
    }
    blocks.push({ type: "paragraph", children: parseInline(paragraph.join("\n")) });
  }

  return blocks;
}
