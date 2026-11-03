/**
 * Render only the model provider block (`LLM_*`) into a fragment file.
 *
 * Usage: node MAIN/deploy/write-llm-env.mjs llm.env
 *
 * Deliberately a *separate* file from the full runtime render: the provider can be added to an
 * existing host without rewriting the rest of the environment (which holds the database
 * passwords and is rendered exactly once, by `bootstrap-host`).
 */

import { writeFileSync } from "node:fs";
import { llmEnvFile } from "./llm-env.mjs";

const target = process.argv[2];
if (!target) {
  throw new Error("usage: node write-llm-env.mjs <output-path>");
}

const body = llmEnvFile(process.env, { mode: "strict" });
if (body.trim() === "") {
  throw new Error("the model provider fragment would be empty");
}
writeFileSync(target, body, { mode: 0o600 });
console.log(
  "rendered model provider fragment:",
  body
    .split("\n")
    .filter(Boolean)
    .map((line) => line.slice(0, line.indexOf("=")))
    .join(", "),
);
