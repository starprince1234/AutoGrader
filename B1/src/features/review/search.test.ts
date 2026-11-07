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
 * 2. 切换标签时另一标签的条件移到 `<tab>_*` 暂存，切回来仍然在（互不覆盖）；
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
    // 路由把查询串按 JSON 解析：`?resolved=true` 到手是布尔值。丢掉它会让刷新后的筛选
    // 悄悄退回「待处理」，所以两种形态必须等价。
    expect(readMailFilter(parseReviewSearch({ resolved: true })).resolved).toBe(true);
    expect(readMailFilter(parseReviewSearch({ resolved: false })).resolved).toBe(false);
    expect(readMailFilter(parseReviewSearch({ resolved: true, held: true })).held).toBe(true);
    expect(readMailFilter(parseReviewSearch({ held: true })).held).toBe(true);
    expect(readMailFilter(parseReviewSearch({ held: false })).held).toBe(false);
    expect(parseReviewSearch({ resolved: "yes" }).resolved).toBeUndefined();
  });

  it("demo 同时接受字符串与路由解析出的数字（刷新/直链不能丢示例模式）", () => {
    // 路由把查询串按 JSON 解析：`?demo=1` 到手时是数字 1，只比较 "1" 会让示例静默失效。
    expect(parseReviewSearch({ demo: "1" }).demo).toBe("1");
    expect(parseReviewSearch({ demo: 1 }).demo).toBe("1");
    expect(parseReviewSearch({ demo: true }).demo).toBe("1");
    expect(parseReviewSearch({ demo: "0" }).demo).toBeUndefined();
    expect(parseReviewSearch({ demo: 2 }).demo).toBeUndefined();
    // 切换标签、改筛选时示例标记必须继续带着走。
    expect(withTab(parseReviewSearch({ demo: 1 }), "quality").demo).toBe("1");
    expect(
      withQualityFilter(parseReviewSearch({ demo: 1 }), {
        courseId: UUID_A,
        questionVersionId: UUID_B,
        from: null,
        to: null,
      }).demo,
    ).toBe("1");
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
      tab: "mail",
      course_id: UUID_A,
      section_id: UUID_B,
      resolved: "false",
      held: "false",
      reason_code: "ambiguous_match",
    });
    expect(next.cursor).toBeUndefined();
  });

  it("切换标签时各自保留查询条件，互不覆盖", () => {
    const mail = parseReviewSearch({
      tab: "mail",
      course_id: UUID_A,
      resolved: "false",
    });
    const quality = withTab(mail, "quality");
    expect(quality.tab).toBe("quality");
    // 质量标签没有自己的条件：不继承邮件的课程/状态。
    expect(quality.course_id).toBeUndefined();
    expect(quality.resolved).toBeUndefined();
    // 邮件条件被暂存，切回时恢复。
    expect(quality.mail_course_id).toBe(UUID_A);

    const qualityFilled = withQualityFilter(quality, {
      courseId: UUID_B,
      questionVersionId: "00000000-0000-4000-8000-0000000000c1",
      from: null,
      to: null,
    });
    const back = withTab(qualityFilled, "mail");
    expect(back.tab).toBe("mail");
    expect(back.course_id).toBe(UUID_A);
    expect(back.resolved).toBe("false");
    expect(back.question_version_id).toBeUndefined();
    // 质量条件也被暂存，再切回去仍然在。
    expect(back.quality_course_id).toBe(UUID_B);
    expect(back.quality_question_version_id).toBe(
      "00000000-0000-4000-8000-0000000000c1",
    );
  });

  it("质量筛选保留课程并暂存邮件条件", () => {
    const next = withQualityFilter(
      parseReviewSearch({ tab: "mail", course_id: UUID_A, resolved: "false" }),
      { courseId: UUID_A, questionVersionId: UUID_B, from: "2026-09-15T00:00:00.000Z", to: null },
    );
    expect(next).toEqual({
      tab: "quality",
      course_id: UUID_A,
      question_version_id: UUID_B,
      from: "2026-09-15T00:00:00.000Z",
      mail_course_id: UUID_A,
      mail_resolved: "false",
      mail_held: "false",
    });
    expect(readQualityFilter(next).courseId).toBe(UUID_A);
    expect(readQualityFilter(next).to).toBeNull();
  });

  it("从题目详情进入时保留来源并自动读取课程/版本", () => {
    const search = parseReviewSearch({
      tab: "quality",
      origin: "question",
      course_id: UUID_A,
      question_version_id: UUID_B,
    });
    expect(search.origin).toBe("question");
    const filter = readQualityFilter(search);
    expect(filter.courseId).toBe(UUID_A);
    expect(filter.questionVersionId).toBe(UUID_B);
  });

  it("「已经查过」的标记同样接受字符串与数字；换标签后不再算「从某道题进来」", () => {
    // 地址栏能恢复一次查询（q=1），但换过标签就回到可换题的视图，否则总览会一直是
    // 上次那道题的锁定页，看起来像"没点查询就出了数字"。
    expect(parseReviewSearch({ q: "1" }).q).toBe("1");
    expect(parseReviewSearch({ q: 1 }).q).toBe("1");
    expect(parseReviewSearch({ q: "0" }).q).toBeUndefined();

    const fromQuestion = parseReviewSearch({
      tab: "quality",
      origin: "question",
      q: 1,
      question_version_id: UUID_B,
    });
    const mail = withTab(fromQuestion, "mail");
    expect(mail.origin).toBeUndefined();
    expect(mail.q).toBe("1");

    const back = withTab(mail, "quality");
    expect(back.origin).toBeUndefined();
    expect(back.q).toBe("1");
    expect(readQualityFilter(back).questionVersionId).toBe(UUID_B);
  });
});
