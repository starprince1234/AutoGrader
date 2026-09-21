import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest, newIdempotencyKey } from "../../api/client";
import type { RosterImportPreview } from "../../api/types";
import { log, newRequestId } from "../../logging/logger";
import { queryKeys } from "../../app/queryClient";

/**
 * 名单导入的写/读路径（RQ-B-1-10）。
 * 关键约束：确认前的所有调用都是**只读或映射**（`POST /roster-imports` 只创建解析任务），
 * 唯一会写入名单的调用是 `confirmRosterImport`，且带幂等键。
 */

export function useRosterImport(importId: string | null) {
  return useQuery({
    queryKey: queryKeys.rosterImport(importId ?? ""),
    enabled: Boolean(importId),
    queryFn: async (): Promise<RosterImportPreview> =>
      apiRequest<RosterImportPreview>(
        `/api/v1/roster-imports/${encodeURIComponent(importId as string)}`,
      ),
  });
}

export function useCreateRosterImport() {
  return useMutation({
    mutationFn: async (input: {
      file: File;
      courseId: string;
      sectionId: string;
    }): Promise<RosterImportPreview> => {
      const requestId = newRequestId();
      const formData = new FormData();
      formData.append("file", input.file, input.file.name);
      formData.append("course_id", input.courseId);
      formData.append("section_id", input.sectionId);
      const preview = await apiRequest<RosterImportPreview>("/api/v1/roster-imports", {
        method: "POST",
        formData,
        requestId,
      });
      log({
        event: "roster_import_uploaded",
        request_id: requestId,
        roster_import_id: preview.roster_import_id,
        status: preview.status,
        // 只记文件名与大小，不记文件内容
        filename: input.file.name,
        size_bytes: input.file.size,
      });
      return preview;
    },
  });
}

export function useSetRosterMapping(importId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (columns: Record<string, string>): Promise<void> => {
      const requestId = newRequestId();
      await apiRequest<void>(
        `/api/v1/roster-imports/${encodeURIComponent(importId)}/mapping`,
        { method: "PUT", body: { columns }, requestId },
      );
      log({
        event: "roster_mapping_submitted",
        request_id: requestId,
        roster_import_id: importId,
        mapped_field_count: Object.keys(columns).length,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.rosterImport(importId),
      });
    },
  });
}

export function useConfirmRosterImport(importId: string) {
  const queryClient = useQueryClient();
  /**
   * 幂等键属于**这一次导入任务**，不属于"这一次点击"：同一次导入的重复确认（网络失败后
   * 用户再点一次）必须带同一个键，B4 才能把它识别为重放而不是第二次写入（`RQ-B-1-10` 验收 4）。
   * 因此键在第一次确认时生成、按 `roster_import_id` 记住，导入换人（`importId` 变化）才重开。
   * 确认成功后按钮消失（状态进入 `committed`），不需要轮换。
   */
  const idempotencyKeyRef = useRef<{ importId: string; key: string } | null>(null);

  return useMutation({
    mutationFn: async (): Promise<void> => {
      const requestId = newRequestId();
      if (idempotencyKeyRef.current?.importId !== importId) {
        idempotencyKeyRef.current = { importId, key: newIdempotencyKey() };
      }
      const idempotencyKey = idempotencyKeyRef.current.key;
      await apiRequest<void>(
        `/api/v1/roster-imports/${encodeURIComponent(importId)}/confirm`,
        { method: "POST", requestId, idempotencyKey },
      );
      log({
        event: "roster_import_confirmed",
        request_id: requestId,
        roster_import_id: importId,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.rosterImport(importId),
      });
    },
  });
}
