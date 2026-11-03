/**
 * The model provider (`LLM_*`), as environment entries.
 *
 * Why this module exists -- the same reason `mail-env.mjs` does: the production environment
 * file is rendered from a **fixed list** (`write-runtime-env.mjs`), not from the whole Doppler
 * config, and it is only rendered once (by `bootstrap-host`). Anything absent from that list
 * never reaches the process, no matter what Doppler holds. The three `LLM_*` values existed in
 * Doppler while B3 read a different set of names, so the provider looked permanently
 * unconfigured: the teacher-facing panel said "AI 出题暂未开通" with a valid key sitting in the
 * vault.
 *
 * Two shapes are supported on purpose:
 *
 * * `strict` (used by `sync-llm-config`): all three must be present, because a partial block
 *   would enable a feature that can only fail at the first call.
 * * `optional` (used when rendering a whole runtime file): if none of them is present the
 *   block is omitted, so a local or preview render still works; a *partial* set is always an
 *   error, because that is a misconfiguration rather than a choice.
 */

const REQUIRED = ["LLM_BASE_URL", "LLM_MODEL_NAME", "LLM_APIKEY"];

function singleLine(name, value) {
  if (!value || /[\r\n]/.test(value)) {
    throw new Error(name + " is required and must be a single line");
  }
  return value;
}

function quote(value) {
  // Double quotes with escaped backslash/quote is valid in both `systemd EnvironmentFile`
  // and a bash `source`, which are the two readers this file has.
  return '"' + value.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
}

export function llmEnvEntries(env, { mode = "optional" } = {}) {
  if (mode !== "optional" && mode !== "strict") {
    throw new Error("llmEnvEntries: unknown mode " + mode);
  }
  const present = REQUIRED.filter((name) => (env[name] ?? "").trim() !== "");
  if (present.length === 0 && mode === "optional") {
    return {};
  }
  const missing = REQUIRED.filter((name) => (env[name] ?? "").trim() === "");
  if (missing.length > 0) {
    throw new Error("the model provider block is incomplete; missing " + missing.join(", "));
  }

  const baseUrl = singleLine("LLM_BASE_URL", env.LLM_BASE_URL.trim());
  if (!/^https?:\/\/[^\s]+$/i.test(baseUrl)) {
    throw new Error("LLM_BASE_URL must be an http(s) URL");
  }
  const model = singleLine("LLM_MODEL_NAME", env.LLM_MODEL_NAME.trim());
  // The credential is not trimmed: leading/trailing spaces can be part of a generated key.
  const apiKey = singleLine("LLM_APIKEY", env.LLM_APIKEY);

  // Raw values, not pre-quoted: the writers quote for their own file format, and a value that
  // arrives already wrapped would be quoted twice.
  return {
    LLM_BASE_URL: baseUrl,
    LLM_MODEL_NAME: model,
    LLM_APIKEY: apiKey,
  };
}

/** `KEY="value"` lines, one per entry. */
export function llmEnvFile(env, options) {
  return Object.entries(llmEnvEntries(env, options))
    .map(([name, value]) => name + "=" + quote(value) + "\n")
    .join("");
}
