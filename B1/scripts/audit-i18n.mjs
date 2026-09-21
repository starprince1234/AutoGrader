/**
 * 一次性审计脚本（**不在门禁里**）：找出 `src/i18n/index.ts` 中已经没有任何调用者的词条。
 *
 * 为什么需要脚本：`tsc` 不会报"词条没人用"，而词条是纯字符串表。判定要同时处理两种引用方式：
 * - 字面量：`t("grades.score")`、`t('grades.score')`；
 * - 模板串：``t(`grades.state.${...}`)``（前缀 + 动态后缀）。
 *
 * 用法：node scripts/audit-i18n.mjs
 */
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");

async function collect(dir, out = []) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "generated") continue;
      await collect(full, out);
      continue;
    }
    if (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) out.push(full);
  }
  return out;
}

const i18nPath = path.join(root, "src/i18n/index.ts");
const i18n = await readFile(i18nPath, "utf8");
const keys = [...i18n.matchAll(/^\s{4}"([A-Za-z0-9_.]+)":/gm)].map((match) => match[1]);

const sources = (await collect(path.join(root, "src")))
  .filter((file) => file !== i18nPath)
  .map((file) => file);
const text = (await Promise.all(sources.map((file) => readFile(file, "utf8")))).join("\n");

const dynamicPrefixes = [...text.matchAll(/t\(`([A-Za-z0-9_.]+)\$\{/g)].map(
  (match) => match[1],
);

const unused = keys.filter((key) => {
  if (dynamicPrefixes.some((prefix) => key.startsWith(prefix))) return false;
  return !text.includes(`"${key}"`) && !text.includes(`'${key}'`);
});

console.log(`词条总数: ${keys.length}`);
console.log(`动态前缀: ${[...new Set(dynamicPrefixes)].join(", ")}`);
console.log(`无引用词条 (${unused.length}):`);
for (const key of unused) console.log(`  ${key}`);
