/**
 * 展示层脱敏（RQ-B-1-10 验收 5：预览不得渲染学号以外的敏感信息）。
 *
 * 这是**展示约束**，不是安全边界：真正的数据边界在 B4/B3/B2。
 * 目的是避免浏览器、截图、录屏、前端日志里出现超范围 PII。
 */

import { t } from "../i18n";

const MASKED_KEYS =
  /(email|mail|phone|mobile|tel|id_card|idcard|identity|passport|address|birth|birthday|gender|sex)/i;

export function isMaskedKey(key: string): boolean {
  return MASKED_KEYS.test(key);
}

/**
 * 按列名决定是否脱敏。命中敏感列时只保留"是否填写"的信息，不泄露内容。
 * 用于**无 schema** 的返回（名单导入预览的 `values` 是自由键值表），
 * 有 schema 的响应不需要它——那些表只渲染契约声明的列。
 */
export function maskCellValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (!isMaskedKey(key)) {
    return typeof value === "string" ? value : String(value);
  }
  return t("roster.sensitiveHidden");
}
