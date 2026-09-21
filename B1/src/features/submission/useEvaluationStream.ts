import { useEffect, useRef, useState } from "react";
import { apiRequestRaw } from "../../api/client";
import { isApiError } from "../../api/problem";
import { log, newRequestId } from "../../logging/logger";
import {
  INITIAL_PROGRESS,
  SseParser,
  applyFrame,
  type ProgressState,
} from "./progress";

/**
 * 评测进度流（RQ-B-1-50）。
 *
 * 为什么不用 `EventSource`：它无法携带 `Authorization` 头，也无法读取服务端的
 * ProblemDetails。这里用 `fetch` + `ReadableStream` 手工解析 SSE，从而复用统一的
 * 认证/请求关联链路。
 *
 * 断线重连：终态之前流中断即按退避重连，并带上 `Last-Event-ID`；服务端从头重放也不会
 * 造成重复展示——去重由 `sequence`/事件 id 在 `progress.ts` 里完成。
 */

export type StreamStatus =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed"
  | "error";

const MAX_ATTEMPTS = 5;

export function useEvaluationStream(runId: string | null) {
  const [progress, setProgress] = useState<ProgressState>(INITIAL_PROGRESS);
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [error, setError] = useState<unknown>(null);
  const stateRef = useRef<ProgressState>(INITIAL_PROGRESS);

  useEffect(() => {
    if (!runId) {
      stateRef.current = INITIAL_PROGRESS;
      setProgress(INITIAL_PROGRESS);
      setStatus("idle");
      return;
    }

    let cancelled = false;
    let lastEventId: string | null = null;
    let attempt = 0;
    const controller = new AbortController();
    stateRef.current = INITIAL_PROGRESS;
    setProgress(INITIAL_PROGRESS);
    setError(null);

    const path = `/api/v1/evaluation-runs/${encodeURIComponent(runId)}/events`;

    const ingest = (frames: ReturnType<SseParser["push"]>) => {
      let next = stateRef.current;
      for (const frame of frames) {
        if (frame.id !== null) lastEventId = frame.id;
        const decision = applyFrame(next, frame);
        next = decision.state;
      }
      if (next !== stateRef.current) {
        stateRef.current = next;
        setProgress(next);
      }
    };

    const connect = async (): Promise<void> => {
      setStatus(attempt === 0 ? "connecting" : "reconnecting");
      try {
        const { response } = await apiRequestRaw(path, {
          accept: "text/event-stream",
          headers: lastEventId ? { "Last-Event-ID": lastEventId } : undefined,
          signal: controller.signal,
          requestId: newRequestId(),
        });
        if (!response.body) throw new Error("SSE 响应没有 body");
        setStatus("open");
        attempt = 0;

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        const parser = new SseParser();
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          ingest(parser.push(decoder.decode(value, { stream: true })));
          if (stateRef.current.terminal) break;
        }
      } catch (streamError) {
        if (cancelled || controller.signal.aborted) return;
        if (isApiError(streamError) && streamError.status < 500 && streamError.status !== 429) {
          // 401/403/404 是确定性结论，重试没有意义。
          setError(streamError);
          setStatus("error");
          return;
        }
        setError(streamError);
      }

      if (cancelled || controller.signal.aborted) return;
      if (stateRef.current.terminal) {
        setStatus("closed");
        return;
      }

      attempt += 1;
      if (attempt > MAX_ATTEMPTS) {
        setStatus("error");
        log({
          event: "sse_reconnect_exhausted",
          level: "error",
          evaluation_run_id: runId,
          attempts: attempt - 1,
        });
        return;
      }
      // 退避：0.5s, 1s, 2s, 4s…（上限 8s）
      const delay = Math.min(500 * 2 ** (attempt - 1), 8000);
      log({
        event: "sse_reconnect_scheduled",
        level: "warn",
        evaluation_run_id: runId,
        attempt,
        delay_ms: delay,
        last_event_id: lastEventId,
      });
      await new Promise((resolve) => setTimeout(resolve, delay));
      if (cancelled || controller.signal.aborted) return;
      await connect();
    };

    void connect();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [runId]);

  return { progress, status, error };
}
