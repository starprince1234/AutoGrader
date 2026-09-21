import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildRecord,
  formatLogLine,
  log,
  pendingLogCount,
  resetLoggerForTests,
  setLogSink,
} from "./logger";
import { REDACTED, maskPiiInText, redactValue } from "./redaction";

afterEach(() => {
  resetLoggerForTests();
  vi.restoreAllMocks();
});

describe("结构化日志（RQ-B-1-99）", () => {
  it("每条日志是一行 JSON，含 timestamp/level/event/request_id", () => {
    const record = buildRecord({ event: "submission_accepted", request_id: "req-1" });
    expect(record).toMatchObject({ level: "info", event: "submission_accepted", request_id: "req-1" });
    expect(typeof record.timestamp).toBe("string");

    const parsed: unknown = JSON.parse(formatLogLine(record));
    expect(parsed).toMatchObject({ event: "submission_accepted", request_id: "req-1" });
    expect(formatLogLine(record)).not.toContain("\n");
  });

  it("凭据、参考答案、隐藏用例、源码内容在字段层面被整体脱敏", () => {
    const record = buildRecord({
      event: "api_error",
      request_id: "req-2",
      access_token: "secret-token",
      refresh_token: "refresh",
      authorization: "Bearer abc",
      reference_answer: "print(3)",
      hidden_cases: [{ expected_output: "3" }],
      content_base64: "cHJpbnQoMSk=",
      email: "a@b.edu",
      path: "/api/v1/submissions",
      status: 403,
    });

    expect(record.access_token).toBe(REDACTED);
    expect(record.refresh_token).toBe(REDACTED);
    expect(record.authorization).toBe(REDACTED);
    expect(record.reference_answer).toBe(REDACTED);
    expect(record.content_base64).toBe(REDACTED);
    expect(record.email).toBe(REDACTED);
    // 隐藏用例整个对象被替换，内部期望输出不会漏出
    expect(JSON.stringify(record)).not.toContain("expected_output");
    // 非敏感诊断字段保留
    expect(record.path).toBe("/api/v1/submissions");
    expect(record.status).toBe(403);
  });

  it("字符串里的邮箱、身份证号、手机号被脱敏；学号保留", () => {
    expect(maskPiiInText("联系 a@b.edu")).toBe(`联系 ${REDACTED}`);
    expect(maskPiiInText("手机 13800138000")).toBe(`手机 ${REDACTED}`);
    expect(maskPiiInText("身份证 11010119900307123X")).toContain(REDACTED);
    expect(maskPiiInText("学号 20260001")).toBe("学号 20260001");
  });

  it("畸形结构不会让脱敏抛异常（循环引用、深层嵌套、函数）", () => {
    const cyclic: Record<string, unknown> = { event: "x" };
    cyclic.self = cyclic;
    expect(() => redactValue(cyclic)).not.toThrow();
    expect(redactValue({ a: { b: { c: { d: { e: { f: { g: 1 } } } } } } })).toBeDefined();
    expect(redactValue({ fn: () => 1 })).toEqual({ fn: "[unsupported]" });
  });

  it("没有上报通道时进入有界缓冲，且日志调用永不抛出", () => {
    const before = pendingLogCount();
    log({ event: "navigation", request_id: "req-3", path: "/grades" });
    expect(pendingLogCount()).toBe(before + 1);
    expect(() => log({ event: "circular", payload: (() => { const c: Record<string, unknown> = {}; c.self = c; return c; })() })).not.toThrow();
  });

  it("上报通道抛错时不影响主流程（静默丢弃）", () => {
    setLogSink(() => {
      throw new Error("sink down");
    });
    expect(() => log({ event: "grades_export_triggered", request_id: "req-4" })).not.toThrow();
  });

  it("上报通道返回 rejected Promise 时不会产生未处理拒绝", async () => {
    setLogSink(() => Promise.reject(new Error("sink down")));
    expect(() => log({ event: "grades_export_triggered" })).not.toThrow();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it("成功上报后缓冲被清空", () => {
    const delivered: string[] = [];
    setLogSink((lines) => {
      delivered.push(...lines.map((line) => line.event));
    });
    log({ event: "render_error", level: "error" });
    expect(delivered).toContain("render_error");
    expect(pendingLogCount()).toBe(0);
  });
});
