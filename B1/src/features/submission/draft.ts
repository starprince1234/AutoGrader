/**
 * 本地未提交草稿（RQ-B-1-50 验收 4；ADR-0007 第 7 条允许的浏览器本地持久化范围）。
 *
 * 纪律：
 * - 只存**学生自己的未提交代码**与 UI 状态；不存令牌、不存成绩/名单/参考答案。
 * - 草稿**不等于**已提交：页面必须显式说明，提交成功后清除。
 * - 键含**用户标识**：机房共用机器是常态，键里没有身份就会把上一个学生的代码显示给下一个
 *   登录的人。退出登录时另外清空本浏览器全部草稿（`clearAllDrafts`）作为第二道保证。
 * - 解析失败（历史格式、手改、归属不符）时返回 `null` 并丢弃，不做"尽力恢复"。
 */

import type { Language } from "../../api/types";

export type DraftFile = { path: string; content: string };

export type Draft = {
  /** 草稿归属者（平台用户 ID）：读取时必须与当前用户一致，否则丢弃。 */
  ownerId: string;
  questionVersionId: string;
  language: Language;
  files: DraftFile[];
  savedAt: string;
};

const PREFIX = "b1.draft.v1.";

export function draftKey(ownerId: string, questionVersionId: string): string {
  return `${PREFIX}${ownerId}.${questionVersionId}`;
}

export function parseDraft(
  raw: string | null,
  ownerId: string,
  questionVersionId: string,
): Draft | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const record = parsed as Record<string, unknown>;
    if (record.ownerId !== ownerId) return null;
    if (record.questionVersionId !== questionVersionId) return null;
    if (record.language !== "python" && record.language !== "bash") return null;
    if (!Array.isArray(record.files)) return null;

    const files: DraftFile[] = [];
    for (const item of record.files) {
      if (typeof item !== "object" || item === null) return null;
      const file = item as Record<string, unknown>;
      if (typeof file.path !== "string" || typeof file.content !== "string") return null;
      files.push({ path: file.path, content: file.content });
    }

    return {
      ownerId,
      questionVersionId,
      language: record.language,
      files,
      savedAt: typeof record.savedAt === "string" ? record.savedAt : new Date().toISOString(),
    };
  } catch {
    return null;
  }
}

export function serializeDraft(draft: Draft): string {
  return JSON.stringify(draft);
}

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function saveDraft(draft: Draft): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(draftKey(draft.ownerId, draft.questionVersionId), serializeDraft(draft));
  } catch {
    // 存储配额满/隐私模式：草稿保存失败不能影响作答主流程。
  }
}

export function loadDraft(ownerId: string, questionVersionId: string): Draft | null {
  const store = storage();
  if (!store) return null;
  return parseDraft(store.getItem(draftKey(ownerId, questionVersionId)), ownerId, questionVersionId);
}

export function clearDraft(ownerId: string, questionVersionId: string): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(draftKey(ownerId, questionVersionId));
  } catch {
    /* 同上：清理失败不影响主流程。 */
  }
}

/**
 * 清空本浏览器**全部**草稿（退出登录时调用）。
 * 先收集键再删除：边遍历边删会跳过条目。清理失败不抛出——退出登录不能被本地存储拖住。
 */
export function clearAllDrafts(): void {
  const store = storage();
  if (!store) return;
  try {
    const keys: string[] = [];
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index);
      if (key !== null && key.startsWith(PREFIX)) keys.push(key);
    }
    for (const key of keys) store.removeItem(key);
  } catch {
    /* 同上。 */
  }
}
