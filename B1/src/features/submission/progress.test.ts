import { describe, expect, it } from "vitest";
import {
  INITIAL_PROGRESS,
  SseParser,
  applyFrame,
  isTerminalEvent,
  parseFrameData,
  type ProgressState,
} from "./progress";
import { streamFrame } from "../../testing/contractFixtures";

function feed(state: ProgressState, calls: { id: string | null; frame: unknown }[]) {
  let current = state;
  const decisions = [];
  for (const call of calls) {
    const decision = applyFrame(current, {
      id: call.id,
      event: null,
      data: JSON.stringify(call.frame),
    });
    decisions.push(decision);
    current = decision.state;
  }
  return { state: current, decisions };
}

describe("SSE 帧去重与顺序（RQ-B-1-50 验收 5）", () => {
  it("按 sequence 单调推进，回放的旧帧被丢弃", () => {
    const { state, decisions } = feed(INITIAL_PROGRESS, [
      { id: "1", frame: streamFrame({ sequence: 1 }) },
      { id: "2", frame: streamFrame({ sequence: 2, cases_done: 2 }) },
      // 重连后服务端可能从一个新的事件 id 重放更小的序号
      { id: "9", frame: streamFrame({ sequence: 1 }) },
    ]);
    expect(decisions.map((decision) => decision.accepted)).toEqual([true, true, false]);
    expect(decisions[2]).toMatchObject({ reason: "stale" });
    expect(state.lastSequence).toBe(2);
    expect(state.frames).toHaveLength(2);
  });

  it("相同事件 id 重复投递时去重（at-least-once）", () => {
    const first = applyFrame(INITIAL_PROGRESS, {
      id: "evt-9",
      event: null,
      data: JSON.stringify(streamFrame({ sequence: 9 })),
    });
    const second = applyFrame(first.state, {
      id: "evt-9",
      event: null,
      data: JSON.stringify(streamFrame({ sequence: 10 })),
    });
    expect(second.accepted).toBe(false);
    expect(second).toMatchObject({ reason: "duplicate_id" });
    expect(second.state.lastSequence).toBe(9);
  });

  it("终态之后的一切帧都被丢弃（不会从 completed 退回 running）", () => {
    const terminal = applyFrame(INITIAL_PROGRESS, {
      id: "5",
      event: "completed",
      data: JSON.stringify(streamFrame({ sequence: 5, event: "completed", state: "completed" })),
    });
    expect(terminal.accepted).toBe(true);
    expect(terminal.state.terminal).toBe("completed");

    const late = applyFrame(terminal.state, {
      id: "6",
      event: "progress",
      data: JSON.stringify(streamFrame({ sequence: 6, state: "running" })),
    });
    expect(late.accepted).toBe(false);
    expect(late).toMatchObject({ reason: "terminal" });
    expect(late.state.terminal).toBe("completed");
  });

  it("结构不符的帧被拒绝（不猜字段）", () => {
    const decision = applyFrame(INITIAL_PROGRESS, { id: null, event: null, data: '{"foo":1}' });
    expect(decision.accepted).toBe(false);
    expect(decision).toMatchObject({ reason: "malformed" });
    expect(parseFrameData("not json")).toBeNull();
  });

  it("三种终态都被识别", () => {
    expect(isTerminalEvent("completed")).toBe(true);
    expect(isTerminalEvent("rejected")).toBe(true);
    expect(isTerminalEvent("failed")).toBe(true);
    expect(isTerminalEvent("progress")).toBe(false);
    expect(isTerminalEvent(null)).toBe(false);
  });
});

describe("SSE 文本解析", () => {
  it("按空行切帧，拼接多行 data，忽略注释行", () => {
    const parser = new SseParser();
    const frames = parser.push(
      ": keepalive\n" +
        'id: 1\nevent: progress\ndata: {"a":1}\ndata: {"b":2}\n\n' +
        'id: 2\ndata: {"c":3}\n',
    );
    expect(frames).toHaveLength(1);
    expect(frames[0]).toMatchObject({ id: "1", event: "progress" });
    expect(frames[0].data).toBe('{"a":1}\n{"b":2}');
  });

  it("跨 chunk 的不完整帧被缓冲到下一次 push", () => {
    const parser = new SseParser();
    expect(parser.push('id: 1\ndata: {"a"')).toEqual([]);
    const frames = parser.push(':1}\n\n');
    expect(frames).toHaveLength(1);
    expect(frames[0].data).toBe('{"a":1}');
  });
});
