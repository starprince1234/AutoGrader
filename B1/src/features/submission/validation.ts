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
 * 作业答案文件的校验与信封构造（CHG-0032 / RQ-B-1-50 验收 1）。
 *
 * 规则全部来自契约，不自行发明：
 * - **一次提交 = 一道题、恰好一个 `.txt`**：`files` 的 `minItems/maxItems` 都是 1，
 *   路径模式 `^[A-Za-z0-9_.-]+\.txt$`；文件名是题目在作业中的 1 基序号（`1.txt`、`2.txt`…）。
 * - 单文件 base64 长度上限：信封 `content_base64.maxLength: 2097152`。
 *
 * 前端拦截只是**体验**：越权/超限/题号不属于作业的最终判定仍在 B2（403/422）。
 */
export const ENVELOPE_MAX_BASE64_LENGTH = 2_097_152;

/** 单文件上限的**字节**数（契约按 base64 字符限制；页面只展示学生能理解的字节量）。 */
export const ENVELOPE_MAX_ANSWER_BYTES = Math.floor((ENVELOPE_MAX_BASE64_LENGTH / 4) * 3);

/** 契约的答案文件名规则：1 基题号（不带前导零）+ `.txt`，大小写敏感的 `.txt`。 */
const ANSWER_PATH_PATTERN = /^[1-9][0-9]*\.txt$/;

export type AnswerValidationIssue =
  | { code: "empty" }
  | { code: "not_txt"; fileName: string }
  | { code: "too_large"; bytes: number; max: number };

/** 第 N 题（1 基）的答案文件名。前端只按这个规则生成路径，不接受学生自定义。 */
export function answerFileName(ordinal: number): string {
  return `${ordinal}.txt`;
}

/** 契约的路径规则（`1.txt`、`2.txt`…）是否成立。 */
export function isValidAnswerPath(path: string): boolean {
  return ANSWER_PATH_PATTERN.test(path);
}

/**
 * 上传文件的扩展名判定。
 *
 * **大小写敏感**：契约的路径模式要求 `.txt` 小写，`Answer.TXT` 会被服务端 422，因此前端
 * 直接告诉学生要改成 `.txt`，而不是发一个必然失败的请求。
 */
export function isTxtFileName(fileName: string): boolean {
  return fileName.endsWith(".txt") && fileName.length > 4;
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

/**
 * 一道题的答案校验。
 *
 * `fileName` 传 `null` 表示内容来自 textarea 粘贴（没有文件名）；只要内容非空即可。
 * 来自文件选择时必须先通过 `isTxtFileName`，避免把 `.TXT`/`.md` 当成可提交答案。
 */
export function validateAnswer(input: {
  fileName: string | null;
  content: string;
}): AnswerValidationIssue[] {
  const issues: AnswerValidationIssue[] = [];
  if (input.fileName !== null && !isTxtFileName(input.fileName)) {
    issues.push({ code: "not_txt", fileName: input.fileName });
  }
  const bytes = byteLength(input.content);
  if (input.content.trim() === "") {
    issues.push({ code: "empty" });
  } else if (bytes > ENVELOPE_MAX_ANSWER_BYTES) {
    issues.push({ code: "too_large", bytes, max: ENVELOPE_MAX_ANSWER_BYTES });
  }
  return issues;
}

/**
 * 构造一次提交的信封：**一道题一个文件**。
 *
 * `submissionId` 与 `idempotencyKey` 由调用方生成：同一份草稿重试时必须复用同一个幂等键，
 * 成功后才换新的键（见工作区的 `idempotencyKeyRef`）。
 */
export async function buildSubmissionEnvelope(input: {
  submissionId: string;
  assignmentId: string;
  studentId: string;
  courseId: string;
  sectionId: string;
  questionVersionId: string;
  /** 该题在作业中的 1 基序号（决定 `N.txt`）。 */
  ordinal: number;
  content: string;
  idempotencyKey: string;
}): Promise<SubmissionEnvelopeBody> {
  return {
    submission_id: input.submissionId,
    assignment_id: input.assignmentId,
    student_id: input.studentId,
    course_id: input.courseId,
    section_id: input.sectionId,
    question_version_id: input.questionVersionId,
    files: [
      {
        path: answerFileName(input.ordinal),
        content_sha256: await sha256Hex(input.content),
        content_base64: base64Encode(input.content),
      },
    ],
    idempotency_key: input.idempotencyKey,
  };
}
