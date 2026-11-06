import { describe, expect, it } from "vitest";
import { isMainNavActive, mainNavIndex, mainNavItems } from "./navigation";
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

  it("教师看到作业、题库、成绩、课程", () => {
    const expected = ["/assignments", "/questions", "/grades", "/roster"];
    expect(paths(session(["teacher"]))).toEqual(expected);
  });

  it("管理员在教学侧入口之后多一个用户管理", () => {
    expect(paths(session(["admin"]))).toEqual([
      "/assignments",
      "/questions",
      "/grades",
      "/roster",
      "/admin/users",
    ]);
  });

  it("角色未知时 fail-closed：不闪现题库/课程", () => {
    expect(paths(session([], false))).toEqual(["/assignments", "/grades"]);
  });

  it("未认证会话不获得任何教学侧入口", () => {
    expect(paths(session([], false, "anonymous"))).toEqual(["/assignments", "/grades"]);
  });

});

describe("导航激活态", () => {
  it("主入口本身与子页面都算激活", () => {
    expect(isMainNavActive("/assignments", "/assignments")).toBe(true);
    expect(isMainNavActive("/assignments/abc", "/assignments")).toBe(true);
    expect(isMainNavActive("/questions/new", "/questions")).toBe(true);
    expect(isMainNavActive("/questions/abc/edit", "/questions")).toBe(true);
    expect(isMainNavActive("/roster/import", "/roster")).toBe(true);
    expect(isMainNavActive("/admin/users", "/admin/users")).toBe(true);
  });

  it("同前缀的另一个词不会被误判", () => {
    expect(isMainNavActive("/roster-import", "/roster")).toBe(false);
    expect(isMainNavActive("/grades-export", "/grades")).toBe(false);
    expect(isMainNavActive("/questions", "/roster")).toBe(false);
    expect(isMainNavActive("/admin", "/admin/users")).toBe(false);
  });
});

describe("换页滚动方向用的序号", () => {
  const teacherItems = mainNavItems(session(["teacher"]));

  it("按侧栏顺序给出序号，子页面跟随所属入口", () => {
    expect(mainNavIndex(teacherItems, "/assignments")).toBe(0);
    expect(mainNavIndex(teacherItems, "/questions/abc/edit")).toBe(1);
    expect(mainNavIndex(teacherItems, "/grades")).toBe(2);
    expect(mainNavIndex(teacherItems, "/roster/import")).toBe(3);
  });

  it("回到更靠上的入口时序号变小（AppShell 据此上滚），反之变大", () => {
    const from = mainNavIndex(teacherItems, "/grades");
    const toEarlier = mainNavIndex(teacherItems, "/questions");
    const toLater = mainNavIndex(teacherItems, "/roster");
    expect(toEarlier).toBeLessThan(from);
    expect(toLater).toBeGreaterThan(from);
  });

  it("不属于任何主入口时返回 -1：调用方按默认方向处理", () => {
    expect(mainNavIndex(teacherItems, "/")).toBe(-1);
    expect(mainNavIndex(teacherItems, "/login")).toBe(-1);
  });
});
