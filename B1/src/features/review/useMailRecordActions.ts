import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";

/**
 * 邮件记录页的处置动作：**打回**与**标记作废**。
 *
 * 这一页原先只读，"有邮件待处理却无法处理"是用户明确报过的 bug。两个动作都打在**隔离记录**上：
 * `MailRecordView.record_id` 对 `pending`/`other` 就是 `intake_quarantine_id`（`processed`
 * 是提交 id，因此那一类不提供动作）。
 *
 * - 打回（`rejectIntakeQuarantine`）= 处置 + 向这封邮件**真实的原发件地址**发失败通知；
 *   响应里的 `notification` 只说这封信有没有交出去（`queued`/`unavailable`），不决定处置结果。
 * - 作废（`dismissIntakeQuarantine`）= 只记录处置，不发任何通知。
 *
 * 成功后失效整个 `mail-records` 前缀：同一条记录会从「待处理」移到「已处理」，三个标签页
 * 与作业页头的待处理徽标一起刷新。
 */

export type MailRecordNotification = "queued" | "unavailable";

export type MailRecordRejection = {
  record: unknown;
  notification: MailRecordNotification;
};

type ActionInput = { recordId: string; note: string | null };

export function useRejectMailRecord() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: ActionInput): Promise<MailRecordRejection> =>
      apiRequest<MailRecordRejection>(
        `/api/v1/intake/quarantines/${encodeURIComponent(input.recordId)}/reject`,
        { method: "POST", body: { note: input.note } },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["mail-records"] });
    },
  });
}

export function useDismissMailRecord() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: ActionInput): Promise<unknown> =>
      apiRequest<unknown>(
        `/api/v1/intake/quarantines/${encodeURIComponent(input.recordId)}/dismiss`,
        { method: "POST", body: { note: input.note } },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["mail-records"] });
    },
  });
}
