import type { MailRecordCategory } from "../../api/types";

/**
 * `/review`（邮件记录页）的 URL 状态。
 *
 * 为什么是独立模块：`router.tsx` 需要同一个 `validateSearch`，页面与测试需要同一套解析/回写；
 * 把它放进组件文件会违反 `react-refresh/only-export-components`。
 *
 * URL 只保留三样东西：课程、课序与**分类**。
 *
 * - `status` 是契约 `listMailRecords.category` 的三态（`processed|pending|other`），缺省是
 *   「待处理」；旧链接上的 `resolved`/`held`/`reason_code`/`cursor` 等参数一律丢弃。
 * - **URL 的课程/课序不是授权依据**：服务端每次按签名令牌与记录行 scope 重新判定；URL 只决定
 *   "请求带上哪些筛选参数"。非法值丢弃，不把它们原样发给服务端。
 * - 分页游标不进 URL：它是服务端签发的位置，切换分类/筛选就重新从第一页开始。
 */

/** 路由查询串（扁平结构：TanStack Router 的 `validateSearch` 与 `navigate` 共用）。 */
export type ReviewSearch = {
  course_id?: string;
  section_id?: string;
  status?: MailRecordCategory;
};

/** 邮件记录的**已解析**筛选：分类 + 课程/课序。 */
export type MailFilter = {
  category: MailRecordCategory;
  courseId: string | null;
  sectionId: string | null;
};

/** 契约 `category` 的取值顺序（页面三个标签的顺序）。 */
export const MAIL_CATEGORIES: readonly MailRecordCategory[] = [
  "processed",
  "pending",
  "other",
];

/** 缺省分类：老师一进来先看"真实提交但没列入作业"的那批记录。 */
export const DEFAULT_MAIL_CATEGORY: MailRecordCategory = "pending";

/** 查询串里的字符串上限：URL 不是内容仓库，超长值直接丢弃（不是截断后照用）。 */
const MAX_URL_TOKEN = 128;

function token(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (trimmed === "" || trimmed.length > MAX_URL_TOKEN) return undefined;
  return trimmed;
}

function isMailCategory(value: string): value is MailRecordCategory {
  return (MAIL_CATEGORIES as readonly string[]).includes(value);
}

/**
 * 解析 URL 查询串。非法值一律**丢弃**（不是把它们原样发给服务端）：
 * - `status` 只接受契约能表达的三态；
 * - `tab`/`resolved`/`held`/`reason_code`/`cursor` 等旧协议参数不作为筛选复活。
 */
export function parseReviewSearch(search: Record<string, unknown>): ReviewSearch {
  const parsed: ReviewSearch = {};
  const course = token(search.course_id);
  if (course) parsed.course_id = course;
  const section = token(search.section_id);
  if (section) parsed.section_id = section;
  const status = token(search.status);
  if (status && isMailCategory(status)) parsed.status = status;
  return parsed;
}

/** 已解析的筛选。URL 省略 `status` 时按缺省「待处理」处理。 */
export function readMailFilter(search: ReviewSearch): MailFilter {
  return {
    category: search.status ?? DEFAULT_MAIL_CATEGORY,
    courseId: search.course_id ?? null,
    sectionId: search.section_id ?? null,
  };
}

/**
 * 写回筛选。
 *
 * 缺省分类不写进 URL：`/review` 不带 `status` 就是「待处理」，这样作业列表带过来的入口链接
 * （只有课程/课序）与页面默认视图是同一个语义。改变筛选后分页自然重置（查询键变化）。
 */
export function withMailFilter(filter: MailFilter): ReviewSearch {
  return compact({
    course_id: filter.courseId ?? undefined,
    section_id: filter.sectionId ?? undefined,
    status: filter.category === DEFAULT_MAIL_CATEGORY ? undefined : filter.category,
  });
}

/** 去掉值为 `undefined` 的键（路由 search 类型不接受 `undefined` 值之外的占位）。 */
function compact(search: ReviewSearch): ReviewSearch {
  const next: ReviewSearch = {};
  for (const [key, value] of Object.entries(search)) {
    if (value !== undefined) (next as Record<string, unknown>)[key] = value;
  }
  return next;
}
