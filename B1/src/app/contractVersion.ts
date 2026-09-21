/**
 * 契约版本。
 *
 * 唯一真源是 `contracts.lock`（见 `docs/README.md` 的权威表）：`vite.config.ts` 在**构建期**
 * 读它并注入 `__CONTRACT_VERSION__`。不要在代码里再写一个字面量——契约升版后顶栏与诊断
 * 信息会立刻开始说谎，而没有任何门禁会发现。
 */
export const CONTRACT_VERSION: string = __CONTRACT_VERSION__;
