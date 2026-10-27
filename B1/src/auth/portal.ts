import type { SessionState } from "./sessionStore";

/**
 * 导览页的「入口意图」。
 *
 * 为什么需要它：用户从教师端/学生端点一个入口时，可能还没登录。登录成功后若只按
 * `redirect` 回到页面，服务端的角色检查只会阻止越权请求，但页面本身会按**当前角色**
 * 渲染（教师账号打开学生入口会看到教师视图）。因此把"用户点的是哪一端"作为一次性的
 * 导航意图保存下来，登录/会话恢复后由 AppShell 校验：
 *
 * - 角色匹配：清除意图，正常进入；
 * - 角色不匹配：保留意图，显示"当前账号不能进入该端"的说明，刷新也不会绕过；
 * - 用户离开该入口：意图不再适用于新路径，自动丢弃。
 *
 * 只保存 "teacher" / "student" 与目标 pathname，不含用户名、角色、令牌或任何凭据；
 * 使用 `sessionStorage` 而不是 localStorage：关闭标签页即失效，符合"最近登录过"的语义。
 */
export type Portal = "teacher" | "student";

export type PortalIntent = {
  portal: Portal;
  /** 目标路径（不含 query/hash）；意图只对这一次目标生效。 */
  path: string;
};

const STORAGE_KEY = "autograder.portal-intent";

function isPortal(value: unknown): value is Portal {
  return value === "teacher" || value === "student";
}

export function rememberPortalIntent(portal: Portal, path: string): void {
  try {
    const intent: PortalIntent = { portal, path };
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(intent));
  } catch {
    // 隐私模式/存储被禁用：意图只是增强，不阻塞正常登录与跳转。
  }
}

export function readPortalIntent(): PortalIntent | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const candidate = parsed as Record<string, unknown>;
    if (!isPortal(candidate.portal) || typeof candidate.path !== "string") return null;
    return { portal: candidate.portal, path: candidate.path };
  } catch {
    return null;
  }
}

export function clearPortalIntent(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // 同 remember：存储不可用时没有需要清理的状态。
  }
}

/** 教师端包含教师/助教/管理员；学生端只认学生。角色未知时 fail-closed。 */
export function portalAllows(session: SessionState, portal: Portal): boolean {
  if (!session.rolesKnown) return false;
  if (portal === "student") {
    // 同时持有教师侧角色的账号一律走教师端：页面按教师角色渲染，
    // 不能再从学生端点进去看到"教师视图"。
    const hasStaffRole = session.roles.some(
      (role) => role === "teacher" || role === "assistant" || role === "admin",
    );
    return session.roles.includes("student") && !hasStaffRole;
  }
  return session.roles.some(
    (role) => role === "teacher" || role === "assistant" || role === "admin",
  );
}
