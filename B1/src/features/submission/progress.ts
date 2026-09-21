import type { EvaluationStreamFrame, StreamEvent } from "../../api/types";

/**
 * SSE 帧去重与顺序（RQ-B-1-50 验收 5）。
 *
 * 规则（纯函数，便于直接单测）：
 * - **终态后丢弃一切**：收到 `completed`/`rejected`/`failed` 之后到达的帧视为过期，
 *   避免"完成后又回退到 running"的视觉假象。
 * - **事件 id 去重**：SSE 的 `id:` 字段存在时按它去重（at-least-once 的重放）。
 * - **序号去重**：`sequence` 单调，`<= lastSequence` 的帧丢弃——重连后服务端可能
 *   从头重放，靠它保证不重复展示历史进度。
 */

export type RawFrame = {
  id: string | null;
  event: string | null;
  data: string;
};

export type ProgressState = {
  lastSequence: number;
  seenIds: string[];
  terminal: StreamEvent | null;
  frames: EvaluationStreamFrame[];
};

export const INITIAL_PROGRESS: ProgressState = {
  lastSequence: -1,
  seenIds: [],
  terminal: null,
  frames: [],
};

export type FrameDecision =
  | { accepted: true; state: ProgressState; frame: EvaluationStreamFrame }
  | { accepted: false; state: ProgressState; reason: "terminal" | "duplicate_id" | "stale" | "malformed" };

const TERMINAL_EVENTS: readonly StreamEvent[] = ["completed", "rejected", "failed"];

export function isTerminalEvent(event: StreamEvent | null | undefined): boolean {
  return event !== null && event !== undefined && TERMINAL_EVENTS.includes(event);
}

/** 把 `data:` 文本解析为 `EvaluationStreamFrame`；结构不符即拒绝（不猜字段）。 */
export function parseFrameData(data: string): EvaluationStreamFrame | null {
  try {
    const parsed: unknown = JSON.parse(data);
    if (typeof parsed !== "object" || parsed === null) return null;
    const record = parsed as Record<string, unknown>;
    if (typeof record.evaluation_run_id !== "string") return null;
    if (typeof record.sequence !== "number") return null;
    if (typeof record.event !== "string") return null;
    if (typeof record.state !== "string" || typeof record.stage !== "string") return null;
    return parsed as EvaluationStreamFrame;
  } catch {
    return null;
  }
}

/** 单个帧的接受/丢弃判定。 */
export function applyFrame(state: ProgressState, raw: RawFrame): FrameDecision {
  if (state.terminal) return { accepted: false, state, reason: "terminal" };

  const frame = parseFrameData(raw.data);
  if (!frame) return { accepted: false, state, reason: "malformed" };

  if (raw.id !== null && state.seenIds.includes(raw.id)) {
    return { accepted: false, state, reason: "duplicate_id" };
  }
  if (frame.sequence <= state.lastSequence) {
    return { accepted: false, state, reason: "stale" };
  }

  const next: ProgressState = {
    lastSequence: frame.sequence,
    seenIds: raw.id === null ? state.seenIds : [...state.seenIds, raw.id].slice(-500),
    terminal: isTerminalEvent(frame.event) ? frame.event : state.terminal,
    frames: [...state.frames, frame],
  };
  return { accepted: true, state: next, frame };
}

/**
 * 增量 SSE 文本解析器。按空行切分帧；缓冲不完整的尾帧等待下一次 `push`。
 * 支持 `id:` / `event:` / `data:`（多行 `data:` 以 `\n` 连接）与注释行（`:` 开头）。
 */
export class SseParser {
  private buffer = "";

  push(chunk: string): RawFrame[] {
    this.buffer += chunk;
    const frames: RawFrame[] = [];
    let separator = this.buffer.search(/\r?\n\r?\n/);
    while (separator !== -1) {
      const rawBlock = this.buffer.slice(0, separator);
      const matched = /\r?\n\r?\n/.exec(this.buffer.slice(separator));
      this.buffer = this.buffer.slice(separator + (matched?.[0].length ?? 2));
      const frame = parseBlock(rawBlock);
      if (frame) frames.push(frame);
      separator = this.buffer.search(/\r?\n\r?\n/);
    }
    return frames;
  }
}

export function parseBlock(block: string): RawFrame | null {
  let id: string | null = null;
  let event: string | null = null;
  const dataLines: string[] = [];

  for (const line of block.split(/\r?\n/)) {
    if (line === "" || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "id") id = value;
    else if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }

  if (dataLines.length === 0) return null;
  return { id, event, data: dataLines.join("\n") };
}
