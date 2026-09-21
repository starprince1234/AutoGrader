/**
 * 受控 URL 白名单（RQ-B-1-40 验收 2/3）。
 *
 * 题干里的链接与图片是**不可信输入**。规则：
 * - 明确拒绝可执行/内联协议（`javascript:`、`data:`、`vbscript:`、`file:`、`blob:`）；
 * - 只放行同源且落在 API 前缀下的地址（`/api/...`），这是契约中唯一的受控资源通道；
 * - 外部主机的地址一律拒绝（前端不代理、不加载第三方内容）。
 *
 * 该判定只决定"能不能渲染"，它不是数据权限：资源是否可读由提供方（B3）判定。
 */

export type UrlDecision =
  | { allowed: true; url: string }
  | { allowed: false; reason: "scheme" | "external" | "relative" | "empty" };

const FORBIDDEN_SCHEME = /^(javascript|data|vbscript|file|blob|filesystem):/i;
const CONTROLLED_PREFIX = "/api/";

function normalizePrefix(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/+$/, "");
}

export function decideUrl(raw: string, apiBaseUrl: string): UrlDecision {
  const value = raw.trim();
  if (value === "") return { allowed: false, reason: "empty" };
  if (FORBIDDEN_SCHEME.test(value.replace(/\s+/g, ""))) {
    return { allowed: false, reason: "scheme" };
  }

  // 协议相对地址（`//host/path`）等同外部主机。
  if (value.startsWith("//")) return { allowed: false, reason: "external" };

  const base = normalizePrefix(apiBaseUrl);

  if (value.startsWith("/")) {
    return value.startsWith(CONTROLLED_PREFIX)
      ? { allowed: true, url: `${base}${value}` }
      : { allowed: false, reason: "relative" };
  }

  if (/^[a-z][a-z0-9+.-]*:/i.test(value)) {
    if (base === "") return { allowed: false, reason: "external" };
    try {
      const absolute = new URL(value);
      const baseUrl = new URL(base);
      if (absolute.origin !== baseUrl.origin) return { allowed: false, reason: "external" };
      if (!absolute.pathname.startsWith(CONTROLLED_PREFIX)) {
        return { allowed: false, reason: "relative" };
      }
      return { allowed: true, url: absolute.toString() };
    } catch {
      return { allowed: false, reason: "external" };
    }
  }

  // 纯相对路径（`img/a.png`）无法确认落在受控通道内。
  return { allowed: false, reason: "relative" };
}
