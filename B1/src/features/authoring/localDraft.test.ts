import { afterEach, describe, expect, it } from "vitest";
import { emptyDraftForm } from "./draft";
import {
  aiAuthoringDraftKey,
  authoringDraftKey,
  clearAiAuthoringDraft,
  clearAllAuthoringDrafts,
  clearAuthoringDraft,
  loadAiAuthoringDraft,
  loadAuthoringDraft,
  parseAiAuthoringDraft,
  parseAuthoringDraft,
  saveAiAuthoringDraft,
  saveAuthoringDraft,
} from "./localDraft";

const TEACHER_A = "00000000-0000-4000-8000-0000000000a1";
const TEACHER_B = "00000000-0000-4000-8000-0000000000b2";
const QUESTION_1 = "00000000-0000-4000-8000-0000000000c1";

afterEach(() => {
  localStorage.clear();
});

describe("出题本地草稿持久化", () => {
  it("按教师身份与作用域保存并恢复完整表单与步骤，且不存储 SSH 口令", () => {
    const form = {
      ...emptyDraftForm(),
      title: "计算两数之和",
      stemMarkdown: "读入两个整数并输出和。",
      language: "python" as const,
      referenceAnswer: "a, b = map(int, input().split())\nprint(a + b)",
      sshUsername: "lab",
      sshPassword: "super-secret-password",
    };

    saveAuthoringDraft({
      ownerId: TEACHER_A,
      scope: "new",
      courseId: "course-1",
      step: "reference",
      form,
      updatedAt: "2026-11-24T07:00:00.000Z",
    });

    const raw = localStorage.getItem(authoringDraftKey(TEACHER_A, "new"));
    expect(raw).not.toBeNull();
    expect(raw).not.toContain("super-secret-password");

    const restored = loadAuthoringDraft(TEACHER_A, "new");
    expect(restored).not.toBeNull();
    expect(restored?.courseId).toBe("course-1");
    expect(restored?.step).toBe("reference");
    expect(restored?.form.title).toBe("计算两数之和");
    expect(restored?.form.stemMarkdown).toBe("读入两个整数并输出和。");
    expect(restored?.form.sshUsername).toBe("lab");
    expect(restored?.form.sshPassword).toBe("");

    // 账号隔离 & 作用域隔离
    expect(loadAuthoringDraft(TEACHER_B, "new")).toBeNull();
    expect(loadAuthoringDraft(TEACHER_A, QUESTION_1)).toBeNull();

    clearAuthoringDraft(TEACHER_A, "new");
    expect(loadAuthoringDraft(TEACHER_A, "new")).toBeNull();
  });

  it("保存与恢复 AI 出题输入，并在退出登录时一并清理", () => {
    saveAuthoringDraft({
      ownerId: TEACHER_A,
      scope: QUESTION_1,
      courseId: "course-1",
      step: "stem",
      form: { ...emptyDraftForm(), title: "编辑中" },
      updatedAt: "2026-11-24T07:00:00.000Z",
    });
    saveAiAuthoringDraft({
      ownerId: TEACHER_A,
      objective: "练习字符串解析",
      difficulty: "hard",
      referenceTiersCount: 3,
      constraints: "只使用标准库",
      updatedAt: "2026-11-24T07:01:00.000Z",
    });
    localStorage.setItem("unrelated.preference", "keep-me");

    expect(localStorage.getItem(aiAuthoringDraftKey(TEACHER_A))).not.toBeNull();
    const restoredAi = loadAiAuthoringDraft(TEACHER_A);
    expect(restoredAi?.objective).toBe("练习字符串解析");
    expect(restoredAi?.difficulty).toBe("hard");
    expect(restoredAi?.referenceTiersCount).toBe(3);
    expect(restoredAi?.constraints).toBe("只使用标准库");

    clearAiAuthoringDraft(TEACHER_A);
    expect(loadAiAuthoringDraft(TEACHER_A)).toBeNull();

    clearAllAuthoringDrafts();
    expect(loadAuthoringDraft(TEACHER_A, QUESTION_1)).toBeNull();
    expect(localStorage.getItem("unrelated.preference")).toBe("keep-me");
  });

  it("损坏或归属不符的 JSON 静默返回 null", () => {
    expect(parseAuthoringDraft("{not-json", TEACHER_A, "new")).toBeNull();
    expect(parseAiAuthoringDraft("{not-json", TEACHER_A)).toBeNull();
    expect(
      parseAuthoringDraft(
        JSON.stringify({
          ownerId: TEACHER_B,
          scope: "new",
          courseId: "c1",
          step: "basic",
          form: {},
        }),
        TEACHER_A,
        "new",
      ),
    ).toBeNull();
  });
});
