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
/**
 * Doppler 里收信走的是与 `SMTP_*` 一致的命名：`IMAP_HOST / IMAP_USER / IMAP_PASS /
 * IMAP_PORT / IMAP_TLS_MODE`；而 B2 读的是带模块前缀的 `B2_IMAP_*`。翻译只放在这里：
 * 少了它，键根本不会出现在环境文件里，入口永远"not configured"，而日志里只说"没配"。
 *
 * 两套名字都接受，显式带前缀的优先——这样有人愿意在 Doppler 里直接用 B2 规范名时也不用改代码。
 */
const IMAP_ALIASES = {
  B2_IMAP_HOST: ["B2_IMAP_HOST", "IMAP_HOST"],
  B2_IMAP_PORT: ["B2_IMAP_PORT", "IMAP_PORT"],
  B2_IMAP_USERNAME: ["B2_IMAP_USERNAME", "IMAP_USER"],
  B2_IMAP_PASSWORD: ["B2_IMAP_PASSWORD", "IMAP_PASS"],
  B2_IMAP_MAILBOX: ["B2_IMAP_MAILBOX", "IMAP_MAILBOX"],
  B2_IMAP_USE_SSL: ["B2_IMAP_USE_SSL", "IMAP_USE_SSL", "IMAP_TLS_MODE"],
  B2_IMAP_PROCESSED_MAILBOX: ["B2_IMAP_PROCESSED_MAILBOX", "IMAP_PROCESSED_MAILBOX"],
  B2_IMAP_MATCHING_JSON: ["B2_IMAP_MATCHING_JSON", "IMAP_MATCHING_JSON"],
};

const IMAP_REQUIRED = ["B2_IMAP_HOST", "B2_IMAP_USERNAME", "B2_IMAP_PASSWORD"];

function pick(env, names) {
  for (const name of names) {
    const value = (env[name] ?? "").trim();
    if (value !== "") {
      return { name, value };
    }
  }
  return null;
}

/** `IMAP_TLS_MODE=tls|ssl` 与 `IMAP_USE_SSL=true` 是同一件事，B2 只认后者。 */
function asBooleanFlag(source, value) {
  if (source !== "IMAP_TLS_MODE") {
    return value;
  }
  const normalised = value.toLowerCase();
  if (normalised === "none" || normalised === "starttls" || normalised === "plain") {
    return "false";
  }
  return "true";
}

export function imapEnvEntries(env) {
  const resolved = {};
  const missing = [];
  for (const [target, names] of Object.entries(IMAP_ALIASES)) {
    const hit = pick(env, names);
    if (hit === null) {
      if (IMAP_REQUIRED.includes(target)) {
        missing.push(target);
      }
      continue;
    }
    resolved[target] = asBooleanFlag(hit.name, hit.value);
  }

  const anyGiven = Object.keys(resolved).length > 0;
  if (!anyGiven) {
    // 一个都没给 = 收信未启用，这是合法状态（README/健康面把它显示为 not-configured）。
    return {};
  }
  if (missing.length > 0) {
    throw new Error(
      "the IMAP intake is half-configured; missing " + missing.join(", "),
    );
  }
  // 收信入口只有在能路由邮件时才值得打开：B2 的 intake 没有 matching 规则会判 unavailable，
  // 而 unavailable 会把整个 /health/ready 拉成 false。所以缺规则 = 保持关闭（not-configured），
  // 并在部署日志里说清楚，而不是写一份"看起来配好了"的半配置。
  // "能路由邮件"有两种合法来源：显式规则表，或按发件人查账号（B2_IDENTITY_* + 目标作业）。
  // 只认规则表会让"已配好身份查询"的部署被误判成不可用，于是收信永远打不开。
  const hasMatching =
    (resolved.B2_IMAP_MATCHING_JSON ?? "").trim() !== "" ||
    (env.B2_IMAP_TARGET_JSON ?? "").trim() !== "";
  if (!hasMatching) {
    console.warn(
      "IMAP intake skipped: no matching rules (set B2_IMAP_MATCHING_JSON); " +
        "the mailbox stays disabled instead of reporting an unusable intake",
    );
    return {};
  }

  if (resolved.B2_IMAP_MAILBOX === undefined) {
    resolved.B2_IMAP_MAILBOX = "INBOX";
  }
  if (resolved.B2_IMAP_PORT === undefined) {
    resolved.B2_IMAP_PORT = "993";
  }
  return resolved;
}

/**
 * 邮件收信所需的身份查询配置。
 *
 * 三项必须成套：缺任何一项，B2 的收信入口就应当保持关闭（而不是退化成"任何邮件都收"）。
 * `B2_IMAP_TARGET_JSON` 由运维给出（本次作业的课程/课序/题目版本），其余两项在这里派生：
 * B4 与本服务同机，URL 固定；服务令牌复用 B2 调 B3 用的那一枚，B4 也认它。
 */
export function intakeEnvEntries(env) {
  const target = (env.B2_IMAP_TARGET_JSON ?? "").trim();
  if (!target) {
    return {};
  }
  const token = (env.B2_B4_SERVICE_TOKEN ?? env.B2_B3_SERVICE_TOKEN ?? "").trim();
  if (!token) {
    throw new Error(
      "B2_IMAP_TARGET_JSON is set but no service token is available " +
        "(set B2_B4_SERVICE_TOKEN or B2_B3_SERVICE_TOKEN)",
    );
  }
  return {
    B2_IDENTITY_URL: (env.B2_IDENTITY_URL ?? "http://127.0.0.1:18000").trim(),
    B2_IDENTITY_AUTHORIZATION: "Bearer " + token,
    B2_IMAP_TARGET_JSON: target,
  };
}
