import { describe, expect, it } from "vitest";
import { decideUrl } from "./urlPolicy";

describe("受控 URL 白名单（RQ-B-1-40 验收 2/3）", () => {
  it("拒绝可执行/内联协议（含大小写与空白混淆）", () => {
    for (const raw of [
      "javascript:alert(1)",
      "JavaScript:alert(1)",
      "java\nscript:alert(1)",
      "data:text/html;base64,PHNjcmlwdD4=",
      "vbscript:msgbox(1)",
      "file:///etc/passwd",
      "blob:https://evil.example/abc",
    ]) {
      const decision = decideUrl(raw, "");
      expect(decision.allowed, raw).toBe(false);
      if (!decision.allowed) expect(decision.reason).toBe("scheme");
    }
  });

  it("同源且落在 /api/ 下的地址放行", () => {
    expect(decideUrl("/api/v1/questions/1/stem.png", "")).toEqual({
      allowed: true,
      url: "/api/v1/questions/1/stem.png",
    });
  });

  it("同源但不在受控前缀下的相对地址被拒绝", () => {
    expect(decideUrl("/static/evil.png", "")).toMatchObject({
      allowed: false,
      reason: "relative",
    });
    expect(decideUrl("img/evil.png", "")).toMatchObject({
      allowed: false,
      reason: "relative",
    });
  });

  it("外部主机一律拒绝，协议相对地址也不例外", () => {
    expect(decideUrl("https://evil.example/api/v1/x.png", "")).toMatchObject({
      allowed: false,
      reason: "external",
    });
    expect(decideUrl("//evil.example/api/v1/x.png", "")).toMatchObject({
      allowed: false,
      reason: "external",
    });
  });

  it("配置了独立 API 源时，只有该源的 /api/ 路径被放行", () => {
    const base = "https://api.example";
    expect(decideUrl("https://api.example/api/v1/x.png", base)).toEqual({
      allowed: true,
      url: "https://api.example/api/v1/x.png",
    });
    expect(decideUrl("https://other.example/api/v1/x.png", base)).toMatchObject({
      allowed: false,
      reason: "external",
    });
    expect(decideUrl("/api/v1/x.png", base)).toEqual({
      allowed: true,
      url: "https://api.example/api/v1/x.png",
    });
  });

  it("空地址被拒绝", () => {
    expect(decideUrl("   ", "")).toMatchObject({ allowed: false, reason: "empty" });
  });
});
