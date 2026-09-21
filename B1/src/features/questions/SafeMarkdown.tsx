import { useState } from "react";
import { t } from "../../i18n";
import { apiBaseUrl } from "../../api/client";
import { parseMarkdown, type BlockNode, type InlineNode } from "./markdown/parse";
import { decideUrl } from "./urlPolicy";
import { useQuestionAsset } from "./useQuestionAsset";

/**
 * 安全 Markdown 渲染器（RQ-B-1-40）。
 *
 * **不使用 `dangerouslySetInnerHTML`**：节点树由 React 元素构成，题干里的 HTML 标签
 * 只会以文字形式出现（eslint 规则 `no-restricted-syntax` 同时禁止该属性）。
 */

function ControlledImage({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  // 受控资产需要带令牌取字节（`<img>` 不会带请求头），因此走这个 hook；
  // 非资产地址返回 `not-asset`，继续走 URL 白名单那条路。
  const asset = useQuestionAsset(src);
  const decision = decideUrl(src, apiBaseUrl());
  const label = alt === "" ? t("questions.assetAltFallback") : alt;

  if (asset.status === "loading") {
    return (
      <span className="muted" data-testid="asset-loading">
        {t("common.loading")}
      </span>
    );
  }
  if (asset.status === "forbidden") {
    return (
      <span className="muted" data-testid="asset-forbidden">
        {t("questions.assetForbidden")}
      </span>
    );
  }
  if (asset.status === "unavailable") {
    return (
      <span className="muted" data-testid="asset-unavailable">
        {t("questions.assetUnavailable")}
      </span>
    );
  }
  if (asset.status === "ready") {
    return (
      <img
        src={asset.url}
        alt={label}
        loading="lazy"
        referrerPolicy="no-referrer"
        data-testid="stem-asset"
      />
    );
  }

  if (!decision.allowed) {
    return (
      <span className="muted" data-testid="blocked-image">
        {t("questions.assetBlocked")}（{decision.reason}）
      </span>
    );
  }
  if (failed) {
    return (
      <span className="muted" data-testid="unavailable-image">
        {t("questions.assetUnavailable")}
      </span>
    );
  }
  return (
    <img
      src={decision.url}
      alt={label}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}

function Inline({ nodes }: { nodes: InlineNode[] }) {
  return (
    <>
      {nodes.map((node, index) => {
        switch (node.type) {
          case "text":
            return <span key={index}>{node.value}</span>;
          case "strong":
            return (
              <strong key={index}>
                <Inline nodes={node.children} />
              </strong>
            );
          case "em":
            return (
              <em key={index}>
                <Inline nodes={node.children} />
              </em>
            );
          case "code":
            return <code key={index}>{node.value}</code>;
          case "link": {
            const decision = decideUrl(node.href, apiBaseUrl());
            if (!decision.allowed) {
              return (
                <span key={index} className="muted" data-testid="blocked-link">
                  {node.children.map((child) => (child.type === "text" ? child.value : "")).join("")}
                </span>
              );
            }
            return (
              <a key={index} href={decision.url} rel="noreferrer noopener" target="_blank">
                <Inline nodes={node.children} />
              </a>
            );
          }
          case "image":
            return <ControlledImage key={index} src={node.src} alt={node.alt} />;
          default:
            return null;
        }
      })}
    </>
  );
}

function Blocks({ nodes }: { nodes: BlockNode[] }) {
  return (
    <>
      {nodes.map((node, index) => {
        switch (node.type) {
          case "heading": {
            const Tag = (`h${Math.min(node.level, 3)}`) as "h1" | "h2" | "h3";
            return (
              <Tag key={index}>
                <Inline nodes={node.children} />
              </Tag>
            );
          }
          case "paragraph":
            return (
              <p key={index}>
                <Inline nodes={node.children} />
              </p>
            );
          case "code_block":
            return (
              <pre key={index} data-language={node.language ?? undefined}>
                <code>{node.value}</code>
              </pre>
            );
          case "list":
            return node.ordered ? (
              <ol key={index}>
                {node.items.map((item, itemIndex) => (
                  <li key={itemIndex}>
                    <Inline nodes={item} />
                  </li>
                ))}
              </ol>
            ) : (
              <ul key={index}>
                {node.items.map((item, itemIndex) => (
                  <li key={itemIndex}>
                    <Inline nodes={item} />
                  </li>
                ))}
              </ul>
            );
          case "blockquote":
            return (
              <blockquote key={index}>
                <Blocks nodes={node.children} />
              </blockquote>
            );
          case "table":
            return (
              <table key={index}>
                <thead>
                  <tr>
                    {node.header.map((cell, cellIndex) => (
                      <th key={cellIndex}>
                        <Inline nodes={cell} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {node.rows.map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex}>
                          <Inline nodes={cell} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            );
          default:
            return null;
        }
      })}
    </>
  );
}

export function SafeMarkdown({ source, testId }: { source: string; testId?: string }) {
  const blocks = parseMarkdown(source);
  return (
    <div className="stem" data-testid={testId ?? "stem"}>
      <Blocks nodes={blocks} />
    </div>
  );
}
