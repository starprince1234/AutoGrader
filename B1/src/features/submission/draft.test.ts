import { beforeEach, describe, expect, it } from "vitest";
import {
  clearAllDrafts,
  clearDraft,
  draftKey,
  loadDraft,
  parseDraft,
  saveDraft,
  type Draft,
} from "./draft";

const ASSIGNMENT = "00000000-0000-4000-8000-0000000000a1";
const OTHER_ASSIGNMENT = "00000000-0000-4000-8000-0000000000a2";
const VERSION = "00000000-0000-4000-8000-0000000000c3";
const STUDENT = "00000000-0000-4000-8000-0000000000d4";
const OTHER_STUDENT = "00000000-0000-4000-8000-0000000000e5";

function draft(overrides: Partial<Draft> = {}): Draft {
  return {
    ownerId: STUDENT,
    assignmentId: ASSIGNMENT,
    questionVersionId: VERSION,
    content: "第一行\n第二行\n",
    updatedAt: "2026-09-15T08:00:00.000Z",
    ...overrides,
  };
}

describe("本地未提交草稿 v2（CHG-0032）", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("保存后可读回，键同时按用户、作业与题目版本隔离", () => {
    saveDraft(draft());

    expect(draftKey(STUDENT, ASSIGNMENT, VERSION)).toBe(
      `b1.draft.v2.${STUDENT}.${ASSIGNMENT}.${VERSION}`,
    );
    expect(loadDraft(STUDENT, ASSIGNMENT, VERSION)?.content).toBe("第一行\n第二行\n");
    // 同一道题在另一个作业里不是同一份草稿（题号/答案文件名按作业确定）。
    expect(loadDraft(STUDENT, OTHER_ASSIGNMENT, VERSION)).toBeNull();
  });

  it("共用机器：另一个用户读不到前一个人的草稿", () => {
    saveDraft(draft());

    expect(loadDraft(OTHER_STUDENT, ASSIGNMENT, VERSION)).toBeNull();
    // 键本身不同：不是"读出来再过滤"，而是根本不在对方的命名空间里。
    expect(
      localStorage.getItem(draftKey(OTHER_STUDENT, ASSIGNMENT, VERSION)),
    ).toBeNull();
    expect(localStorage.getItem(draftKey(STUDENT, ASSIGNMENT, VERSION))).not.toBeNull();
  });

  it("归属/作业/版本不符或内容不是字符串的草稿被丢弃，不做尽力恢复", () => {
    expect(
      parseDraft(
        JSON.stringify({
          ownerId: OTHER_STUDENT,
          assignmentId: ASSIGNMENT,
          questionVersionId: VERSION,
          content: "x",
        }),
        STUDENT,
        ASSIGNMENT,
        VERSION,
      ),
    ).toBeNull();
    expect(
      parseDraft(
        JSON.stringify({
          ownerId: STUDENT,
          assignmentId: OTHER_ASSIGNMENT,
          questionVersionId: VERSION,
          content: "x",
        }),
        STUDENT,
        ASSIGNMENT,
        VERSION,
      ),
    ).toBeNull();
    expect(
      parseDraft(
        JSON.stringify({
          ownerId: STUDENT,
          assignmentId: ASSIGNMENT,
          questionVersionId: VERSION,
          content: 42,
        }),
        STUDENT,
        ASSIGNMENT,
        VERSION,
      ),
    ).toBeNull();
    expect(parseDraft("{not json", STUDENT, ASSIGNMENT, VERSION)).toBeNull();
  });

  it("清除后不再恢复（提交成功必须清草稿）", () => {
    saveDraft(draft());
    clearDraft(STUDENT, ASSIGNMENT, VERSION);
    expect(loadDraft(STUDENT, ASSIGNMENT, VERSION)).toBeNull();
  });

  it("退出登录清空全部 v2 草稿，并清理被取代的 v1 键（不迁移内容）", () => {
    saveDraft(draft());
    saveDraft(draft({ questionVersionId: "00000000-0000-4000-8000-0000000000aa" }));
    saveDraft(draft({ ownerId: OTHER_STUDENT }));
    // 被删除的单题作答页留下的旧键：登出时一并清掉，但不会读进新草稿。
    localStorage.setItem(`b1.draft.v1.${STUDENT}.${VERSION}`, "legacy");
    // 非草稿键不能被误删。
    localStorage.setItem("b1.ui.theme", "dark");

    clearAllDrafts();

    expect(loadDraft(STUDENT, ASSIGNMENT, VERSION)).toBeNull();
    expect(loadDraft(STUDENT, ASSIGNMENT, "00000000-0000-4000-8000-0000000000aa")).toBeNull();
    expect(loadDraft(OTHER_STUDENT, ASSIGNMENT, VERSION)).toBeNull();
    expect(localStorage.getItem(`b1.draft.v1.${STUDENT}.${VERSION}`)).toBeNull();
    expect(localStorage.getItem("b1.ui.theme")).toBe("dark");
    expect(localStorage.length).toBe(1);
  });

  it("草稿内容不含令牌或敏感字段（键名与值层面的可核对项）", () => {
    saveDraft(draft());
    const raw = localStorage.getItem(draftKey(STUDENT, ASSIGNMENT, VERSION)) ?? "";
    expect(raw).not.toMatch(/token|refresh|credential/i);
    // 值与读取回来的一致（答案文本不被二次编码）
    expect((JSON.parse(raw) as Draft).content).toBe("第一行\n第二行\n");
  });
});
