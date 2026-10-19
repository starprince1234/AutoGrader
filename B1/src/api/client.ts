import { log, newRequestId } from "../logging/logger";
import { ApiError, isProblemDetails } from "./problem";
import type { ProblemDetails } from "./types";

/**
 * 契约 HTTP 客户端。
 *
 * 关键规则：
 * 1. **路径来自契约原文**（`MAIN/contracts/openapi/*.yaml`），已含 `/api/v1` 前缀。
 *    因此 `VITE_API_BASE_URL` 表示 **API 源**（协议 + 主机），不是路径前缀：
 *    把它当前缀会得到 `/api/api/v1/...`（见 `docs/api/contract.md`）。
 * 2. 每个请求携带 `X-Request-ID`，与后端日志、前端结构化日志同源。
 * 3. 访问令牌只存在内存（ADR-0007 第 7 条：令牌/刷新凭据禁止持久化）。
 * 4. `401` 只重试一次（刷新后重放）；`403` 一律不重试——那是服务端的授权结论。
 */

type QueryValue = string | number | boolean | null | undefined;

/**
 * 查询参数值。数组表示**重复同名参数**（`?tags=a&tags=b`），契约里 `listQuestions` 的
 * `tags`（`style: form, explode: true`）就是这个形状——把它序列化成 `tags=a,b` 会得到一个
 * 服务端读不到的值，而且看起来像"筛选没生效"。
 */
export type QueryParams = Record<string, QueryValue | QueryValue[]>

export type ApiMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export type ApiRequestOptions = {
  method?: ApiMethod;
  query?: QueryParams;
  /** JSON 请求体（与 `formData` 互斥）。 */
  body?: unknown;
  /** multipart 请求体（名单上传）。 */
  formData?: FormData;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** 复用调用方的 request_id，使前端日志与本次 HTTP 请求可关联。 */
  requestId?: string;
  /** 幂等键（名单确认等写操作）。 */
  idempotencyKey?: string;
  accept?: string;
};

type TokenProvider = () => string | null;
type RefreshHandler = () => Promise<string | null>;

let tokenProvider: TokenProvider = () => null;
let refreshHandler: RefreshHandler | null = null;

export function configureApiAuth(options: {
  getAccessToken?: TokenProvider;
  refreshAccessToken?: RefreshHandler | null;
}): void {
  if (options.getAccessToken) tokenProvider = options.getAccessToken;
  if (options.refreshAccessToken !== undefined) {
    refreshHandler = options.refreshAccessToken;
  }
}

/** API 源。生产构建必须显式配置；空值只用于同源测试。 */
export function apiBaseUrl(): string {
  const raw = (import.meta.env?.VITE_API_BASE_URL ?? "").trim();
  if (raw === "" || raw === "/") {
    if (import.meta.env?.PROD) {
      throw new Error("VITE_API_BASE_URL is required for production builds");
    }
    return "";
  }
  return raw.replace(/\/+$/, "");
}

export function buildQueryString(query?: QueryParams): string {
  if (!query) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item === undefined || item === null || item === "") continue;
        search.append(key, String(item));
      }
      continue;
    }
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs === "" ? "" : `?${qs}`;
}

/** 契约路径 → 实际请求 URL。契约路径原样保留（不做前缀拼接）。 */
export function apiUrl(path: string, query?: QueryParams): string {
  return `${apiBaseUrl()}${path}${buildQueryString(query)}`;
}

function isAuthPath(path: string): boolean {
  return path.startsWith("/api/v1/auth/");
}

async function parseProblem(response: Response): Promise<ProblemDetails | null> {
  try {
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("json")) return null;
    const payload: unknown = await response.json();
    return isProblemDetails(payload) ? payload : null;
  } catch {
    return null;
  }
}

function buildHeaders(
  options: ApiRequestOptions,
  requestId: string,
  hasJsonBody: boolean,
): Headers {
  const headers = new Headers(options.headers ?? {});
  headers.set("X-Request-ID", requestId);
  headers.set("accept", options.accept ?? "application/json");
  if (hasJsonBody && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  if (options.idempotencyKey) {
    headers.set("Idempotency-Key", options.idempotencyKey);
  }
  const token = tokenProvider();
  if (token) headers.set("authorization", `Bearer ${token}`);
  return headers;
}

async function send(
  path: string,
  options: ApiRequestOptions,
  requestId: string,
  allowRefreshRetry: boolean,
): Promise<Response> {
  const url = apiUrl(path, options.query);
  const hasJsonBody = options.body !== undefined && options.formData === undefined;
  const init: RequestInit = {
    method: options.method ?? "GET",
    headers: buildHeaders(options, requestId, hasJsonBody),
    signal: options.signal,
    // The refresh cookie belongs to the API subdomain while the SPA is on its sibling
    // subdomain. They are same-site but cross-origin, so same-origin would omit the cookie.
    credentials: "include",
  };
  if (options.formData) init.body = options.formData;
  else if (hasJsonBody) init.body = JSON.stringify(options.body);

  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    // 网络层失败（断网/被中断）。它不是 ProblemDetails：不臆造，原样上报。
    log({
      event: "api_error",
      level: "warn",
      request_id: requestId,
      path,
      failure: "network",
      message: error instanceof Error ? error.message : "unknown network failure",
    });
    throw error;
  }

  if (response.status === 401 && allowRefreshRetry && !isAuthPath(path) && refreshHandler) {
    const refreshed = await refreshHandler();
    if (refreshed) {
      return send(path, options, requestId, false);
    }
  }

  if (!response.ok) {
    const problem = await parseProblem(response);
    log({
      event: "api_error",
      level: response.status >= 500 ? "error" : "warn",
      request_id: requestId,
      path,
      status: response.status,
      problem_type: problem?.type ?? null,
      problem_title: problem?.title ?? null,
    });
    throw new ApiError({ status: response.status, problem, requestId, url });
  }

  return response;
}

/** 原始响应（需要读响应头时使用，例如导出下载的文件名与 Content-Type）。 */
export async function apiRequestRaw(
  path: string,
  options: ApiRequestOptions = {},
): Promise<{ response: Response; requestId: string }> {
  const requestId = options.requestId ?? newRequestId();
  const response = await send(path, options, requestId, true);
  return { response, requestId };
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { response } = await apiRequestRaw(path, options);
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("json")) {
    return (await response.json()) as T;
  }
  return (await response.text()) as unknown as T;
}

/** 生成幂等键：同一份草稿重复提交必须复用同一个键（RQ-B-1-50 验收 1）。 */
export function newIdempotencyKey(): string {
  return newRequestId();
}
