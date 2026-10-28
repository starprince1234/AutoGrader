/**
 * Render only the self-service registration mail channel into a fragment file.
 *
 * Usage: node MAIN/deploy/write-mail-env.mjs mail.env
 *
 * This is deliberately a *separate* file from the full runtime render: the mail channel can
 * be added to an existing host without rewriting the rest of the environment (which holds the
 * database passwords and is rendered exactly once, by `bootstrap-host`).
 */

import { writeFileSync } from "node:fs";
import { imapEnvEntries, mailEnvFile } from "./mail-env.mjs";

const target = process.argv[2];
if (!target) {
  throw new Error("usage: node write-mail-env.mjs <output-path>");
}

const smtp = mailEnvFile(process.env, { mode: "strict" });
const imap = Object.entries(imapEnvEntries(process.env))
  .map(([name, value]) => name + '=' + JSON.stringify(value) + '\n')
  .join("");
const body = smtp + imap;
if (body.trim() === "") {
  throw new Error("the mail channel fragment would be empty");
}
writeFileSync(target, body, { mode: 0o600 });
console.log(
  "rendered mail channel fragment:",
  body
    .split("\n")
    .filter(Boolean)
    .map((line) => line.slice(0, line.indexOf("=")))
    .join(", "),
);
