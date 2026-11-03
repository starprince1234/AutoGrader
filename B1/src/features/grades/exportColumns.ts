import type { MessageKey } from "../../i18n";

/**
 * 成绩导出的可选列（`exportGrades.columns`，RQ-B-1-60）。
 *
 * **必需列不在这里**：学号、姓名、课程、课序、题目版本、得分、满分、提交时间、评测 ID 由 B2
 * 始终写入，前端既不能勾选也不能取消。这里只列可选附加列；服务端对未知字段返回 422（不静默忽略），
 * 因此这份清单必须与契约 `exportGrades.columns` 的说明保持一致。
 *
 * 为什么不从 `GradeRow` 的键推导：`GradeRow` 的键包含必需列，而"哪些能取消"是**业务规则**
 * （`RQ-B-1-60`），不是类型的形状。把两者混在一起，某天给 `GradeRow` 加一个内部字段就会自动
 * 变成可导出列。
 */
export const OPTIONAL_EXPORT_FIELDS = [
  "submission_id",
  "student_id",
  "state",
  "is_pass",
  "security_status",
] as const;

export type OptionalExportField = (typeof OPTIONAL_EXPORT_FIELDS)[number];

/** 可选列的界面标签（i18n key；不在组件里硬编码中文）。 */
export function optionalExportFieldLabel(field: OptionalExportField): MessageKey {
  switch (field) {
    case "submission_id":
      return "grades.exportColumn.submissionId";
    case "student_id":
      return "grades.exportColumn.studentId";
    case "state":
      return "grades.exportColumn.state";
    case "is_pass":
      return "grades.exportColumn.isPass";
    case "security_status":
      return "grades.exportColumn.securityStatus";
    default: {
      // 类型穷尽检查：新增可选列而忘了加标签会在这里编译失败。
      const exhaustive: never = field;
      return exhaustive;
    }
  }
}

/** 选中列 → `columns` 查询参数；空选择返回 `undefined`（让服务端用默认列集）。 */
export function exportColumnsValue(
  selected: readonly OptionalExportField[],
): string | undefined {
  return selected.length === 0 ? undefined : selected.join(",");
}
