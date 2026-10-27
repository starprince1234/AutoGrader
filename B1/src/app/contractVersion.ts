/**
 * 契约版本。
 *
 * 唯一真源是 `contracts.lock`（见 `docs/README.md` 的权威表）：`vite.config.ts` 在**构建期**
 * 读它并注入 `__CONTRACT_VERSION__`。不要在代码里再写一个字面量——契约升版后
 * 「复制诊断信息」里的版本会立刻开始说谎，而没有任何门禁会发现。
 *
 * 它**不显示在页头**：契约版本是排障信息，不是用户界面语言（见 phase-06）。
 */
export const CONTRACT_VERSION: string = __CONTRACT_VERSION__;
