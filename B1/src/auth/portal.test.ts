import { afterEach, describe, expect, it } from "vitest";
import type { SessionState } from "./sessionStore";
import {
  clearPortalIntent,
  portalAllows,
  readPortalIntent,
  rememberPortalIntent,
} from "./portal";

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

afterEach(() => {
  sessionStorage.clear();
});

describe("导览页入口端与角色", () => {
  it("教师端只对教师/助教/管理员开放", () => {
    expect(portalAllows(session(["teacher"]), "teacher")).toBe(true);
    expect(portalAllows(session(["assistant"]), "teacher")).toBe(true);
    expect(portalAllows(session(["admin"]), "teacher")).toBe(true);
    expect(portalAllows(session(["student"]), "teacher")).toBe(false);
    expect(portalAllows(session([], false), "teacher")).toBe(false);
  });

  it("学生端只对纯学生开放：同时持有教师侧角色时不能从学生端进入", () => {
    expect(portalAllows(session(["student"]), "student")).toBe(true);
    expect(portalAllows(session(["student", "teacher"]), "student")).toBe(false);
    expect(portalAllows(session(["teacher"]), "student")).toBe(false);
    expect(portalAllows(session([], false), "student")).toBe(false);
  });

  it("入口意图只保存端与目标路径，可读取也可清除", () => {
    rememberPortalIntent("teacher", "/assignments");
    expect(readPortalIntent()).toEqual({ portal: "teacher", path: "/assignments" });
    clearPortalIntent();
    expect(readPortalIntent()).toBeNull();
  });

  it("损坏的存储不会让页面崩溃或误判", () => {
    sessionStorage.setItem("autograder.portal-intent", "{not-json");
    expect(readPortalIntent()).toBeNull();
    sessionStorage.setItem(
      "autograder.portal-intent",
      JSON.stringify({ portal: "admin", path: "/assignments" }),
    );
    expect(readPortalIntent()).toBeNull();
  });
});
