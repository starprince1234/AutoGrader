/**
 * 本地未提交草稿（RQ-B-1-50 验收 4；ADR-0007 第 7 条允许的浏览器本地持久化范围）。
 *
 * 作业工作区的草稿纪律（CHG-0032）：
 * - 键：`b1.draft.v2.<owner>.<assignmentId>.<questionVersionId>`。同一道题可以属于多个作业，
 *   而"第 N 题"与答案文件名的映射按作业确定，因此作业 id 必须进键；否则在两个作业里作答
 *   同一道题会共用一份草稿。
 * - 值：`{ownerId, assignmentId, questionVersionId, content, updatedAt}`——一题一个 `.txt`，
 *   所以内容就是一个字符串，不再有"文件数组"与语言。
 * - 只存**学生自己的未提交答案文本**；不存令牌、不存成绩/名单/参考答案。
 * - 草稿**不等于**已提交：页面必须显式说明，提交成功后清除。
 * - 旧 `b1.draft.v1.*` 键不迁移内容，只在退出登录时一并清理（换机/换人时不留下旧键）。
 * - 解析失败（历史格式、手改、归属不符）时返回 `null` 并丢弃，不做"尽力恢复"。
 */

export type Draft = {
  /** 草稿归属者（平台用户 ID）：读取时必须与当前用户一致，否则丢弃。 */
  ownerId: string;
  assignmentId: string;
  questionVersionId: string;
  content: string;
  updatedAt: string;
};

/** v2 键前缀（作业工作区）。 */
const PREFIX_V2 = "b1.draft.v2.";
/** 单题作答页时代的前缀（已删除该页面）：只在登出时清理，不迁移内容。 */
const LEGACY_PREFIX_V1 = "b1.draft.v1.";

export function draftKey(
  ownerId: string,
  assignmentId: string,
  questionVersionId: string,
): string {
  return `${PREFIX_V2}${ownerId}.${assignmentId}.${questionVersionId}`;
}

export function parseDraft(
  raw: string | null,
  ownerId: string,
  assignmentId: string,
  questionVersionId: string,
): Draft | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const record = parsed as Record<string, unknown>;
    if (record.ownerId !== ownerId) return null;
    if (record.assignmentId !== assignmentId) return null;
    if (record.questionVersionId !== questionVersionId) return null;
    if (typeof record.content !== "string") return null;

    return {
      ownerId,
      assignmentId,
      questionVersionId,
      content: record.content,
      updatedAt:
        typeof record.updatedAt === "string" ? record.updatedAt : new Date().toISOString(),
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
    store.setItem(
      draftKey(draft.ownerId, draft.assignmentId, draft.questionVersionId),
      serializeDraft(draft),
    );
  } catch {
    // 存储配额满/隐私模式：草稿保存失败不能影响作答主流程。
  }
}

export function loadDraft(
  ownerId: string,
  assignmentId: string,
  questionVersionId: string,
): Draft | null {
  const store = storage();
  if (!store) return null;
  return parseDraft(
    store.getItem(draftKey(ownerId, assignmentId, questionVersionId)),
    ownerId,
    assignmentId,
    questionVersionId,
  );
}

export function clearDraft(
  ownerId: string,
  assignmentId: string,
  questionVersionId: string,
): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(draftKey(ownerId, assignmentId, questionVersionId));
  } catch {
    /* 同上：清理失败不影响主流程。 */
  }
}

/**
 * 清空本浏览器**全部**草稿（退出登录时调用）：v2 与已废弃的 v1 键都清。
 * 先收集键再删除：边遍历边删会跳过条目。清理失败不抛出——退出登录不能被本地存储拖住。
 */
export function clearAllDrafts(): void {
  const store = storage();
  if (!store) return;
  try {
    const keys: string[] = [];
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index);
      if (key !== null && (key.startsWith(PREFIX_V2) || key.startsWith(LEGACY_PREFIX_V1))) {
        keys.push(key);
      }
    }
    for (const key of keys) store.removeItem(key);
  } catch {
    /* 同上。 */
  }
}
