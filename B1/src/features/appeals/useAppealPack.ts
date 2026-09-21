import { useQuery } from "@tanstack/react-query";
import { apiRequestRaw } from "../../api/client";
import type { AppealPack, AppealPackView } from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { log, newRequestId } from "../../logging/logger";

/**
 * 申诉证据包（`GET /api/v1/evaluation-runs/{evaluation_run_id}/appeal-pack`，CHG-0014）。
 *
 * 两个刻意的设计点：
 *
 * 1. **一次请求，两种用途**：显示与下载必须来自同一份字节。若先 `apiRequest<AppealPack>()`
 *    再另发一次请求取文件，两次响应的 `result_digest` 理论上可以不同，读者就无从判断
 *    "我读到的"与"我下载的"是不是同一份。这里取 `blob` 一次，显示用 `blob.text()` 解析，
 *    下载直接用同一个 `blob` —— 因此下载的是响应体原字节，不做重新序列化、不重排字段。
 * 2. **`view` 必须显式**：契约给 `view` 声明了 `default: teacher`。省略它等于以教师视图发请求，
 *    所以调用方（`appealPackView`）从真实角色推出该值并始终发送；推不出来时**不发请求**。
 */

export type AppealPackPayload = {
  pack: AppealPack;
  /** 响应体原字节（下载用）。 */
  blob: Blob;
  /** 同一份字节的文本形式（展示用）。 */
  raw: string;
  bytes: number;
  requestId: string;
};

export function appealPackPath(runId: string): string {
  return `/api/v1/evaluation-runs/${encodeURIComponent(runId)}/appeal-pack`;
}

export function appealPackFilename(runId: string): string {
  return `appeal-pack-${runId}.json`;
}

export function useAppealPack(
  runId: string | null,
  view: AppealPackView | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: queryKeys.appealPack(runId ?? "", view ?? ""),
    enabled: enabled && Boolean(runId) && view !== null,
    queryFn: async (): Promise<AppealPackPayload> => {
      const requestId = newRequestId();
      const { response } = await apiRequestRaw(appealPackPath(runId as string), {
        query: { view: view as AppealPackView },
        requestId,
      });
      const blob = await response.blob();
      const raw = await blob.text();
      const pack = JSON.parse(raw) as AppealPack;
      log({
        event: "appeal_pack_loaded",
        request_id: requestId,
        evaluation_run_id: runId,
        view,
        bytes: blob.size,
        // 只记摘要，不记证据包内容（它是可移动文件，内容属于复核者而不是日志）。
        result_digest: pack.result_digest,
      });
      return { pack, blob, raw, bytes: blob.size, requestId };
    },
  });
}

/**
 * 触发下载。用的是 react-query 缓存里的**同一个 `Blob`**，因此不需要再发一次请求，
 * 也不会把 JSON 重新序列化一遍。
 */
export function downloadAppealPack(args: {
  runId: string;
  blob: Blob;
  /** 复用加载那次请求的 `request_id`：日志里"读到的"与"下载的"因此指向同一次响应。 */
  requestId: string;
}): { filename: string; bytes: number } {
  const filename = appealPackFilename(args.runId);
  const url = URL.createObjectURL(args.blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
  log({
    event: "appeal_pack_downloaded",
    request_id: args.requestId,
    evaluation_run_id: args.runId,
    filename,
    bytes: args.blob.size,
  });
  return { filename, bytes: args.blob.size };
}
