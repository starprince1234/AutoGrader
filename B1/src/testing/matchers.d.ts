/**
 * 让 `@testing-library/jest-dom` 的匹配器（`toBeInTheDocument` 等）在测试文件里
 * 获得类型支持。放在 `src/testing/` 下（而不是根目录的 `vitest.setup.ts`），
 * 是因为 `tsconfig.json` 的 `include` 只覆盖 `src`；写在程序内的 .d.ts 才能被 `tsc -b` 看到。
 */
import "@testing-library/jest-dom/vitest";
