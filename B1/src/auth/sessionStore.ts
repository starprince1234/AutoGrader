import { apiRequest, configureApiAuth } from "../api/client";
import { isApiError } from "../api/problem";
import type { CourseScope, Role, SessionCreated, SessionRefreshed } from "../api/types";
import { clearAllDrafts } from "../features/submission/draft";
import { log, newRequestId } from "../logging/logger";

/**
 * 会话状态机（框架无关，便于直接单测）。
 *
 * 安全约束（ADR-0007 第 7 条 / AGENTS.md 硬规则 4）：
 * - 访问令牌**只存在内存**，永不写入 localStorage / sessionStorage / cookie（刷新凭据由
 *   B4 以 `HttpOnly` cookie 下发，前端既读不到也不需要读）。
 * - 刷新后拿不到角色声明时**保持"角色未知"**并隐藏特权入口（fail-closed），
 *   而不是假定一个角色。
 * - 前端可见性只是体验；每一次写操作仍由 B4/B2 独立判定（403 必须如实展示）。
 */

export type SessionStatus = "anonymous" | "authenticated";

export type SessionState = {
  status: SessionStatus;
  username: string | null;
  roles: Role[];
  /** 角色声明是否可得。`false` 时所有特权入口按 fail-closed 隐藏。 */
  rolesKnown: boolean;
  scope: CourseScope[];
  /**
   * 访问令牌的 `sub`（平台用户 ID），没有则 `null`。
   * 放进会话状态而不是在渲染期读模块变量：令牌在 `restoreSession()` 完成前是 `null`，
   * 非响应式读取会让"令牌里就有身份"这条路径在真实使用中永远走不到。
   */
  subject: string | null;
  expiresAt: number | null;
};

const ANONYMOUS: SessionState = {
  status: "anonymous",
  username: null,
  roles: [],
  rolesKnown: false,
  scope: [],
  subject: null,
  expiresAt: null,
};

/** 主体声明必须是 UUID：它会被当作提交信封的 `student_id`（契约类型 `Uuid`）。 */
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

let state: SessionState = ANONYMOUS;
let accessToken: string | null = null;
const listeners = new Set<() => void>();

function publish(next: SessionState): void {
  state = next;
  for (const listener of listeners) listener();
}

export function getSessionState(): SessionState {
  return state;
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** 把会话接进 HTTP 客户端（401 时由客户端负责刷新一次后重放）。 */
export function installSessionAuth(): void {
  configureApiAuth({
    getAccessToken: () => accessToken,
    refreshAccessToken: () => refreshAccessToken(),
  });
}

function setAuthenticated(args: {
  token: string;
  username: string | null;
  roles: Role[];
  rolesKnown: boolean;
  scope: CourseScope[];
  subject: string | null;
  expiresIn?: number;
}): SessionState {
  accessToken = args.token;
  const next: SessionState = {
    status: "authenticated",
    username: args.username,
    roles: args.roles,
    rolesKnown: args.rolesKnown,
    scope: args.scope,
    subject: args.subject,
    expiresAt:
      typeof args.expiresIn === "number" ? Date.now() + args.expiresIn * 1000 : null,
  };
  publish(next);
  return next;
}

function clearSession(): void {
  accessToken = null;
  publish(ANONYMOUS);
}

/**
 * 从访问令牌的载荷里读取主体（`sub`）、角色与课程作用域。
 *
 * 依据：`RQ-B-4-50` 验收 1 要求令牌可被其他模块**本地验证** `iss`/`aud`/`role`/课程作用域。
 * 这里只做**只读解析且不信任**：解析失败或声明缺失时返回 `rolesKnown: false`，
 * 由上层按最小权限处理。**绝不**用解析结果替代服务端授权。
 */
export function readClaimsFromToken(token: string): {
  subject: string | null;
  roles: Role[];
  scope: CourseScope[];
  rolesKnown: boolean;
} {
  const empty = {
    subject: null,
    roles: [] as Role[],
    scope: [] as CourseScope[],
    rolesKnown: false,
  };
  const segments = token.split(".");
  if (segments.length < 2) return empty;
  try {
    const payloadSegment = segments[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = payloadSegment.padEnd(
      payloadSegment.length + ((4 - (payloadSegment.length % 4)) % 4),
      "=",
    );
    const json = atob(padded);
    const payload: unknown = JSON.parse(json);
    if (typeof payload !== "object" || payload === null) return empty;
    const record = payload as Record<string, unknown>;

    const rawSubject = record.sub;
    // 主体必须是 UUID：提交信封的 `student_id` 是 Uuid，非 UUID 的 `sub` 不能当身份用。
    const subject =
      typeof rawSubject === "string" && UUID_PATTERN.test(rawSubject) ? rawSubject : null;

    const rawRoles = record.role ?? record.roles;
    const roles = (Array.isArray(rawRoles) ? rawRoles : [rawRoles]).filter(
      (value): value is Role =>
        value === "student" ||
        value === "teacher" ||
        value === "assistant" ||
        value === "admin",
    );

    const rawScope = record.scope ?? record.course_scope;
    const scope: CourseScope[] = Array.isArray(rawScope)
      ? rawScope.filter(
          (value): value is CourseScope =>
            typeof value === "object" &&
            value !== null &&
            typeof (value as Record<string, unknown>).course_id === "string",
        )
      : [];

    return { subject, roles, scope, rolesKnown: roles.length > 0 };
  } catch {
    return empty;
  }
}

export async function signIn(username: string, password: string): Promise<SessionState> {
  const requestId = newRequestId();
  const created = await apiRequest<SessionCreated>("/api/v1/auth/session", {
    method: "POST",
    body: { username, password },
    requestId,
  });
  // 角色/作用域以**响应体**为准（服务端是权威），主体只能从令牌里读（响应体没有它）。
  const claims = readClaimsFromToken(created.access_token);
  const next = setAuthenticated({
    token: created.access_token,
    username,
    roles: created.roles as Role[],
    rolesKnown: true,
    scope: created.scope as CourseScope[],
    subject: claims.subject,
    expiresIn: created.expires_in,
  });
  log({ event: "session_signed_in", request_id: requestId, role_count: next.roles.length });
  return next;
}

/**
 * 刷新访问令牌。401 表示刷新凭据不存在/已失效——这是**正常**的匿名状态，
 * 不作为错误抛出，调用方据此渲染登录页。
 */
export async function refreshAccessToken(): Promise<string | null> {
  const requestId = newRequestId();
  try {
    const refreshed = await apiRequest<SessionRefreshed>("/api/v1/auth/session/refresh", {
      method: "POST",
      requestId,
    });
    const claims = readClaimsFromToken(refreshed.access_token);
    if (!claims.rolesKnown) {
      log({
        event: "role_claims_missing",
        level: "warn",
        request_id: requestId,
        effect: "privileged entries hidden (fail-closed)",
      });
    }
    setAuthenticated({
      token: refreshed.access_token,
      // Prefer the server's answer: after a hard reload `state.username` is null (the
      // access token lives in memory only), and the JWT carries just `sub`, so without
      // this the header shows "-" for the rest of the session.
      username: refreshed.username ?? state.username,
      roles: claims.roles,
      rolesKnown: claims.rolesKnown,
      scope: claims.scope,
      subject: claims.subject,
      expiresIn: refreshed.expires_in,
    });
    return refreshed.access_token;
  } catch (error) {
    if (isApiError(error) && error.isUnauthorized) {
      clearSession();
      return null;
    }
    throw error;
  }
}

/** 应用启动时尝试用 HttpOnly 刷新凭据恢复会话；失败即匿名。 */
export async function restoreSession(): Promise<SessionState> {
  if (accessToken) return state;
  try {
    await refreshAccessToken();
  } catch {
    clearSession();
  }
  return state;
}

export async function signOut(): Promise<void> {
  const requestId = newRequestId();
  try {
    await apiRequest<void>("/api/v1/auth/session/logout", {
      method: "DELETE",
      requestId,
    });
  } catch (error) {
    // 退出失败也必须清空本地令牌：不能因为网络问题保留一个可用凭据。
    log({
      event: "session_logout_failed",
      level: "warn",
      request_id: requestId,
      reason: isApiError(error) ? `http_${error.status}` : "network",
    });
  } finally {
    clearSession();
    // 共用机器上的隐私边界：退出后下一个登录的学生不得读到上一个人的未提交草稿。
    // 无论退出请求成功与否都要清——这是本地状态，与服务端会话是否已失效无关。
    clearAllDrafts();
    log({ event: "session_signed_out", request_id: requestId });
  }
}

export function resetSessionForTests(): void {
  clearSession();
  listeners.clear();
}
