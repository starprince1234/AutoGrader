import { REASON_CODES, type ReasonCode } from "./reasonCodes";

/**
 * P13 复核工作区的 URL 状态（`/review?tab=mail|quality`）。
 *
 * 为什么是独立模块：`router.tsx` 需要同一个 `validateSearch`，页面与测试需要同一套解析/回写；
 * 把它放进组件文件会违反 `react-refresh/only-export-components`。
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

export type ReviewTab = "mail" | "quality";

/** 路由查询串（扁平结构：TanStack Router 的 `validateSearch` 与 `navigate` 共用）。 */
export type ReviewSearch = {
  tab?: ReviewTab;
  course_id?: string;
  section_id?: string;
  resolved?: "true" | "false" | "all";
  reason_code?: string;
  cursor?: string;
  question_version_id?: string;
  from?: string;
  to?: string;
};

/** 邮件标签的**已解析**筛选。`resolved: null` 表示"全部状态"。 */
export type MailFilter = {
  courseId: string | null;
  sectionId: string | null;
  resolved: boolean | null;
  reasonCode: string | null;
  /** 起始游标：刷新时从该位置重开（追加不回写），而不是"已加载行数"。 */
  cursor: string | null;
};

/** 题目质量标签的已解析筛选（三项一起进查询键）。 */
export type QualityFilter = {
  questionVersionId: string | null;
  from: string | null;
  to: string | null;
};

/** 查询串里的字符串上限：URL 不是内容仓库，超长值直接丢弃（不是截断后照用）。 */
const MAX_URL_TOKEN = 128;

function token(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (trimmed === "" || trimmed.length > MAX_URL_TOKEN) return undefined;
  return trimmed;
}

/** 未知 tab 归一到 `mail`：不制造第三条未定义页面，也不给旧入口留兼容路由。 */
export function normalizeReviewTab(value: unknown): ReviewTab {
  return value === "quality" ? "quality" : "mail";
}

/**
 * 解析 URL 查询串。非法值一律**丢弃**（不是把它们原样发给服务端）：
 * - `resolved` 只接受契约能表达的三态；
 * - `reason_code` 只接受契约 enum（未知值不成为筛选，也不成为可执行操作）；
 * - 课程/课序/版本是否合法由各标签用 `isUuid` 判定后再决定是否发请求（这样页面能给出
 *   "题目版本格式不正确"这类针对性的说明，而不是静默变成"没有数据"）。
 */
export function parseReviewSearch(search: Record<string, unknown>): ReviewSearch {
  const parsed: ReviewSearch = {};
  const tab = normalizeReviewTab(search.tab);
  if (tab === "quality") parsed.tab = "quality";
  const course = token(search.course_id);
  if (course) parsed.course_id = course;
  const section = token(search.section_id);
  if (section) parsed.section_id = section;
  if (search.resolved === "true" || search.resolved === "false" || search.resolved === "all") {
    parsed.resolved = search.resolved;
  }
  const reason = token(search.reason_code);
  if (reason && (REASON_CODES as readonly string[]).includes(reason)) {
    parsed.reason_code = reason as ReasonCode;
  }
  const cursor = token(search.cursor);
  if (cursor) parsed.cursor = cursor;
  const version = token(search.question_version_id);
  if (version) parsed.question_version_id = version;
  const from = token(search.from);
  if (from) parsed.from = from;
  const to = token(search.to);
  if (to) parsed.to = to;
  return parsed;
}

/** 已解析的邮件筛选。URL 省略 `resolved` 时按默认「未处理」（显式 false）处理。 */
export function readMailFilter(search: ReviewSearch): MailFilter {
  return {
    courseId: search.course_id ?? null,
    sectionId: search.section_id ?? null,
    resolved: search.resolved === "true" ? true : search.resolved === "all" ? null : false,
    reasonCode: search.reason_code ?? null,
    cursor: search.cursor ?? null,
  };
}

/** 已解析的质量筛选。 */
export function readQualityFilter(search: ReviewSearch): QualityFilter {
  return {
    questionVersionId: search.question_version_id ?? null,
    from: search.from ?? null,
    to: search.to ?? null,
  };
}

/**
 * 写回邮件筛选：**丢弃质量专属参数**，并把 `resolved` 写成显式三态。
 * 改变筛选（课程/课序/状态/原因）必然重置游标——旧游标属于另一份可见集合。
 */
export function withMailFilter(
  _search: ReviewSearch,
  filter: Omit<MailFilter, "cursor">,
): ReviewSearch {
  return compact({
    tab: "mail",
    course_id: filter.courseId ?? undefined,
    section_id: filter.sectionId ?? undefined,
    resolved: filter.resolved === null ? "all" : filter.resolved ? "true" : "false",
    reason_code: filter.reasonCode ?? undefined,
  });
}

/** 只更新游标（"加载更多"之后 URL 保留起始位置；追加不改变筛选）。 */
export function withMailCursor(search: ReviewSearch, cursor: string | null): ReviewSearch {
  return compact({ ...readMailSearch(search), cursor: cursor ?? undefined });
}

/** 写回质量筛选：丢弃邮件专属参数。 */
export function withQualityFilter(
  _search: ReviewSearch,
  filter: QualityFilter,
): ReviewSearch {
  return compact({
    tab: "quality",
    question_version_id: filter.questionVersionId ?? undefined,
    from: filter.from ?? undefined,
    to: filter.to ?? undefined,
  });
}

/** 切换标签：保留目标标签的筛选，**移除另一标签的专属参数**。 */
export function withTab(search: ReviewSearch, tab: ReviewTab): ReviewSearch {
  if (tab === "quality") {
    return withQualityFilter(search, readQualityFilter(search));
  }
  return withMailFilter(search, readMailFilter(search));
}

/** 去掉值为 `undefined` 的键（路由 search 类型不接受 `undefined` 值之外的占位）。 */
function compact(search: ReviewSearch): ReviewSearch {
  const next: ReviewSearch = {};
  for (const [key, value] of Object.entries(search)) {
    if (value !== undefined) (next as Record<string, unknown>)[key] = value;
  }
  return next;
}

/** 当前搜索串的邮件维度（`withMailCursor` 用；保留 `resolved` 的 URL 三态）。 */
function readMailSearch(search: ReviewSearch): ReviewSearch {
  return compact({
    tab: "mail",
    course_id: search.course_id,
    section_id: search.section_id,
    resolved: search.resolved,
    reason_code: search.reason_code,
    cursor: search.cursor,
  });
}
