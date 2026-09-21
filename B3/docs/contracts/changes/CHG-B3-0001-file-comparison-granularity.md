# CHG-B3-0001 file adapter 的"产出文件比较粒度"部分不可表达

- **提出方**：B3（WP-B3-B）
- **日期**：2026-09-15
- **状态**：**已裁决（MAIN，2026-09-18）：文本差异本期不在范围 → 收窄 RQ 措辞**（B3 侧实现无需改动）
- **规程**：`../../../MAIN/docs/contracts/change-protocol.md`

## 1. 观察到的差异

| 侧 | 内容 |
|---|---|
| 需求侧（权威） | `rq-catalog.md` `RQ-B-3-50` 验收 1：manifest 能声明"产出文件比较策略（**存在性** / **内容摘要** / **文本差异**）"；B3 `acceptance.md` 3-50-1 同义复述。 |
| 契约侧（权威） | `MAIN/contracts/schemas/evaluation-package.schema.json`：`adapter` 段 `additionalProperties: false`；产出文件只能表达为 `adapter.files[]` 的 `{path, content_sha256, mount}`（`mount: "input" \| "expected_output"`）；`adapter.comparison` 只允许 `stdout` / `stderr` / `numeric_tolerance`。 |

结论：**内容摘要**比较可表达（`mount=expected_output` + `content_sha256`）；**存在性**比较可由"必须匹配 expected_output 清单"承载；**文本差异**（行级 diff / 容差 / 忽略空白等）在冻结 schema 中**没有**承载位置，且 `adapter.comparison` 不允许新增键。

## 2. B3 当前实现（契约内，不自行扩展）

1. `kind=file` 的 manifest 声明 `files[]`：`mount=input` 为输入 fixture，`mount=expected_output` 为期望产出文件，二者都必须带 `content_sha256`（构建期由 B3 从内容计算，不接受调用方传入的摘要）。
2. 预期产出文件的**存在性 + 内容摘要一致**即"通过"判据（摘要由 B2 runner 比对，B3 只负责绑定摘要）。
3. **文本差异比较不支持**。B3 不发明 `comparison.file_mode` 之类的键（那会违反 `additionalProperties: false`）。

## 3. 请 MAIN 裁决的问题

1. "文本差异"是否为本期必须交付的能力？
   - 若**是**：需要 P3 契约变更，在 `adapter.comparison` 增加文件比较策略键（例如 `files: {mode: "existence"|"content_digest"|"text_diff", ...}`），B3 再实现。
   - 若**否**：请更新 `rq-catalog.md` `RQ-B-3-50` 验收 1 的措辞，使"比较策略"限定为存在性/内容摘要。
2. 官方示例 `MAIN/contracts/examples/evaluation-package.json` 未包含 `mount=expected_output` 的条目，是否需要补一个 file adapter 示例以便 B2/B3 双方联调对齐？

## 4. B3 侧的痕迹

- 阻塞范围内的实现与测试：`src/autograder_b3/compile/adapters.py`（`FileAdapterCompiler`）、`tests/test_adapters.py::test_file_adapter_requires_expected_output_digest`。
- 需求状态：`RQ-B-3-50` 在 `../requirements/traceability.md` 记为 `已完成`，并在同表"已知限制"列显式标注本缺口（不把文本差异写成已实现）。

---

## 5. MAIN 裁决（2026-09-18）

### 问题 1：文本差异是否为本期必须交付 → **不是；收窄需求措辞**

**理由。**

1. **它需要的不是"多一个枚举值"，而是一整套判定语义。** 行级 diff 至少要定义：比较的单位（行/字节/
   归一化后的 token）、空白与行尾处理、编码与 BOM、缺失文件与多余文件各自算什么、以及"差异多大算
   不通过"。这些语义一旦进入契约就会被评分结果依赖，而**评分必须确定性可重放**（ADR-0005）——
   在没有明确口径的前提下引入 diff，等于把"是否通过"交给一组未定义的默认值。
2. **本期已有的两种粒度覆盖了课程作业的实际判据。** 存在性（必须产出清单里的文件）+ 内容摘要
   （逐字节一致）已经能表达"输出文件正确"这一判据，且两者的判定都是**无参数、可复算**的。
   文本差异是"更宽松的相等"，属于**质量改进**而非**能力缺口**。
3. **不对称性。** 收窄 RQ 措辞的成本是一行文档；引入 diff 的成本是新契约键 + B2 runner 与比较器
   实现 + B3 编译器 + 两侧一致性测试 + 一组必须冻结的默认口径。在没有任何作业要求它的前提下，
   后者不划算。

**已实施的改动（需求侧）。**

- `MAIN/docs/requirements/rq-catalog.md` 的 `RQ-B-3-50` 验收 1：把"产出文件比较策略（存在性 / 内容
  摘要 / **文本差异**）"改为"**存在性 / 内容摘要**"，并注明"文本差异不在本期范围（见 CHG-B3-0001
  裁决）；如后续需要，须先定义比较单位、空白处理与差异容忍口径，再走 P3 契约变更"。
- B3 的 `docs/requirements/traceability.md` 同条 RQ 的"已知限制"列同步为同一口径。

**契约改动：无。** `evaluation-package.schema.json` 的 `adapter.comparison` 保持三个键；
B3 的 `FileAdapterCompiler` 与 `test_file_adapter_requires_expected_output_digest` 无需改动。
因此**摘要未因本项变化**。B3 现在可以如实写"契约支持的比较策略均已实现"，而不是"部分实现"。

**若将来要做（不在本期）**：需要 P3 变更，在 `adapter.comparison` 下增加文件比较策略键
（例如 `files: {mode: "existence"|"content_digest"|"text_diff", ...}`），并且**先**冻结上面第 1 条
列出的全部默认口径，再改 B2/B3。

### 问题 2：是否补一个 `mount=expected_output` 的官方示例 → **是，已补**

`MAIN/contracts/examples/evaluation-package.json` 现在包含 file adapter 的 `mount: input` 与
`mount: expected_output` 各一条，两者都带 `content_sha256`，并通过归属 schema 的实例校验
（`checks.py` 的 `check_contract_examples()`）。这样 B2 与 B3 有一个可对齐的官方形状，不必各自从
schema 反推。示例只含路径与摘要，不含参考答案或隐藏用例内容。
