import { t } from "../../i18n";
import type { MessageKey, MessageParams } from "../../i18n";
import type {
  AdapterManifest,
  Language,
  QuestionDraftContent,
  QuestionDraftInput,
  QuestionDraftProposal,
  QuestionPublic,
  QuestionVersionPublic,
} from "../../api/types";
import {
  EMAIL_ATTEMPT_MAX,
  EMAIL_ATTEMPT_MIN,
  FILE_CHECK_MODE_PATTERN,
  FILE_CHECK_PATH_MAX_LENGTH,
  MAX_FILE_CHECKS_PER_CASE,
  SEGMENT_DELIMITER_MAX_LENGTH,
  SEGMENT_DELIMITER_MIN_LENGTH,
  SEGMENT_DELIMITER_PATTERN,
  SSH_HOST,
  SSH_PASSWORD_MAX_LENGTH,
  SSH_PORT_MAX,
  SSH_PORT_MIN,
  SSH_TIMEOUT_SECONDS_MAX,
  SSH_USERNAME_PATTERN,
  attachExecution,
  isEmailCheck,
  isSshCheck,
  readCaseInput,
  readDraftExecution,
  readExecutionMode,
  type EmailCheck,
  type ExecutionManifestInput,
  type ExecutionMode,
  type FileCheckInput,
  type SshCheck,
  type TestCaseInputWithExecution,
} from "./execution";

/**
 * 出题表单的**纯模型**：表单状态 ↔ 契约 `QuestionDraftInput` 的转换与校验。
 *
 * 为什么单独一层：请求体的每个字段都由这里决定，而"哪些值非法"必须能脱离 React 被单测。
 * 组件只负责渲染与事件，不参与字段映射。
 *
 * **可读回与不可读回的字段**（这是本页最重要的一条约束）：
 * - 可读回：`title`/`stem_markdown`/`language`/`tags`（`QuestionPublic`），
 *   `adapter_kind`/`max_files`/`max_total_bytes` 与 `execution_mode`（`QuestionVersionPublic`）；
 * - 可读回（仅作者、经受保护草稿通道）：`reference_answer`、`cases[]` 与 `execution` 的完整配置
 *   （`QuestionDraftContent`）；
 * - **不可从公开 DTO 读回**：参考答案、用例、适配器细节、夹具清单与执行配置。公开面只有
 *   `execution_mode` 一个枚举值，SSH 口令这类配置永远不会出现在公开 DTO 里。
 *
 * **评分方式是显式声明**（设计基线 §1）：`execution` 只由作者在这里选择并有意识地保存，
 * 编辑器绝不根据标题、标签或路径推断模式；切换模式时必须清掉不属于目标模式的配置，
 * 避免把练习环境口令之类的值带进一个不读取它的请求。
 *
 * **省略即保留（CHG-0017）**：两个受保护字段都是**可选**的，留空表示"我对这个字段不作声明"，
 * **不是**"清空它"：
 * - `cases` 省略 ⇒ 服务端保留该版本已有的用例；提供 ⇒ **整体替换**那个集合；
 * - `reference_answer` 省略**或空白** ⇒ 保留已存的答案。
 *
 * 因此本页对这两个字段的做法是：**留空就不发**（而不是发一个空值）。`required` 一字未改，
 * 仍是 `[title, stem_markdown, language, adapter]`——那四个字段**必须每次都发**，
 * 不要试图只发变动的字段（省略语义只对上面两个受保护字段成立，给别的字段加省略语义是另一个契约变更）。
 *
 * 夹具（`adapter.files[]`）不属于"可省略"的受保护字段：`adapter` 每次都要整体重发，所以夹具声明
 * 也每次都要重发。摘要**只能来自服务端上传结果**（见 `FixtureForm.contentSha256`），本模块从不
 * 自行计算或接受用户输入的摘要。
 */

/** 契约 `QuestionDraftInput.tags[].pattern` 与长度/数量上限（`b3.yaml`）。 */
const TAG_PATTERN = /^[a-z0-9][a-z0-9-]*$/;
export const TAG_MAX_ITEMS = 20;
export const TAG_MAX_LENGTH = 40;

/** 契约 `max_files` / `max_total_bytes` 的上限。 */
export const MAX_FILES_LIMIT = 100;
export const MAX_TOTAL_BYTES_LIMIT = 10485760;

/** 题目标题上限（契约 `title.maxLength`）。 */
export const TITLE_MAX_LENGTH = 300;

/**
 * 图片白名单：**与 B3 的 `ALLOWED_ASSET_CONTENT_TYPES` 一致**。
 * SVG 刻意不在其中——它能携带脚本，而资源是从 API 源返回的。B3 会再校验一次字节魔数，
 * 因此这里的列别只是让教师在选错文件时立刻看到原因。
 */
export const IMAGE_CONTENT_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"] as const;
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
/** 契约 `FixtureUploadInput` 没有字节上限字段，B3 的默认限制是 1 MiB。 */
export const MAX_FIXTURE_BYTES = 1024 * 1024;
export const MAX_FIXTURE_PATH_LENGTH = 200;

export type AdapterKind = AdapterManifest["kind"];
export type ComparisonPolicy = NonNullable<AdapterManifest["comparison"]>;
export type StdoutComparison = ComparisonPolicy["stdout"];
export type StderrComparison = NonNullable<ComparisonPolicy["stderr"]>;
export type FixtureMount = "input" | "expected_output";

export const LANGUAGE_OPTIONS: readonly Language[] = ["python", "bash"] as const;
export const STDOUT_COMPARISONS: readonly StdoutComparison[] = [
  "exact",
  "ignore_trailing_whitespace",
  "numeric_tolerance",
  "ignored",
] as const;
export const STDERR_COMPARISONS: readonly StderrComparison[] = [
  "exact",
  "ignore_trailing_whitespace",
  "ignored",
] as const;

/**
 * 本页可选择的适配器类型：**三种全部可选**。
 *
 * `file` 曾经被排除，理由是夹具端点当时是 `internal` + serviceToken，浏览器不能安全调用。
 * 那个端点现在使用教师 bearer JWT + 草稿所属课程授权（见 `useUploadFixture`），摘要仍由 B3 计算，
 * 因此浏览器可以完整创建 file 题型——**前提是先保存草稿拿到 `question_version_id`**，
 * 因为夹具是挂在一个版本上的。
 */
export const SELECTABLE_ADAPTER_KINDS: readonly AdapterKind[] = [
  "stdin",
  "file",
  "callable",
] as const;

/**
 * 一条文件校验声明（`FileCheckInput` 的表单副本）。
 *
 * `content` 是教师输入的**期望内容原文**，只用于在本机计算摘要；它从不进入请求体
 * （请求里只有 `contentSha256`）。`contentSha256` 为空且 `content` 非空表示"摘要还没算完"，
 * 提交会被拒绝并提示稍候——不拿一个过期的摘要冒充当前内容。
 *
 * 编辑已有声明时，服务端只读回摘要、不读回内容原文，因此 `content` 为空而 `contentSha256`
 * 非空是正常状态：留空表示沿用已保存摘要，输入新内容会重新计算。
 */
export type FileCheckForm = {
  path: string;
  /** 八进制权限位文本，例如 `"0744"`；留空表示不校验权限。 */
  mode: string;
  content: string;
  contentSha256: string;
};

export type CaseForm = {
  name: string;
  weight: string;
  visibleToStudent: boolean;
  stdin: string;
  args: string;
  expectedStdout: string;
  expectedStderr: string;
  expectedExitCode: string;
  /** `single_run` 专用：该用例是否按分段评分（关闭时改用 `fileChecks`）。 */
  segmentScored: boolean;
  /** 分段序号文本（`single_run` 且开启分段评分时必填）。 */
  segment: string;
  /** 运行结束后的文件校验（`per_case` 可选；`single_run` 未按分段评分时必填）。 */
  fileChecks: FileCheckForm[];
  /** `ssh_session` 专用：该用例判定的服务端事实。 */
  sshCheck: SshCheck | "";
  /** `email_delivery` 专用：该用例判定的邮件事实。 */
  emailCheck: EmailCheck | "";
};

/** 用例级执行声明字段的默认值：所有模式下都是"未声明"，不替教师预置主张。 */
function emptyCaseExecution(): Pick<
  CaseForm,
  "segmentScored" | "segment" | "fileChecks" | "sshCheck" | "emailCheck"
> {
  return {
    segmentScored: false,
    segment: "",
    fileChecks: [],
    sshCheck: "",
    emailCheck: "",
  };
}

export function emptyFileCheck(): FileCheckForm {
  return { path: "", mode: "", content: "", contentSha256: "" };
}

/**
 * 已上传的夹具。`contentSha256` 与 `sizeBytes` **只能**来自服务端上传结果，
 * 表单里没有让用户输入摘要的入口——摘要就是"这份字节"的绑定值。
 */
export type FixtureForm = {
  fixtureId: string;
  path: string;
  mount: FixtureMount;
  contentSha256: string;
  sizeBytes: number;
  fileName: string;
};

export type DraftForm = {
  title: string;
  stemMarkdown: string;
  language: Language;
  tags: string;
  referenceAnswer: string;
  adapterKind: AdapterKind;
  adapterArgs: string;
  adapterStdin: string;
  adapterExpectExitCode: string;
  callableModule: string;
  callableAttribute: string;
  callableArgumentsJson: string;
  comparisonStdout: StdoutComparison | "";
  comparisonStderr: StderrComparison | "";
  numericTolerance: string;
  maxFiles: string;
  maxTotalBytes: string;
  revisionNote: string;
  /**
   * 评分方式（`ExecutionManifest.mode`）。**唯一的模式来源**：标题、标签、路径都不参与推断。
   * 新建时缺省 `per_case`（与契约的缺省语义一致）。
   */
  executionMode: ExecutionMode;
  /** `single_run`：独占一行的固定分隔标记；为空表示本题没有分段用例。 */
  segmentDelimiter: string;
  /** `email_delivery`：这是第几次尝试（1..99）。 */
  emailAttempt: string;
  /**
   * `email_delivery` 的可选模板（契约有默认值，界面上不单独编辑）。
   * 读回草稿时原样保留，避免"只改标题保存"把服务端已有的模板悄悄重置成默认值。
   */
  emailSubjectTemplate: string;
  emailAttachmentTemplate: string;
  /** `ssh_session`：练习环境口令。只在内存与本页请求体里出现，不进 URL/日志/持久化存储。 */
  sshPort: string;
  sshUsername: string;
  sshPassword: string;
  sshLoginTimeout: string;
  sshSessionTimeout: string;
  cases: CaseForm[];
  /** 已上传的夹具声明（`adapter.files[]`）。未上传的文本建议留在这里没有意义：摘要未知。 */
  fixtures: FixtureForm[];
  /**
   * 编辑已有题目时，提交用例会**整体替换**服务端那份读不回来的集合（CHG-0017）。
   * 本页读不到现值，因此这个替换必须由人显式确认一次，而不是靠"我加了一个用例"推断出来。
   */
  confirmCaseReplacement: boolean;
};

export type DraftIssue = { key: MessageKey; params?: MessageParams };

/** 空用例：`ordinal` 由提交顺序决定，因此表单里不存它。 */
export function emptyCase(): CaseForm {
  return {
    name: "",
    // 分值不预置：留空让教师自己填（提交前仍校验"必须是大于 0 的数字"）。
    weight: "",
    visibleToStudent: false,
    stdin: "",
    args: "",
    expectedStdout: "",
    expectedStderr: "",
    // 程序正常退出码几乎总是 0：预置它，省掉一次几乎不会填错的手工输入。
    expectedExitCode: "0",
    ...emptyCaseExecution(),
  };
}

export function emptyDraftForm(): DraftForm {
  return {
    title: "",
    stemMarkdown: "",
    language: "python",
    tags: "",
    referenceAnswer: "",
    adapterKind: "stdin",
    adapterArgs: "",
    adapterStdin: "",
    // 同 `emptyCase`：命令行题型的期望退出码预置 0。
    adapterExpectExitCode: "0",
    callableModule: "",
    callableAttribute: "",
    callableArgumentsJson: "",
    comparisonStdout: "",
    comparisonStderr: "",
    numericTolerance: "",
    maxFiles: "20",
    maxTotalBytes: "1048576",
    revisionNote: "",
    // 评分方式默认 per_case：这是契约的缺省语义（旧版本全部如此），不是一个"猜测"。
    executionMode: "per_case",
    segmentDelimiter: "",
    emailAttempt: "1",
    emailSubjectTemplate: "",
    emailAttachmentTemplate: "",
    sshPort: "22",
    sshUsername: "",
    sshPassword: "",
    // 契约默认 10 秒：把默认值显示出来，教师能看到也可以改。
    sshLoginTimeout: "10",
    sshSessionTimeout: "10",
    // 新建时给一行空用例：这是"请填一个用例"的邀请，它本身不构成声明
    // （提交前仍会被逐字段校验）。清空它等于"这次不声明用例"。
    cases: [emptyCase()],
    fixtures: [],
    confirmCaseReplacement: false,
  };
}

/**
 * 从公开 DTO 预填。**只有公开 DTO 里真的存在的字段会被填**：参考答案、用例、适配器细节与夹具
 * 清单不会被"顺手留空"以外的任何方式对待——它们的空是有含义的（"本页读不到服务端的现值"），
 * 不是加载失败，**也不是"服务端没有"**。
 *
 * 编辑路径刻意以**零个用例**开场：本页读不到该版本已有的用例集合，凭空渲染一行空用例会让
 * "我不知道有几个"看起来像"它没有用例"，而提交那一行还会把整个集合替换掉。
 *
 * 夹具同理，且后果更硬：`adapter` 是整体重发的，所以编辑一个 file 题型时**必须重新上传**夹具，
 * 否则新版本会声明零个 input 夹具，发布时会被编译器拒绝（"must declare at least one input
 * fixture"）。本页把这件事写成可见提示，而不是假装清单已经读回来了。
 */
export function draftFormFrom(
  question: QuestionPublic | undefined,
  version: QuestionVersionPublic | undefined,
): DraftForm {
  const base = emptyDraftForm();
  return {
    ...base,
    title: question?.title ?? base.title,
    stemMarkdown: question?.stem_markdown ?? base.stemMarkdown,
    language: question?.language ?? base.language,
    tags: (question?.tags ?? []).join(", "),
    adapterKind: version?.adapter_kind ?? base.adapterKind,
    maxFiles: version?.max_files === undefined ? base.maxFiles : String(version.max_files),
    maxTotalBytes:
      version?.max_total_bytes === undefined
        ? base.maxTotalBytes
        : String(version.max_total_bytes),
    // 公开版本 DTO 只暴露模式枚举：先把模式显示对，完整配置等受保护草稿内容到达后再填。
    executionMode: readExecutionMode(version),
    cases: [],
    fixtures: [],
    confirmCaseReplacement: false,
  };
}

/**
 * 把一份 AI 候选（AI-FLOW-01A）应用到表单，得到**教师可继续编辑**的工作副本。
 *
 * 三条边界：
 * - 只填表单里存在的字段；语言与适配器沿用**教师已选**的值（它们不是模型的建议，见
 *   `QuestionDraftGenerationRequest` 的说明），候选里不一致的值也不会覆盖它们；
 * - `weight` 为空（服务端判定不可用）时留空，由逐字段校验提示教师填写——不替它填一个数字；
 * - provider 没给 `comparison.stdout` 时取 `"exact"`：manifest 要求这一项，而 `exact` 是最严格的
 *   默认值，教师在下拉里能看到并修改。这是**界面默认值**，不是模型输出，也不是"猜出来的结论"。
 *
 * 返回值不含服务端的 field warnings：那些属于候选预览，由调用方与预览一起展示并定位到控件。
 */
export function applyProposal(form: DraftForm, proposal: QuestionDraftProposal): DraftForm {
  const suggested = proposal.adapter;
  // 模型没声明比对方式时用"忽略行尾空白"：AI 的期望输出经常少写一个行尾换行，
  // 用"精确匹配"会让整套用例因为一个换行全部不过（教师仍可在第 5 步改）。
  const comparisonStdout = suggested?.comparison?.stdout ?? "ignore_trailing_whitespace";
  const comparisonStderr = suggested?.comparison?.stderr ?? form.comparisonStderr;
  const tolerance = suggested?.comparison?.numeric_tolerance;
  return {
    ...form,
    title: proposal.title,
    stemMarkdown: proposal.stem_markdown,
    tags: (proposal.tags ?? []).join(", "),
    referenceAnswer: proposal.reference_answer ?? "",
    adapterKind: proposal.adapter_kind,
    adapterArgs: (suggested?.args ?? []).join(" "),
    adapterStdin: suggested?.stdin ?? "",
    adapterExpectExitCode:
      suggested?.expect_exit_code === null || suggested?.expect_exit_code === undefined
        ? ""
        : String(suggested.expect_exit_code),
    callableModule: suggested?.callable?.module ?? "",
    callableAttribute: suggested?.callable?.attribute ?? "",
    callableArgumentsJson: suggested?.callable?.arguments_json ?? "",
    comparisonStdout,
    comparisonStderr,
    numericTolerance:
      tolerance === null || tolerance === undefined ? "" : String(tolerance),
    cases: (proposal.cases ?? []).map((item) => ({
      name: item.name,
      weight: item.weight === null || item.weight === undefined ? "" : String(item.weight),
      visibleToStudent: item.visible_to_student ?? false,
      stdin: item.stdin ?? "",
      args: (item.args ?? []).join(" "),
      expectedStdout: item.expected_stdout ?? "",
      expectedStderr: item.expected_stderr ?? "",
      expectedExitCode:
        item.expected_exit_code === null || item.expected_exit_code === undefined
          ? ""
          : String(item.expected_exit_code),
      // AI 候选不声明执行方式：模式与配置是教师的显式决定，模型不替它选。
      ...emptyCaseExecution(),
    })),
    // 候选本身不携带夹具摘要（B3 从不建议摘要），所以已上传的夹具保留，文本建议由教师手动上传。
    fixtures: form.fixtures,
    // 应用候选会整体替换用例集合，编辑态下仍需教师确认。
    confirmCaseReplacement: form.confirmCaseReplacement,
  };
}

/** 空格分隔的参数向量。契约要求按数组传递，本页从不拼 shell 字符串。 */
export function parseArgv(raw: string): string[] {
  return raw
    .split(/\s+/)
    .map((item) => item.trim())
    .filter((item) => item !== "");
}

/**
 * 单个标签的归一化：小写、空白与下划线转短横线、丢掉其余非法字符、去掉开头的非字母数字。
 * 归一化是契约自己声明的形状（"Normalised form is lowercase with hyphens"），不是本页发明的规则。
 */
export function normalizeTag(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/[\s_]+/g, "-")
    .replace(/[^a-z0-9-]/g, "")
    .replace(/-{2,}/g, "-")
    .replace(/^[^a-z0-9]+/, "");
}

export type ParsedTags = {
  /** 归一化后合法、已去重、保持输入顺序的标签。 */
  tags: string[];
  /** 归一化后仍不符合契约形状的原始片段（会被明确列出，不静默丢弃）。 */
  invalid: string[];
};

/** 逗号分隔的标签输入 → 契约 `tags`。中文逗号也接受：它是输入便利，不是字段。 */
export function parseTags(raw: string): ParsedTags {
  const tags: string[] = [];
  const invalid: string[] = [];
  for (const piece of raw.split(/[,，]/)) {
    if (piece.trim() === "") continue;
    const normalized = normalizeTag(piece);
    if (normalized === "" || normalized.length > TAG_MAX_LENGTH || !TAG_PATTERN.test(normalized)) {
      invalid.push(piece.trim());
      continue;
    }
    if (!tags.includes(normalized)) tags.push(normalized);
  }
  return { tags, invalid };
}

/**
 * 按题目的**结构化特征**给出默认标签（文档 2.0 第 4 条：题库里的题目按特点打标签）。
 *
 * 只用写入时确定无误的两件事：语言（`python` / `bash`）与运行方式（`stdin` / `file` /
 * `callable`）。这两个值本身就是契约里的枚举原值，天然满足标签形态（小写、以字母开头），
 * 因此不需要"归一化"，也不会出现"系统把我的标签改写了"这种情况。
 *
 * 只在教师**一个标签都没填**时使用：填了就原样提交，绝不覆盖。
 */
export function deriveTags(language: string, adapterKind: string): string[] {
  const tags: string[] = [];
  if (language === "python" || language === "bash") tags.push(language);
  if (adapterKind === "stdin" || adapterKind === "file" || adapterKind === "callable") {
    tags.push(adapterKind);
  }
  return tags;
}

/**
 * 逻辑路径校验：与 B3 的 `validate_workspace_path` 同一条规则（工作区相对、无 `..`、无绝对路径、
 * 无盘符、无 `~`、无空段）。放在这里是为了让教师在输入时就看到原因，而不是上传后才收到 422。
 */
const WORKSPACE_PATH = /^[A-Za-z0-9_.-]+(\/[A-Za-z0-9_.-]+)*$/;

export function isValidFixturePath(path: string): boolean {
  const trimmed = path.trim();
  if (trimmed === "" || trimmed !== path) return false;
  if (trimmed.length > MAX_FIXTURE_PATH_LENGTH) return false;
  if (trimmed.startsWith("/") || trimmed.startsWith("\\") || trimmed.startsWith("~")) return false;
  if (trimmed.includes("\\") || /^[A-Za-z]:/.test(trimmed)) return false;
  if (trimmed.split("/").some((segment) => segment === "" || segment === "." || segment === "..")) {
    return false;
  }
  return WORKSPACE_PATH.test(trimmed);
}

function intValue(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^-?\d+$/.test(trimmed)) return null;
  return Number.parseInt(trimmed, 10);
}

function floatValue(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^\d+(?:\.\d+)?$/.test(trimmed)) return null;
  const parsed = Number.parseFloat(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

/** 文本框 → 可空字符串字段：留空 = 不声明（`null`），不是空字符串。 */
function textOrNull(raw: string): string | null {
  return raw === "" ? null : raw;
}

/* ============================================================ 评分方式 / 执行声明 */

/**
 * 用例级执行声明的前端校验问题。`ordinal === null` 表示作用在**整组**用例上
 * （例如分段序号集合不完整），显示在用例列表下方而不是某一张卡片里。
 */
export type CaseExecutionIssue = {
  ordinal: number | null;
  key: MessageKey;
  params?: MessageParams;
};

/** 与契约 `ExecutionManifest.segment_delimiter` 的长度上限/字符规则一致。 */
export function isValidSegmentDelimiter(value: string): boolean {
  return (
    value.length >= SEGMENT_DELIMITER_MIN_LENGTH &&
    value.length <= SEGMENT_DELIMITER_MAX_LENGTH &&
    SEGMENT_DELIMITER_PATTERN.test(value)
  );
}

/** 文本里是否有**整行**等于 `line`（Python `splitlines()` 的近似：CRLF / CR / LF 都切）。 */
function hasWholeLine(text: string, line: string): boolean {
  return text.split(/\r\n|\r|\n/).includes(line);
}

/**
 * 版本级执行声明的前端校验（与 B3 `validate_execution_manifest` 同一条规则）。
 *
 * 返回的问题会被 `buildDraftInput` 用来**阻止发请求**，同时作者界面在「评分方式」区域
 * 立即展示同一份清单——校验只有这一处来源，界面与提交不会各说一套。
 */
export function executionDeclarationIssues(form: DraftForm): DraftIssue[] {
  const issues: DraftIssue[] = [];
  const mode = form.executionMode;
  if (mode === "per_case") return issues;

  if (mode === "single_run") {
    const segmentCases = form.cases.filter((item) => item.segmentScored);
    // `single_run` 的批量执行只能用 stdin/file；分段比较的是 stdout，因此声明分段时必须是 stdin。
    if (form.adapterKind === "callable") {
      issues.push({ key: "authoring.execution.issue.adapterKindSingleRun" });
    } else if (segmentCases.length > 0 && form.adapterKind !== "stdin") {
      issues.push({ key: "authoring.execution.issue.adapterKindSegment" });
    }

    const delimiter = form.segmentDelimiter;
    if (delimiter === "") {
      // 留空本身合法（整题都用 file_checks）；但有用例声明分段时必须给出分隔行。
      if (segmentCases.length > 0) {
        issues.push({ key: "authoring.execution.issue.delimiterRequired" });
      }
      return issues;
    }
    if (!isValidSegmentDelimiter(delimiter)) {
      issues.push({ key: "authoring.execution.issue.delimiterShape" });
      return issues;
    }
    if (segmentCases.length === 0 && form.cases.length > 0) {
      // 零个用例 = 本次不提交用例（服务端保留已有集合），本页无法判断那些用例是否分段。
      issues.push({ key: "authoring.execution.issue.delimiterWithoutSegments" });
    }
    // 分隔行只能出现在分段处：作为整行出现在任何期望输出里都会被后端拒绝。
    form.cases.forEach((item, ordinal) => {
      if (hasWholeLine(item.expectedStdout, delimiter)) {
        issues.push({
          key: "authoring.execution.issue.delimiterInExpected",
          params: { ordinal: ordinal + 1 },
        });
      }
    });
    return issues;
  }

  // ssh_session / email_delivery：包里的语言与适配器由 runner 的会话/投递驱动。
  if (form.language !== "bash" || form.adapterKind !== "stdin") {
    issues.push({ key: "authoring.execution.issue.requiresBashStdin" });
  }

  if (mode === "email_delivery") {
    const attempt = intValue(form.emailAttempt);
    if (attempt === null || attempt < EMAIL_ATTEMPT_MIN || attempt > EMAIL_ATTEMPT_MAX) {
      issues.push({
        key: "authoring.execution.issue.emailAttempt",
        params: { min: EMAIL_ATTEMPT_MIN, max: EMAIL_ATTEMPT_MAX },
      });
    }
    return issues;
  }

  const port = intValue(form.sshPort);
  if (port === null || port < SSH_PORT_MIN || port > SSH_PORT_MAX) {
    issues.push({
      key: "authoring.execution.issue.sshPort",
      params: { min: SSH_PORT_MIN, max: SSH_PORT_MAX },
    });
  }
  if (!SSH_USERNAME_PATTERN.test(form.sshUsername)) {
    issues.push({ key: "authoring.execution.issue.sshUsername" });
  }
  if (
    form.sshPassword === "" ||
    form.sshPassword.length > SSH_PASSWORD_MAX_LENGTH ||
    form.sshPassword.includes("\x00")
  ) {
    issues.push({
      key: "authoring.execution.issue.sshPassword",
      params: { max: SSH_PASSWORD_MAX_LENGTH },
    });
  }
  if (form.sshLoginTimeout.trim() !== "") {
    const login = floatValue(form.sshLoginTimeout);
    if (login === null || login <= 0 || login > SSH_TIMEOUT_SECONDS_MAX) {
      issues.push({
        key: "authoring.execution.issue.sshLoginTimeout",
        params: { max: SSH_TIMEOUT_SECONDS_MAX },
      });
    }
  }
  if (form.sshSessionTimeout.trim() !== "") {
    const session = floatValue(form.sshSessionTimeout);
    if (session === null || session <= 0 || session > SSH_TIMEOUT_SECONDS_MAX) {
      issues.push({
        key: "authoring.execution.issue.sshSessionTimeout",
        params: { max: SSH_TIMEOUT_SECONDS_MAX },
      });
    }
  }
  return issues;
}

/**
 * 用例级执行声明的前端校验（与 B3 `validate_case_execution(s)` 同一条规则）。
 *
 * 覆盖设计基线 §2 列出的规则：`single_run` 每个用例 `segment` XOR 非空 `file_checks`、
 * `segment` 值必须正好是 `0..N-1`、每种 ssh/email 判定至多一个用例、
 * `file_checks` 每项至少声明摘要或权限位之一。
 */
export function caseExecutionIssues(
  cases: CaseForm[],
  mode: ExecutionMode,
): CaseExecutionIssue[] {
  const issues: CaseExecutionIssue[] = [];

  if (mode === "per_case") {
    // 逐个用例独立运行：`file_checks` 可选，其余执行字段不允许（表单在切换模式时已清空）。
    cases.forEach((item, ordinal) => validateFileChecks(item, ordinal, issues));
    return issues;
  }

  if (mode === "single_run") {
    const segmentCases = cases.filter((item) => item.segmentScored);
    const segmentCount = segmentCases.length;
    const seenSegments = new Map<number, number>();

    cases.forEach((item, ordinal) => {
      if (!item.segmentScored) {
        if (item.fileChecks.length === 0) {
          issues.push({
            ordinal,
            key: "authoring.execution.issue.caseFileChecksRequired",
            params: { ordinal: ordinal + 1 },
          });
        }
        validateFileChecks(item, ordinal, issues);
        return;
      }
      const segment = intValue(item.segment);
      if (segment === null || segment < 0) {
        issues.push({
          ordinal,
          key: "authoring.execution.issue.caseSegment",
          params: { ordinal: ordinal + 1 },
        });
        return;
      }
      if (segment > segmentCount - 1) {
        issues.push({
          ordinal,
          key: "authoring.execution.issue.caseSegmentRange",
          params: { ordinal: ordinal + 1, max: segmentCount - 1 },
        });
        return;
      }
      seenSegments.set(segment, (seenSegments.get(segment) ?? 0) + 1);
    });

    for (const [segment, count] of seenSegments) {
      if (count > 1) {
        issues.push({
          ordinal: null,
          key: "authoring.execution.issue.segmentDuplicate",
          params: { segment },
        });
      }
    }
    const missing = Array.from({ length: segmentCount }, (_value, index) => index).filter(
      (value) => !seenSegments.has(value),
    );
    if (missing.length > 0) {
      issues.push({
        ordinal: null,
        key: "authoring.execution.issue.segmentIncomplete",
        params: { missing: missing.join("、") },
      });
    }
    return issues;
  }

  // ssh_session / email_delivery：每个用例恰好一个判定项，且每种判定至多一个用例。
  const used = new Map<string, number>();
  cases.forEach((item, ordinal) => {
    const value = mode === "ssh_session" ? item.sshCheck : item.emailCheck;
    if (value === "") {
      issues.push({
        ordinal,
        key:
          mode === "ssh_session"
            ? "authoring.execution.issue.sshCheckRequired"
            : "authoring.execution.issue.emailCheckRequired",
        params: { ordinal: ordinal + 1 },
      });
      return;
    }
    used.set(value, (used.get(value) ?? 0) + 1);
  });

  for (const [value, count] of used) {
    if (count <= 1) continue;
    if (mode === "ssh_session" && isSshCheck(value)) {
      issues.push({
        ordinal: null,
        key: "authoring.execution.issue.sshCheckDuplicate",
        params: { check: t(`authoring.execution.sshCheck.${value}`) },
      });
    } else if (mode === "email_delivery" && isEmailCheck(value)) {
      issues.push({
        ordinal: null,
        key: "authoring.execution.issue.emailCheckDuplicate",
        params: { check: t(`authoring.execution.emailCheck.${value}`) },
      });
    }
  }
  return issues;
}

function validateFileChecks(
  item: CaseForm,
  ordinal: number,
  issues: CaseExecutionIssue[],
): void {
  if (item.fileChecks.length > MAX_FILE_CHECKS_PER_CASE) {
    issues.push({
      ordinal,
      key: "authoring.execution.issue.fileCheckTooMany",
      params: { ordinal: ordinal + 1, max: MAX_FILE_CHECKS_PER_CASE },
    });
  }
  const seenPaths = new Set<string>();
  item.fileChecks.forEach((check, index) => {
    const position = index + 1;
    if (!isValidFixturePath(check.path) || check.path.length > FILE_CHECK_PATH_MAX_LENGTH) {
      issues.push({
        ordinal,
        key: "authoring.execution.issue.fileCheckPath",
        params: { ordinal: ordinal + 1, index: position },
      });
    } else if (seenPaths.has(check.path)) {
      issues.push({
        ordinal,
        key: "authoring.execution.issue.fileCheckDuplicate",
        params: { ordinal: ordinal + 1, path: check.path },
      });
    } else {
      seenPaths.add(check.path);
    }

    if (check.mode !== "" && !FILE_CHECK_MODE_PATTERN.test(check.mode)) {
      issues.push({
        ordinal,
        key: "authoring.execution.issue.fileCheckMode",
        params: { ordinal: ordinal + 1, index: position },
      });
    }

    const hasDigest = check.contentSha256 !== "";
    const hasMode = check.mode !== "";
    if (check.content !== "" && !hasDigest) {
      // 摘要由 WebCrypto 异步计算：内容刚改完就点保存时不能拿旧摘要冒充新内容。
      issues.push({
        ordinal,
        key: "authoring.execution.issue.fileCheckDigestPending",
        params: { ordinal: ordinal + 1, index: position },
      });
    } else if (!hasDigest && !hasMode) {
      issues.push({
        ordinal,
        key: "authoring.execution.issue.fileCheckEmpty",
        params: { ordinal: ordinal + 1, index: position },
      });
    }
  });
}

/** 文件校验的请求体形状：只发送摘要与权限位，期望内容原文**永不上传**。 */
function fileChecksPayload(checks: FileCheckForm[]): FileCheckInput[] {
  return checks.map((check) => ({
    path: check.path,
    ...(check.contentSha256 === "" ? {} : { content_sha256: check.contentSha256 }),
    ...(check.mode === "" ? {} : { mode: check.mode }),
  }));
}

/** 用例级执行声明在请求体里的形状（只含 `TestCaseInput` 的四个可选扩展字段）。 */
type CaseExecutionPayload = {
  segment?: number;
  file_checks?: FileCheckInput[];
  ssh_check?: SshCheck;
  email_check?: EmailCheck;
};

/**
 * 按模式取用例级执行字段。这里是"哪些字段会随哪个模式发送"的唯一定义：
 * 非 ssh 模式永远看不到 `ssh_check`，因此 SSH 配置（含口令）不可能被别的模式带出去。
 */
function executionCasePayload(
  item: CaseForm,
  mode: ExecutionMode,
): CaseExecutionPayload {
  if (mode === "single_run") {
    if (item.segmentScored) {
      const segment = intValue(item.segment);
      return segment === null || segment < 0 ? {} : { segment };
    }
    return item.fileChecks.length === 0
      ? {}
      : { file_checks: fileChecksPayload(item.fileChecks) };
  }
  if (mode === "per_case") {
    return item.fileChecks.length === 0
      ? {}
      : { file_checks: fileChecksPayload(item.fileChecks) };
  }
  if (mode === "ssh_session") {
    return {
      ...(item.sshCheck === "" ? {} : { ssh_check: item.sshCheck }),
      // SSH 模式不显示文件校验编辑器；但既有内容里的声明（写入校验允许）原样保留，避免丢失。
      ...(item.fileChecks.length === 0
        ? {}
        : { file_checks: fileChecksPayload(item.fileChecks) }),
    };
  }
  return item.emailCheck === "" ? {} : { email_check: item.emailCheck };
}

/** 用例（受保护草稿内容 / AI 候选之外的读回路径）→ 表单副本。 */
export function caseFormFrom(item: TestCaseInputWithExecution): CaseForm {
  return {
    name: item.name,
    weight: String(item.weight),
    visibleToStudent: item.visible_to_student,
    stdin: item.stdin ?? "",
    args: (item.args ?? []).join(" "),
    expectedStdout: item.expected_stdout ?? "",
    expectedStderr: item.expected_stderr ?? "",
    expectedExitCode:
      item.expected_exit_code === null || item.expected_exit_code === undefined
        ? ""
        : String(item.expected_exit_code),
    segmentScored: item.segment !== null && item.segment !== undefined,
    segment:
      item.segment === null || item.segment === undefined ? "" : String(item.segment),
    fileChecks: (item.file_checks ?? []).map((check) => ({
      path: check.path,
      mode: check.mode ?? "",
      // 期望内容原文不可读回：只把服务端算好的摘要带回表单；留空内容 = 沿用该摘要。
      content: "",
      contentSha256: check.content_sha256 ?? "",
    })),
    sshCheck: item.ssh_check ?? "",
    emailCheck: item.email_check ?? "",
  };
}

/**
 * 切换评分方式：只保留目标模式读取的配置，其余一律清空。
 *
 * 最要紧的一条是 SSH 口令：切到 `per_case`（或任何非 ssh 模式）后它必须从表单里消失，
 * 否则一个不读取它的请求里仍可能带着这段受保护内容。
 */
export function applyExecutionMode(form: DraftForm, next: ExecutionMode): DraftForm {
  const defaults = emptyDraftForm();
  return {
    ...form,
    executionMode: next,
    segmentDelimiter: next === "single_run" ? form.segmentDelimiter : "",
    emailAttempt: next === "email_delivery" ? form.emailAttempt : defaults.emailAttempt,
    emailSubjectTemplate: next === "email_delivery" ? form.emailSubjectTemplate : "",
    emailAttachmentTemplate: next === "email_delivery" ? form.emailAttachmentTemplate : "",
    sshPort: next === "ssh_session" ? form.sshPort : defaults.sshPort,
    sshUsername: next === "ssh_session" ? form.sshUsername : "",
    sshPassword: next === "ssh_session" ? form.sshPassword : "",
    sshLoginTimeout: next === "ssh_session" ? form.sshLoginTimeout : defaults.sshLoginTimeout,
    sshSessionTimeout:
      next === "ssh_session" ? form.sshSessionTimeout : defaults.sshSessionTimeout,
    cases: form.cases.map((item) => applyExecutionModeToCase(item, next)),
  };
}

function applyExecutionModeToCase(item: CaseForm, mode: ExecutionMode): CaseForm {
  return {
    ...item,
    segmentScored: mode === "single_run" ? item.segmentScored : false,
    segment: mode === "single_run" ? item.segment : "",
    // ssh/email 模式没有工作区文件判定，清掉以免"看不见的声明"被提交。
    fileChecks: mode === "ssh_session" || mode === "email_delivery" ? [] : item.fileChecks,
    sshCheck: mode === "ssh_session" ? item.sshCheck : "",
    emailCheck: mode === "email_delivery" ? item.emailCheck : "",
  };
}

function secondsText(value: number | undefined, fallback: string): string {
  return value === undefined ? fallback : String(value);
}

/**
 * 把读回的 `execution`（受保护草稿通道，或本地刚读到的规范化对象）填进表单。
 *
 * 未声明/不属于该模式的配置会被清空，而不是"留在表单里但假装没看见"：模式互斥的字段一旦
 * 残留，下一次保存就可能把它们带进一个不读取它们的请求。
 */
export function applyExecutionDeclaration(
  form: DraftForm,
  manifest: ExecutionManifestInput,
): DraftForm {
  const defaults = emptyDraftForm();
  const mode = manifest.mode;
  return {
    ...form,
    executionMode: mode,
    segmentDelimiter:
      mode === "single_run" && typeof manifest.segment_delimiter === "string"
        ? manifest.segment_delimiter
        : "",
    emailAttempt:
      mode === "email_delivery"
        ? String(manifest.email?.attempt ?? EMAIL_ATTEMPT_MIN)
        : defaults.emailAttempt,
    emailSubjectTemplate:
      mode === "email_delivery" ? manifest.email?.subject_template ?? "" : "",
    emailAttachmentTemplate:
      mode === "email_delivery" ? manifest.email?.required_attachment_template ?? "" : "",
    sshPort:
      mode === "ssh_session" && manifest.ssh ? String(manifest.ssh.port) : defaults.sshPort,
    sshUsername: mode === "ssh_session" ? manifest.ssh?.username ?? "" : "",
    sshPassword: mode === "ssh_session" ? manifest.ssh?.password ?? "" : "",
    sshLoginTimeout:
      mode === "ssh_session"
        ? secondsText(manifest.ssh?.login_timeout_seconds, defaults.sshLoginTimeout)
        : defaults.sshLoginTimeout,
    sshSessionTimeout:
      mode === "ssh_session"
        ? secondsText(manifest.ssh?.session_timeout_seconds, defaults.sshSessionTimeout)
        : defaults.sshSessionTimeout,
  };
}

/**
 * 受保护草稿内容 → 表单（参考答案、用例、执行声明）。
 *
 * 与 `draftFormFrom` 的分工：那个只填公开 DTO 里存在的字段；这里填的是作者通道才有的内容。
 * `execution` 在服务端总是规范化后的对象（缺省 `{mode:"per_case"}`），因此这里不需要猜
 * "服务端有没有声明过"。
 */
export function applyProtectedContent(form: DraftForm, content: QuestionDraftContent): DraftForm {
  const declared = applyExecutionDeclaration(form, readDraftExecution(content));
  return {
    ...declared,
    referenceAnswer: content.reference_answer ?? "",
    cases: content.cases.map((item) => caseFormFrom(readCaseInput(item))),
    // 现在拿到的是**真实的那份集合**，替换确认不再有意义。
    confirmCaseReplacement: true,
  };
}

export type DraftBuild =
  | { ok: true; input: QuestionDraftInput; tags: ParsedTags }
  | { ok: false; issues: DraftIssue[] };

/**
 * 表单 → `QuestionDraftInput`。校验与映射共用一次遍历：非法时返回问题清单，
 * 合法时返回**完整**请求体，因此不存在"校验通过但字段映射另一套逻辑"的漂移。
 *
 * `newVersion` 决定 `revision_note` 是否随请求发送：契约说该字段只在 `as_new_version=true`
 * 时有意义，就地修改草稿时带上它会给服务端一个没有语义的值。
 *
 * `editing` 决定"提交用例"是否需要一次显式确认：编辑时本页读不到服务端已有的用例集合，
 * 而提供的 `cases` 会**整体替换**它（CHG-0017）。新建时没有可替换的东西，因此不需要确认。
 */
export function buildDraftInput(
  form: DraftForm,
  options: { newVersion: boolean; editing: boolean },
): DraftBuild {
  const issues: DraftIssue[] = [];
  const parsedTags = parseTags(form.tags);

  if (form.title.trim() === "") issues.push({ key: "authoring.issue.title" });
  if (form.title.length > TITLE_MAX_LENGTH) issues.push({ key: "authoring.issue.titleTooLong" });
  if (form.stemMarkdown.trim() === "") issues.push({ key: "authoring.issue.stem" });

  if (parsedTags.tags.length > TAG_MAX_ITEMS) {
    issues.push({
      key: "authoring.issue.tagsTooMany",
      params: { count: parsedTags.tags.length, max: TAG_MAX_ITEMS },
    });
  }
  if (parsedTags.invalid.length > 0) {
    issues.push({
      key: "authoring.issue.tagShape",
      params: { tags: parsedTags.invalid.join(", ") },
    });
  }

  const maxFiles = intValue(form.maxFiles);
  if (maxFiles === null || maxFiles < 1 || maxFiles > MAX_FILES_LIMIT) {
    issues.push({ key: "authoring.issue.maxFiles", params: { max: MAX_FILES_LIMIT } });
  }
  const maxTotalBytes = intValue(form.maxTotalBytes);
  if (maxTotalBytes === null || maxTotalBytes < 1 || maxTotalBytes > MAX_TOTAL_BYTES_LIMIT) {
    issues.push({
      key: "authoring.issue.maxTotalBytes",
      params: { max: MAX_TOTAL_BYTES_LIMIT },
    });
  }

  const adapter = buildAdapter(form, issues);
  // 评分方式与用例级执行声明：与 B3 的写入校验同一套规则，非法时同样不发请求。
  const executionIssues = executionDeclarationIssues(form);
  issues.push(...executionIssues);
  const execution = executionIssues.length === 0 ? buildExecution(form) : null;
  // 零个用例 = **不声明**这个字段（服务端保留已有的集合），因此不走逐行校验。
  const cases =
    form.cases.length === 0 ? null : buildCases(form.cases, issues, form.executionMode);
  if (
    form.cases.length > 0 &&
    options.editing &&
    !form.confirmCaseReplacement
  ) {
    // 读不到现值却要整体替换：必须由人显式确认（否则"我加了一个用例"会静默丢掉别的用例）。
    issues.push({ key: "authoring.issue.casesReplaceUnconfirmed" });
  }
  if (options.newVersion && form.revisionNote.trim() === "") {
    // 已发布版本另存新版本时，版本说明是必填的：不填就不发请求，而不是让服务端替它编一个
    // 推导出来的说明（那会让"为什么有这一版"变成系统猜的）。
    issues.push({ key: "authoring.revisionNoteRequired" });
  }

  // 可空结果与 issues 是同一遍校验的两个投影：任一为空必然已经压入对应问题。
  // 一起判空让类型收窄成立，也保证"有问题就一定不发请求"。
  if (
    adapter === null ||
    execution === null ||
    maxFiles === null ||
    maxTotalBytes === null ||
    issues.length > 0
  ) {
    return { ok: false, issues };
  }

  // `reference_answer` 留空（或只有空白）⇒ **不发该字段**：契约（CHG-0017）里省略与空白
  // 都是"保留已存答案"，而发一个空字符串读起来像"把答案设成空"。
  const referenceAnswer = form.referenceAnswer.trim() === "" ? {} : {
    reference_answer: form.referenceAnswer,
  };

  return {
    ok: true,
    tags: parsedTags,
    input: attachExecution(
      {
        title: form.title,
        stem_markdown: form.stemMarkdown,
        language: form.language,
        adapter,
        ...referenceAnswer,
        // 同上：零个用例不发 `cases`，服务端保留原集合；非空则整体替换。
        ...(cases === null ? {} : { cases }),
        max_files: maxFiles,
        max_total_bytes: maxTotalBytes,
        tags: parsedTags.tags,
        ...(options.newVersion && form.revisionNote.trim() !== ""
          ? { revision_note: form.revisionNote.trim() }
          : {}),
      },
      execution,
    ),
  };
}

/**
 * 只够"建出一份草稿"的最小请求体。
 *
 * 图片上传的目标是 `/questions/{id}/assets`——**没有题目 id 就没有上传目标**。而 2.0 之后
 * 保存草稿只在最后一步，教师在题干页插图时还填不到"标准输出比较""用例分值"那些最后一步的
 * 项目。如果这时跑完整校验，老师只会看到一串与他当前动作无关的错误，图片也就永远插不进去。
 *
 * 契约 `QuestionDraftInput` 的必需字段只有 `title` / `stem_markdown` / `language` / `adapter`
 * （`adapter` 只要求 `kind`），受保护内容与用例都可以省略。因此这里只发这四个：
 * 先拿到题目 id 完成上传，之后在最后一步保存时再补齐其余内容（那是 PATCH）。
 */
export function buildDraftSkeleton(form: DraftForm): DraftBuild {
  const issues: DraftIssue[] = [];
  if (form.title.trim() === "") issues.push({ key: "authoring.issue.title" });
  if (form.stemMarkdown.trim() === "") issues.push({ key: "authoring.issue.stem" });
  if (issues.length > 0) return { ok: false, issues };
  return {
    ok: true,
    tags: parseTags(form.tags),
    input: {
      title: form.title,
      stem_markdown: form.stemMarkdown,
      language: form.language,
      adapter: { kind: form.adapterKind },
      // 这两个上限在契约里有默认值，但生成的类型把它们标成必填；取表单里合法的现值，
      // 万一教师在"运行配置"里填坏了就用契约默认值，不让插图被一个无关字段挡住。
      max_files: intValue(form.maxFiles) ?? 20,
      max_total_bytes: intValue(form.maxTotalBytes) ?? 1048576,
    },
  };
}

/** 适配器：按 `kind` 组装；夹具声明只使用**服务端返回**的摘要。 */
function buildAdapter(form: DraftForm, issues: DraftIssue[]): AdapterManifest | null {
  if (form.comparisonStdout === "") {
    issues.push({ key: "authoring.issue.comparisonStdout" });
  }

  const tolerance = floatValue(form.numericTolerance);
  // 容差只在「数值容差」模式下有意义：其他模式里那个输入框是禁用的，值也不进载荷，
  // 因此这里只在数值模式下校验它（否则一个残留的旧值会挡住一次与它无关的保存）。
  if (form.comparisonStdout === "numeric_tolerance" && tolerance === null) {
    issues.push({ key: "authoring.issue.numericTolerance" });
  }

  const expectExitCode = intValue(form.adapterExpectExitCode);
  if (form.adapterExpectExitCode.trim() !== "" && expectExitCode === null) {
    issues.push({ key: "authoring.issue.adapterExpectExitCode" });
  }

  if (form.adapterKind === "callable") {
    if (form.callableModule.trim() === "") issues.push({ key: "authoring.issue.callableModule" });
    if (form.callableAttribute.trim() === "") {
      issues.push({ key: "authoring.issue.callableAttribute" });
    }
    if (form.callableArgumentsJson.trim() !== "") {
      try {
        JSON.parse(form.callableArgumentsJson);
      } catch {
        issues.push({ key: "authoring.issue.callableArgumentsJson" });
      }
    }
  }

  // 命令行题型的三个全局项（命令参数 / 全局标准输入 / 期望退出码）允许全部留空：
  // 用例自带输入就能正常评测，因此这里不再要求"三者至少填一个"。

  const files = form.fixtures.map((fixture) => ({
    path: fixture.path,
    content_sha256: fixture.contentSha256,
    mount: fixture.mount,
  }));
  if (form.adapterKind === "file") {
    // 摘要只能来自上传结果；这里只校验"至少有一个 input 夹具已经上传"，
    // 因为 file 题型没有它就无法编译（B3 在编译期拒绝）。
    if (!files.some((entry) => entry.mount === "input")) {
      issues.push({ key: "authoring.issue.fileFixtureMissing" });
    }
  } else if (files.length > 0) {
    issues.push({ key: "authoring.issue.fixturesWrongAdapter" });
  }

  if (form.comparisonStdout === "") return null;

  const comparison: ComparisonPolicy = {
    stdout: form.comparisonStdout,
    ...(form.comparisonStderr === "" ? {} : { stderr: form.comparisonStderr }),
    ...(form.comparisonStdout === "numeric_tolerance" && tolerance !== null
      ? { numeric_tolerance: tolerance }
      : {}),
  };

  const argv = parseArgv(form.adapterArgs);

  return {
    kind: form.adapterKind,
    ...(argv.length > 0 ? { args: argv } : {}),
    ...(form.adapterStdin === "" ? {} : { stdin: form.adapterStdin }),
    ...(expectExitCode === null ? {} : { expect_exit_code: expectExitCode }),
    comparison,
    ...(form.adapterKind === "callable"
      ? {
          callable: {
            module: form.callableModule.trim(),
            attribute: form.callableAttribute.trim(),
            ...(form.callableArgumentsJson.trim() === ""
              ? {}
              : { arguments_json: form.callableArgumentsJson.trim() }),
          },
        }
      : {}),
    ...(files.length === 0 ? {} : { files }),
  };
}

/**
 * 执行声明 → 契约 `ExecutionManifest`。
 *
 * 只在 `executionDeclarationIssues` 为空时调用，因此这里不再制造第二套校验；但它只根据
 * **当前模式**读取配置：`per_case` 的返回对象里只有 `mode`，SSH 口令之类的受保护配置
 * 不可能跟着别的模式进请求。
 */
function buildExecution(form: DraftForm): ExecutionManifestInput | null {
  if (form.executionMode === "per_case") return { mode: "per_case" };

  if (form.executionMode === "single_run") {
    const delimiter = form.segmentDelimiter;
    // 留空 = 本题没有分段用例（整题都用 file_checks）：契约允许该字段为 null。
    return { mode: "single_run", segment_delimiter: delimiter === "" ? null : delimiter };
  }

  if (form.executionMode === "email_delivery") {
    const attempt = intValue(form.emailAttempt);
    if (attempt === null) return null;
    return {
      mode: "email_delivery",
      email: {
        attempt,
        // 契约里这两个模板有默认值；只有读回草稿时确实存在（非空）才原样带回，不做改写。
        ...(form.emailSubjectTemplate === ""
          ? {}
          : { subject_template: form.emailSubjectTemplate }),
        ...(form.emailAttachmentTemplate === ""
          ? {}
          : { required_attachment_template: form.emailAttachmentTemplate }),
      },
    };
  }

  const port = intValue(form.sshPort);
  if (port === null) return null;
  const loginTimeout = form.sshLoginTimeout.trim() === "" ? null : floatValue(form.sshLoginTimeout);
  const sessionTimeout =
    form.sshSessionTimeout.trim() === "" ? null : floatValue(form.sshSessionTimeout);
  return {
    mode: "ssh_session",
    ssh: {
      // host 是固定值而不是默认值：契约只接受 127.0.0.1。
      host: SSH_HOST,
      port,
      username: form.sshUsername,
      password: form.sshPassword,
      ...(loginTimeout === null ? {} : { login_timeout_seconds: loginTimeout }),
      ...(sessionTimeout === null ? {} : { session_timeout_seconds: sessionTimeout }),
    },
  };
}

/**
 * 用例：`ordinal` 取数组下标（0 基），权重与退出码必须能解析成数字。
 * 只在**确实要提交用例**时调用（零个用例走的是"不声明该字段"那条路，见 `buildDraftInput`）。
 *
 * 用例级执行字段同时在这里校验并映射：两者用同一个 `mode` 遍历，因此不会出现
 * "界面按 A 模式校验、请求按 B 模式发送"。
 */
function buildCases(
  forms: CaseForm[],
  issues: DraftIssue[],
  mode: ExecutionMode,
): TestCaseInputWithExecution[] {
  // 整组/逐用例的执行声明问题进入同一份清单：保存被它们挡住，界面也用同一份清单展示。
  for (const issue of caseExecutionIssues(forms, mode)) {
    issues.push({ key: issue.key, params: issue.params });
  }

  const cases: TestCaseInputWithExecution[] = [];
  forms.forEach((item, ordinal) => {
    // 用例名是选填：留空时按序号自动生成「用例1、用例2…」，教师不必每次起名。
    const weight = floatValue(item.weight);
    if (weight === null || weight <= 0) {
      issues.push({ key: "authoring.issue.caseWeight", params: { ordinal: ordinal + 1 } });
    }
    const expectedExitCode = intValue(item.expectedExitCode);
    if (item.expectedExitCode.trim() !== "" && expectedExitCode === null) {
      issues.push({ key: "authoring.issue.caseExitCode", params: { ordinal: ordinal + 1 } });
    }
    const argv = parseArgv(item.args);

    cases.push({
      name: item.name.trim() === "" ? autoCaseName(ordinal) : item.name.trim(),
      ordinal,
      weight: weight ?? 1,
      visible_to_student: item.visibleToStudent,
      ...(item.stdin === "" ? {} : { stdin: item.stdin }),
      ...(argv.length === 0 ? {} : { args: argv }),
      expected_stdout: textOrNull(item.expectedStdout),
      expected_stderr: textOrNull(item.expectedStderr),
      ...(expectedExitCode === null ? {} : { expected_exit_code: expectedExitCode }),
      ...executionCasePayload(item, mode),
    });
  });

  return cases;
}

/**
 * 自动用例名：`ordinal` 是 0 基下标，显示名从 1 开始。
 *
 * 同一个函数既用于"留空保存时的名字"，也用于卡片标题与占位提示，三处不会漂。
 */
export function autoCaseName(ordinal: number): string {
  return `用例${ordinal + 1}`;
}

/** 受控题干图片的 Markdown 地址：只用**服务端返回的** question_id 与 asset_id 拼。 */
export function assetMarkdown(questionId: string, assetId: string, alt: string): string {
  const safeAlt = alt.replace(/[[\]]/g, "").trim();
  return `![${safeAlt}](/api/v1/questions/${questionId}/assets/${assetId})`;
}

/** 把一段 Markdown 图片插入题干（追加到末尾并保证换行）。 */
export function insertIntoStem(stem: string, snippet: string): string {
  const trimmed = stem.replace(/\s+$/, "");
  return trimmed === "" ? `${snippet}\n` : `${trimmed}\n\n${snippet}\n`;
}

/**
 * 表单是否相对"载入时的快照"有未保存的编辑。
 *
 * 存在的理由只有一个：AI 候选（以及任何编辑）**不会**被自动持久化，刷新恢复的是服务端原始候选，
 * 不是教师的修改。离开前必须能问出"要不要丢掉这些编辑"，所以比较必须只看表单本身。
 */
export function isDirty(current: DraftForm, baseline: DraftForm): boolean {
  return JSON.stringify(current) !== JSON.stringify(baseline);
}

/* ------------------------------------------------------------ 面向教师的字段位置名 */

const FIELD_LABELS: Record<string, string> = {
  title: "标题",
  stem_markdown: "题干",
  tags: "标签",
  cases: "测试用例",
  fixtures: "测试文件",
  examples: "示例",
  notes: "说明",
  difficulty: "难度",
  adapter: "题型设置",
  "adapter.args": "命令参数",
  "adapter.stdin": "标准输入",
  "adapter.expect_exit_code": "期望退出码",
  "adapter.comparison.stdout": "输出比较方式",
  "adapter.comparison.stderr": "错误输出比较方式",
  "adapter.comparison.numeric_tolerance": "数值容差",
  "adapter.callable.module": "函数所在文件",
  "adapter.callable.attribute": "函数名",
  "adapter.callable.arguments_json": "函数参数",
};

const CASE_SUB_LABELS: Record<string, string> = {
  name: "的名称",
  weight: "的分值",
  stdin: "的输入",
  args: "的命令参数",
  expected_stdout: "的期望输出",
  expected_stderr: "的错误输出",
  expected_exit_code: "的退出码",
  visible_to_student: "的可见性",
};

const FIXTURE_SUB_LABELS: Record<string, string> = {
  path: "的位置",
  mount: "的用途",
  content_text: "的内容",
  content_base64: "的内容",
  content_sha256: "的校验信息",
};

/**
 * 把 `cases[2].weight`、`stem_markdown` 这类内部定位翻成教师读得懂的位置说明。
 *
 * 用途只有一个：AI 草稿的"需要你检查的地方"要指向具体控件，但**不能把内部字段路径贴到界面上**。
 * 未知字段统一回落到「本页内容」——绝不回显原始路径：那既是内部命名，也读不懂。
 */
export function fieldLabel(field: string): string {
  const direct = FIELD_LABELS[field];
  if (direct) return direct;

  const indexed = /^([a-z_]+)\[(\d+)\](?:\.([A-Za-z_]+))?$/.exec(field);
  if (indexed) {
    const [, group, index, sub] = indexed;
    const ordinal = Number(index) + 1;
    if (group === "cases") {
      return sub && CASE_SUB_LABELS[sub]
        ? `第 ${ordinal} 个用例${CASE_SUB_LABELS[sub]}`
        : `第 ${ordinal} 个用例`;
    }
    if (group === "tags") return `第 ${ordinal} 个标签`;
    if (group === "fixtures") {
      return sub && FIXTURE_SUB_LABELS[sub]
        ? `第 ${ordinal} 个文件${FIXTURE_SUB_LABELS[sub]}`
        : `第 ${ordinal} 个文件`;
    }
    if (group === "examples") return `第 ${ordinal} 个示例`;
  }

  return "本页内容";
}
