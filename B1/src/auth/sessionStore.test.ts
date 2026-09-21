import { afterEach, describe, expect, it } from "vitest";
import { installFetchStub, jsonResponse, problemResponse, type FetchStub } from "../testing/httpBoundary";
import { sessionCreated } from "../testing/contractFixtures";
import { loadDraft, saveDraft } from "../features/submission/draft";
import {
  getAccessToken,
  getSessionState,
  installSessionAuth,
  readClaimsFromToken,
  refreshAccessToken,
  resetSessionForTests,
  signIn,
  signOut,
} from "./sessionStore";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  resetSessionForTests();
  localStorage.clear();
});

function tokenWith(payload: Record<string, unknown>): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "EdDSA" })}.${encode(payload)}.signature`;
}

describe("会话状态机", () => {
  it("登录把访问令牌保存在内存，不写入任何持久化存储", async () => {
    installSessionAuth();
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
      },
    ]);

    const state = await signIn("teacher1", "pw");
    expect(state).toMatchObject({ status: "authenticated", rolesKnown: true, roles: ["teacher"] });
    expect(getAccessToken()).toBe("test.token.value");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);

    const request = stub.lastRequest();
    expect(request.body).toEqual({ username: "teacher1", password: "pw" });
  });

  it("登录失败（401）抛出且不建立会话", async () => {
    installSessionAuth();
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => problemResponse({ status: 401, title: "Unauthorized", detail: "bad creds" }),
      },
    ]);

    await expect(signIn("u", "bad")).rejects.toMatchObject({ status: 401 });
    expect(getSessionState().status).toBe("anonymous");
    expect(getAccessToken()).toBeNull();
  });

  it("刷新时缺少角色声明 → rolesKnown=false（fail-closed）", async () => {
    installSessionAuth();
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session/refresh",
        respond: () =>
          jsonResponse({
            access_token: tokenWith({ sub: "00000000-0000-4000-8000-0000000000a1" }),
            token_type: "Bearer",
            expires_in: 900,
          }),
      },
    ]);

    const token = await refreshAccessToken();
    expect(token).not.toBeNull();
    const state = getSessionState();
    expect(state.status).toBe("authenticated");
    expect(state.rolesKnown).toBe(false);
    expect(state.roles).toEqual([]);
  });

  it("刷新响应里带角色声明时被解析出来（只读、不替代服务端授权）", () => {
    const token = tokenWith({
      sub: "00000000-0000-4000-8000-0000000000a1",
      role: ["assistant"],
      scope: [{ course_id: "00000000-0000-4000-8000-0000000000a2" }],
    });
    expect(readClaimsFromToken(token)).toEqual({
      subject: "00000000-0000-4000-8000-0000000000a1",
      roles: ["assistant"],
      scope: [{ course_id: "00000000-0000-4000-8000-0000000000a2" }],
      rolesKnown: true,
    });
  });

  it("主体声明必须是 UUID 才能当身份用（提交信封的 student_id 是 Uuid）", () => {
    expect(readClaimsFromToken(tokenWith({ sub: "not-a-uuid" })).subject).toBeNull();
    expect(readClaimsFromToken(tokenWith({ sub: 42 })).subject).toBeNull();
  });

  it("登录后会话状态里带着令牌的 sub（提交身份的来源，且是响应式的）", async () => {
    installSessionAuth();
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () =>
          jsonResponse(
            sessionCreated({
              access_token: tokenWith({ sub: "00000000-0000-4000-8000-0000000000a1" }),
            }),
          ),
      },
    ]);

    const state = await signIn("student1", "pw");
    expect(state.subject).toBe("00000000-0000-4000-8000-0000000000a1");
    expect(getSessionState().subject).toBe("00000000-0000-4000-8000-0000000000a1");
  });

  it("畸形令牌不会抛异常，按未知角色处理", () => {
    for (const token of ["", "a.b", "not-a-jwt", `${btoa("{}")}.${btoa("[]")}.x`]) {
      const claims = readClaimsFromToken(token);
      expect(claims.rolesKnown).toBe(false);
      expect(claims.roles).toEqual([]);
      expect(claims.subject).toBeNull();
    }
  });

  it("刷新 401 表示匿名状态，不抛错", async () => {
    installSessionAuth();
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session/refresh",
        respond: () => problemResponse({ status: 401, title: "Unauthorized", detail: "no cookie" }),
      },
    ]);

    await expect(refreshAccessToken()).resolves.toBeNull();
    expect(getSessionState().status).toBe("anonymous");
  });

  it("退出即使请求失败也清空内存中的令牌", async () => {
    installSessionAuth();
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated()),
      },
      {
        method: "DELETE",
        path: "/api/v1/auth/session/logout",
        respond: () => problemResponse({ status: 503, title: "Unavailable", detail: "down" }),
      },
    ]);

    await signIn("u", "p");
    expect(getAccessToken()).not.toBeNull();
    await signOut();
    expect(getAccessToken()).toBeNull();
    expect(getSessionState().status).toBe("anonymous");
    expect(getSessionState().subject).toBeNull();
  });

  it("退出登录清空本机未提交草稿（共用机器上不把代码留给下一个人）", async () => {
    installSessionAuth();
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated()),
      },
      {
        method: "DELETE",
        path: "/api/v1/auth/session/logout",
        respond: () => new Response(null, { status: 204 }),
      },
    ]);

    const studentId = "00000000-0000-4000-8000-0000000000d4";
    const versionId = "00000000-0000-4000-8000-0000000000c3";
    saveDraft({
      ownerId: studentId,
      questionVersionId: versionId,
      language: "python",
      files: [{ path: "solution.py", content: "print(1)" }],
      savedAt: "2026-09-15T08:00:00.000Z",
    });

    await signIn("u", "p");
    await signOut();

    expect(loadDraft(studentId, versionId)).toBeNull();
  });
});
