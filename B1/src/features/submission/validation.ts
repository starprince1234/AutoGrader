import type { SubmissionEnvelope } from "../../api/types";

/**
 * 提交信封的请求体类型。
 *
 * 生成类型里带上了 JSON Schema 根部的 `$defs`（生成器把 schema 内部定义也展开成属性），
 * 而它并不是契约的请求字段。这里显式去掉它，其余字段完全来自
 * `MAIN/contracts/schemas/submission-envelope.schema.json`，不做任何增删。
 */
export type SubmissionEnvelopeBody = Omit<SubmissionEnvelope, "$defs">;

/**
 * 提交前校验与信封构造（RQ-B-1-50 验收 1/2）。
 *
 * 校验规则全部来自契约，不自行发明：
 * - 路径模式：`submission-envelope.schema.json` 的 `^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$`
 * - 文件数：信封 `maxItems: 100` 与题目 `QuestionVersionPublic.max_files`
 * - 单文件 base64 长度上限：信封 `content_base64.maxLength: 2097152`
 * - 合计大小：题目 `QuestionVersionPublic.max_total_bytes`（按内容字节计）
 *
 * 前端拦截只是**体验**：越权/超限的最终判定仍在 B2（超限返回 413/422）。
 */

export type SubmissionFile = { path: string; content: string };

export type ValidationIssue =
  | { code: "no_files" }
  | { code: "empty_path"; index: number }
  | { code: "bad_path"; index: number; path: string }
  | { code: "duplicate_path"; path: string }
  | { code: "too_many_files"; count: number; max: number }
  | { code: "file_too_large"; path: string; bytes: number; max: number }
  | { code: "total_too_large"; bytes: number; max: number };

export const PATH_PATTERN = /^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/;
export const ENVELOPE_MAX_FILES = 100;
export const ENVELOPE_MAX_BASE64_LENGTH = 2_097_152;

/**
 * B1 的**额外保守检查**：契约的路径模式允许 `..` 段（`[A-Za-z0-9_.-]+` 能匹配 `..`），
 * 而 `RQ-B-2-10` 明确要求"路径不合规（`..`、绝对路径、反斜杠逃逸）返回 422"。
 * 因此 B1 在提交前就把 `.`/`..` 段挡掉，省掉一次注定失败的往返。
 * **B2 仍然是判定方**：这个检查不放宽任何服务端校验。
 */
export function hasTraversalSegment(path: string): boolean {
  return path.split("/").some((segment) => segment === "." || segment === "..");
}

export function byteLength(content: string): number {
  return new TextEncoder().encode(content).length;
}

/** base64 编码（浏览器 `btoa` 只接受 latin1，先按 UTF-8 转字节）。 */
export function base64Encode(content: string): string {
  const bytes = new TextEncoder().encode(content);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export async function sha256Hex(content: string): Promise<string> {
  const bytes = new TextEncoder().encode(content);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export type SubmissionLimits = {
  maxFiles: number | null;
  maxTotalBytes: number | null;
};

export function validateSubmission(
  files: SubmissionFile[],
  limits: SubmissionLimits,
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (files.length === 0) {
    issues.push({ code: "no_files" });
    return issues;
  }

  const effectiveMaxFiles = Math.min(limits.maxFiles ?? ENVELOPE_MAX_FILES, ENVELOPE_MAX_FILES);
  if (files.length > effectiveMaxFiles) {
    issues.push({ code: "too_many_files", count: files.length, max: effectiveMaxFiles });
  }

  const seen = new Set<string>();
  let total = 0;
  files.forEach((file, index) => {
    if (file.path.trim() === "") {
      issues.push({ code: "empty_path", index });
    } else if (!PATH_PATTERN.test(file.path) || hasTraversalSegment(file.path)) {
      issues.push({ code: "bad_path", index, path: file.path });
    } else if (seen.has(file.path)) {
      issues.push({ code: "duplicate_path", path: file.path });
    } else {
      seen.add(file.path);
    }

    const bytes = byteLength(file.content);
    total += bytes;
    if (base64Encode(file.content).length > ENVELOPE_MAX_BASE64_LENGTH) {
      issues.push({
        code: "file_too_large",
        path: file.path,
        bytes,
        max: ENVELOPE_MAX_BASE64_LENGTH,
      });
    }
  });

  if (limits.maxTotalBytes !== null && total > limits.maxTotalBytes) {
    issues.push({ code: "total_too_large", bytes: total, max: limits.maxTotalBytes });
  }

  return issues;
}

export async function buildSubmissionEnvelope(input: {
  submissionId: string;
  studentId: string;
  courseId: string;
  sectionId: string;
  questionVersionId: string;
  idempotencyKey: string;
  files: SubmissionFile[];
}): Promise<SubmissionEnvelopeBody> {
  const files = await Promise.all(
    input.files.map(async (file) => ({
      path: file.path,
      content_sha256: await sha256Hex(file.content),
      content_base64: base64Encode(file.content),
    })),
  );
  return {
    submission_id: input.submissionId,
    student_id: input.studentId,
    course_id: input.courseId,
    section_id: input.sectionId,
    question_version_id: input.questionVersionId,
    files,
    idempotency_key: input.idempotencyKey,
  };
}
