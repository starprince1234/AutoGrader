/**
 * 日志脱敏（RQ-B-1-99 / AGENTS.md 硬规则 4）。
 *
 * 规则是**拒绝清单**而不是"尽量过滤"：命中即替换为 `[redacted]`，不做部分保留，
 * 因为"部分保留的 token"仍然不可接受。
 */

export const REDACTED = "[redacted]";

/**
 * 命中即整体脱敏的键名。覆盖：凭据、参考答案、隐藏用例与期望输出、源码内容。
 * 注意 `content_base64` 命中 `content` 前缀——源码内容一律不落日志。
 */
const SENSITIVE_KEY_PATTERN =
  /(token|secret|password|passwd|credential|cookie|authorization|auth|refresh|jwt|answer|reference|expected|hidden|source|content|base64|email|phone|id_card|identity)/i;

const EMAIL_PATTERN = /[\w.+-]+@[\w-]+\.[\w.-]+/g;
const CN_ID_PATTERN = /\b\d{17}[\dXx]\b/g;
const CN_PHONE_PATTERN = /\b1[3-9]\d{9}\b/g;

/** 浅层字符串脱敏：邮箱、身份证号、手机号。学号（8~12 位数字）按需求允许保留明文。 */
export function maskPiiInText(text: string): string {
  return text
    .replace(EMAIL_PATTERN, REDACTED)
    .replace(CN_ID_PATTERN, REDACTED)
    .replace(CN_PHONE_PATTERN, REDACTED);
}

export function isSensitiveKey(key: string): boolean {
  return SENSITIVE_KEY_PATTERN.test(key);
}

/**
 * 递归脱敏任意值：命中敏感键名的值整体替换；字符串值额外做 PII 文本脱敏。
 * 有深度上限与循环保护——日志路径不允许因为畸形结构抛异常。
 */
export function redactValue(value: unknown, depth = 0, seen = new WeakSet<object>()): unknown {
  if (value === null || value === undefined) return value;
  if (typeof value === "string") return maskPiiInText(value);
  if (typeof value === "number" || typeof value === "boolean") return value;
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "function" || typeof value === "symbol") return "[unsupported]";
  if (depth >= 6) return "[truncated]";

  if (Array.isArray(value)) {
    return value.slice(0, 50).map((item) => redactValue(item, depth + 1, seen));
  }

  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (seen.has(record)) return "[circular]";
    seen.add(record);
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(record)) {
      out[key] = isSensitiveKey(key) ? REDACTED : redactValue(item, depth + 1, seen);
    }
    return out;
  }

  return "[unsupported]";
}

export function redactRecord(
  record: Record<string, unknown>,
): Record<string, unknown> {
  return redactValue(record) as Record<string, unknown>;
}
