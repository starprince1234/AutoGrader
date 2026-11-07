import { describe, expect, it } from "vitest";
import {
  ENVELOPE_MAX_ANSWER_BYTES,
  answerFileName,
  base64Encode,
  buildSubmissionEnvelope,
  byteLength,
  isValidAnswerPath,
  isTxtFileName,
  sha256Hex,
  validateAnswer,
} from "./validation";

describe("作业答案校验与信封构造（CHG-0032）", () => {
  it("答案文件名按 1 基题号生成，且符合契约的路径模式", () => {
    expect(answerFileName(1)).toBe("1.txt");
    expect(answerFileName(12)).toBe("12.txt");
    expect(isValidAnswerPath("1.txt")).toBe(true);
    expect(isValidAnswerPath("12.txt")).toBe(true);
    // 契约要求单段、小写 .txt、不带前导零。
    expect(isValidAnswerPath("01.txt")).toBe(false);
    expect(isValidAnswerPath("0.txt")).toBe(false);
    expect(isValidAnswerPath("answer.txt")).toBe(false);
    expect(isValidAnswerPath("dir/1.txt")).toBe(false);
  });

  it("只接受 .txt（扩展名大小写敏感；文件名本身不必是 N.txt）", () => {
    expect(isTxtFileName("1.txt")).toBe(true);
    // 学生选的文件叫什么不重要：提交时路径统一按 1 基题号改写成 N.txt。
    expect(isTxtFileName("我的答案.txt")).toBe(true);
    expect(isTxtFileName("1.TXT")).toBe(false);
    expect(isTxtFileName("1.md")).toBe(false);
    expect(isTxtFileName(".txt")).toBe(false);
  });

  it("空答案与非 .txt 文件被拦截；超限按字节数拦截", () => {
    expect(validateAnswer({ fileName: null, content: "   \n" })).toEqual([
      { code: "empty" },
    ]);
    expect(validateAnswer({ fileName: "答案.TXT", content: "x" })).toContainEqual({
      code: "not_txt",
      fileName: "答案.TXT",
    });
    const tooLarge = validateAnswer({
      fileName: "1.txt",
      content: "x".repeat(ENVELOPE_MAX_ANSWER_BYTES + 1),
    });
    expect(tooLarge).toContainEqual({
      code: "too_large",
      bytes: ENVELOPE_MAX_ANSWER_BYTES + 1,
      max: ENVELOPE_MAX_ANSWER_BYTES,
    });
    // 上限按 UTF-8 字节计：汉字占 3 字节。
    const multibyte = validateAnswer({
      fileName: null,
      content: "中".repeat(Math.floor(ENVELOPE_MAX_ANSWER_BYTES / 3) + 1),
    });
    expect(multibyte.some((issue) => issue.code === "too_large")).toBe(true);
  });

  it("信封字段全部来自契约：assignment_id + 单文件 N.txt + 摘要 + 幂等键", async () => {
    const envelope = await buildSubmissionEnvelope({
      submissionId: "00000000-0000-4000-8000-0000000000a1",
      assignmentId: "00000000-0000-4000-8000-0000000000a2",
      studentId: "00000000-0000-4000-8000-0000000000b2",
      courseId: "00000000-0000-4000-8000-0000000000c3",
      sectionId: "00000000-0000-4000-8000-0000000000d4",
      questionVersionId: "00000000-0000-4000-8000-0000000000e5",
      ordinal: 3,
      content: "答案：echo 中\n",
      idempotencyKey: "idem-1",
    });

    expect(Object.keys(envelope).sort()).toEqual([
      "assignment_id",
      "course_id",
      "files",
      "idempotency_key",
      "question_version_id",
      "section_id",
      "student_id",
      "submission_id",
    ]);
    expect(envelope.assignment_id).toBe("00000000-0000-4000-8000-0000000000a2");
    expect(envelope.files).toHaveLength(1);
    expect(envelope.files[0].path).toBe("3.txt");
    expect(envelope.files[0].content_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(envelope.files[0].content_base64).toBe(base64Encode("答案：echo 中\n"));
  });

  it("SHA-256 与已知向量一致，字节长度按 UTF-8 计", async () => {
    expect(await sha256Hex("abc")).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(byteLength("中")).toBe(3);
    expect(base64Encode("中")).toBe("5Lit");
  });
});
