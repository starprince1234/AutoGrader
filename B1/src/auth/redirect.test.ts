import { describe, expect, it } from "vitest";
import {
  DEFAULT_AUTHENTICATED_PATH,
  currentLocationTarget,
  safeRedirectTarget,
} from "./redirect";

describe("登录回跳目标的校验（开放重定向防护）", () => {
  it("接受站内绝对路径，保留 query 与 hash", () => {
    expect(safeRedirectTarget("/assignments")).toBe("/assignments");
    expect(safeRedirectTarget("/grades?course_id=abc&section_id=def")).toBe(
      "/grades?course_id=abc&section_id=def",
    );
    expect(safeRedirectTarget("/questions/00000000-0000-4000-8000-0000000000a1?version=2")).toBe(
      "/questions/00000000-0000-4000-8000-0000000000a1?version=2",
    );
    expect(safeRedirectTarget("/assignments#top")).toBe("/assignments#top");
    expect(safeRedirectTarget("  /assignments  ")).toBe("/assignments");
  });

  it("拒绝协议相对地址（//host）", () => {
    expect(safeRedirectTarget("//evil.example/path")).toBeNull();
    expect(safeRedirectTarget("//evil.example")).toBeNull();
    expect(safeRedirectTarget("/%2f%2fevil.example/path")).toBeNull();
    expect(safeRedirectTarget("/%2F%2Fevil.example")).toBeNull();
  });

  it("拒绝绝对 URL 与非 http 协议", () => {
    expect(safeRedirectTarget("https://evil.example/path")).toBeNull();
    expect(safeRedirectTarget("http://evil.example")).toBeNull();
    expect(safeRedirectTarget("javascript:alert(1)")).toBeNull();
    expect(safeRedirectTarget("data:text/html,<script>alert(1)</script>")).toBeNull();
    expect(safeRedirectTarget("//user:pass@evil.example")).toBeNull();
  });

  it("拒绝反斜线与编码变体（浏览器会把它们规范化为外站）", () => {
    expect(safeRedirectTarget("/\\evil.example")).toBeNull();
    expect(safeRedirectTarget("\\\\evil.example")).toBeNull();
    expect(safeRedirectTarget("/%5cevil.example")).toBeNull();
    expect(safeRedirectTarget("/%5C%5Cevil.example")).toBeNull();
    expect(safeRedirectTarget("/%255cevil.example")).toBeNull();
    // 二次编码：%255c 展开一层是 %5c，再展开才是反斜线。
    expect(safeRedirectTarget("/%25255cevil.example")).toBeNull();
  });

  it("拒绝空值、根路径、认证页与控制字符", () => {
    for (const value of [undefined, null, 1, {}, "", "   ", "/", "/login", "/register", "/forgot-password", "/login?x=1"]) {
      expect(safeRedirectTarget(value)).toBeNull();
    }
    expect(safeRedirectTarget("/assign\u0000ments")).toBeNull();
    expect(safeRedirectTarget("/assign\u001fments")).toBeNull();
    expect(safeRedirectTarget(`/${"a".repeat(2048)}`)).toBeNull();
  });

  it("非法百分号编码不作为回跳目标", () => {
    expect(safeRedirectTarget("/grades?q=%E0%A4%A")).toBeNull();
  });
});

describe("当前位置转回跳目标", () => {
  it("拼接 pathname、search 与 hash", () => {
    expect(
      currentLocationTarget({
        pathname: "/grades",
        searchStr: "?course_id=abc",
        hash: "top",
      }),
    ).toBe("/grades?course_id=abc#top");
    expect(
      currentLocationTarget({ pathname: "/assignments", searchStr: "", hash: "" }),
    ).toBe("/assignments");
  });

  it("根路径与认证页不作为回跳目标（交给默认落点）", () => {
    expect(currentLocationTarget({ pathname: "/", searchStr: "", hash: "" })).toBeNull();
    expect(currentLocationTarget({ pathname: "/login", searchStr: "", hash: "" })).toBeNull();
    expect(DEFAULT_AUTHENTICATED_PATH).toBe("/assignments");
  });
});
