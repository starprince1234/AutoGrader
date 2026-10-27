import { describe, expect, it } from "vitest";
import {
  normalizeReviewTab,
  parseReviewSearch,
  readMailFilter,
  readQualityFilter,
  withMailFilter,
  withQualityFilter,
  withTab,
} from "./search";

/**
 * P13 的 URL 状态（纯函数）。
 *
 * 这些断言钉住三件容易写错、且会影响授权观感的事：
 * 1. 未知 tab / 未知原因码 / 非法布尔值**丢弃**，不是原样发给服务端；
 * 2. 切换标签必须移除另一标签的专属参数（否则会把 A 标签的筛选带进 B 标签）；
 * 3. `resolved` 的 URL 三态（false / true / all）与请求语义（显式布尔 / 省略）一一对应。
 */
describe("复核工作区的 URL 状态", () => {
  const UUID_A = "00000000-0000-4000-8000-0000000000a1";
  const UUID_B = "00000000-0000-4000-8000-0000000000b1";

  it("未知 tab 归一到 mail", () => {
    expect(normalizeReviewTab(undefined)).toBe("mail");
    expect(normalizeReviewTab("platform")).toBe("mail");
    expect(normalizeReviewTab("quality")).toBe("quality");
  });

  it("解析只保留契约能表达的取值", () => {
    const parsed = parseReviewSearch({
      tab: "quality",
      question_version_id: UUID_B,
      from: "2026-09-15T00:00:00.000Z",
      course_id: UUID_A,
      resolved: "all",
      reason_code: "not_a_reason",
      cursor: "abc",
    });
    expect(parsed.tab).toBe("quality");
    expect(parsed.question_version_id).toBe(UUID_B);
    expect(parsed.resolved).toBe("all");
    expect(parsed.reason_code).toBeUndefined();
    expect(parsed.cursor).toBe("abc");
    // 契约 enum 内的原因码保留；词表之外的取值被丢弃（不成为筛选，也不成为可执行操作）。
    expect(parseReviewSearch({ reason_code: "archive_bomb" }).reason_code).toBe("archive_bomb");
    expect(parseReviewSearch({ reason_code: "not_a_reason" }).reason_code).toBeUndefined();
    expect(parseReviewSearch({ resolved: "yes" }).resolved).toBeUndefined();
  });

  it("resolved 的 URL 三态映射到请求语义", () => {
    expect(readMailFilter(parseReviewSearch({})).resolved).toBe(false);
    expect(readMailFilter(parseReviewSearch({ resolved: "false" })).resolved).toBe(false);
    expect(readMailFilter(parseReviewSearch({ resolved: "true" })).resolved).toBe(true);
    expect(readMailFilter(parseReviewSearch({ resolved: "all" })).resolved).toBeNull();
  });

  it("改变筛选会丢弃旧游标，并显式写回 resolved", () => {
    const next = withMailFilter(parseReviewSearch({ cursor: "old" }), {
      courseId: UUID_A,
      sectionId: UUID_B,
      resolved: false,
      reasonCode: "ambiguous_match",
    });
    expect(next).toEqual({
      tab: "mail",
      course_id: UUID_A,
      section_id: UUID_B,
      resolved: "false",
      reason_code: "ambiguous_match",
    });
    expect(next.cursor).toBeUndefined();
  });

  it("切换标签移除另一标签的专属参数", () => {
    const mail = parseReviewSearch({
      tab: "mail",
      course_id: UUID_A,
      resolved: "false",
      question_version_id: UUID_B,
    });
    const quality = withTab(mail, "quality");
    expect(quality.tab).toBe("quality");
    expect(quality.course_id).toBeUndefined();
    expect(quality.resolved).toBeUndefined();
    expect(quality.question_version_id).toBe(UUID_B);

    const back = withTab(
      parseReviewSearch({ tab: "quality", question_version_id: UUID_B, course_id: UUID_A }),
      "mail",
    );
    expect(back.tab).toBe("mail");
    expect(back.question_version_id).toBeUndefined();
    expect(back.course_id).toBe(UUID_A);
  });

  it("质量筛选丢弃邮件专属参数并保留时间范围", () => {
    const next = withQualityFilter(
      parseReviewSearch({ tab: "mail", course_id: UUID_A, resolved: "false" }),
      { questionVersionId: UUID_B, from: "2026-09-15T00:00:00.000Z", to: null },
    );
    expect(next).toEqual({
      tab: "quality",
      question_version_id: UUID_B,
      from: "2026-09-15T00:00:00.000Z",
    });
    expect(readQualityFilter(next).to).toBeNull();
  });
});
