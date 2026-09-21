import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type {
  IntakeQuarantinePage,
  IntakeQuarantineView,
  ResolveIntakeQuarantineRequest,
} from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { log, newRequestId } from "../../logging/logger";

/**
 * 隔离区读取与归属（CHG-0014；语义修复见 CHG-0021）。
 *
 * 背景值得写在这里，因为它解释了为什么这个页面必须存在：`b2.intake_quarantines` 从邮件接收
 * 路径建成起就在写——主题/正文里解析不出作业标识的来件被记在隔离区，而不是被丢掉——但**没有
 * 读取路径**。从教师的角度看，未匹配的邮件就是无声无息地消失了。这就是它的出口。
 *
 * `resolved` **始终显式发送**，而且这一点现在是硬要求：`CHG-0021` §2.5 把该参数的省略语义定为
 * "**不过滤（全部）**"（判据是表达力：`boolean` 只有两个取值，若省略等于 `false`，"列出全部"
 * 就再也无法表达）。契约 `0.15.0` 已把这句话写进 `resolved` 的 `description`，因此"只看未归属"
 * 只能由 `?resolved=false` 表达，本页不允许依赖省略。
 */

/**
 * 原因码的类型**取自生成类型**，不在这里抄字段表（MAIN 硬规则 3）。
 *
 * 今天它是 `string`（契约 `0.15.0` 的 `IntakeQuarantineView.reason_code` 只有 `type: string`
 * + 描述里的分组表）；`CHG-0027` 实施后（`0.16.0`）该字段会加上 19 项 `enum`，这个别名就自动变成
 * 字面量联合类型，于是下面那一对值会在**编译期**被核对——若契约里不再有 `ambiguous_match`，
 * `tsc` 会直接报错，而不是让运行时悄悄少一个可归属项。
 */
export type ReasonCode = IntakeQuarantineView["reason_code"];

/**
 * **可归属**的 `reason_code` 集合（`CHG-0021` §2.1）。
 *
 * 契约原先写的是"归属重跑与自动路径相同的校验"，那句话在数据模型下不可判定（隔离表不存报文与
 * 摘要），因此 MAIN 把它换成这个可判定的集合：**只有"身份没推出来"的两类可以人工补身份**；
 * 其余原因码（报文结构、附件策略、内容闸门）是内容类拒绝，人工归属修不好它们，一律 `422`。
 *
 * > **为什么这一对值仍然手写在 B1 里（`CHG-0027` §4 明确接受）**：它**不是字段形状，而是一个行为谓词**
 * > ——"只有身份未推出的记录可归属"。把它做成 `x-attributable-reason-codes` 之类的扩展会得到
 * > **一份没有任何工具核对的"机器可读"副本**（权威在 B2 的 Python 元组里，`check_shared_vocabularies()`
 * > 够不着它），而"假的机器可读"比散文更危险：散文会让人去读实现，假机器可读会让人信任它。
 * > 若将来要机器可读，正确机制是加一道门禁（AST 读 B2 的 `ATTRIBUTABLE_REASON_CODES` 元组再与契约比对
 * > ——"两份副本 + 一个核对者"），`CHG-0027` 记为选项但不引入。
 * >
 * > 注意：`reason_code` 的**词表**（19 项）自 `0.16.0` 起由上面的 `ReasonCode` 承载，
 * > 本文件不再复制它；这里只保留那**两个**行为上可归属的值。
 */
export const ATTRIBUTABLE_REASON_CODES: readonly ReasonCode[] = [
  "not_uniquely_matched",
  "ambiguous_match",
];

/**
 * 该记录能不能人工归属。**fail-closed**：不认识的原因码一律视为不可归属（返回 `false`），
 * 这样"服务端新增了一个原因码"不会变成"教师点了一个必然 422 的按钮"。
 */
export function isAttributableReason(reasonCode: ReasonCode): boolean {
  return ATTRIBUTABLE_REASON_CODES.includes(reasonCode);
}

/** 契约的 `Limit` 上限（`common.yaml`）：一页拿满，减少翻页次数。 */
export const QUARANTINE_PAGE_SIZE = 200;

export type QuarantineFilter = { resolved: boolean; reasonCode: string | null };
export type QuarantineListPage = { items: IntakeQuarantineView[]; nextCursor: string | null };

export function useQuarantines(filter: QuarantineFilter) {
  return useInfiniteQuery({
    queryKey: queryKeys.quarantines(filter),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<QuarantineListPage> => {
      const page = await apiRequest<IntakeQuarantinePage>("/api/v1/intake/quarantines", {
        query: {
          // 始终显式发送，且**永不省略**：省略在 CHG-0021 之后意味着"不过滤（全部）"，
          // 依赖它就等于"筛选器说只看未处理、列表却给了全部"。
          resolved: filter.resolved,
          reason_code: filter.reasonCode ?? undefined,
          limit: QUARANTINE_PAGE_SIZE,
          cursor: pageParam ?? undefined,
        },
      });
      return { items: page.items ?? [], nextCursor: page.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
}

export function loadedQuarantines(
  pages: readonly QuarantineListPage[] | undefined,
): IntakeQuarantineView[] {
  return (pages ?? []).flatMap((page) => page.items);
}

export function useResolveQuarantine() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (args: {
      quarantineId: string;
      body: ResolveIntakeQuarantineRequest;
    }): Promise<IntakeQuarantineView> => {
      const requestId = newRequestId();
      const view = await apiRequest<IntakeQuarantineView>(
        `/api/v1/intake/quarantines/${encodeURIComponent(args.quarantineId)}/resolve`,
        { method: "POST", body: args.body, requestId },
      );
      log({
        event: "intake_quarantine_resolved",
        request_id: requestId,
        intake_quarantine_id: args.quarantineId,
        // 归属说明可能含人写的上下文，只记"有没有"。
        has_note: args.body.note !== null && args.body.note !== undefined,
        resolved: view.resolved,
      });
      return view;
    },
    onSuccess: async () => {
      // 归属后该记录属于「已处理」，因此两个列表（未处理/已处理）都要失效。
      await queryClient.invalidateQueries({ queryKey: ["intake-quarantines"] });
    },
  });
}
