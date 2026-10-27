import type { MessageKey, MessageParams } from "../../i18n";
import type {
  AdapterManifest,
  Language,
  QuestionDraftInput,
  QuestionDraftProposal,
  QuestionPublic,
  QuestionVersionPublic,
  TestCaseInput,
} from "../../api/types";

/**
 * 出题表单的**纯模型**：表单状态 ↔ 契约 `QuestionDraftInput` 的转换与校验。
 *
 * 为什么单独一层：请求体的每个字段都由这里决定，而"哪些值非法"必须能脱离 React 被单测。
 * 组件只负责渲染与事件，不参与字段映射。
 *
 * **可读回与不可读回的字段**（这是本页最重要的一条约束）：
 * - 可读回：`title`/`stem_markdown`/`language`/`tags`（`QuestionPublic`），
 *   `adapter_kind`/`max_files`/`max_total_bytes`（`QuestionVersionPublic`）；
 * - **不可读回**：`reference_answer`、`cases[]`（契约明确标为 PROTECTED，公开 DTO 里不存在）、
 *   适配器的 `args`/`stdin`/`expect_exit_code`/`comparison`/`callable`，以及**夹具清单**
 *   （`adapter.files[]` 只有摘要，且公开 DTO 不返回它）。
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

export type CaseForm = {
  name: string;
  weight: string;
  visibleToStudent: boolean;
  stdin: string;
  args: string;
  expectedStdout: string;
  expectedStderr: string;
  expectedExitCode: string;
};

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
    weight: "1",
    visibleToStudent: false,
    stdin: "",
    args: "",
    expectedStdout: "",
    expectedStderr: "",
    expectedExitCode: "",
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
    adapterExpectExitCode: "",
    callableModule: "",
    callableAttribute: "",
    callableArgumentsJson: "",
    comparisonStdout: "",
    comparisonStderr: "",
    numericTolerance: "",
    maxFiles: "20",
    maxTotalBytes: "1048576",
    revisionNote: "",
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
  const comparisonStdout = suggested?.comparison?.stdout ?? "exact";
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
  // 零个用例 = **不声明**这个字段（服务端保留已有的集合），因此不走逐行校验。
  const cases = form.cases.length === 0 ? null : buildCases(form.cases, issues);
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
  if (adapter === null || maxFiles === null || maxTotalBytes === null || issues.length > 0) {
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
    input: {
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
  };
}

/** 适配器：按 `kind` 组装；夹具声明只使用**服务端返回**的摘要。 */
function buildAdapter(form: DraftForm, issues: DraftIssue[]): AdapterManifest | null {
  if (form.comparisonStdout === "") {
    issues.push({ key: "authoring.issue.comparisonStdout" });
  }

  const tolerance = floatValue(form.numericTolerance);
  if (form.comparisonStdout === "numeric_tolerance" && tolerance === null) {
    issues.push({ key: "authoring.issue.numericTolerance" });
  } else if (form.numericTolerance.trim() !== "" && tolerance === null) {
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

  // `stdin` 题型在契约里必须声明 args / stdin / expect_exit_code 之一：三者全空时 B3 会 422，
  // 这里提前说清楚，省掉一次往返。
  if (
    form.adapterKind === "stdin" &&
    parseArgv(form.adapterArgs).length === 0 &&
    form.adapterStdin === "" &&
    expectExitCode === null
  ) {
    issues.push({ key: "authoring.issue.adapterStdinEmpty" });
  }

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
 * 用例：`ordinal` 取数组下标（0 基），权重与退出码必须能解析成数字。
 * 只在**确实要提交用例**时调用（零个用例走的是"不声明该字段"那条路，见 `buildDraftInput`）。
 */
function buildCases(forms: CaseForm[], issues: DraftIssue[]): TestCaseInput[] {
  const cases: TestCaseInput[] = [];
  forms.forEach((item, ordinal) => {
    if (item.name.trim() === "") {
      issues.push({ key: "authoring.issue.caseName", params: { ordinal: ordinal + 1 } });
    }
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
      name: item.name,
      ordinal,
      weight: weight ?? 1,
      visible_to_student: item.visibleToStudent,
      ...(item.stdin === "" ? {} : { stdin: item.stdin }),
      ...(argv.length === 0 ? {} : { args: argv }),
      expected_stdout: textOrNull(item.expectedStdout),
      expected_stderr: textOrNull(item.expectedStderr),
      ...(expectedExitCode === null ? {} : { expected_exit_code: expectedExitCode }),
    });
  });

  return cases;
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
