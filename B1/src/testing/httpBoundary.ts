/**
 * HTTP 边界夹具（**只在测试中使用**）。
 *
 * 用途边界（`docs/testing/strategy.md`）：夹具只隔离**不可控的外部边界**——网络与
 * 后端服务。它们**不是**业务实现，也不作为"功能已验证"的交付证明；业务逻辑、组件、
 * 校验、去重、草稿等都必须由真实代码执行。
 *
 * 因此本目录在生产构建中不被任何模块导入（lint 的 `no-restricted-imports` 之外，
 * 由 code review 与 `grep` 保证：`src/testing/` 只被 `*.test.ts(x)` 引用）。
 */

export type RecordedRequest = {
  method: string;
  url: URL;
  headers: Headers;
  body: unknown;
  formData: FormData | null;
};

export type StubbedRoute = {
  method?: string;
  /** 与 `URL.pathname` 精确匹配，或正则匹配。 */
  path: string | RegExp;
  respond: (context: {
    url: URL;
    request: RecordedRequest;
  }) => Response | Promise<Response>;
};

export type FetchStub = {
  requests: RecordedRequest[];
  restore: () => void;
  lastRequest: () => RecordedRequest;
};

function matches(route: StubbedRoute, method: string, pathname: string): boolean {
  if (route.method && route.method.toUpperCase() !== method.toUpperCase()) return false;
  if (typeof route.path === "string") return route.path === pathname;
  return route.path.test(pathname);
}

async function readBody(
  body: unknown,
  contentType: string,
): Promise<{ body: unknown; formData: FormData | null }> {
  if (body === undefined || body === null) return { body: null, formData: null };

  // jsdom 与 Node 的 FormData 实现不同，因此按鸭子类型判断，不构造真正的 Request。
  const maybeFormData = body as { append?: unknown; entries?: unknown };
  if (typeof maybeFormData.append === "function" && typeof maybeFormData.entries === "function") {
    return { body: null, formData: body as FormData };
  }

  if (typeof body === "string") {
    if (contentType.includes("application/json")) {
      return { body: body === "" ? null : JSON.parse(body), formData: null };
    }
    return { body, formData: null };
  }

  return { body: null, formData: null };
}

/**
 * 安装 fetch 桩。未匹配任何 route 的请求会**立即失败**——避免测试静默打真实网络，
 * 也避免"没走到断言"的假通过。
 */
export function installFetchStub(routes: StubbedRoute[]): FetchStub {
  const originalFetch = globalThis.fetch;
  const requests: RecordedRequest[] = [];

  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = (init?.method ?? "GET").toUpperCase();
    const headers = new Headers(init?.headers ?? {});
    const url = new URL(rawUrl, "http://localhost");
    const { body, formData } = await readBody(init?.body, headers.get("content-type") ?? "");
    const recorded: RecordedRequest = { method, url, headers, body, formData };
    requests.push(recorded);

    const route = routes.find((candidate) => matches(candidate, method, url.pathname));
    if (!route) {
      throw new Error(
        `测试未声明的 HTTP 请求：${method} ${url.pathname}${url.search}（请在 installFetchStub 中声明该边界）`,
      );
    }
    return route.respond({ url, request: recorded });
  };

  return {
    requests,
    restore: () => {
      globalThis.fetch = originalFetch;
    },
    lastRequest: () => {
      const last = requests.at(-1);
      if (!last) throw new Error("尚未收到任何请求");
      return last;
    },
  };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** RFC 9457 Problem Details 响应（契约 `common.yaml`）。 */
export function problemResponse(args: {
  status: number;
  title: string;
  detail: string;
  type?: string;
  instance?: string;
}): Response {
  return new Response(
    JSON.stringify({
      type: args.type ?? `https://autograder.local/problems/${args.status}`,
      title: args.title,
      status: args.status,
      detail: args.detail,
      instance: args.instance ?? "/api/v1",
    }),
    { status: args.status, headers: { "content-type": "application/problem+json" } },
  );
}

/** 二进制下载响应（成绩导出）。 */
export function binaryResponse(args: {
  body: string;
  contentType: string;
  filename?: string;
}): Response {
  const headers: Record<string, string> = { "content-type": args.contentType };
  if (args.filename) {
    headers["content-disposition"] = `attachment; filename="${encodeURIComponent(args.filename)}"; filename*=UTF-8''${encodeURIComponent(args.filename)}`;
  }
  return new Response(args.body, { status: 200, headers });
}

/**
 * SSE 响应。`frames` 为已序列化的帧文本（含 `id:`/`event:`/`data:` 行）。
 * 用 `ReadableStream` 模拟真实流式响应，使去重/重连逻辑走真实解析路径。
 */
export function sseResponse(frames: string[], options?: { closeAfter?: number }): Response {
  const encoder = new TextEncoder();
  const selected = frames.slice(0, options?.closeAfter ?? frames.length);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of selected) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream", "cache-control": "no-store" },
  });
}

/** 一帧 SSE：`id` 对应事件 id，`data` 是 `EvaluationStreamFrame` 的 JSON。 */
export function sseFrame(id: string | number | null, data: unknown, event?: string): string {
  const lines: string[] = [];
  if (id !== null) lines.push(`id: ${id}`);
  if (event) lines.push(`event: ${event}`);
  lines.push(`data: ${JSON.stringify(data)}`);
  return `${lines.join("\n")}\n\n`;
}
