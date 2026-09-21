import { useEffect, useState } from "react";
import { apiBaseUrl, apiRequestRaw } from "../../api/client";

/**
 * 题干图形的受控读取（RQ-B-1-40 验收 2）。
 *
 * 为什么不能直接写 `<img src="/api/v1/questions/{qid}/assets/{aid}">`：该端点需要
 * `Authorization: Bearer …`，而浏览器不会为 `<img>` 附加请求头（B1 的访问令牌只在内存里，
 * 从不写 cookie / localStorage）。所以这里用带令牌的 `fetch` 取回字节，再造一个**由本模块
 * 自己创建**的 `blob:` URL 交给 `<img>`。
 *
 * 这一点让白名单不必放开 `blob:`：`urlPolicy` 仍然拒绝题干文本里出现的任何 `blob:` 地址，
 * 能渲染的 blob URL 只可能来自本函数。把 blob 加进白名单会等于允许题干引用任意同源对象
 * URL——那是把"受控资源"变成"任意资源"。
 */

const ASSET_PATH = /^\/api\/v1\/questions\/([0-9a-fA-F-]{36})\/assets\/([0-9a-fA-F-]{36})$/;

export type AssetLocation = { questionId: string; assetId: string };

/** 从题干里的图片地址解析出受控资产坐标；不是受控资产地址时返回 null。 */
export function parseAssetUrl(src: string): AssetLocation | null {
  const value = src.trim();
  if (value === "") return null;

  const isAbsolute = /^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith("//");
  let path = value;

  if (isAbsolute) {
    // 绝对地址必须先证明"同源"，否则下面那次带令牌的 fetch 会把 Bearer 令牌发给另一个源。
    // `apiBaseUrl()` 为空表示同源部署（Vite dev 与 MAIN host 都是这种情况），此时基线就是
    // 当前页面的 origin；早期版本在基址为空时跳过了这项检查，等于放过了跨源地址。
    const base = apiBaseUrl();
    const expectedOrigin = base === "" ? window.location.origin : new URL(base).origin;
    let url: URL;
    try {
      url = new URL(value, window.location.origin);
    } catch {
      return null;
    }
    if (url.origin !== expectedOrigin) return null;
    if (base !== "") {
      const prefix = new URL(base).pathname.replace(/\/+$/, "");
      if (prefix !== "" && !url.pathname.startsWith(`${prefix}/`)) return null;
    }
    path = url.pathname;
  }

  const match = ASSET_PATH.exec(path);
  if (!match) return null;
  return { questionId: match[1], assetId: match[2] };
}

export type AssetState =
  | { status: "not-asset" }
  | { status: "loading" }
  | { status: "ready"; url: string }
  | { status: "forbidden" }
  | { status: "unavailable" };

/** 取回受控资产并返回一个由本模块创建的 object URL（卸载时撤销）。 */
export function useQuestionAsset(src: string): AssetState {
  const [state, setState] = useState<AssetState>({ status: "not-asset" });

  useEffect(() => {
    const location = parseAssetUrl(src);
    if (location === null) {
      setState({ status: "not-asset" });
      return;
    }

    let revoked = false;
    let objectUrl: string | null = null;
    setState({ status: "loading" });

    void apiRequestRaw(`/api/v1/questions/${location.questionId}/assets/${location.assetId}`)
      .then(async ({ response }) => {
        if (revoked) return;
        const blob = await response.blob();
        if (revoked) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ status: "ready", url: objectUrl });
      })
      .catch((error: unknown) => {
        if (revoked) return;
        const status =
          typeof error === "object" && error !== null && "status" in error
            ? Number((error as { status: unknown }).status)
            : 0;
        setState({ status: status === 403 || status === 401 ? "forbidden" : "unavailable" });
      });

    return () => {
      revoked = true;
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [src]);

  return state;
}
