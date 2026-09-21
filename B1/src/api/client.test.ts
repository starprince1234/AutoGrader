import { afterEach, describe, expect, it, vi } from "vitest";
import {
  apiUrl,
  buildQueryString,
  configureApiAuth,
  apiRequest,
} from "./client";
import { ApiError, isProblemDetails } from "./problem";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type FetchStub,
} from "../testing/httpBoundary";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  configureApiAuth({ getAccessToken: () => null, refreshAccessToken: null });
});

describe("契约 HTTP 客户端", () => {
  it("契约路径原样使用（已含 /api/v1，不做二次前缀）", () => {
    expect(apiUrl("/api/v1/grades", { course_id: "c1", section_id: undefined })).toBe(
      "/api/v1/grades?course_id=c1",
    );
    expect(buildQueryString({ a: 1, b: false, c: null, d: "" })).toBe("?a=1&b=false");
  });

  it("数组值序列化为重复同名参数（契约的 tags 是 explode 形式，不是逗号拼接）", () => {
    expect(buildQueryString({ tags: ["unix", "scheduling"] })).toBe("?tags=unix&tags=scheduling");
    // 空数组与空串一样表示"不加这个筛选条件"。
    expect(buildQueryString({ tags: [] })).toBe("");
    expect(buildQueryString({ tags: ["unix", ""] })).toBe("?tags=unix");
  });

  it("携带 X-Request-ID 与访问令牌，且不把令牌写入任何持久化存储", async () => {
    const tokenSpy = vi.fn(() => "memory-token");
    configureApiAuth({ getAccessToken: tokenSpy });
    stub = installFetchStub([
      { path: "/api/v1/courses", respond: () => jsonResponse({ items: [] }) },
    ]);

    await apiRequest("/api/v1/courses", { requestId: "req-42" });

    const request = stub.lastRequest();
    expect(request.headers.get("x-request-id")).toBe("req-42");
    expect(request.headers.get("authorization")).toBe("Bearer memory-token");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("ProblemDetails 被解析为 ApiError，403 不重试", async () => {
    let calls = 0;
    stub = installFetchStub([
      {
        path: "/api/v1/grades/export",
        respond: () => {
          calls += 1;
          return problemResponse({ status: 403, title: "Forbidden", detail: "no scope" });
        },
      },
    ]);

    await expect(
      apiRequest("/api/v1/grades/export", { query: { course_id: "c1" } }),
    ).rejects.toMatchObject({ status: 403 });
    expect(calls).toBe(1);

    try {
      await apiRequest("/api/v1/grades/export", { query: { course_id: "c1" } });
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      const apiError = error as ApiError;
      expect(apiError.isForbidden).toBe(true);
      expect(apiError.problem?.detail).toBe("no scope");
      expect(isProblemDetails(apiError.problem)).toBe(true);
    }
  });

  it("401 触发一次刷新并重放请求", async () => {
    const refresh = vi.fn(async () => "new-token");
    configureApiAuth({ getAccessToken: () => "expired", refreshAccessToken: refresh });
    let attempts = 0;
    stub = installFetchStub([
      {
        path: "/api/v1/courses",
        respond: () => {
          attempts += 1;
          if (attempts === 1) {
            return problemResponse({ status: 401, title: "Unauthorized", detail: "expired" });
          }
          return jsonResponse({ items: [] });
        },
      },
    ]);

    await apiRequest("/api/v1/courses");
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(attempts).toBe(2);
  });

  it("刷新失败时不重放，401 原样抛出", async () => {
    const refresh = vi.fn(async () => null);
    configureApiAuth({ getAccessToken: () => "expired", refreshAccessToken: refresh });
    let attempts = 0;
    stub = installFetchStub([
      {
        path: "/api/v1/grades",
        respond: () => {
          attempts += 1;
          return problemResponse({ status: 401, title: "Unauthorized", detail: "expired" });
        },
      },
    ]);

    await expect(apiRequest("/api/v1/grades")).rejects.toMatchObject({ status: 401 });
    expect(attempts).toBe(1);
  });

  it("认证端点自身的 401 不触发刷新（避免递归）", async () => {
    const refresh = vi.fn(async () => "new-token");
    configureApiAuth({ refreshAccessToken: refresh });
    stub = installFetchStub([
      {
        path: "/api/v1/auth/session/refresh",
        respond: () => problemResponse({ status: 401, title: "Unauthorized", detail: "no cookie" }),
      },
    ]);

    await expect(
      apiRequest("/api/v1/auth/session/refresh", { method: "POST" }),
    ).rejects.toMatchObject({ status: 401 });
    expect(refresh).not.toHaveBeenCalled();
  });

  it("非 ProblemDetails 的错误响应不会被臆造成 ProblemDetails", async () => {
    stub = installFetchStub([
      { path: "/api/v1/grades", respond: () => new Response("boom", { status: 500 }) },
    ]);

    try {
      await apiRequest("/api/v1/grades");
      throw new Error("应当抛出");
    } catch (error) {
      expect((error as ApiError).problem).toBeNull();
    }
  });
});
