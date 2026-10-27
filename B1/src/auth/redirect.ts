/**
 * 登录后回跳目标的校验（共享入口：AppShell 与 P02 登录页共用）。
 *
 * `?redirect=` 来自"未登录被拦下"的那次导航，但它是一个**用户可控**的字符串：
 * 浏览器把 `//host`、`/\host`、`https://host` 都当成外站，协议相对地址、反斜线变体与
 * 百分号编码变体都能绕过"以 / 开头"这种单条判断，因此这里只放行**确实落在本站的绝对路径**。
 *
 * 规则（逐条都有测试）：
 * 1. 必须是字符串、非空，且以**恰好一个** `/` 开头；
 * 2. 不含控制字符、反斜线，也不以 `/` 以外的协议形式出现（`javascript:`、`https:` 等）；
 * 3. 对百分号编码最多展开三层后再判一次：`%5c`、`%2f%2f` 这类写法在浏览器里与服务端都可能被规范化；
 * 4. 最后用 `URL` 解析一次并核对 origin —— 字符串检查会漏掉浏览器自己的规范化规则；
 * 5. 回跳到登录页/注册页没有意义（会造成认证循环），根路径 `/` 也不作为目标。
 *
 * 不合法时返回 `null`，调用方使用默认目标 `/assignments`。
 */

const DEFAULT_ORIGIN = "https://autograder.invalid";
const ABSOLUTE_SCHEME = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;
/** 认证页自身不是回跳目标：登录成功后再回到认证表单只会形成循环。 */
const AUTH_PATHS = new Set(["/login", "/register", "/forgot-password"]);
const MAX_LENGTH = 2048;

/** 控制字符会被浏览器/代理规范化，是 URL 走私的常见载体：逐个 charCode 判断（不用正则）。 */
function containsControlCharacter(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

function hasUnsafeForm(value: string): boolean {
  return (
    value.includes("\\") ||
    containsControlCharacter(value) ||
    ABSOLUTE_SCHEME.test(value) ||
    value.startsWith("//")
  );
}

export function safeRedirectTarget(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const value = raw.trim();
  if (value === "" || value.length > MAX_LENGTH) return null;
  if (!value.startsWith("/") || value.startsWith("//")) return null;
  if (hasUnsafeForm(value)) return null;

  // 编码展开：只用于判断，最终仍返回原字符串（避免改写合法查询串）。
  let decoded = value;
  for (let depth = 0; depth < 3; depth += 1) {
    let next: string;
    try {
      next = decodeURIComponent(decoded);
    } catch {
      // 非法的百分号序列不是可回跳的站内路径。
      return null;
    }
    if (next === decoded) break;
    decoded = next;
    if (hasUnsafeForm(decoded)) return null;
  }

  let parsed: URL;
  try {
    parsed = new URL(value, DEFAULT_ORIGIN);
  } catch {
    return null;
  }
  if (parsed.origin !== DEFAULT_ORIGIN) return null;
  // 浏览器可能把未知形式规范化成路径；只接受真正的单斜线绝对路径。
  if (!parsed.pathname.startsWith("/") || parsed.pathname.startsWith("//")) return null;
  const pathname = parsed.pathname.replace(/\/+$/, "") || "/";
  if (pathname === "/" || AUTH_PATHS.has(pathname)) return null;

  return value;
}

/** 登录/恢复会话后的唯一默认落点（P01：不再有独立首页）。 */
export const DEFAULT_AUTHENTICATED_PATH = "/assignments";

/**
 * 把"当前站内位置"整理成可回跳的目标。`pathname + search + hash` 组合在解析失败时
 * 由 `safeRedirectTarget` 兜底成 `null`。
 */
export function currentLocationTarget(location: {
  pathname: string;
  searchStr: string;
  hash: string;
}): string | null {
  const hash = location.hash === "" ? "" : `#${location.hash}`;
  return safeRedirectTarget(`${location.pathname}${location.searchStr}${hash}`);
}
