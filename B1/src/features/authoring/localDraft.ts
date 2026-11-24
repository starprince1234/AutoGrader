import { emptyDraftForm, type DraftForm } from "./draft";
import type { StepId } from "./steps";

/**
 * 教师出题页的浏览器本地草稿持久化（防止刷新丢失未保存的输入）。
 *
 * 边界与安全约束：
 * - 键按账号与范围隔离：`b1.authoring.draft.v1.<ownerId>.<scope>`
 *   （`scope` 为 `"new"` 或 `questionId`，AI 面板使用 `b1.authoring.draft.v1.<ownerId>.ai`）。
 * - `sshPassword` 永远不落盘（写入前强制置空），避免练习环境口令进入 localStorage。
 * - 退出登录时调用 `clearAllAuthoringDrafts()` 一并清理。
 * - 解析失败或归属不匹配时返回 `null` 并忽略，不抛异常也不阻断页面。
 */

const PREFIX = "b1.authoring.draft.v1.";
const VALID_STEPS: readonly StepId[] = ["basic", "stem", "reference", "preview"];

export type AuthoringLocalDraft = {
  ownerId: string;
  scope: string;
  courseId: string;
  step: StepId;
  form: DraftForm;
  updatedAt: string;
};

export type AiAuthoringLocalDraft = {
  ownerId: string;
  objective: string;
  difficulty: string;
  referenceTiersCount: number;
  constraints: string;
  updatedAt: string;
};

export function authoringDraftKey(ownerId: string, scope: string): string {
  return `${PREFIX}${ownerId}.${scope}`;
}

export function aiAuthoringDraftKey(ownerId: string): string {
  return `${PREFIX}${ownerId}.ai`;
}

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function sanitizeFormForStorage(form: DraftForm): DraftForm {
  return {
    ...form,
    sshPassword: "",
  };
}

function isStepId(value: unknown): value is StepId {
  return typeof value === "string" && (VALID_STEPS as readonly string[]).includes(value);
}

function mergeDraftForm(rawForm: unknown): DraftForm | null {
  if (typeof rawForm !== "object" || rawForm === null) return null;
  const record = rawForm as Record<string, unknown>;
  const defaults = emptyDraftForm();
  const next: DraftForm = {
    ...defaults,
    title: typeof record.title === "string" ? record.title : defaults.title,
    stemMarkdown:
      typeof record.stemMarkdown === "string" ? record.stemMarkdown : defaults.stemMarkdown,
    language:
      record.language === "python" || record.language === "bash"
        ? record.language
        : defaults.language,
    tags: typeof record.tags === "string" ? record.tags : defaults.tags,
    referenceAnswer:
      typeof record.referenceAnswer === "string"
        ? record.referenceAnswer
        : defaults.referenceAnswer,
    referenceAnswers: Array.isArray(record.referenceAnswers)
      ? record.referenceAnswers
          .filter(
            (item): item is { label: string; body: string } =>
              typeof item === "object" &&
              item !== null &&
              typeof (item as Record<string, unknown>).label === "string" &&
              typeof (item as Record<string, unknown>).body === "string",
          )
          .map((item) => ({ label: item.label, body: item.body }))
      : defaults.referenceAnswers,
    gradingPrompt:
      typeof record.gradingPrompt === "string" ? record.gradingPrompt : defaults.gradingPrompt,
    adapterKind:
      record.adapterKind === "stdin" ||
      record.adapterKind === "file" ||
      record.adapterKind === "callable"
        ? record.adapterKind
        : defaults.adapterKind,
    adapterArgs:
      typeof record.adapterArgs === "string" ? record.adapterArgs : defaults.adapterArgs,
    adapterStdin:
      typeof record.adapterStdin === "string" ? record.adapterStdin : defaults.adapterStdin,
    adapterExpectExitCode:
      typeof record.adapterExpectExitCode === "string"
        ? record.adapterExpectExitCode
        : defaults.adapterExpectExitCode,
    callableModule:
      typeof record.callableModule === "string"
        ? record.callableModule
        : defaults.callableModule,
    callableAttribute:
      typeof record.callableAttribute === "string"
        ? record.callableAttribute
        : defaults.callableAttribute,
    callableArgumentsJson:
      typeof record.callableArgumentsJson === "string"
        ? record.callableArgumentsJson
        : defaults.callableArgumentsJson,
    comparisonStdout:
      typeof record.comparisonStdout === "string"
        ? (record.comparisonStdout as DraftForm["comparisonStdout"])
        : defaults.comparisonStdout,
    comparisonStderr:
      typeof record.comparisonStderr === "string"
        ? (record.comparisonStderr as DraftForm["comparisonStderr"])
        : defaults.comparisonStderr,
    numericTolerance:
      typeof record.numericTolerance === "string"
        ? record.numericTolerance
        : defaults.numericTolerance,
    maxFiles: typeof record.maxFiles === "string" ? record.maxFiles : defaults.maxFiles,
    maxTotalBytes:
      typeof record.maxTotalBytes === "string" ? record.maxTotalBytes : defaults.maxTotalBytes,
    revisionNote:
      typeof record.revisionNote === "string" ? record.revisionNote : defaults.revisionNote,
    executionMode:
      typeof record.executionMode === "string"
        ? (record.executionMode as DraftForm["executionMode"])
        : defaults.executionMode,
    segmentDelimiter:
      typeof record.segmentDelimiter === "string"
        ? record.segmentDelimiter
        : defaults.segmentDelimiter,
    emailAttempt:
      typeof record.emailAttempt === "string" ? record.emailAttempt : defaults.emailAttempt,
    emailSubjectTemplate:
      typeof record.emailSubjectTemplate === "string"
        ? record.emailSubjectTemplate
        : defaults.emailSubjectTemplate,
    sshPort: typeof record.sshPort === "string" ? record.sshPort : defaults.sshPort,
    sshUsername:
      typeof record.sshUsername === "string" ? record.sshUsername : defaults.sshUsername,
    sshPassword: "",
    sshLoginTimeout:
      typeof record.sshLoginTimeout === "string"
        ? record.sshLoginTimeout
        : defaults.sshLoginTimeout,
    sshSessionTimeout:
      typeof record.sshSessionTimeout === "string"
        ? record.sshSessionTimeout
        : defaults.sshSessionTimeout,
    cases: Array.isArray(record.cases)
      ? (record.cases as DraftForm["cases"])
      : defaults.cases,
    fixtures: Array.isArray(record.fixtures)
      ? (record.fixtures as DraftForm["fixtures"])
      : defaults.fixtures,
    confirmCaseReplacement:
      typeof record.confirmCaseReplacement === "boolean"
        ? record.confirmCaseReplacement
        : defaults.confirmCaseReplacement,
  };
  if (next.referenceAnswers.length === 0) {
    next.referenceAnswers = defaults.referenceAnswers;
  }
  return next;
}

export function parseAuthoringDraft(
  raw: string | null,
  ownerId: string,
  scope: string,
): AuthoringLocalDraft | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const record = parsed as Record<string, unknown>;
    if (record.ownerId !== ownerId || record.scope !== scope) return null;
    if (typeof record.courseId !== "string") return null;
    const step: StepId = isStepId(record.step) ? record.step : "basic";
    const form = mergeDraftForm(record.form);
    if (form === null) return null;
    return {
      ownerId,
      scope,
      courseId: record.courseId,
      step,
      form,
      updatedAt:
        typeof record.updatedAt === "string" ? record.updatedAt : new Date().toISOString(),
    };
  } catch {
    return null;
  }
}

export function loadAuthoringDraft(
  ownerId: string,
  scope: string,
): AuthoringLocalDraft | null {
  const store = storage();
  if (!store) return null;
  return parseAuthoringDraft(store.getItem(authoringDraftKey(ownerId, scope)), ownerId, scope);
}

export function saveAuthoringDraft(draft: AuthoringLocalDraft): void {
  const store = storage();
  if (!store) return;
  try {
    const payload: AuthoringLocalDraft = {
      ...draft,
      form: sanitizeFormForStorage(draft.form),
    };
    store.setItem(authoringDraftKey(draft.ownerId, draft.scope), JSON.stringify(payload));
  } catch {
    // 存储配额满或隐私模式：本地草稿保存失败不能影响正常出题流程。
  }
}

export function clearAuthoringDraft(ownerId: string, scope: string): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(authoringDraftKey(ownerId, scope));
  } catch {
    // 清理失败不影响主流程。
  }
}

export function parseAiAuthoringDraft(
  raw: string | null,
  ownerId: string,
): AiAuthoringLocalDraft | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const record = parsed as Record<string, unknown>;
    if (record.ownerId !== ownerId) return null;
    if (typeof record.objective !== "string") return null;
    return {
      ownerId,
      objective: record.objective,
      difficulty: typeof record.difficulty === "string" ? record.difficulty : "medium",
      referenceTiersCount:
        typeof record.referenceTiersCount === "number" &&
        Number.isFinite(record.referenceTiersCount)
          ? record.referenceTiersCount
          : 2,
      constraints: typeof record.constraints === "string" ? record.constraints : "",
      updatedAt:
        typeof record.updatedAt === "string" ? record.updatedAt : new Date().toISOString(),
    };
  } catch {
    return null;
  }
}

export function loadAiAuthoringDraft(ownerId: string): AiAuthoringLocalDraft | null {
  const store = storage();
  if (!store) return null;
  return parseAiAuthoringDraft(store.getItem(aiAuthoringDraftKey(ownerId)), ownerId);
}

export function saveAiAuthoringDraft(draft: AiAuthoringLocalDraft): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(aiAuthoringDraftKey(draft.ownerId), JSON.stringify(draft));
  } catch {
    // 同上。
  }
}

export function clearAiAuthoringDraft(ownerId: string): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(aiAuthoringDraftKey(ownerId));
  } catch {
    // 同上。
  }
}

export function clearAllAuthoringDrafts(): void {
  const store = storage();
  if (!store) return;
  try {
    const keys: string[] = [];
    for (let i = 0; i < store.length; i += 1) {
      const key = store.key(i);
      if (key && key.startsWith(PREFIX)) keys.push(key);
    }
    for (const key of keys) {
      store.removeItem(key);
    }
  } catch {
    // 同上。
  }
}
