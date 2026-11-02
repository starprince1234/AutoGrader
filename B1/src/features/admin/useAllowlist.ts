import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { components } from "../../api/generated/b4";
import { queryKeys } from "../../app/queryClient";

/**
 * 注册名单（管理员）：每个上传的文件是一个**班级**，班级名来自文件名。
 *
 * 与旧的"全量替换"不同：新增一个班级不影响其他班级；删除班级会连同它的名单行一起删除，
 * 否则那些学生仍然可以自助注册。
 */

export type AllowlistBatch = components["schemas"]["AllowlistBatch"];
export type AllowlistEntry = components["schemas"]["AllowlistEntry"];

export type AllowlistBatchesBody = { items: AllowlistBatch[] };
export type AllowlistEntriesBody = { batch_id: string; items: AllowlistEntry[] };

export type AddBatchResult = {
  batch_id: string;
  class_name: string;
  row_count: number;
  skipped_row_count: number;
  duplicate_student_numbers: string[];
  content_sha256: string;
};

export function useAllowlistBatches() {
  return useQuery({
    queryKey: queryKeys.allowlistBatches,
    queryFn: () =>
      apiRequest<AllowlistBatchesBody>("/api/v1/registration-allowlists/batches", {
        method: "GET",
      }),
  });
}

/** 某个班级的学生；`enabled` 为假时不请求（折叠状态下不必拉取）。 */
export function useAllowlistEntries(batchId: string | null) {
  return useQuery({
    queryKey: queryKeys.allowlistEntries(batchId ?? ""),
    enabled: batchId !== null,
    queryFn: () =>
      apiRequest<AllowlistEntriesBody>(
        `/api/v1/registration-allowlists/batches/${encodeURIComponent(batchId as string)}/entries`,
        { method: "GET" },
      ),
  });
}

export function useAddAllowlistBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File): Promise<AddBatchResult> => {
      const formData = new FormData();
      formData.append("file", file);
      return apiRequest<AddBatchResult>("/api/v1/registration-allowlists", {
        method: "POST",
        formData,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.allowlistBatches });
    },
  });
}

export function useDeleteAllowlistBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (batchId: string): Promise<void> =>
      apiRequest<void>(
        `/api/v1/registration-allowlists/batches/${encodeURIComponent(batchId)}`,
        { method: "DELETE" },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.allowlistBatches });
    },
  });
}

export function useDeleteAllowlistEntry(batchId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (entryId: string): Promise<void> =>
      apiRequest<void>(
        `/api/v1/registration-allowlists/entries/${encodeURIComponent(entryId)}`,
        { method: "DELETE" },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.allowlistEntries(batchId ?? "") });
      await queryClient.invalidateQueries({ queryKey: queryKeys.allowlistBatches });
    },
  });
}
