import type { MessageKey, MessageParams } from "../../i18n";
import type {
  AdapterManifest,
  Language,
  QuestionDraftInput,
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
 * - **不可读回**：`reference_answer`、`cases[]`（契约明确标为 PROTECTED，公开 DTO 里不存在），
 *   以及适配器的 `args`/`stdin`/`expect_exit_code`/`comparison`/`callable`（公开 DTO 只给
 *   `adapter_kind`，`RevisionDiff` 也只给布尔与长度，读不出这些值）。
 *
 * **省略即保留（CHG-0017，契约 0.15.0）**：两个受保护字段都是**可选**的，留空表示
 * "我对这个字段不作声明"，**不是**"清空它"：
 * - `cases` 省略 ⇒ 服务端保留该版本已有的用例；提供 ⇒ **整体替换**那个集合；
 * - `reference_answer` 省略**或空白** ⇒ 保留已存的答案。
 *
 * 因此本页对这两个字段的做法是：**留空就不发**（而不是发一个空值）。`required` 一字未改，
 * 仍是 `[title, stem_markdown, language, adapter]`——那四个字段**必须每次都发**，
 * 不要试图只发变动的字段（省略语义只对上面两个受保护字段成立，给别的字段加省略语义是另一个契约变更）。
 */

/** 契约 `QuestionDraftInput.tags[].pattern` 与长度/数量上限（`b3.yaml`）。 */
const TAG_PATTERN = /^[a-z0-9][a-z0-9-]*$/;
export const TAG_MAX_ITEMS = 20;
export const TAG_MAX_LENGTH = 40;

/** 契约 `max_files` / `max_total_bytes` 的上限。 */
export const MAX_FILES_LIMIT = 100;
export const MAX_TOTAL_BYTES_LIMIT = 10485760;

export type AdapterKind = AdapterManifest["kind"];
export type ComparisonPolicy = NonNullable<AdapterManifest["comparison"]>;
export type StdoutComparison = ComparisonPolicy["stdout"];
export type StderrComparison = NonNullable<ComparisonPolicy["stderr"]>;

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
 * 本页可选择的适配器类型。
 *
 * `file` 不在其中：`AdapterManifest.files[]` 只带 `{path, content_sha256, mount}`，字节必须经
 * `POST /api/v1/question-versions/{id}/fixtures` 上传，而该端点是 `internal` + `serviceToken`，
 * 摘要由服务端计算。B1 既拿不到服务令牌，也不会替服务端声明一个摘要。
 * 已存在的 `file` 版本仍会被读出来（`adapter_kind` 可读），但本页拒绝保存它以免静默改写。
 */
export const SELECTABLE_ADAPTER_KINDS: readonly AdapterKind[] = ["stdin", "callable"] as const;

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
    confirmCaseReplacement: false,
  };
}

/**
 * 从公开 DTO 预填。**只有公开 DTO 里真的存在的字段会被填**：参考答案、用例与适配器细节不会被
 * "顺手留空"以外的任何方式对待——它们的空是有含义的（"本页读不到服务端的现值"），
 * 不是加载失败，**也不是"服务端没有"**。
 *
 * 编辑路径刻意以**零个用例**开场：本页读不到该版本已有的用例集合，凭空渲染一行空用例会让
 * "我不知道有几个"看起来像"它没有用例"，而提交那一行还会把整个集合替换掉。
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
    confirmCaseReplacement: false,
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

function intValue(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  return Number.parseInt(trimmed, 10);
}

function floatValue(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^\d+(?:\.\d+)?$/.test(trimmed)) return null;
  return Number.parseFloat(trimmed);
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
  if (form.title.length > 300) issues.push({ key: "authoring.issue.titleTooLong" });
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

/** 适配器：按 `kind` 组装；`file` 被明确拒绝而不是猜一个摘要出来。 */
function buildAdapter(form: DraftForm, issues: DraftIssue[]): AdapterManifest | null {
  if (form.adapterKind === "file") {
    issues.push({ key: "authoring.issue.adapterFileLocked" });
    return null;
  }

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
