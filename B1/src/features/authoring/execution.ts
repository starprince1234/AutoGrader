import type { QuestionDraftInput, TestCaseInput } from "../../api/types";

/**
 * 「评分方式 / 执行声明」（`b3.yaml` 的 `ExecutionManifest`、`ExecutionEmail`、`ExecutionSsh`、
 * `FileCheckInput` 以及 `TestCaseInput` 的四个新字段）在 B1 侧的**本地窄类型与边界映射**。
 *
 * 为什么另写一份类型：`src/api/types.ts` 是 `pnpm api:generate` 的生成物（ADR-0002），B1 不手改它。
 * 在生成物重新生成之前，编辑器需要一份与契约同形的窄类型；它只在**边界处**出现：
 *
 * - 读：`readExecutionMode`（公开版本 DTO 的 `execution_mode`）与 `readDraftExecution`
 *   （作者通道的 `QuestionDraftContent.execution`）。两者都从 `unknown` 出发做形状判断，
 *   因此服务端字段缺失时回落 `per_case`，而不是把一个未验证值塞进表单。
 * - 写：`attachExecution` 把执行声明附加到 `QuestionDraftInput` 上——这是生成类型尚未包含
 *   该字段时唯一的绕行点，只做一次显式映射，不在组件里散落断言。
 *
 * 行为只由这份显式声明决定，绝不从标题、标签或路径推断（设计基线 §1 第 2 条）。
 */

export type ExecutionMode = "per_case" | "single_run" | "ssh_session" | "email_delivery";

export const EXECUTION_MODES: readonly ExecutionMode[] = [
  "per_case",
  "single_run",
  "ssh_session",
  "email_delivery",
] as const;

export const SSH_CHECKS = ["service_ready", "auth_succeeded", "session_closed"] as const;
export type SshCheck = (typeof SSH_CHECKS)[number];

export const EMAIL_CHECKS = [
  "email_received",
  "subject",
  "sender",
  "attachment",
] as const;
export type EmailCheck = (typeof EMAIL_CHECKS)[number];

/** 契约把 host 固定成这个值：任何其他地址都会离开评测使用的隔离网络命名空间。 */
export const SSH_HOST = "127.0.0.1";

export const SSH_PORT_MIN = 1;
export const SSH_PORT_MAX = 65535;
export const SSH_PASSWORD_MAX_LENGTH = 128;
export const SSH_TIMEOUT_SECONDS_MAX = 30;
export const EMAIL_ATTEMPT_MIN = 1;
export const EMAIL_ATTEMPT_MAX = 99;

/** 与 `ExecutionManifest.segment_delimiter` 的 `minLength`/`maxLength`/`pattern` 一致。 */
export const SEGMENT_DELIMITER_MIN_LENGTH = 8;
export const SEGMENT_DELIMITER_MAX_LENGTH = 120;
export const SEGMENT_DELIMITER_PATTERN = /^[!-~](?:[ -~]*[!-~])?$/;

/** 与 `ExecutionSsh.username.pattern` 一致。 */
export const SSH_USERNAME_PATTERN = /^[a-z_][a-z0-9_-]{0,31}$/;

/** 与 `FileCheckInput.mode.pattern` 一致。 */
export const FILE_CHECK_MODE_PATTERN = /^[0-7]{3,4}$/;
export const FILE_CHECK_PATH_MAX_LENGTH = 200;
export const MAX_FILE_CHECKS_PER_CASE = 20;

export type ExecutionEmailInput = {
  attempt: number;
  subject_template?: string;
};

export type ExecutionSshInput = {
  host: typeof SSH_HOST;
  port: number;
  username: string;
  password: string;
  login_timeout_seconds?: number;
  session_timeout_seconds?: number;
};

export type ExecutionManifestInput = {
  mode: ExecutionMode;
  segment_delimiter?: string | null;
  email?: ExecutionEmailInput | null;
  ssh?: ExecutionSshInput | null;
};

export type FileCheckInput = {
  path: string;
  content_sha256?: string;
  mode?: string;
};

/**
 * `TestCaseInput` + 四个可选用例级字段。生成类型更新后本类型仍然与契约结构兼容：
 * 所有新增字段都是可选的，因此既不会把缺省值说成声明，也不会多出第二套语义。
 */
export type TestCaseInputWithExecution = TestCaseInput & {
  segment?: number | null;
  file_checks?: FileCheckInput[];
  ssh_check?: SshCheck | null;
  email_check?: EmailCheck | null;
};

/** 请求体 + 执行声明。只在测试里用来读取已构造的请求体（生成类型更新前没有该字段）。 */
export type QuestionDraftInputWithExecution = QuestionDraftInput & {
  execution?: ExecutionManifestInput | null;
};

/* ------------------------------------------------------------------- 边界：读 */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isExecutionMode(value: unknown): value is ExecutionMode {
  return typeof value === "string" && (EXECUTION_MODES as readonly string[]).includes(value);
}

export function isSshCheck(value: unknown): value is SshCheck {
  return typeof value === "string" && (SSH_CHECKS as readonly string[]).includes(value);
}

export function isEmailCheck(value: unknown): value is EmailCheck {
  return typeof value === "string" && (EMAIL_CHECKS as readonly string[]).includes(value);
}

/**
 * 公开版本 DTO 的 `execution_mode`。公开面只有 mode（配置是受保护的）：读不到或值不认识时
 * 回落 `per_case`——那是所有旧版本的事实，而不是"猜一个新模式"。
 */
export function readExecutionMode(version: unknown): ExecutionMode {
  if (!isRecord(version)) return "per_case";
  const mode = version.execution_mode;
  return isExecutionMode(mode) ? mode : "per_case";
}

/**
 * 草稿内容里的 `execution`。服务端总是返回**规范化对象**（缺省 `{mode:"per_case"}`，见
 * `QuestionDraftContent.execution`）；这里仍从 `unknown` 建立形状，使旧服务端缺失该键时
 * 编辑页得到 `per_case` 而不是 `undefined`。
 *
 * 只保留当前 mode 真正会读取的配置：编辑器重新序列化时也只发送这些键，因此受保护字段
 * （例如 SSH 口令）不会因为 mode 不同而被顺手带上。
 */
export function readDraftExecution(content: unknown): ExecutionManifestInput {
  if (!isRecord(content)) return { mode: "per_case" };
  return normalizeExecutionManifest(content.execution);
}

/** 把服务端/请求体形状收窄成本地 `ExecutionManifestInput`；不认识的值回落 `per_case`。 */
function normalizeExecutionManifest(raw: unknown): ExecutionManifestInput {
  if (!isRecord(raw)) return { mode: "per_case" };
  const mode = raw.mode;
  if (!isExecutionMode(mode)) return { mode: "per_case" };
  const manifest: ExecutionManifestInput = { mode };

  const delimiter = raw.segment_delimiter;
  if (typeof delimiter === "string" || delimiter === null) {
    manifest.segment_delimiter = delimiter;
  }

  const email = raw.email;
  if (isRecord(email) && typeof email.attempt === "number") {
    const normalizedEmail: ExecutionEmailInput = { attempt: email.attempt };
    if (typeof email.subject_template === "string") {
      normalizedEmail.subject_template = email.subject_template;
    }
    manifest.email = normalizedEmail;
  }

  const ssh = raw.ssh;
  if (
    isRecord(ssh) &&
    typeof ssh.port === "number" &&
    typeof ssh.username === "string" &&
    typeof ssh.password === "string"
  ) {
    const normalizedSsh: ExecutionSshInput = {
      host: SSH_HOST,
      port: ssh.port,
      username: ssh.username,
      password: ssh.password,
    };
    if (typeof ssh.login_timeout_seconds === "number") {
      normalizedSsh.login_timeout_seconds = ssh.login_timeout_seconds;
    }
    if (typeof ssh.session_timeout_seconds === "number") {
      normalizedSsh.session_timeout_seconds = ssh.session_timeout_seconds;
    }
    manifest.ssh = normalizedSsh;
  }

  return manifest;
}

/* ------------------------------------------------------------------- 边界：写 */

/**
 * 边界：把生成类型里的用例（生成物可能尚未包含执行字段）视为可读的扩展形状。
 *
 * 用例级字段全是可选的，因此这只是让同一份服务端数据在编辑器里可读；不做任何默认值填充，
 * 缺省的字段仍然表示"这个用例没有声明它"。
 */
export function readCaseInput(item: TestCaseInput): TestCaseInputWithExecution {
  return item as TestCaseInputWithExecution;
}

/**
 * 把执行声明附加到 `QuestionDraftInput`。
 *
 * 这里用 `Object.assign` 而不是类型断言：生成类型更新前后，请求体都只是多出一个 `execution`
 * 属性，而对象里其他字段仍然完整地按 `QuestionDraftInput` 校验；`Object.assign` 的返回类型
 * （原类型 ∧ 新属性）在两种生成状态下都能赋值回 `QuestionDraftInput`。
 */
export function attachExecution(
  input: QuestionDraftInput,
  execution: ExecutionManifestInput,
): QuestionDraftInput {
  return Object.assign({}, input, { execution });
}
