import { describe, expect, it } from "vitest";
import { parseReviewSearch, readMailFilter, withMailFilter } from "./search";

/**
 * `/review`（邮件记录页）的 URL 状态（纯函数）。
 *
 * 这些断言钉住两件容易写错、且会影响观感与请求的事：
 * 1. URL 只承载 `course_id`/`section_id`/`status`；旧协议参数（`resolved`/`held`/
 *    `reason_code`/`cursor`/`tab`）与非法 `status` 一律**丢弃**，不原样发给服务端；
 * 2. 缺省分类是「待处理」，且写回 URL 时省略它——作业列表带过来的入口链接（只有课程/课序）
 *    与页面默认视图因此是同一个语义。
 */
describe("邮件记录页的 URL 状态", () => {
  const UUID_A = "00000000-0000-4000-8000-0000000000a1";
  const UUID_B = "00000000-0000-4000-8000-0000000000b1";

  it("只解析课程/课序/分类；已删除的参数一律丢弃", () => {
    const parsed = parseReviewSearch({
      course_id: UUID_A,
      section_id: UUID_B,
      status: "other",
      resolved: "all",
      held: "true",
      reason_code: "archive_bomb",
      cursor: "abc",
      tab: "mail",
      demo: "1",
      q: "1",
      question_id: UUID_A,
      question_version_id: UUID_B,
    });
    expect(parsed).toEqual({
      course_id: UUID_A,
      section_id: UUID_B,
      status: "other",
    });
  });

  it("分类只接受契约三态，非法值丢弃", () => {
    expect(parseReviewSearch({ status: "processed" }).status).toBe("processed");
    expect(parseReviewSearch({ status: "pending" }).status).toBe("pending");
    expect(parseReviewSearch({ status: "other" }).status).toBe("other");
    expect(parseReviewSearch({ status: "resolved" }).status).toBeUndefined();
    expect(parseReviewSearch({ status: 1 }).status).toBeUndefined();
    // 空白会被裁掉，裁剪后仍为空则丢弃；超长值不进入 URL 状态。
    expect(parseReviewSearch({ status: "  pending  " }).status).toBe("pending");
    expect(parseReviewSearch({ status: "x".repeat(200) }).status).toBeUndefined();
  });

  it("缺省分类是「待处理」", () => {
    expect(readMailFilter(parseReviewSearch({})).category).toBe("pending");
    expect(readMailFilter(parseReviewSearch({ status: "processed" })).category).toBe("processed");
    expect(readMailFilter(parseReviewSearch({ course_id: UUID_A }))).toEqual({
      category: "pending",
      courseId: UUID_A,
      sectionId: null,
    });
  });

  it("切换分类写回 status；缺省分类不写进 URL", () => {
    expect(
      withMailFilter({
        category: "processed",
        courseId: UUID_A,
        sectionId: UUID_B,
      }),
    ).toEqual({
      course_id: UUID_A,
      section_id: UUID_B,
      status: "processed",
    });
    expect(
      withMailFilter({ category: "pending", courseId: UUID_A, sectionId: null }),
    ).toEqual({ course_id: UUID_A });
    expect(withMailFilter({ category: "pending", courseId: null, sectionId: null })).toEqual({});
  });

  it("切回「待处理」时旧 status 被移除（URL 不残留旧分类）", () => {
    const next = withMailFilter({
      category: "pending",
      courseId: UUID_A,
      sectionId: null,
    });
    expect(next.status).toBeUndefined();
  });
});
