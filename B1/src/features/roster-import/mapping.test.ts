import { describe, expect, it } from "vitest";
import {
  REQUIRED_TARGET_FIELDS,
  duplicateAssignments,
  isCommitted,
  isPreviewable,
  needsMapping,
  validateMapping,
} from "./mapping";

const DETECTED = ["姓名", "学号", "课程编号", "课序号", "邮箱"];

const COMPLETE_DRAFT = {
  display_name: "姓名",
  student_number: "学号",
  course_code: "课程编号",
  section_code: "课序号",
};

describe("列映射校验（RQ-B-1-10 验收 2）", () => {
  it("空草稿不会自动猜测：必填字段全部报缺失", () => {
    const result = validateMapping({}, DETECTED);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.missing).toEqual([...REQUIRED_TARGET_FIELDS]);
  });

  it("必填字段齐全时产出服务端列名到目标字段的映射", () => {
    const result = validateMapping(COMPLETE_DRAFT, DETECTED);
    expect(result).toEqual({ ok: true, columns: COMPLETE_DRAFT });
  });

  it("指派服务端未检测到的列会被拒绝（防止伪造/过期界面状态）", () => {
    const result = validateMapping(
      { ...COMPLETE_DRAFT, section_code: "不存在的列" },
      DETECTED,
    );
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.unknownColumns).toEqual(["不存在的列"]);
  });

  it("同一列指派给两个目标字段时可被识别", () => {
    const duplicates = duplicateAssignments({
      ...COMPLETE_DRAFT,
      class_name: "姓名",
    });
    expect(duplicates).toEqual(["姓名"]);
  });

  it("阶段判定与契约的 RosterImportPreview.status 枚举一致", () => {
    expect(needsMapping("mapping_required")).toBe(true);
    expect(needsMapping("previewed")).toBe(false);
    expect(isPreviewable("parsed")).toBe(true);
    expect(isPreviewable("previewed")).toBe(true);
    expect(isPreviewable("mapping_required")).toBe(false);
    expect(isCommitted("committed")).toBe(true);
    expect(isCommitted("previewed")).toBe(false);
  });
});
