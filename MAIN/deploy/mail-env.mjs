/**
 * The self-service registration mail channel, as environment entries.
 *
 * Why this module exists: the production environment file is **rendered from a fixed list**
 * (`write-runtime-env.mjs`), not from the whole Doppler config. Anything absent from that
 * list never reaches the process, no matter what Doppler holds -- which is exactly how the
 * first deployment failed: the variables existed, the file did not carry them, and B4
 * reported the channel as unconfigured.
 *
 * Two shapes are supported on purpose:
 *
 * * `strict` (used by `sync-mail-channel`): all four required variables must be present.
 * * `optional` (used when rendering a whole runtime file): if none of them is present the
 *   block is simply omitted, so a local or preview render still works; a *partial* set is
 *   always an error, because that is a misconfiguration rather than a choice.
 */

const REQUIRED = ["SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM"];
const TLS_MODES = new Set(["tls", "ssl", "starttls", "none"]);

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

export function mailEnvEntries(env, { mode = "optional" } = {}) {
  if (mode !== "optional" && mode !== "strict") {
    throw new Error("mailEnvEntries: unknown mode " + mode);
  }
  const present = REQUIRED.filter((name) => (env[name] ?? "").trim() !== "");
  if (present.length === 0 && mode === "optional") {
    return {};
  }
  const missing = REQUIRED.filter((name) => (env[name] ?? "").trim() === "");
  if (missing.length > 0) {
    throw new Error(
      "the registration mail channel is incomplete; missing " + missing.join(", "),
    );
  }

  const host = singleLine("SMTP_HOST", env.SMTP_HOST.trim());
  const user = singleLine("SMTP_USER", env.SMTP_USER.trim());
  const pass = singleLine("SMTP_PASS", env.SMTP_PASS);
  const from = singleLine("SMTP_FROM", env.SMTP_FROM.trim());

  const port = (env.SMTP_PORT ?? "465").trim();
  if (!/^[0-9]{1,5}$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
    throw new Error("SMTP_PORT must be a TCP port number");
  }
  const tlsMode = (env.SMTP_TLS_MODE ?? "tls").trim().toLowerCase();
  if (!TLS_MODES.has(tlsMode)) {
    throw new Error("SMTP_TLS_MODE must be one of " + [...TLS_MODES].join(", "));
  }
  const timeout = (env.SMTP_TIMEOUT_SECONDS ?? "60").trim();
  if (!/^[0-9]{1,4}$/.test(timeout) || Number(timeout) < 1) {
    throw new Error("SMTP_TIMEOUT_SECONDS must be a positive integer");
  }
  const fromName = (env.SMTP_FROM_NAME ?? "AutoGrader").trim() || "AutoGrader";

  // Raw values, not pre-quoted: the two writers quote for their own file format, and a
  // value that arrives already wrapped would be quoted twice (`SMTP_HOST="\"smtp...\""`),
  // which a reader then hands to the SMTP client verbatim.
  return {
    SMTP_HOST: host,
    SMTP_PORT: port,
    SMTP_USER: user,
    SMTP_PASS: pass,
    SMTP_FROM: from,
    SMTP_FROM_NAME: fromName,
    SMTP_TLS_MODE: tlsMode,
    SMTP_TIMEOUT_SECONDS: timeout,
  };
}

/** `KEY="value"` lines, one per entry. */
export function mailEnvFile(env, options) {
  return (
    Object.entries(mailEnvEntries(env, options))
      .map(([name, value]) => name + "=" + quote(value) + "\n")
      .join("") + ""
  );
}

/**
 * B2 的 IMAP 收信配置。
 *
 * 与 SMTP 同一个坑：write-runtime-env.mjs 是按白名单渲染的，Doppler 里新增的键不会自动
 * 出现在 /etc/autograder/runtime.env，进程永远读不到。所以收信通道也走同一条合并通道。
 *
 * 校验规则与 SMTP 略有不同：**一个都没有** = 收信未启用（合法状态，email-intake: not-configured
 * 就是它）；**给了但缺关键项** = 配置错误，直接拒绝，因为半配置的收信入口比关着更危险
 * （健康检查会显示"已配置"，而实际拉不到邮件）。
 */
const IMAP_REQUIRED = ["B2_IMAP_HOST", "B2_IMAP_USERNAME", "B2_IMAP_PASSWORD"];
const IMAP_OPTIONAL = [
  "B2_IMAP_PORT",
  "B2_IMAP_MAILBOX",
  "B2_IMAP_USE_SSL",
  "B2_IMAP_PROCESSED_MAILBOX",
  "B2_IMAP_MATCHING_JSON",
  "B2_IMAP_MATCHING_PATH",
  "B2_IMAP_MAX_MESSAGES",
  "B2_IMAP_MAX_MESSAGE_BYTES",
];

export function imapEnvEntries(env) {
  const present = IMAP_REQUIRED.filter((name) => (env[name] ?? "").trim() !== "");
  if (present.length === 0) {
    return {};
  }
  const missing = IMAP_REQUIRED.filter((name) => (env[name] ?? "").trim() === "");
  if (missing.length > 0) {
    throw new Error("the IMAP intake is half-configured; missing " + missing.join(", "));
  }
  const entries = {};
  for (const name of [...IMAP_REQUIRED, ...IMAP_OPTIONAL]) {
    const value = (env[name] ?? "").trim();
    if (value !== "") {
      entries[name] = value;
    }
  }
  return entries;
}
