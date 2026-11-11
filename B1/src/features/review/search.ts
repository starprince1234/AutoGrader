import { REASON_CODES, type ReasonCode } from "./reasonCodes";

/**
 * P13 复核工作区的 URL 状态（`/review`）。
 *
 * 为什么是独立模块：`router.tsx` 需要同一个 `validateSearch`，页面与测试需要同一套解析/回写；
 * 把它放进组件文件会违反 `react-refresh/only-export-components`。
 *
 * 老师删掉了「题目质量」与「示例预览」之后，这里只剩**邮件隔离**的一组筛选：课程/课序、
 * 处理状态、原因、游标。多标签时代的 `tab`、`origin`、`question_id`、`demo`、`q`、`from`、
 * `to` 与 `mail_*` / `quality_*` 暂存参数一并移除——URL 里再出现它们只会被丢弃。
 *
 * 两条纪律：
 *
 * 1. **URL 只放筛选与对象 ID**：不放姓名、学号、student_id、备注正文、邮件正文或凭据。
 * 2. URL 的课程/课序**不是授权依据**：服务端每次按签名令牌与记录行 scope 重新判定；
 *    URL 只决定"请求带上哪些筛选参数"。
 *
 * `resolved` 的 URL 取值是 `true|false|all`：`all` 是本页的表达（表示"两种状态都要"），
 * 请求时会**省略**该参数（契约里省略 = 不过滤）；省略 URL 参数时按默认「未处理」处理。
 * 契约的 `resolved` 是 boolean，没有"两者都要"的取值，因此不能在请求里发明 `all`。
 */

/** 路由查询串（扁平结构：TanStack Router 的 `validateSearch` 与 `navigate` 共用）。 */
export type ReviewSearch = {
  course_id?: string;
  section_id?: string;
  resolved?: "true" | "false" | "all";
  held?: "true" | "false";
  reason_code?: string;
  cursor?: string;
};

/** 邮件列表的**已解析**筛选。`resolved: null` 表示"全部状态"。 */
export type MailFilter = {
  courseId: string | null;
  sectionId: string | null;
  resolved: boolean | null;
  /** 暂存筛选：`true` 只看暂存，`false` 排除暂存，`null` 不筛。 */
  held: boolean | null;
  reasonCode: string | null;
  /** 起始游标：刷新时从该位置重开（追加不回写），而不是"已加载行数"。 */
  cursor: string | null;
};

/** 查询串里的字符串上限：URL 不是内容仓库，超长值直接丢弃（不是截断后照用）。 */
const MAX_URL_TOKEN = 128;

function token(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (trimmed === "" || trimmed.length > MAX_URL_TOKEN) return undefined;
  return trimmed;
}

/**
 * 查询串里的布尔值。
 *
 * 路由会先把查询串按 JSON 解析：`?resolved=true` 到手时已经是布尔 `true`，不是字符串
 * `"true"`。只比较字符串会让"刷新后邮件筛选还在"这件事悄悄失效（默认回到「待处理」），
 * 因此这里两种形态都接受。
 */
function boolValue(value: unknown): boolean | undefined {
  if (value === true || value === "true") return true;
  if (value === false || value === "false") return false;
  return undefined;
}

/**
 * 解析 URL 查询串。非法值一律**丢弃**（不是把它们原样发给服务端）：
 * - `resolved`/`held` 只接受契约能表达的三态/两态；
 * - `reason_code` 只接受契约 enum（未知值不成为筛选，也不成为可执行操作）。
 */
export function parseReviewSearch(search: Record<string, unknown>): ReviewSearch {
  const parsed: ReviewSearch = {};
  const course = token(search.course_id);
  if (course) parsed.course_id = course;
  const section = token(search.section_id);
  if (section) parsed.section_id = section;
  if (search.resolved === "all") {
    parsed.resolved = "all";
  } else {
    const resolved = boolValue(search.resolved);
    if (resolved !== undefined) parsed.resolved = resolved ? "true" : "false";
  }
  const held = boolValue(search.held);
  if (held !== undefined) parsed.held = held ? "true" : "false";
  const reason = token(search.reason_code);
  if (reason && (REASON_CODES as readonly string[]).includes(reason)) {
    parsed.reason_code = reason as ReasonCode;
  }
  const cursor = token(search.cursor);
  if (cursor) parsed.cursor = cursor;
  return parsed;
}

/** 已解析的邮件筛选。URL 省略 `resolved` 时按默认「未处理」（显式 false）处理。 */
export function readMailFilter(search: ReviewSearch): MailFilter {
  const resolved =
    search.resolved === "true" ? true : search.resolved === "all" ? null : false;
  return {
    courseId: search.course_id ?? null,
    sectionId: search.section_id ?? null,
    resolved,
    // 默认视图是"待处理"：既不含暂存，也不含已处理。
    held:
      search.held === "true"
        ? true
        : search.held === "false"
          ? false
          : resolved === false
            ? false
            : null,
    reasonCode: search.reason_code ?? null,
    cursor: search.cursor ?? null,
  };
}

function resolvedValue(value: boolean | null): "true" | "false" | "all" {
  return value === null ? "all" : value ? "true" : "false";
}

function heldValue(value: boolean | null): "true" | "false" | undefined {
  return value === null ? undefined : value ? "true" : "false";
}

/** 写回筛选。改变筛选必然重置游标（旧游标只对旧条件有意义）。 */
export function withMailFilter(
  search: ReviewSearch,
  filter: Omit<MailFilter, "cursor">,
): ReviewSearch {
  return compact({
    course_id: filter.courseId ?? undefined,
    section_id: filter.sectionId ?? undefined,
    resolved: resolvedValue(filter.resolved),
    held: heldValue(filter.held),
    reason_code: filter.reasonCode ?? undefined,
  });
}

/** 只更新游标（"加载更多"之后 URL 保留起始位置；追加不改变筛选）。 */
export function withMailCursor(search: ReviewSearch, cursor: string | null): ReviewSearch {
  return compact({ ...search, cursor: cursor ?? undefined });
}

/** 去掉值为 `undefined` 的键（路由 search 类型不接受 `undefined` 值之外的占位）。 */
function compact(search: ReviewSearch): ReviewSearch {
  const next: ReviewSearch = {};
  for (const [key, value] of Object.entries(search)) {
    if (value !== undefined) (next as Record<string, unknown>)[key] = value;
  }
  return next;
}
