#!/usr/bin/env node
/**
 * 从 MAIN OpenAPI 生成 TypeScript 类型（ADR-0002：禁止手写平行 DTO）。
 *
 * 用法：
 *   node scripts/generate-api-types.mjs            # 生成 src/api/generated/*.ts
 *   node scripts/generate-api-types.mjs --check    # 只校验生成物是否与契约一致（漂移门禁）
 *
 * 契约目录默认为 `../MAIN/contracts`，可用 `CONTRACTS_DIR` 覆盖。
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import openapiTS, { astToString } from "openapi-typescript";

const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, "..");
const contractRoot = path.resolve(
  projectRoot,
  process.env.CONTRACTS_DIR ?? "../MAIN/contracts",
);
const outDir = path.resolve(projectRoot, "src/api/generated");
const checkOnly = process.argv.includes("--check");

const targets = [
  { name: "common", source: "openapi/common.yaml" },
  { name: "b2", source: "openapi/b2.yaml" },
  { name: "b3", source: "openapi/b3.yaml" },
  { name: "b4", source: "openapi/b4.yaml" },
];

const header = (source) =>
  `/**\n` +
  ` * AUTO-GENERATED FROM ${source} — DO NOT EDIT BY HAND.\n` +
  ` *\n` +
  ` * Regenerate with: pnpm api:generate\n` +
  ` * Source of truth: MAIN/contracts/** (see docs/adr/ADR-0002-contract-generated-api-client.md).\n` +
  ` * Any hand edit is reported as contract drift by \`pnpm api:check\`.\n` +
  ` */\n\n`;

await mkdir(outDir, { recursive: true });

const drift = [];
for (const target of targets) {
  const schemaPath = path.join(contractRoot, target.source);
  // 用 file:// URL 传入：openapi-typescript 的本地文件解析在 Windows 反斜杠路径下会
  // 误判文档格式（"Unsupported schema format, expected openapi: 3.x"）。
  const ast = await openapiTS(pathToFileURL(schemaPath));
  const next = header(target.source) + astToString(ast);
  const outPath = path.join(outDir, `${target.name}.ts`);

  if (checkOnly) {
    const current = await readFile(outPath, "utf8").catch(() => null);
    if (current !== next) drift.push(path.relative(projectRoot, outPath));
    continue;
  }
  await writeFile(outPath, next, "utf8");
  console.log(`generated ${path.relative(projectRoot, outPath)}`);
}

if (checkOnly) {
  if (drift.length > 0) {
    console.error(
      `API type drift detected in: ${drift.join(", ")}\nRun \`pnpm api:generate\` and commit the result.`,
    );
    process.exit(1);
  }
  console.log("API types match MAIN/contracts.");
}
