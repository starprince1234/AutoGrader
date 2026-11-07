import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../styles.css";
import { Root } from "../app/Root";
import { installSessionAuth, restoreSession, signIn } from "../auth/sessionStore";
import {
  DEMO_COURSE,
  DEMO_COURSE_ID,
  DEMO_TEACHER_ID,
  DEMO_VERSION_ID,
  DEMO_VERSION_V2_ID,
} from "../features/review/demoData";

/**
 * 离线样例页（`/app/demo.html`，**只在开发预览时使用**，不进生产构建）。
 *
 * 为什么需要它：复核页的两个标签在真实环境里要有后端、邮件与评测记录才有内容，而
 * "界面长什么样、点下去会怎样"必须在没有这些数据时也能评审。这里只做两件事：
 *
 * 1. 用一个**假会话**（签名不可验、只活在这个页面里）让路由守卫通过——不是登录后门，
 *    生产入口仍然只认 B4 下发的令牌；
 * 2. 在 fetch 层拦下所有 API：登录接口返回假令牌，其它请求一律 501 并打印路径。
 *
 * 第二点是刻意的：示例模式如果偷偷打到真实后端，样例就可能在没人注意时变成"半真半假"。
 * 打印出来的路径同时是"示例模式有没有漏网请求"的检查器。
 */

const DEMO_SCOPE = [{ course_id: DEMO_COURSE_ID, section_id: null }];

function base64url(value: string): string {
  return btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * 假访问令牌：`sessionStore` 只从载荷里读 `sub`/`role`/`scope`，不验签（验签是服务端的事），
 * 因此这里手写一个结构合法的三段式字符串即可。
 */
const DEMO_TOKEN = [
  base64url(JSON.stringify({ alg: "none", typ: "JWT" })),
  base64url(
    JSON.stringify({
      sub: DEMO_TEACHER_ID,
      role: ["teacher"],
      scope: DEMO_SCOPE,
      exp: Math.floor(Date.now() / 1000) + 3600,
    }),
  ),
  "demo",
].join(".");

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function problem(status: number, detail: string): Response {
  return new Response(
    JSON.stringify({
      type: `https://autograder.local/problems/${status}`,
      title: status === 501 ? "Not Implemented" : "Unauthorized",
      status,
      detail,
      instance: "/api/v1",
    }),
    { status, headers: { "content-type": "application/problem+json" } },
  );
}

/**
 * 安装假 API。
 *
 * `empty` 模式用来预览"真实环境但没有任何数据"的样子（页面上的空态与示例入口）：
 * 只对课程目录与隔离列表返回空页，其它请求仍然 501——示例页不替真实接口编数据。
 */
function installDemoApi(empty: boolean): void {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const rawUrl =
      typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(rawUrl, window.location.origin);
    const method = (init?.method ?? "GET").toUpperCase();

    if (method === "POST" && url.pathname === "/api/v1/auth/session") {
      return json({
        access_token: DEMO_TOKEN,
        token_type: "Bearer",
        expires_in: 3600,
        roles: ["teacher"],
        scope: DEMO_SCOPE,
      });
    }
    if (method === "POST" && url.pathname === "/api/v1/auth/session/refresh") {
      return problem(401, "示例页没有刷新凭据。");
    }
    if (empty && method === "GET" && url.pathname === "/api/v1/courses") {
      // 留一门课，空态看起来才像"真实环境但还没有数据"，而不是"课程都读不到"。
      return json({ items: [DEMO_COURSE], next_cursor: null });
    }
    if (empty && method === "GET" && url.pathname === "/api/v1/intake/quarantines") {
      return json({ items: [], next_cursor: null });
    }
    // 示例模式不应发出任何其它请求：响一声比静默返回空数据更容易被发现。
    console.warn(`[demo] 未拦截的请求：${method} ${url.pathname}${url.search}`);
    return problem(501, "示例预览页不连接后端。");
  };
  window.addEventListener("pagehide", () => {
    globalThis.fetch = originalFetch;
  });
}

async function boot(): Promise<void> {
  const params = new URLSearchParams(window.location.search);
  const empty = params.get("empty") === "1";

  installSessionAuth();
  installDemoApi(empty);

  // 先登录再挂载：`SessionProvider` 一渲染就应该看到 teacher 会话，否则会闪一下导览页。
  await signIn("teacher-demo", "demo");
  await restoreSession();

  const container = document.getElementById("root");
  if (!container) throw new Error("示例页缺少 #root 容器");

  const tab = params.get("tab") === "quality" ? "quality" : "mail";
  // 让路由地址与真实页面一致：示例模式下两个标签都在 `/review`，刷新前先看 query 里的 demo。
  const search = new URLSearchParams(empty ? { tab } : { demo: "1", tab });
  // 可选的预设状态：用于截图/评审某一种状态，不需要先手点筛选器。
  // `show=1` 相当于"刚点过查询"：地址栏带着示例版本与查询标记，页面直接给结果。
  if (tab === "quality" && params.get("show") === "1") {
    search.set("course_id", DEMO_COURSE_ID);
    search.set("question_version_id", DEMO_VERSION_ID);
    search.set("q", "1");
  }
  if (tab === "quality" && params.get("version") === "2") {
    search.set("question_version_id", DEMO_VERSION_V2_ID);
  }
  if (tab === "mail") {
    const state = params.get("state");
    if (state === "processed") search.set("resolved", "true");
    else if (state === "held") {
      search.set("resolved", "false");
      search.set("held", "true");
    } else if (state === "all") search.set("resolved", "all");
  }
  window.history.replaceState(null, "", `/app/review?${search.toString()}`);

  createRoot(container).render(
    <StrictMode>
      <Root />
    </StrictMode>,
  );
}

void boot();
