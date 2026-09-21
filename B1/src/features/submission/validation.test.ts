import { describe, expect, it } from "vitest";
import {
  ENVELOPE_MAX_BASE64_LENGTH,
  base64Encode,
  buildSubmissionEnvelope,
  byteLength,
  sha256Hex,
  validateSubmission,
} from "./validation";

describe("提交前校验（RQ-B-1-50 验收 1/2）", () => {
  it("空文件列表被拦截", () => {
    expect(validateSubmission([], { maxFiles: 5, maxTotalBytes: 1000 })).toEqual([
      { code: "no_files" },
    ]);
  });

  it("路径必须符合契约的路径模式（拒绝绝对路径、反斜杠、.. 逃逸）", () => {
    const issues = validateSubmission(
      [
        { path: "/abs/solution.py", content: "x" },
        { path: "..\\escape.py", content: "x" },
        { path: "dir/../../escape.py", content: "x" },
      ],
      { maxFiles: null, maxTotalBytes: null },
    );
    expect(issues.filter((issue) => issue.code === "bad_path")).toHaveLength(3);
  });

  it("空路径、重复路径被逐一指出", () => {
    const issues = validateSubmission(
      [
        { path: "", content: "x" },
        { path: "a.py", content: "x" },
        { path: "a.py", content: "y" },
      ],
      { maxFiles: null, maxTotalBytes: null },
    );
    expect(issues).toContainEqual({ code: "empty_path", index: 0 });
    expect(issues).toContainEqual({ code: "duplicate_path", path: "a.py" });
  });

  it("文件数上限取题目声明与契约信封上限的较小值", () => {
    const files = Array.from({ length: 4 }, (_, index) => ({
      path: `f${index}.py`,
      content: "x",
    }));
    const issues = validateSubmission(files, { maxFiles: 2, maxTotalBytes: null });
    expect(issues).toContainEqual({ code: "too_many_files", count: 4, max: 2 });

    // 题目未声明时退到信封的 maxItems
    const many = Array.from({ length: 101 }, (_, index) => ({
      path: `f${index}.py`,
      content: "x",
    }));
    const fallback = validateSubmission(many, { maxFiles: null, maxTotalBytes: null });
    expect(fallback).toContainEqual({ code: "too_many_files", count: 101, max: 100 });
  });

  it("合计大小超限被拦截，单文件 base64 上限使用契约值", () => {
    const issues = validateSubmission(
      [
        { path: "a.py", content: "x".repeat(10) },
        { path: "b.py", content: "y".repeat(10) },
      ],
      { maxFiles: null, maxTotalBytes: 15 },
    );
    expect(issues).toContainEqual({ code: "total_too_large", bytes: 20, max: 15 });

    const big = validateSubmission(
      [{ path: "a.py", content: "x".repeat(ENVELOPE_MAX_BASE64_LENGTH) }],
      { maxFiles: null, maxTotalBytes: null },
    );
    expect(big.some((issue) => issue.code === "file_too_large")).toBe(true);
  });

  it("信封字段全部来自契约：路径、摘要、base64 内容与幂等键", async () => {
    const envelope = await buildSubmissionEnvelope({
      submissionId: "00000000-0000-4000-8000-0000000000a1",
      studentId: "00000000-0000-4000-8000-0000000000b2",
      courseId: "00000000-0000-4000-8000-0000000000c3",
      sectionId: "00000000-0000-4000-8000-0000000000d4",
      questionVersionId: "00000000-0000-4000-8000-0000000000e5",
      idempotencyKey: "idem-1",
      files: [{ path: "solution.py", content: "print('中')\n" }],
    });

    expect(Object.keys(envelope).sort()).toEqual([
      "course_id",
      "files",
      "idempotency_key",
      "question_version_id",
      "section_id",
      "student_id",
      "submission_id",
    ]);
    expect(envelope.files[0].content_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(envelope.files[0].content_base64).toBe(base64Encode("print('中')\n"));
  });

  it("SHA-256 与已知向量一致，字节长度按 UTF-8 计", async () => {
    expect(await sha256Hex("abc")).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(byteLength("中")).toBe(3);
    expect(base64Encode("中")).toBe("5Lit");
  });
});
