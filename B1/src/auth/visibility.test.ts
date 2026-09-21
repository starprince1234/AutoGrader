import { describe, expect, it } from "vitest";
import {
  canAuthorQuestions,
  canExportGrades,
  canImportRoster,
  canManageUsers,
  canSubmitAnswer,
  canViewCohortGrades,
  canViewRoster,
  canViewTeacherEvidence,
} from "./visibility";
import type { SessionState } from "./sessionStore";

function session(roles: SessionState["roles"], rolesKnown = true): SessionState {
  return {
    status: "authenticated",
    username: "u",
    roles,
    rolesKnown,
    scope: [],
    subject: null,
    expiresAt: null,
  };
}

describe("角色可见性（RQ-B-1-20）", () => {
  it("学生看不到名单/导入/导出/用户管理入口", () => {
    const student = session(["student"]);
    expect(canViewRoster(student)).toBe(false);
    expect(canImportRoster(student)).toBe(false);
    expect(canExportGrades(student)).toBe(false);
    expect(canViewCohortGrades(student)).toBe(false);
    expect(canManageUsers(student)).toBe(false);
    // 出题入口与契约"Teacher/assistant only, inside the authoring flow"一致。
    expect(canAuthorQuestions(student)).toBe(false);
    // 学生可以作答
    expect(canSubmitAnswer(student)).toBe(true);
  });

  it("教师与助教可看授权作用域内的名单，但只有教师可导入", () => {
    const teacher = session(["teacher"]);
    const assistant = session(["assistant"]);
    expect(canViewRoster(teacher)).toBe(true);
    expect(canImportRoster(teacher)).toBe(true);
    expect(canViewRoster(assistant)).toBe(true);
    expect(canImportRoster(assistant)).toBe(false);
    expect(canExportGrades(assistant)).toBe(true);
    expect(canViewTeacherEvidence(assistant)).toBe(true);
    expect(canViewCohortGrades(assistant)).toBe(true);
    // 教师/助教不是作答角色
    expect(canSubmitAnswer(teacher)).toBe(false);
  });

  it("管理员可管理用户与角色", () => {
    const admin = session(["admin"]);
    expect(canManageUsers(admin)).toBe(true);
    expect(canViewRoster(admin)).toBe(true);
    expect(canExportGrades(admin)).toBe(true);
  });

  it("角色未知时一律 fail-closed（fail-closed 优先于任何便利）", () => {
    const unknown = session([], false);
    for (const check of [
      canViewRoster,
      canImportRoster,
      canExportGrades,
      canViewCohortGrades,
      canManageUsers,
      canSubmitAnswer,
      canViewTeacherEvidence,
    ]) {
      expect(check(unknown)).toBe(false);
    }
  });

  it("未认证（roles 为空）不获得任何权限", () => {
    const anonymous = session([]);
    expect(canViewRoster(anonymous)).toBe(false);
    expect(canSubmitAnswer(anonymous)).toBe(false);
    expect(canExportGrades(anonymous)).toBe(false);
  });
});
