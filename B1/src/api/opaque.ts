/**
 * 契约缺口适配层（**不是**兼容层）。
 *
 * 背景：`MAIN/contracts/openapi/{b3,b4}.yaml` 中若干端点的 `200` 响应只有
 * `description`、没有 `schema`（见 `docs/collaboration/interfaces.md` "已知契约缺口" 第 3 条）：
 * `GET /courses/{course_id}/sections`、`GET /assignments`、`GET /audit-records`、
 * `GET /modules/{module}/health`。`GET /sections/{section_id}/enrollments` 曾在此列，
 * 已由 CHG-0007 补上 `EnrollmentPublic`，因此改走生成类型，不再经本文件。
 *
 * 因此生成的 TS 类型对这些响应是 `unknown`。B1 的处理原则：
 * - **不发明字段名**：表格列完全来自服务端返回的键。
 * - **只依赖契约定义的标识符**：路径参数名即资源标识（`section_id`、`enrollment_id`、
 *   `course_id`），缺标识符时显式报错，绝不猜测。
 * - 缺口本身记录在 B1 文档中并按变更规程提请 MAIN 判定；本文件在缺口闭合后应删除。
 */

export type OpaqueRow = Record<string, unknown>;

export class ContractGapError extends Error {
  readonly gap: string;

  constructor(message: string, gap: string) {
    super(message);
    this.name = "ContractGapError";
    this.gap = gap;
  }
}

function isRecord(value: unknown): value is OpaqueRow {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * 读取游标分页信封（`common.yaml` 的 `CursorPage` 要求 `items` 必填）。
 * 响应结构与信封都不符时抛错——**不**把畸形响应渲染成"空列表"。
 */
export function readCursorItems(
  payload: unknown,
  context: string,
): { items: OpaqueRow[]; nextCursor: string | null } {
  if (!isRecord(payload)) {
    throw new ContractGapError(
      `${context}: 响应不是对象（契约 ${context} 的 200 无响应 schema）。`,
      "response-schema-missing",
    );
  }
  const items = payload.items;
  if (!Array.isArray(items)) {
    throw new ContractGapError(
      `${context}: 响应缺少 CursorPage 信封要求的 items 数组。`,
      "response-schema-missing",
    );
  }
  const nextCursor =
    typeof payload.next_cursor === "string" ? payload.next_cursor : null;
  return { items: items.filter(isRecord), nextCursor };
}

/**
 * 读取"无 schema 的集合响应"。契约缺口下服务端可能返回裸数组或 `CursorPage` 信封，
 * 两者都接受；其它形状一律抛错（不把畸形响应当空列表）。
 */
export function readCollection(
  payload: unknown,
  context: string,
): { items: OpaqueRow[]; nextCursor: string | null } {
  if (Array.isArray(payload)) {
    return { items: payload.filter(isRecord), nextCursor: null };
  }
  return readCursorItems(payload, context);
}

/**
 * 取契约定义的资源标识符（路径参数名）。
 * 缺失时返回 `null`，由调用方显示"无法安全定位该行"，而不是猜一个键名。
 */
export function readIdentifier(row: OpaqueRow, key: string): string | null {
  const value = row[key];
  return typeof value === "string" && value !== "" ? value : null;
}

/** 动态列：按服务端返回键的首次出现顺序取并集。 */
export function collectColumns(rows: OpaqueRow[], preferredFirst: string[] = []): string[] {
  const seen = new Set<string>();
  const columns: string[] = [];
  for (const key of preferredFirst) {
    if (!seen.has(key)) {
      seen.add(key);
      columns.push(key);
    }
  }
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key);
        columns.push(key);
      }
    }
  }
  return columns;
}
