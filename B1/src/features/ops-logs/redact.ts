/**
 * 日志脱敏与模糊匹配的小工具。
 *
 * 单独成文件是为了满足 `react-refresh/only-export-components`：组件文件里只能导出组件。
 */

/**
 * 敏感字段一律脱敏后再展示：密码、令牌、参考答案、评分细则、用例真实内容。
 * 抽屉里学生也可能通过申诉链路看到，不能明文。
 */
const SENSITIVE_KEY =
  /(password|passwd|secret|token|authorization|cookie|api[_-]?key|session[_-]?id|reference[_-]?answer|grading[_-]?prompt|expected[_-]?(stdout|stderr|output)|cases?|stdin|fixture)/i;

export function redactSensitive(value: unknown, keyHint = ""): unknown {
  if (SENSITIVE_KEY.test(keyHint)) return "[REDACTED]";
  if (Array.isArray(value)) return value.map((item) => redactSensitive(item, keyHint));
  if (value !== null && typeof value === "object") {
    const output: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      output[key] = redactSensitive(item, key);
    }
    return output;
  }
  return value;
}

/** 事件名模糊匹配：输入片段即可（不区分大小写），空串表示不过滤。 */
export function matchesFuzzy(haystack: string, needle: string): boolean {
  if (needle === "") return true;
  return haystack.toLocaleLowerCase().includes(needle.toLocaleLowerCase());
}
