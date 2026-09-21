import { beforeEach, describe, expect, it } from "vitest";
import {
  clearAllDrafts,
  clearDraft,
  draftKey,
  loadDraft,
  parseDraft,
  saveDraft,
} from "./draft";

const VERSION = "00000000-0000-4000-8000-0000000000c3";
const STUDENT = "00000000-0000-4000-8000-0000000000d4";
const OTHER_STUDENT = "00000000-0000-4000-8000-0000000000e5";

function draft(overrides: Partial<Parameters<typeof saveDraft>[0]> = {}) {
  return {
    ownerId: STUDENT,
    questionVersionId: VERSION,
    language: "python" as const,
    files: [{ path: "solution.py", content: "print(1)" }],
    savedAt: "2026-09-15T08:00:00.000Z",
    ...overrides,
  };
}

describe("本地未提交草稿（RQ-B-1-50 验收 4）", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("保存后可读回，草稿键同时按用户与 question_version_id 隔离", () => {
    saveDraft(draft());

    expect(draftKey(STUDENT, VERSION)).toBe(`b1.draft.v1.${STUDENT}.${VERSION}`);
    const restored = loadDraft(STUDENT, VERSION);
    expect(restored?.files).toEqual([{ path: "solution.py", content: "print(1)" }]);
    expect(loadDraft(STUDENT, "00000000-0000-4000-8000-0000000000ff")).toBeNull();
  });

  it("共用机器：另一个用户读不到前一个人的草稿", () => {
    saveDraft(draft());

    // 同一个 question_version_id、不同的用户 → 必须什么都读不到。
    expect(loadDraft(OTHER_STUDENT, VERSION)).toBeNull();
    // 而且键本身就不同：不是"读出来再过滤"，而是根本不在对方的命名空间里。
    expect(localStorage.getItem(draftKey(OTHER_STUDENT, VERSION))).toBeNull();
    expect(localStorage.getItem(draftKey(STUDENT, VERSION))).not.toBeNull();
  });

  it("归属不符的草稿被丢弃，不做尽力恢复", () => {
    const raw = JSON.stringify({
      ownerId: OTHER_STUDENT,
      questionVersionId: VERSION,
      language: "python",
      files: [{ path: "solution.py", content: "print(1)" }],
      savedAt: "2026-09-15T08:00:00.000Z",
    });
    expect(parseDraft(raw, STUDENT, VERSION)).toBeNull();
  });

  it("清除后不再恢复（提交成功必须清草稿）", () => {
    saveDraft(draft({ language: "bash", files: [{ path: "a.sh", content: "echo 1" }] }));
    clearDraft(STUDENT, VERSION);
    expect(loadDraft(STUDENT, VERSION)).toBeNull();
  });

  it("退出登录清空本浏览器全部草稿（换人使用同一台机器）", () => {
    saveDraft(draft());
    saveDraft(draft({ questionVersionId: "00000000-0000-4000-8000-0000000000aa" }));
    saveDraft(draft({ ownerId: OTHER_STUDENT }));
    // 非草稿键不能被误删
    localStorage.setItem("b1.ui.theme", "dark");

    clearAllDrafts();

    expect(loadDraft(STUDENT, VERSION)).toBeNull();
    expect(loadDraft(STUDENT, "00000000-0000-4000-8000-0000000000aa")).toBeNull();
    expect(loadDraft(OTHER_STUDENT, VERSION)).toBeNull();
    expect(localStorage.getItem("b1.ui.theme")).toBe("dark");
    expect(localStorage.length).toBe(1);
  });

  it("损坏或版本不匹配的草稿被丢弃，不做尽力恢复", () => {
    expect(parseDraft("{not json", STUDENT, VERSION)).toBeNull();
    expect(parseDraft(JSON.stringify({ questionVersionId: "other" }), STUDENT, VERSION)).toBeNull();
    expect(
      parseDraft(
        JSON.stringify({
          ownerId: STUDENT,
          questionVersionId: VERSION,
          language: "python",
          files: "nope",
        }),
        STUDENT,
        VERSION,
      ),
    ).toBeNull();
    expect(
      parseDraft(
        JSON.stringify({
          ownerId: STUDENT,
          questionVersionId: VERSION,
          language: "ruby",
          files: [{ path: "a", content: "b" }],
        }),
        STUDENT,
        VERSION,
      ),
    ).toBeNull();
  });

  it("草稿内容不含令牌或敏感字段（键名层面可核对）", () => {
    saveDraft(draft());
    const raw = localStorage.getItem(draftKey(STUDENT, VERSION)) ?? "";
    expect(raw).not.toMatch(/token|refresh|credential|answer/i);
  });
});
