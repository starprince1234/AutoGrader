import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";

/**
 * 真实 ESLint 配置（flat config）。
 *
 * 设计取舍：
 * - 不启用 type-checked 规则集：它会强制所有文件进入 program，显著拖慢门禁，且与
 *   `pnpm typecheck` 的职责重叠。类型正确性由 `tsc -b` 保证，ESLint 负责规范与禁用项。
 * - 生成产物 `src/api/generated/**` 不参与 lint（它们由 `pnpm api:generate` 从
 *   MAIN OpenAPI 产出，人工修改无意义）。
 * - 三条自定义限制直接来自 B1 的硬规则（见 AGENTS.md）：禁止 innerHTML、禁止跨模块
 *   导入、禁止把令牌写入持久化存储。
 */
export default tseslint.config(
  {
    ignores: [
      "dist/**",
      "coverage/**",
      "node_modules/**",
      "src/api/generated/**",
      ".vite-temp/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.browser },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message:
            "题干/报告一律白名单渲染，禁止 innerHTML（AGENTS.md 硬规则 4）。",
        },
        {
          selector:
            "CallExpression[callee.object.name='localStorage'][callee.property.name='setItem'][arguments.0.value=/token|refresh|session|credential|answer/i]",
          message:
            "令牌/参考答案不得写入浏览器持久化存储（ADR-0007 第 7 条 / AGENTS.md 硬规则 4）。",
        },
      ],
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["**/B2/**", "**/B3/**", "**/B4/**", "**/MAIN/**"],
              message:
                "B1 不导入其他模块的代码，只消费 MAIN/contracts 的契约（AGENTS.md 硬规则）。",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["**/*.test.{ts,tsx}", "vitest.setup.ts", "src/testing/**/*.{ts,tsx}"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    rules: {
      // 测试夹具需要构造不完整的服务端载荷来验证防御性渲染。
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
  {
    files: ["scripts/**/*.mjs", "*.config.{js,ts}", "eslint.config.js"],
    languageOptions: { globals: { ...globals.node } },
  },
);
