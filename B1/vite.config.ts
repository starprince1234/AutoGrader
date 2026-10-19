import { readFileSync } from "node:fs";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/**
 * 契约版本的唯一真源是 `contracts.lock`（`docs/README.md` 的权威表）。
 * 构建期读一次并注入 `__CONTRACT_VERSION__`：顶栏与"复制诊断信息"都用它，
 * 于是契约升版后不可能漏改某一处字面量。
 */
function contractVersion(): string {
  const lock = readFileSync(new URL("./contracts.lock", import.meta.url), "utf8");
  const match = /^contract_version:\s*(\S+)\s*$/m.exec(lock);
  if (!match) {
    throw new Error("contracts.lock 缺少 contract_version：无法注入契约版本");
  }
  return match[1];
}

/**
 * Vite 7 + Vitest 5 单配置。
 * 测试环境为 jsdom（组件测试），`setupFiles` 注册 jest-dom 匹配器与自动 cleanup。
 * 生成的 API 类型不进 bundle：`src/api/generated/**` 只有类型，编译期被擦除。
 */
export default defineConfig({
  // Vercel serves the SPA under `/app/`; the rewrite config maps those asset URLs back
  // into the Vite output directory while preserving browser routes.
  base: "/app/",
  define: {
    __CONTRACT_VERSION__: JSON.stringify(contractVersion()),
  },
  plugins: [react()],
  server: {
    host: "127.0.0.1",
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
    testTimeout: 15000,
  },
});
