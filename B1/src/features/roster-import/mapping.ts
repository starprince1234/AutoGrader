import type { MessageKey } from "../../i18n";

/**
 * 列映射的纯逻辑（RQ-B-1-10 验收 2：**不自动猜测**）。
 *
 * 设计要点：目标字段集合是 B4 名单导入契约的语义字段；映射草稿初始为**空**，
 * 必须由人工指派。服务端给出的 `proposed_mapping` 只作为参考信息展示，不预填，
 * 因此"自动猜测"在客户端不可能发生。
 */

export const TARGET_FIELDS = [
  "display_name",
  "student_number",
  "course_code",
  "section_code",
  "class_name",
  "email",
] as const;

export type TargetField = (typeof TARGET_FIELDS)[number];

/** 必填目标字段：契约要求的"姓名、学号、课程编号、课序号"。 */
export const REQUIRED_TARGET_FIELDS: readonly TargetField[] = [
  "display_name",
  "student_number",
  "course_code",
  "section_code",
];

export type MappingDraft = Partial<Record<TargetField, string>>;

const FIELD_MESSAGE_KEY: Record<TargetField, MessageKey> = {
  display_name: "import.field.name",
  student_number: "import.field.student_number",
  course_code: "import.field.course_code",
  section_code: "import.field.section_code",
  class_name: "import.field.class_name",
  email: "import.field.email",
};

export function fieldMessageKey(field: TargetField): MessageKey {
  return FIELD_MESSAGE_KEY[field];
}

export type MappingValidation =
  | { ok: true; columns: Record<string, string> }
  | { ok: false; missing: TargetField[]; unknownColumns: string[] };

/**
 * 校验映射草稿：
 * - 必填字段必须都指派；
 * - 指派的列必须出现在服务端检测到的列里（否则是伪造/过期的界面状态）。
 * 不满足时返回**具体的**缺失项，由 UI 明确报错，而不是静默放行。
 */
export function validateMapping(
  draft: MappingDraft,
  detectedColumns: readonly string[],
): MappingValidation {
  const missing = REQUIRED_TARGET_FIELDS.filter((field) => {
    const column = draft[field];
    return column === undefined || column === "";
  });

  const unknownColumns: string[] = [];
  const columns: Record<string, string> = {};
  for (const field of TARGET_FIELDS) {
    const column = draft[field];
    if (column === undefined || column === "") continue;
    if (!detectedColumns.includes(column)) {
      unknownColumns.push(column);
      continue;
    }
    columns[field] = column;
  }

  if (missing.length > 0 || unknownColumns.length > 0) {
    return { ok: false, missing, unknownColumns };
  }
  return { ok: true, columns };
}

/** 一次性赋值是否自相矛盾（同一列被指派给两个目标字段）。 */
export function duplicateAssignments(draft: MappingDraft): string[] {
  const counts = new Map<string, number>();
  for (const field of TARGET_FIELDS) {
    const column = draft[field];
    if (!column) continue;
    counts.set(column, (counts.get(column) ?? 0) + 1);
  }
  return [...counts.entries()].filter(([, count]) => count > 1).map(([column]) => column);
}

/** 是否仍在"必须人工映射"的阶段。 */
export function needsMapping(status: string): boolean {
  return status === "mapping_required";
}

/** 已经可以进入人工确认的阶段。 */
export function isPreviewable(status: string): boolean {
  return status === "parsed" || status === "previewed" || status === "validating";
}

export function isCommitted(status: string): boolean {
  return status === "committed";
}
