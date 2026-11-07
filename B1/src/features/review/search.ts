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
  /** 从题目详情进入 quality 标签：返回按钮回那道题，而不是作业列表。 */
  origin?: "question";
  /** 来源题目：从题目详情进来时带上，右上角「返回这个题」直接回那道题。 */
  question_id?: string;
  /** 示例预览（前端 mock，不请求/不写真实数据）。 */
  demo?: "1";
  /** 质量标签已经按当前条件查询过一次（地址栏能恢复结果，但不会自动"选好就出数字"）。 */
  q?: "1";
  course_id?: string;
  section_id?: string;
  resolved?: "true" | "false" | "all";
  held?: "true" | "false";
  reason_code?: string;
  cursor?: string;
  question_version_id?: string;
  from?: string;
  to?: string;
  /** 非当前标签的查询条件：切换标签时暂存，切回来原样恢复。 */
  mail_course_id?: string;
  mail_section_id?: string;
  mail_resolved?: "true" | "false" | "all";
  mail_held?: "true" | "false";
  mail_reason_code?: string;
  mail_cursor?: string;
  quality_course_id?: string;
  quality_question_version_id?: string;
  quality_from?: string;
  quality_to?: string;
};

/** 邮件标签的**已解析**筛选。`resolved: null` 表示"全部状态"。 */
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

/** 题目质量标签的已解析筛选（三项一起进查询键）。 */
export type QualityFilter = {
  courseId: string | null;
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
 *
 * `demo` 必须同时接受字符串与数字：路由把查询串按 JSON 解析，`?demo=1` 到手时是数字 `1`，
 * 只比较 `"1"` 会让刷新/直链的示例模式悄悄失效（与导览页 `registered` 同一类坑）。
 */
export function parseReviewSearch(search: Record<string, unknown>): ReviewSearch {
  const parsed: ReviewSearch = {};
  const tab = normalizeReviewTab(search.tab);
  if (tab === "quality") parsed.tab = "quality";
  if (search.origin === "question") parsed.origin = "question";
  const questionId = token(search.question_id);
  if (questionId) parsed.question_id = questionId;
  if (search.demo === "1" || search.demo === 1 || search.demo === true) parsed.demo = "1";
  if (search.q === "1" || search.q === 1 || search.q === true) parsed.q = "1";
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
  const version = token(search.question_version_id);
  if (version) parsed.question_version_id = version;
  const from = token(search.from);
  if (from) parsed.from = from;
  const to = token(search.to);
  if (to) parsed.to = to;

  const mailCourse = token(search.mail_course_id);
  if (mailCourse) parsed.mail_course_id = mailCourse;
  const mailSection = token(search.mail_section_id);
  if (mailSection) parsed.mail_section_id = mailSection;
  if (search.mail_resolved === "all") {
    parsed.mail_resolved = "all";
  } else {
    const mailResolved = boolValue(search.mail_resolved);
    if (mailResolved !== undefined) parsed.mail_resolved = mailResolved ? "true" : "false";
  }
  const mailHeld = boolValue(search.mail_held);
  if (mailHeld !== undefined) parsed.mail_held = mailHeld ? "true" : "false";
  const mailReason = token(search.mail_reason_code);
  if (mailReason && (REASON_CODES as readonly string[]).includes(mailReason)) {
    parsed.mail_reason_code = mailReason as ReasonCode;
  }
  const mailCursor = token(search.mail_cursor);
  if (mailCursor) parsed.mail_cursor = mailCursor;

  const qualityCourse = token(search.quality_course_id);
  if (qualityCourse) parsed.quality_course_id = qualityCourse;
  const qualityVersion = token(search.quality_question_version_id);
  if (qualityVersion) parsed.quality_question_version_id = qualityVersion;
  const qualityFrom = token(search.quality_from);
  if (qualityFrom) parsed.quality_from = qualityFrom;
  const qualityTo = token(search.quality_to);
  if (qualityTo) parsed.quality_to = qualityTo;
  return parsed;
}

/** 已解析的邮件筛选。URL 省略 `resolved` 时按默认「未处理」（显式 false）处理。 */
export function readMailFilter(search: ReviewSearch): MailFilter {
  const active = normalizeReviewTab(search.tab) === "mail";
  const rawResolved = active ? search.resolved : search.mail_resolved;
  const resolved =
    rawResolved === "true" ? true : rawResolved === "all" ? null : false;
  const rawHeld = active ? search.held : search.mail_held;
  return {
    courseId: (active ? search.course_id : search.mail_course_id) ?? null,
    sectionId: (active ? search.section_id : search.mail_section_id) ?? null,
    resolved,
    // 默认视图是"待处理"：既不含暂存，也不含已处理。
    held:
      rawHeld === "true" ? true : rawHeld === "false" ? false : resolved === false ? false : null,
    reasonCode: (active ? search.reason_code : search.mail_reason_code) ?? null,
    cursor: (active ? search.cursor : search.mail_cursor) ?? null,
  };
}

/** 已解析的质量筛选。 */
export function readQualityFilter(search: ReviewSearch): QualityFilter {
  const active = normalizeReviewTab(search.tab) === "quality";
  return {
    courseId: (active ? search.course_id : search.quality_course_id) ?? null,
    questionVersionId:
      (active ? search.question_version_id : search.quality_question_version_id) ?? null,
    from: (active ? search.from : search.quality_from) ?? null,
    to: (active ? search.to : search.quality_to) ?? null,
  };
}

function resolvedValue(value: boolean | null): "true" | "false" | "all" {
  return value === null ? "all" : value ? "true" : "false";
}

function heldValue(value: boolean | null): "true" | "false" | undefined {
  return value === null ? undefined : value ? "true" : "false";
}

/**
 * 写回邮件筛选：当前标签用短参数；**另一标签的条件移到 `quality_*` 暂存**，
 * 这样切到质量再切回来时邮件筛选还在。改变筛选必然重置游标。
 */
export function withMailFilter(
  search: ReviewSearch,
  filter: Omit<MailFilter, "cursor">,
): ReviewSearch {
  const quality = readQualityFilter(search);
  return compact({
    tab: "mail",
    origin: search.origin,
    question_id: search.question_id,
    demo: search.demo,
    q: search.q,
    course_id: filter.courseId ?? undefined,
    section_id: filter.sectionId ?? undefined,
    resolved: resolvedValue(filter.resolved),
    held: heldValue(filter.held),
    reason_code: filter.reasonCode ?? undefined,
    quality_course_id: quality.courseId ?? undefined,
    quality_question_version_id: quality.questionVersionId ?? undefined,
    quality_from: quality.from ?? undefined,
    quality_to: quality.to ?? undefined,
  });
}

/** 只更新游标（"加载更多"之后 URL 保留起始位置；追加不改变筛选）。 */
export function withMailCursor(search: ReviewSearch, cursor: string | null): ReviewSearch {
  return compact({
    ...search,
    tab: "mail",
    cursor: cursor ?? undefined,
    mail_cursor: undefined,
  });
}

/** 写回质量筛选：当前标签用短参数；邮件条件移到 `mail_*` 暂存。 */
export function withQualityFilter(
  search: ReviewSearch,
  filter: QualityFilter,
): ReviewSearch {
  const mail = readMailFilter(search);
  return compact({
    tab: "quality",
    origin: search.origin,
    question_id: search.question_id,
    demo: search.demo,
    q: search.q,
    course_id: filter.courseId ?? undefined,
    question_version_id: filter.questionVersionId ?? undefined,
    from: filter.from ?? undefined,
    to: filter.to ?? undefined,
    mail_course_id: mail.courseId ?? undefined,
    mail_section_id: mail.sectionId ?? undefined,
    mail_resolved: resolvedValue(mail.resolved),
    mail_held: heldValue(mail.held),
    mail_reason_code: mail.reasonCode ?? undefined,
    mail_cursor: mail.cursor ?? undefined,
  });
}

/**
 * 切换标签：目标标签恢复自己的条件，当前标签的条件暂存到 `<tab>_*`。
 * 两个标签因此互不干扰，也不会把邮件的「待处理」筛选带进题目质量。
 *
 * `origin=question` 只属于"刚从某道题点进来"的那一次：换过标签就回到**可换题**的视图，
 * 否则老师从题目详情看过一次质量后，再回到「题目质量」会一直是那道题的锁定视图，
 * 看起来像"总览页只能看一道题、而且没点查询就出了数字"。
 */
export function withTab(search: ReviewSearch, tab: ReviewTab): ReviewSearch {
  if (normalizeReviewTab(search.tab) === tab) return search;
  const next =
    tab === "quality"
      ? withQualityFilter(search, readQualityFilter(search))
      : withMailFilter(search, readMailFilter(search));
  // 换过标签就不再"从某道题进来"：锁定视图与"返回这个题"一起退出。
  return next.origin === undefined
    ? next
    : { ...next, origin: undefined, question_id: undefined };
}

/** 去掉值为 `undefined` 的键（路由 search 类型不接受 `undefined` 值之外的占位）。 */
function compact(search: ReviewSearch): ReviewSearch {
  const next: ReviewSearch = {};
  for (const [key, value] of Object.entries(search)) {
    if (value !== undefined) (next as Record<string, unknown>)[key] = value;
  }
  return next;
}
