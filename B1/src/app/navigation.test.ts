import { describe, expect, it } from "vitest";
import { isMainNavActive, mainNavItems } from "./navigation";
import type { SessionState } from "../auth/sessionStore";

function session(
  roles: SessionState["roles"],
  rolesKnown = true,
  status: SessionState["status"] = "authenticated",
): SessionState {
  return {
    status,
    username: "u",
    roles,
    rolesKnown,
    scope: [],
    subject: null,
    expiresAt: null,
  };
}

function paths(state: SessionState): string[] {
  return mainNavItems(state).map((item) => item.to);
}

describe("按角色的主导航（体验层，服务端仍判权）", () => {
  it("学生只看到作业与成绩", () => {
    expect(paths(session(["student"]))).toEqual(["/assignments", "/grades"]);
  });

  it("教师/助教看到作业、题库、成绩、课程", () => {
    const expected = ["/assignments", "/questions", "/grades", "/roster"];
    expect(paths(session(["teacher"]))).toEqual(expected);
    expect(paths(session(["assistant"]))).toEqual(expected);
  });

  it("管理员使用教学侧业务入口（用户管理在账户菜单，不在侧栏）", () => {
    expect(paths(session(["admin"]))).toEqual([
      "/assignments",
      "/questions",
      "/grades",
      "/roster",
    ]);
  });

  it("角色未知时 fail-closed：不闪现题库/课程", () => {
    expect(paths(session([], false))).toEqual(["/assignments", "/grades"]);
  });

  it("未认证会话不获得任何教学侧入口", () => {
    expect(paths(session([], false, "anonymous"))).toEqual(["/assignments", "/grades"]);
  });

  it("多重角色取并集：学生 + 助教仍看到完整教学入口", () => {
    expect(paths(session(["student", "assistant"]))).toEqual([
      "/assignments",
      "/questions",
      "/grades",
      "/roster",
    ]);
  });
});

describe("导航激活态", () => {
  it("主入口本身与子页面都算激活", () => {
    expect(isMainNavActive("/assignments", "/assignments")).toBe(true);
    expect(isMainNavActive("/assignments/abc", "/assignments")).toBe(true);
    expect(isMainNavActive("/questions/new", "/questions")).toBe(true);
    expect(isMainNavActive("/questions/abc/edit", "/questions")).toBe(true);
    expect(isMainNavActive("/roster/import", "/roster")).toBe(true);
  });

  it("同前缀的另一个词不会被误判", () => {
    expect(isMainNavActive("/roster-import", "/roster")).toBe(false);
    expect(isMainNavActive("/grades-export", "/grades")).toBe(false);
    expect(isMainNavActive("/questions", "/roster")).toBe(false);
  });
});
