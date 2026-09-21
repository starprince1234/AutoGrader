import { redactRecord } from "./redaction";

/**
 * 结构化前端日志（RQ-B-1-99）。
 *
 * 契约（与后端日志可关联）：
 * - 每行一条 JSON，字段：`timestamp` / `level` / `event` / `request_id`（+ 事件自身字段）。
 * - 生产构建不输出 `debug`；日志不含 token、源码、参考答案、隐藏用例、超范围 PII。
 * - 上报失败**不阻塞**主流程：降级为有界本地缓冲 + 静默丢弃，且不吞掉调用方的异常。
 */

export type LogLevel = "debug" | "info" | "warn" | "error";

export type LogRecord = {
  timestamp: string;
  level: LogLevel;
  event: string;
  request_id: string | null;
  [key: string]: unknown;
};

export type LogSink = (lines: LogRecord[]) => void | Promise<void>;

export type LogInput = {
  event: string;
  level?: LogLevel;
  request_id?: string | null;
  [key: string]: unknown;
};

const BUFFER_LIMIT = 200;
const PRODUCTION = Boolean(import.meta.env?.PROD);

let sink: LogSink | null = null;
const buffer: LogRecord[] = [];

/** 生成与后端 `X-Request-ID` 同源的关联标识（UUID v4）。 */
export function newRequestId(): string {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    return cryptoObj.randomUUID();
  }
  // 仅用于无法访问 WebCrypto 的环境（例如部分测试运行器）。
  const hex = "0123456789abcdef";
  let out = "";
  for (let i = 0; i < 32; i += 1) out += hex[Math.floor(Math.random() * 16)];
  return `${out.slice(0, 8)}-${out.slice(8, 12)}-4${out.slice(13, 16)}-a${out.slice(17, 20)}-${out.slice(20, 32)}`;
}

export function buildRecord(input: LogInput): LogRecord {
  const { event, level = "info", request_id = null, ...rest } = input;
  return {
    timestamp: new Date().toISOString(),
    level,
    event,
    request_id,
    ...(redactRecord(rest) as Record<string, unknown>),
  };
}

/** 把一条记录格式化为单行 JSON（`pnpm test` 直接断言该输出）。 */
export function formatLogLine(record: LogRecord): string {
  return JSON.stringify(record);
}

function emit(record: LogRecord): void {
  if (PRODUCTION && record.level === "debug") return;

  const line = formatLogLine(record);
  const consoleFn =
    record.level === "error"
      ? console.error
      : record.level === "warn"
        ? console.warn
        : console.log;
  consoleFn(line);

  if (!sink) {
    buffer.push(record);
    if (buffer.length > BUFFER_LIMIT) buffer.splice(0, buffer.length - BUFFER_LIMIT);
    return;
  }

  const pending = buffer.splice(0, buffer.length);
  pending.push(record);
  try {
    const result = sink(pending);
    if (result && typeof (result as Promise<void>).catch === "function") {
      (result as Promise<void>).catch(() => {
        /* 上报失败：静默丢弃，不阻塞主流程。 */
      });
    }
  } catch {
    /* 同上。 */
  }
}

/**
 * 记录一条结构化日志。**永不抛出**：日志故障不得影响用户操作。
 */
export function log(input: LogInput): LogRecord | null {
  try {
    const record = buildRecord(input);
    emit(record);
    return record;
  } catch {
    return null;
  }
}

export function setLogSink(next: LogSink | null): void {
  sink = next;
}

export function pendingLogCount(): number {
  return buffer.length;
}

/** 无副作用的只读快照，供测试与排障使用。 */
export function peekBuffer(): readonly LogRecord[] {
  return buffer;
}

export function resetLoggerForTests(): void {
  sink = null;
  buffer.length = 0;
}

/**
 * 可选的 HTTP 上报通道（`VITE_LOG_ENDPOINT`）。
 * 未配置时 B1 只落本地控制台——契约里没有日志端点，B1 不得发明一个。
 */
export function createHttpLogSink(endpoint: string): LogSink {
  return async (lines) => {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ lines }),
      keepalive: true,
    });
    if (!response.ok) throw new Error(`log sink rejected: ${response.status}`);
  };
}

export function installLogSinkFromEnv(): void {
  const endpoint = import.meta.env?.VITE_LOG_ENDPOINT;
  if (endpoint && endpoint.trim() !== "") {
    setLogSink(createHttpLogSink(endpoint.trim()));
  }
}
