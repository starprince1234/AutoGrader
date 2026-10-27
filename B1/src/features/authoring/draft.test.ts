import { describe, expect, it } from "vitest";
import { questionPublic, questionVersionPublic } from "../../testing/contractFixtures";
import type { QuestionDraftProposal } from "../../api/types";
import {
  MAX_FILES_LIMIT,
  TAG_MAX_ITEMS,
  applyProposal,
  assetMarkdown,
  buildDraftInput,
  draftFormFrom,
  emptyDraftForm,
  insertIntoStem,
  isDirty,
  isValidFixturePath,
  normalizeTag,
  parseTags,
  type DraftForm,
} from "./draft";

/**
 * 表单 → `QuestionDraftInput` 的纯映射与校验。
 *
 * 这里固定的是**契约形状**而不是实现细节：标签的归一化规则、用例 `ordinal` 的来源、
 * 适配器按 `kind` 的组装方式，以及 CHG-0017 的省略语义（留空 = 不声明该字段，服务端保留已存值）。
 */

type BuildOptions = { newVersion: boolean; editing: boolean };

function validForm(overrides: Partial<DraftForm> = {}): DraftForm {
  return {
    ...emptyDraftForm(),
    title: "统计文件行数",
    stemMarkdown: "# 统计文件行数\n\n请实现 `wc -l` 的最小版本。",
    tags: "unix, text-processing",
    referenceAnswer: "print(len(open('input.txt').readlines()))",
    comparisonStdout: "exact",
    // 契约要求 `stdin` 题型至少声明 args / stdin / expect_exit_code 之一（B3 的适配器校验），
    // 因此一份"合法表单"必须带上其中之一个，否则它本来就该被拒绝。
    adapterExpectExitCode: "0",
    confirmCaseReplacement: true,
    cases: [
      {
        ...emptyDraftForm().cases[0],
        name: "基本用例",
        stdin: "a\nb\n",
        expectedStdout: "2\n",
        expectedExitCode: "0",
      },
    ],
    ...overrides,
  };
}

function built(form: DraftForm, options: BuildOptions = { newVersion: false, editing: false }) {
  const result = buildDraftInput(form, options);
  if (!result.ok) {
    throw new Error(`expected a valid draft, got issues: ${result.issues.map((i) => i.key).join(", ")}`);
  }
  return result.input;
}

function issueKeys(form: DraftForm, options: BuildOptions = { newVersion: false, editing: false }) {
  const result = buildDraftInput(form, options);
  if (result.ok) throw new Error("expected validation issues, got a valid draft");
  return result.issues.map((issue) => issue.key);
}

describe("标签的契约形状（CHG-0008）", () => {
  it("归一化到小写 + 短横线，并丢掉开头的非法字符", () => {
    expect(normalizeTag("Process Scheduling")).toBe("process-scheduling");
    expect(normalizeTag("  OS_2026  ")).toBe("os-2026");
    expect(normalizeTag("--Deadlock")).toBe("deadlock");
    expect(normalizeTag("进程调度")).toBe("");
  });

  it("去重、保留顺序，并把仍不合法的片段单列出来（不静默丢弃）", () => {
    const parsed = parseTags("unix, Unix, 进程调度, os-2026");
    expect(parsed.tags).toEqual(["unix", "os-2026"]);
    expect(parsed.invalid).toEqual(["进程调度"]);
  });
});

describe("表单 → QuestionDraftInput", () => {
  it("按契约组装：查询参数之外的字段全部来自请求体，用例 ordinal 取数组下标", () => {
    const input = built(validForm());
    expect(input.title).toBe("统计文件行数");
    expect(input.language).toBe("python");
    expect(input.tags).toEqual(["unix", "text-processing"]);
    expect(input.adapter.kind).toBe("stdin");
    expect(input.adapter.comparison).toEqual({ stdout: "exact" });
    expect(input.cases).toHaveLength(1);
    expect(input.cases?.[0].ordinal).toBe(0);
    expect(input.cases?.[0].visible_to_student).toBe(false);
    expect(input.cases?.[0].expected_stdout).toBe("2\n");
    expect(input.cases?.[0].expected_exit_code).toBe(0);
    expect(input.max_files).toBe(20);
    expect(input.max_total_bytes).toBe(1048576);
  });

  it("revision_note 只在 as_new_version 的路径上发送，且那时必填", () => {
    const form = validForm({ revisionNote: "例题的截止时间写错了" });
    expect(built(form, { newVersion: false, editing: true }).revision_note).toBeUndefined();
    expect(built(form, { newVersion: true, editing: true }).revision_note).toBe(
      "例题的截止时间写错了",
    );
    // 另存新版本却不填说明：不发请求（服务端会替它推导一个，但那等于系统在解释"为什么有这一版"）。
    expect(
      issueKeys(validForm({ revisionNote: "" }), { newVersion: true, editing: true }),
    ).toContain("authoring.revisionNoteRequired");
  });

  it("期望输出留空提交 null（不声明），不是空字符串", () => {
    const form = validForm();
    form.cases[0].expectedStdout = "";
    form.cases[0].expectedStderr = "";
    const input = built(form);
    expect(input.cases?.[0].expected_stdout).toBeNull();
    expect(input.cases?.[0].expected_stderr).toBeNull();
  });

  it("kind=callable 时组装 callable 块，参数向量按空格切分", () => {
    const input = built(
      validForm({
        adapterKind: "callable",
        callableModule: "solution",
        callableAttribute: "run",
        callableArgumentsJson: '{"n": 3}',
        adapterArgs: "--verbose out.txt",
      }),
    );
    expect(input.adapter.kind).toBe("callable");
    expect(input.adapter.args).toEqual(["--verbose", "out.txt"]);
    expect(input.adapter.callable).toEqual({
      module: "solution",
      attribute: "run",
      arguments_json: '{"n": 3}',
    });
  });

  it("数值容差只在选择该策略时随请求发送", () => {
    const withTolerance = built(
      validForm({ comparisonStdout: "numeric_tolerance", numericTolerance: "0.01" }),
    );
    expect(withTolerance.adapter.comparison).toEqual({
      stdout: "numeric_tolerance",
      numeric_tolerance: 0.01,
    });
    const exact = built(validForm({ comparisonStdout: "exact", numericTolerance: "" }));
    expect(exact.adapter.comparison).toEqual({ stdout: "exact" });
  });
});

describe("受保护字段的省略语义（CHG-0017）", () => {
  it("两个受保护字段留空 ⇒ **不发这两个键**（省略 = 服务端保留已存值）", () => {
    const input = built(validForm({ referenceAnswer: "", cases: [] }));
    // 关键：不是发空串、不是发空数组，而是整个键不出现。
    expect(input).not.toHaveProperty("reference_answer");
    expect(input).not.toHaveProperty("cases");
    // required 里那四个字段**必须**每次都发（省略语义不适用于它们）。
    expect(input.title).toBe("统计文件行数");
    expect(input.stem_markdown).not.toBe("");
    expect(input.language).toBe("python");
    expect(input.adapter.kind).toBe("stdin");
  });

  it("只有空白的参考答案同样视为「不声明」，不发该键", () => {
    const input = built(validForm({ referenceAnswer: "   \n  " }));
    expect(input).not.toHaveProperty("reference_answer");
  });

  it("填了就发：参考答案原样、用例整体替换", () => {
    const input = built(validForm());
    expect(input.reference_answer).toBe("print(len(open('input.txt').readlines()))");
    expect(input.cases).toHaveLength(1);
    expect(input.cases?.[0].name).toBe("基本用例");
  });

  it("编辑时提交用例必须显式确认（否则会静默替换掉读不到的旧集合）", () => {
    const unconfirmed = validForm({ confirmCaseReplacement: false });
    expect(issueKeys(unconfirmed, { newVersion: false, editing: true })).toContain(
      "authoring.issue.casesReplaceUnconfirmed",
    );
    // 新建时没有可替换的集合，因此不需要确认（能直接构建出请求体就是证明）。
    expect(
      built(unconfirmed, { newVersion: false, editing: false }).cases,
    ).toHaveLength(1);
    // 确认之后放行。
    const confirmed = built(validForm({ confirmCaseReplacement: true }), {
      newVersion: false,
      editing: true,
    });
    expect(confirmed.cases).toHaveLength(1);
  });

  it("编辑时零个用例仍然合法：那只是「不声明该字段」", () => {
    const input = built(validForm({ cases: [], confirmCaseReplacement: false }), {
      newVersion: false,
      editing: true,
    });
    expect(input).not.toHaveProperty("cases");
  });
});

describe("校验（不通过就不发请求）", () => {
  it("比较策略没有默认值：不声明就是非法", () => {
    expect(issueKeys(validForm({ comparisonStdout: "" }))).toContain(
      "authoring.issue.comparisonStdout",
    );
    expect(
      issueKeys(validForm({ comparisonStdout: "numeric_tolerance", numericTolerance: "" })),
    ).toContain("authoring.issue.numericTolerance");
  });

  it("kind=file：没有输入夹具时拒绝保存，摘要只能来自服务端上传结果", () => {
    // 夹具挂在版本上、摘要由 B3 计算，所以"没上传就保存"必然是一份编译不出 package 的 manifest。
    expect(issueKeys(validForm({ adapterKind: "file" }))).toContain(
      "authoring.issue.fileFixtureMissing",
    );

    const uploaded = built(
      validForm({
        adapterKind: "file",
        fixtures: [
          {
            fixtureId: "11111111-1111-4111-8111-111111111111",
            path: "data/input.txt",
            mount: "input",
            // 这个值来自 uploadVersionFixture 的响应；本页从不自己算摘要。
            contentSha256: "a".repeat(64),
            sizeBytes: 12,
            fileName: "input.txt",
          },
          {
            fixtureId: "22222222-2222-4222-8222-222222222222",
            path: "data/expected.txt",
            mount: "expected_output",
            contentSha256: "b".repeat(64),
            sizeBytes: 12,
            fileName: "expected.txt",
          },
        ],
      }),
    );
    expect(uploaded.adapter.kind).toBe("file");
    expect(uploaded.adapter.files).toEqual([
      { path: "data/input.txt", content_sha256: "a".repeat(64), mount: "input" },
      { path: "data/expected.txt", content_sha256: "b".repeat(64), mount: "expected_output" },
    ]);
  });

  it("只有 file 题型可以带夹具：其他题型带上夹具会被明确拒绝", () => {
    const form = validForm({
      fixtures: [
        {
          fixtureId: "11111111-1111-4111-8111-111111111111",
          path: "data/input.txt",
          mount: "input",
          contentSha256: "a".repeat(64),
          sizeBytes: 1,
          fileName: "input.txt",
        },
      ],
    });
    expect(issueKeys(form)).toContain("authoring.issue.fixturesWrongAdapter");
  });

  it("契约上限：标签数、上限字节数与标题长度", () => {
    const tooMany = Array.from({ length: TAG_MAX_ITEMS + 1 }, (_v, index) => `tag-${index}`).join(
      ",",
    );
    expect(issueKeys(validForm({ tags: tooMany }))).toContain("authoring.issue.tagsTooMany");
    expect(issueKeys(validForm({ maxFiles: String(MAX_FILES_LIMIT + 1) }))).toContain(
      "authoring.issue.maxFiles",
    );
    expect(issueKeys(validForm({ maxTotalBytes: "0" }))).toContain(
      "authoring.issue.maxTotalBytes",
    );
    expect(issueKeys(validForm({ title: "x".repeat(301) }))).toContain(
      "authoring.issue.titleTooLong",
    );
  });

  it("用例名与权重按序号定位问题", () => {
    const form = validForm();
    form.cases.push({ ...emptyDraftForm().cases[0], name: "", weight: "0" });
    const keys = issueKeys(form);
    expect(keys).toContain("authoring.issue.caseName");
    expect(keys).toContain("authoring.issue.caseWeight");
  });

  it("stdin 题型三者全空时明确报错（B3 会 422，本地先说清楚）", () => {
    expect(
      issueKeys(validForm({ adapterExpectExitCode: "", adapterArgs: "", adapterStdin: "" })),
    ).toContain("authoring.issue.adapterStdinEmpty");
  });
});

describe("夹具路径（与服务端同一条规则）", () => {
  it("接受工作区相对路径", () => {
    for (const path of ["data/input.txt", "a.txt", "deep/dir/file.bin", ".hidden"]) {
      expect(isValidFixturePath(path)).toBe(true);
    }
  });

  it("拒绝绝对路径、盘符、.. 段、反斜杠与首尾空格", () => {
    for (const path of [
      "/etc/passwd",
      "\\windows\\system32",
      "C:/temp/a.txt",
      "~/a.txt",
      "data/../secret.txt",
      "data//a.txt",
      "data/",
      " data/a.txt",
      "a\\b.txt",
    ]) {
      expect(isValidFixturePath(path)).toBe(false);
    }
  });
});

describe("AI 候选应用到表单（AI-FLOW-01A）", () => {
  const proposal: QuestionDraftProposal = {
    title: "行数统计",
    stem_markdown: "统计标准输入的行数。",
    language: "python",
    adapter_kind: "stdin",
    tags: ["unix", "text-processing"],
    reference_answer: "import sys\nprint(len(sys.stdin.read().splitlines()))\n",
    adapter: {
      expect_exit_code: 0,
      comparison: { stdout: "ignore_trailing_whitespace", stderr: "ignored" },
    },
    cases: [
      {
        name: "三行",
        ordinal: 0,
        weight: 2,
        visible_to_student: true,
        stdin: "a\nb\nc\n",
        expected_stdout: "3\n",
        expected_exit_code: 0,
      },
      // 服务端判定权重不可用时是 null：这里必须留空，不能填一个数字。
      { name: "权重待填", ordinal: 1, weight: null, visible_to_student: false },
    ],
  };

  it("只填表单里存在的字段，并保留教师已选的约束", () => {
    const applied = applyProposal(validForm({ language: "bash" }), proposal);
    expect(applied.title).toBe("行数统计");
    expect(applied.stemMarkdown).toBe("统计标准输入的行数。");
    expect(applied.tags).toBe("unix, text-processing");
    expect(applied.referenceAnswer).toContain("splitlines");
    expect(applied.adapterKind).toBe("stdin");
    expect(applied.adapterExpectExitCode).toBe("0");
    expect(applied.comparisonStdout).toBe("ignore_trailing_whitespace");
    // 候选里的 language 不会覆盖教师已选的约束（两者在服务端本就相同，这里证明来源）。
    expect(applied.language).toBe("bash");
  });

  it("权重为 null 的候选用例留空，由逐字段校验提示教师填写", () => {
    const applied = applyProposal(validForm(), proposal);
    expect(applied.cases).toHaveLength(2);
    expect(applied.cases[1].weight).toBe("");
    const keys = issueKeys(applied, { newVersion: false, editing: false });
    expect(keys).toContain("authoring.issue.caseWeight");
  });

  it("候选没给比较策略时取最严格的默认值，而不是留空让保存静默失败", () => {
    const applied = applyProposal(validForm({ comparisonStdout: "" }), {
      ...proposal,
      adapter: undefined,
    });
    expect(applied.comparisonStdout).toBe("exact");
  });

  it("应用候选不丢弃已上传的夹具（摘要只来自服务端）", () => {
    const withFixture = validForm({
      fixtures: [
        {
          fixtureId: "11111111-1111-4111-8111-111111111111",
          path: "data/input.txt",
          mount: "input",
          contentSha256: "a".repeat(64),
          sizeBytes: 4,
          fileName: "input.txt",
        },
      ],
    });
    const applied = applyProposal(withFixture, proposal);
    expect(applied.fixtures).toHaveLength(1);
    expect(applied.fixtures[0].contentSha256).toBe("a".repeat(64));
  });
});

describe("题干编辑辅助", () => {
  it("插入图片用受控资源地址，并按需补空行", () => {
    const snippet = assetMarkdown("q-1", "a-1", "一张图");
    expect(snippet).toBe("![一张图](/api/v1/questions/q-1/assets/a-1)");
    expect(insertIntoStem("", snippet)).toBe(`${snippet}\n`);
    expect(insertIntoStem("正文", snippet)).toBe(`正文\n\n${snippet}\n`);
  });

  it("alt 文本里的方括号被去掉，避免破坏 Markdown 结构", () => {
    expect(assetMarkdown("q", "a", "a[b]c")).toBe("![abc](/api/v1/questions/q/assets/a)");
  });

  it("isDirty 只看表单内容：未保存的编辑必须能被识别", () => {
    const baseline = validForm();
    expect(isDirty(baseline, baseline)).toBe(false);
    expect(isDirty({ ...baseline, title: "改过的标题" }, baseline)).toBe(true);
  });
});

describe("从公开 DTO 预填", () => {
  it("只预填能读回的字段；受保护字段与适配器细节保持为空，且**以零个用例开场**", () => {
    const form = draftFormFrom(
      questionPublic({ tags: ["unix", "scheduling"] }),
      questionVersionPublic({ adapter_kind: "stdin", max_files: 5, max_total_bytes: 2048 }),
    );
    expect(form.title).toBe(questionPublic().title);
    expect(form.stemMarkdown).toBe(questionPublic().stem_markdown);
    expect(form.tags).toBe("unix, scheduling");
    expect(form.adapterKind).toBe("stdin");
    expect(form.maxFiles).toBe("5");
    expect(form.maxTotalBytes).toBe("2048");
    // 公开 DTO 里没有的字段：空表示"本页读不到服务端的现值"，不是"服务端没有"。
    expect(form.referenceAnswer).toBe("");
    expect(form.adapterArgs).toBe("");
    expect(form.comparisonStdout).toBe("");
    // 编辑路径**不**凭空渲染一行空用例：那会让"我不知道有几个"看起来像"它没有用例"，
    // 而提交那一行还会把整个集合替换掉。零个用例 = 这次不声明该字段。
    expect(form.cases).toHaveLength(0);
    expect(form.confirmCaseReplacement).toBe(false);
  });

  it("新建路径给一行空用例作为邀请（与编辑路径不同）", () => {
    const form = emptyDraftForm();
    expect(form.cases).toHaveLength(1);
    expect(form.referenceAnswer).toBe("");
  });
});
