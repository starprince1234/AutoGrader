import { describe, expect, it } from "vitest";
import { parseReviewSearch, readMailFilter, withMailCursor, withMailFilter } from "./search";

/**
 * P13 的 URL 状态（纯函数）。老师删掉「题目质量」与「示例预览」后，这里只剩邮件筛选。
 *
 * 这些断言钉住两件容易写错、且会影响授权观感的事：
 * 1. 未知原因码 / 非法布尔值 / 已删除的参数（`tab`、`demo`、`q`、`question_version_id` 等）
 *    一律**丢弃**，不是原样发给服务端，也不会在页面里复活一个视图；
 * 2. `resolved` 的 URL 三态（false / true / all）与请求语义（显式布尔 / 省略）一一对应。
 */
describe("复核工作区的 URL 状态", () => {
  const UUID_A = "00000000-0000-4000-8000-0000000000a1";
  const UUID_B = "00000000-0000-4000-8000-0000000000b1";

  it("只解析邮件筛选；已删除的参数一律丢弃", () => {
    const parsed = parseReviewSearch({
      course_id: UUID_A,
      section_id: UUID_B,
      resolved: "all",
      held: "true",
      reason_code: "not_a_reason",
      cursor: "abc",
      tab: "quality",
      demo: "1",
      q: "1",
      origin: "question",
      question_id: UUID_A,
      question_version_id: UUID_B,
      from: "2026-09-15T00:00:00.000Z",
      to: "2026-09-16T00:00:00.000Z",
      mail_course_id: UUID_A,
      quality_course_id: UUID_A,
    });
    expect(parsed).toEqual({
      course_id: UUID_A,
      section_id: UUID_B,
      resolved: "all",
      held: "true",
      cursor: "abc",
    });
  });

  it("原因码只接受契约 enum（词表之外的取值不成为筛选）", () => {
    expect(parseReviewSearch({ reason_code: "archive_bomb" }).reason_code).toBe("archive_bomb");
    expect(parseReviewSearch({ reason_code: "not_a_reason" }).reason_code).toBeUndefined();
  });

  it("resolved 的 URL 三态映射到请求语义", () => {
    expect(readMailFilter(parseReviewSearch({})).resolved).toBe(false);
    expect(readMailFilter(parseReviewSearch({ resolved: "false" })).resolved).toBe(false);
    expect(readMailFilter(parseReviewSearch({ resolved: "true" })).resolved).toBe(true);
    expect(readMailFilter(parseReviewSearch({ resolved: "all" })).resolved).toBeNull();
    // 路由把查询串按 JSON 解析：`?resolved=true` 到手是布尔值。丢掉它会让刷新后的筛选
    // 悄悄退回「待处理」，所以两种形态必须等价。
    expect(readMailFilter(parseReviewSearch({ resolved: true })).resolved).toBe(true);
    expect(readMailFilter(parseReviewSearch({ resolved: false })).resolved).toBe(false);
    expect(readMailFilter(parseReviewSearch({ resolved: true, held: true })).held).toBe(true);
    expect(readMailFilter(parseReviewSearch({ held: true })).held).toBe(true);
    expect(readMailFilter(parseReviewSearch({ held: false })).held).toBe(false);
    expect(parseReviewSearch({ resolved: "yes" }).resolved).toBeUndefined();
  });

  it("改变筛选会丢弃旧游标，并显式写回 resolved", () => {
    const next = withMailFilter(parseReviewSearch({ cursor: "old" }), {
      courseId: UUID_A,
      sectionId: UUID_B,
      resolved: false,
      held: false,
      reasonCode: "ambiguous_match",
    });
    expect(next).toEqual({
      course_id: UUID_A,
      section_id: UUID_B,
      resolved: "false",
      held: "false",
      reason_code: "ambiguous_match",
    });
    expect(next.cursor).toBeUndefined();
  });

  it("「全部状态」写回 all，暂存筛选清空时不写 held", () => {
    const next = withMailFilter(parseReviewSearch({}), {
      courseId: null,
      sectionId: null,
      resolved: null,
      held: null,
      reasonCode: null,
    });
    expect(next).toEqual({ resolved: "all" });
  });

  it("加载更多只改游标，不动筛选", () => {
    const base = parseReviewSearch({ course_id: UUID_A, resolved: "false" });
    expect(withMailCursor(base, "next-page")).toEqual({
      course_id: UUID_A,
      resolved: "false",
      cursor: "next-page",
    });
    expect(withMailCursor(base, null)).toEqual({ course_id: UUID_A, resolved: "false" });
  });
});
