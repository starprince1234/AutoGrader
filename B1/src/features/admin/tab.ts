/**
 * P14 的标签标识与归一。
 *
 * 独立成模块（而不是和页面组件放在一起）：页面文件只导出组件，热更新与"一个文件一件事"都
 * 因此成立；而 `router.tsx`/`routeComponents.tsx` 需要同一个归一函数。
 */

export type AdminTab = "users" | "allowlist";

/**
 * URL 里的 `tab` 归一：未知值（例如旧链接里的 `tab=platform`）回落 `users`，
 * 不制造第三条未定义页面，也不给旧入口留兼容路由。
 */
export function normalizeAdminTab(value: unknown): AdminTab {
  return value === "allowlist" ? "allowlist" : "users";
}
