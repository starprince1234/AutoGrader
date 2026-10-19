/// <reference types="vite/client" />

/**
 * B1 只读取这三个环境变量；契约路径本身已包含 `/api/v1` 前缀，
 * 因此 `VITE_API_BASE_URL` 表示 **API 源**（协议+主机），不是路径前缀。
 * 理由见 `docs/api/contract.md` 的"消费规则"。
 */
interface ImportMetaEnv {
  /** API 源。生产指向 api.91idc.me；本地开发连接 127.0.0.1:18000。 */
  readonly VITE_API_BASE_URL?: string;
  /** 可选的前端日志上报端点。未配置时日志只落本地控制台。 */
  readonly VITE_LOG_ENDPOINT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/**
 * 构建期注入的契约版本（`vite.config.ts` 从 `contracts.lock` 读取）。
 * 组件通过 `src/app/contractVersion.ts` 使用它，不直接引用这个全局量。
 */
declare const __CONTRACT_VERSION__: string;
