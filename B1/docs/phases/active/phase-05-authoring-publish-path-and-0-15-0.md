# Phase 05 出题发布路径打通（CHG-0019/0023 / ADR-0015）与契约 0.15.0 批（CHG-0017/0021/0022）

本文件记录本轮**真实执行的命令、原始读数、未验证项与契约缺口**。状态词只用
`已完成` / `部分` / `阻塞` / `未验证`；未跑过的命令不写成通过。

## 目标与范围

契约从 `0.14.0` 升到 **`0.15.0`**（同批包含 `CHG-0019`/`CHG-0021`/`CHG-0022`，并在修订后并入
`CHG-0023`；摘要 `9ae4ebfe…` → `511f27ad…`，版本号仍是 `0.15.0`，因为该批尚未被任何模块实现消费）。
本轮处理四件事：

1. **出题发布路径**：`CHG-0008` 的发布门禁要求一份与 `content_digest` 绑定的参考解答验证存证，
   而它的前提是"执行者 ≠ 记录者"——B1 不能自证、也不能持有 `serviceToken`，于是**出题流程卡在发布**
   （B1 此前上报、MAIN 从未裁决的那一条）。`ADR-0015` 裁决为 **B2 执行、B3 记录、教师一键触发**，
   契约是 `POST /api/v1/reference-verifications`（`CHG-0019`；`CHG-0023` 补上 B2 取参考解答的内部通道）。
2. **隔离区语义修复**（`CHG-0021`）：`resolved` 省略 = **不过滤（全部）**，"只看未归属"必须显式传
   `?resolved=false`；且只有 `not_uniquely_matched` / `ambiguous_match` 可以人工归属。
3. **标签形态约束补齐**（`CHG-0022`）：`QuestionTagCoverage.tags[].tag` 与 `QuestionPublic.tags[]`
   现在都声明了 `pattern`/长度（后者还有 `maxItems: 20`）。
4. **草稿省略语义**（`CHG-0017`，批内落地）：`cases` 省略 ⇒ 保留该版本的用例集合；
   `reference_answer` 省略或空白 ⇒ 保留已存答案。`required` 未变，仍是
   `[title, stem_markdown, language, adapter]`。

范围外：`MAIN/contracts/**`（未修改）、`B1/module.yaml`、`B1/contracts.lock`、`B1/package.json`、
`B1/pnpm-lock.yaml`。未引入任何 UI 组件库或状态管理库。

## 完成条件

1. 出题页有真实的"验证并发布"入口，三态（通过 / 未通过 / 需要 Linux 沙箱）**互不混淆**，
   并且**不做自动重试、不做自动发布、不推断原因**。
2. `resolved` 始终显式发送；不可归属的记录**不显示**归属按钮。
3. 覆盖度面板里那段已被契约作废的防御分支有一个明确取舍（本轮：**删除**）与理由。
4. 出题表单按 `CHG-0017` 放开：两个受保护字段留空即**不发该键**，其余四个必需字段照发。
5. 门禁全绿：`generate-api-types --check` / `eslint` / `tsc -b` / `vitest` / `vite build`。
6. 文档如实区分"对着契约构建"与"真联调过"：本轮**没有任何真机读数**。

## 实际变更（落点）

| 能力 | 文件:行 | 说明 |
|---|---|---|
| 契约类型 | `src/api/types.ts:193`/`:198` | `ReferenceVerification` / 逐用例 / 失败类别 / 请求体，全部来自生成类型 |
| 验证与发布 mutation | `src/features/authoring/useAuthoring.ts:163`/`:193` | 两者都是 **mutation**：每次验证都会**起一次沙箱作业**，不能被重渲染或窗口聚焦自动重发；发布成功后失效版本与题目缓存 |
| 验证面板 | `src/features/authoring/ReferenceVerificationPanel.tsx:95` | 外层只做"已发布 → 不渲染"，内层（`:119`）持有 hooks；`key={question_version_id}` 让**换版本 = 重新挂载**（结论不跨版本，靠结构而不是清理代码） |
| 环境结论判定 | `src/features/authoring/ReferenceVerificationPanel.tsx:34` | 全部失败且失败类别都是 `infrastructure_error` ⇒ 环境，而不是"未通过" |
| 摘要绑定 | `src/features/authoring/ReferenceVerificationPanel.tsx:42` | 结论的 `content_digest` 与当前版本不一致 ⇒ 结论失效；任一侧拿不到摘要 ⇒ **不属于**当前版本（不放行） |
| 存证未记录 | `src/features/authoring/ReferenceVerificationPanel.tsx:270` | `attestation_id: null`（B3 没记下）⇒ 全通过也**不可发布**，并说明执行者/记录者分离的意义 |
| 503 / 环境态 | `src/features/authoring/ReferenceVerificationPanel.tsx:211` | "验证需要 Linux 沙箱环境"；文案不含"成功"也不含"验证失败"（有反向断言） |
| 发布动作 | `src/features/authoring/ReferenceVerificationPanel.tsx:314` | 可发布 = 全通过 ∧ 存证已记录 ∧ 摘要未变；发布是**独立的显式动作** |
| 被取代的旧组件 | ~~`src/features/authoring/PublishRequirement.tsx`~~ | **已删除**（连同 3 条 `authoring.publish.*` 词条）：它渲染的是"发布没有 UI 路径"，那句话现在不成立。无兼容层、无双分支 |
| 适配器写必填无读出口 | `src/features/authoring/AuthoringPage.tsx:371` | 常驻横幅：保存是**重新声明整个 manifest**，而非继承已存那份（见"契约缺口 10"） |
| 用例整体替换确认 | `src/features/authoring/AuthoringPage.tsx:308`、`src/features/authoring/draft.ts:106` | 编辑时提交用例会整体替换读不到的旧集合 ⇒ 必须显式勾选，否则不发请求 |
| 省略语义 | `src/features/authoring/draft.ts:258` | 留空 ⇒ 请求体里**不出现**该键；`:177` 编辑路径以零个用例开场（新建仍给一行邀请） |
| 可归属集合 | `src/features/intake/useQuarantines.ts` 的 `ReasonCode` / `ATTRIBUTABLE_REASON_CODES` / `isAttributableReason` | 原因码类型**从生成类型派生**（`IntakeQuarantineView["reason_code"]`）：今天等于 `string`，`CHG-0027` 的 `enum` 落地后自动变成字面量联合，词表核对回到编译期；可归属的那**一对值**按裁决继续手写（行为谓词） |
| 归属按钮显隐 | `src/features/intake/QuarantinePage.tsx:323` | 不可归属 → 显示"不可归属（原因码）"而不是让教师点了才拿 422 |
| 覆盖度标签按钮 | `src/features/questions/QuestionCoveragePanel.tsx:91` | 删除了"这个标签可能不是筛选取值"的分支（见下"取舍"） |
| 契约夹具 | `src/testing/contractFixtures.ts:620` | `referenceVerification()`（默认=全通过+存证已记录）；隔离区夹具默认原因码改为可归属的 `not_uniquely_matched` |
| 文案 | `src/i18n/index.ts:358`（`authoring.verify.*`）、`:717`（`quarantine.notAttributable`） | 删除了 `authoring.issue.referenceAnswer` / `casesRequired` / `protectedRequired` 四条已作废的文案 |

**未改动**：`MAIN/contracts/**`、`B1/module.yaml`、`B1/contracts.lock`、`B1/package.json` 与锁文件。

## CHG-0022 那处取舍：**删除**防御分支（明确选一个）

契约 `0.15.0` 之后 `QuestionTagCoverage.tags[].tag` 声明了 `pattern: '^[a-z0-9][a-z0-9-]*$'`，
与 `listQuestions.tags` 完全一致，因此"这个标签可能不是列表筛选取值"那段分支**按其自身逻辑不可达**。

**选择：删除。** 理由（写进了代码注释）：
1. 契约现在是该字段的唯一权威；保留一个不信任刚拿到的契约的分支，等于把"字段定义只在
   `MAIN/contracts/**` 里"这条规则打折。
2. 删它**不会**制造静默失败：万一服务端真的返回违反契约的标签，值会被列表页**既有的**标签校验
   归入 `invalid`，并出现在它自己的"以下标签不符合契约形状"横幅里——那条路径本来就在，
   不需要覆盖度面板再兜一层。
3. 反过来的代价是真实的：留着它，每个标签都多一个"可能不可用"的状态，而我们刚刚从契约里
   读到了它一定可用。

## 出题表单按 CHG-0017 放开（这一处改的是**行为收紧的反面**，需要写清）

此前本页对两个受保护字段采取的是"必须重新声明"的硬拦（契约当时**没有**说明省略语义，B1 不赌）。
`CHG-0017` 把语义写进契约后，硬拦失去依据，本页改为：

- `reference_answer` 留空（或只有空白）⇒ 请求体里**不发** `reference_answer`（`{}` 展开），
  服务端保留已存答案。不发空串：空串读起来像"把答案设成空"。
- `cases` 零个 ⇒ 请求体里**不发** `cases`。非空则逐行校验后**整体替换**。
- `title`/`stem_markdown`/`language`/`adapter` 四个必需字段**照发**（省略语义不适用于它们；
  "只发变动的字段"需要给它们也加省略语义，那是另一个契约变更）。
- **编辑路径以零个用例开场**：本页读不到服务端那份集合，凭空渲染一行空用例会让"我不知道有几个"
  看起来像"它没有用例"，而提交那一行还会把整个集合替换掉。
- 因此编辑时提交用例需要一次**显式确认**（`authoring.issues.casesReplaceUnconfirmed`）。
  这不是新增需求，而是"客户端无法核对却要执行破坏性写入"时的最低要求——
  与名单页"拿不到 `updated_at` 就禁止保存"是同一条纪律的弱化版（那里能禁止，这里禁止会让功能不可用）。

## 门禁（本批最终代码上的原始输出）

```text
$ node scripts/generate-api-types.mjs --check（第一次，重生成之前）
API type drift detected in: src\api\generated\common.ts, src\api\generated\b2.ts, src\api\generated\b3.ts, src\api\generated\b4.ts
Run `pnpm api:generate` and commit the result.
check_before_exit=1        ← 预期的失败：契约已升到 0.15.0

$ node scripts/generate-api-types.mjs
generated src\api\generated\common.ts
generated src\api\generated\b2.ts
generated src\api\generated\b3.ts
generated src\api\generated\b4.ts
gen_exit=0

# 期间 MAIN 又落了两次契约文本（CHG-0021 与 CHG-0024），--check 再次报漂移：
#   API type drift detected in: src\api\generated\b2.ts, src\api\generated\b3.ts
# 再次重生成后，最终读数如下（digest 链：9ae4ebfe… → 511f27ad… → 29271ee2…，版本号始终 0.15.0）：

$ node scripts/generate-api-types.mjs --check
API types match MAIN/contracts.
apicheck_exit=0

$ node node_modules/eslint/bin/eslint.js . --max-warnings 0
eslint_exit=0

$ node node_modules/typescript/bin/tsc -b --pretty false
tsc_exit=0

$ node node_modules/vitest/vitest.mjs run
 Test Files  32 passed (32)
      Tests  262 passed (262)
   Start at  18:48:30
   Duration  31.87s (environment 73%, setup 18%, tests 4%, import 3%, transform 2%)
vitest_exit=0

$ node node_modules/vite/bin/vite.js build
dist/index.html                                  1.36 kB │ gzip:   0.82 kB
dist/assets/index-Q6_L646q.css                  16.63 kB │ gzip:   3.93 kB
dist/assets/editor-CbtiQhOu.css                146.37 kB │ gzip:  23.01 kB
dist/assets/index-CRxKPVIg.js                  583.91 kB │ gzip: 176.51 kB
dist/assets/index-C1rdNg7P.js                1,134.53 kB │ gzip: 381.47 kB
dist/assets/editor.main-D3C2oac9.js          3,772.66 kB │ gzip: 973.88 kB
(!) Some chunks are larger than 500 kB after minification. Consider:
✓ built in 39.17s
vite_exit=0
```

（最后一次运行是在"裁决回写"之后：唯一改动是把原因码的类型改为从生成类型派生（`ReasonCode`），
它是**纯类型改动**——构建产物的哈希 `index-CRxKPVIg.js` 与前一次运行逐字节一致，可核对。）

（`eslint`/`tsc` 无任何输出且退出码为 0；`vite build` 的 chunk 体积警告是既有的 Monaco
`editor.main`，不是本轮引入。）

构建产物侧的核对（`dist/assets/index-CRxKPVIg.js` 逐串计数，不是从源码推断）：
`/api/v1/reference-verifications` 1 次、`验证需要 Linux 沙箱环境` 1 次、`不可归属` 1 次、
`adapter-not-readable` 1 次。

- 测试规模：**32 个文件 / 262 个用例**（上一阶段为 31 文件 / 240 用例），净增 **22** 个用例。

## 新增/改写的测试（净增 22 个用例）

| 文件 | 用例数 | 关键断言 |
|---|---|---|
| `src/features/authoring/ReferenceVerificationPanel.test.tsx`（新） | 15 | 未验证前零请求且请求体精确等于 `{question_version_id}`；一次点击只发一次（不自动重试）；**全通过 + 存证已记录 → 出现发布按钮，且发布请求数为 0（不自动发布）**；**全通过但 `attestation_id: null` → 不可发布**并说明原因；未通过 → 逐用例 `failure_category` 与"哪些用例没过"；**全 `infrastructure_error` → "验证需要 Linux 沙箱环境"，横幅文案不含"成功"也不含"验证失败"，且不出现"未通过"结论**；503 → 同一条环境结论 + 服务端说明；混合失败 → 不整体归因为环境；摘要不一致 → 结论失效不可发布；版本无 `content_digest` → 不放行；发布 422 → 原样展示；**同一实例换版本 → 上一次结论消失**（Harness 保持 Provider 单例，避免用 `rerender` 假通过）；已发布版本 → 面板不渲染 |
| `src/features/authoring/draft.test.ts`（改写/新增 5） | 18 | 两个受保护字段留空 ⇒ `not.toHaveProperty("reference_answer"/"cases")`；只有空白同样不发；填了就发且用例整体替换；**编辑时未确认替换 ⇒ `casesReplaceUnconfirmed` 且不发请求**；新建不需要确认；编辑零用例合法；`draftFormFrom` 以**零个用例**开场而 `emptyDraftForm` 给一行邀请 |
| `src/features/intake/QuarantinePage.test.tsx`（新增 2） | 14 | **每一次列表请求都必须带 `resolved`**（反向断言，因为省略现在等于"全部"）；不可归属原因码（`archive_bomb`）**没有**归属按钮并显示原因与 422；已归属与不可归属必须能被分开（夹具默认码改为可归属的 `not_uniquely_matched`） |
| `src/features/authoring/AuthoringPage.test.tsx`（改写 2） | 12 | 验证入口与门禁说明可见、未验证前无发布动作；**受保护字段留空 ⇒ 请求体里不出现那两个键**，而 `title`/`stem_markdown`/`language`/`adapter` 照发；编辑路径必须先"加一行用例 + 勾选整体替换确认"才发 PATCH |
| `src/features/questions/QuestionCoveragePanel.test.tsx`（改写 1） | 13 | 每个标签都可点（`queryAllByTestId(/^coverage-tag-static-/)` 为空），点它把契约给的原值交给列表筛选 |

生产代码**没有**引用 `src/testing/**`。

## 未验证项（诚实边界，**不得**读成已验证）

| 能力 | 证据到哪为止 | 原因 |
|---|---|---|
| 「验证并发布」（`verifyReferenceAnswer`） | 生成类型 + 契约夹具 + 组件测试（15 用例） | **对着已声明契约构建、未经真实 B2 验证**：B2 侧实现未完成，本轮**未对运行中的 host 发过任何请求**。页面上的证据边界横幅常驻 |
| `publishQuestionVersion` 的真实成功路径 | 契约 + 夹具（200 与 422 两条分支） | 同上；且真实成功还依赖 B3 侧的存证与 `CHG-0017` 落地 |
| 隔离区 `?resolved=false` 的真实读数 | 契约（`0.15.0` 的 `resolved` 描述已写明省略 = 不过滤）+ 组件测试（请求参数反向断言） | 本批**未对运行中的 B2 发过请求**；契约文本在批内两度变动（见"契约缺口"），最终版本已落地 |
| 可归属集合的机器可读性 | 契约描述里的分组表 + B1 手写的一对值 | `reason_code` 的类型仍是 `string`（无 `enum`、无 `x-` 扩展），因此"哪些可归属"没有机器可读形式，B1 只能重复声明这一对值（缺口 11） |
| 分支版本不继承 fixture（B3 上报） | **无界面读数：B1 触达不到该路径** | `kind=file` 在 B1 本来就不可保存（夹具字节只能经 internal + `serviceToken` 上传），因此 B1 无法对声明了 `adapter.files` 的版本"先分支再发布"；B1 能保存的 `stdin`/`callable` 没有 `files` 声明。若某版本在 UI 之外声明了 files，B1 会拒绝保存它（`authoring.issue.adapterFileLocked`），因此也分支不了 |
| 适配器 manifest 的保留 | **B1 是重新构造的**，见缺口 10 | 写必填 + 无读出口 |
| `CHG-0024`（`published_at` 对草稿为 `null`） | 生成类型 | B1 的**生产代码从不读 `published_at`**（全仓检索：只有测试夹具设置它），因此这处放宽对 B1 没有代码影响；测试夹具继续显式给出时间戳 |

## 契约缺口 / 需要 MAIN 表态项（**未自行裁决**）

1. **缺口 8 已关闭**：`QuestionTagCoverage.tags[].tag` 与 `QuestionPublic.tags[]` 的形态约束由
   `CHG-0022` 补齐，B1 因此删除了对应的防御分支（见上文取舍）。
2. **缺口 9 已关闭（批内由 MAIN 补齐）**：开工时 `CHG-0021` 已生效、`CHANGELOG.md` 也把四处列进
   "变更文件"，但 `b2.yaml` 里**一处都找不到**（`resolved` 的旧散文、`resolveIntakeQuarantine` 的
   不可判定正文、`reason_code` 的开放描述、`detail.maxLength` 仍是 500）。B1 当时按 CHG 正文实现并
   登记为缺口 9；**随后 MAIN 在同一批内把这些文本落进了 `b2.yaml`**（这也解释了 `--check` 在本轮
   两次报漂移），因此最终版本已一致，缺口关闭。落点可核对：`listIntakeQuarantines` 的 `resolved`
   描述写明"省略 = 不过滤（全部）"与理由；`reason_code` 描述给出 19 项分组表并标出可归属的两项；
   `resolveIntakeQuarantine` 写清"只补身份 + 可归属集合 + 记录一次（重复 `409`）+ 不建提交"。
3. **缺口 10（已裁决，排 `0.16.0`）**：`adapter` 写必填但**没有读出口**——
   `QuestionVersionPublic` 只暴露 `adapter_kind`/`max_files`/`max_total_bytes`，`RevisionDiff` 只给
   布尔与长度。含义：任何不自己保存 manifest 的客户端都改不了这道题的其它字段（B1 因此会在保存时
   **重新声明**整个 manifest，已由界面常驻说明）。**MAIN 已裁决（`CHG-0025`）**：新增"写形状的读形状"
   `GET /api/v1/questions/{question_id}/draft-input`（课程写权限，返回与 `QuestionDraftInput` 同形、
   **减去** `cases`/`reference_answer`），并在同一 CHG 中**否决了"客户端自缓存"**。因此 B1 在本批
   **保持**界面说明这一现状、不升级成缓存；`0.16.0` 类型再生成后再改为从该端点预填。
   （`CHG-0025` 正文把这条缺口的真实后果记为**数据损坏**而非"脚本不方便"：前端测试发现不了它，
   因为前端正好是那个会重新构造的客户端。）
4. **缺口 11（已裁决，本批收口）**：`IntakeQuarantineView.reason_code` 的类型仍是 `string`——
   词表与"可归属"写在 `description` 的表格里。**MAIN 的裁决是 `CHG-0027`（排 `0.16.0`），分两半**：
   - **响应字段加 19 项 `enum`**（同 `CHG-0022` 那一族：实现已对、契约少写）。B1 本批已把原因码的类型
     改为**从生成类型派生**（`src/features/intake/useQuarantines.ts` 的
     `export type ReasonCode = IntakeQuarantineView["reason_code"]`）：今天它是 `string`，`0.16.0`
     再生成后自动变成字面量联合类型，那时那一对可归属值会在**编译期**被核对——**B1 不再复制词表**，
     只保留那两个行为上可归属的值。
   - **可归属的那一对值继续手写，且属明确接受**：它是**行为谓词**而不是字段形状。做成
     `x-attributable-reason-codes` 会得到一份**没有任何工具核对的"机器可读"副本**（权威在 B2 的
     Python 元组里，`check_shared_vocabularies()` 够不着它）；**"假的机器可读比散文更危险——散文会让
     人去读实现，假机器可读会让人信任它"**。将来若要机器可读，正确机制是**加一道门禁**（AST 读
     `B2/.../intake/quarantine.py` 的 `ATTRIBUTABLE_REASON_CODES` 再与契约比对 = 两份副本 + 一个核对者），
     `CHG-0027` 记为选项但**不引入**。B1 因此**不**把它改成任何"半机器可读"形式。
   - **查询参数 `reason_code` 刻意不加 `enum`**：那会把"用不存在的码筛选"从**返回空页**变成 **`422`**，
     属行为变更而非补声明、需要 B2 改实现 → 已记为**待决**。B1 不依赖该参数的任何校验语义。
5. **两处页面级推断**（沿用上一批）：覆盖度的 `published_only` 由 B1 显式发送，以便与同页列表共用
   同一作用域（契约已声明默认值，因此这不是依赖未声明的默认行为）；`reference_answer` 留空时
   B1 选择**不发该键**而不是发空串（契约允许两者，B1 选了不会读成"清空"的那一种）。
6. **`0.16.0` 已落地（本阶段末尾）**：`CHG-0026`（参考解答的 `409` 语义）、`CHG-0027`
   （`reason_code` 的 19 项 `enum`）、`CHG-0028`（评测包与存证对**草稿**开放）已并入
   `0.16.0`（摘要 `372cfcab…`），B1 已按新版本重新生成类型且门禁全绿。
   `CHG-0027` 让 `ReasonCode` 从生成类型派生的那一手**真的开始生效**（它现在是字面量联合类型，
   那对可归属值受编译期核对）；`CHG-0026` 只影响 `GET /question-versions/{id}/reference-answer`
   的 `409` 语义，那是 `serviceToken` 内部端点，**B1 永不消费**。
   **B1 唯一的待办**：`CHG-0025` 的 `draft-input` 读出口（**未随 `0.16.0` 落地**）一旦可用，
   就接入它以替换掉"适配器字段必须重新填"的成因（见缺口 10）。

## 验收后修正：真实浏览器发现的"假话横幅"与过宽的 `503` 结论（2026-09-20）

MAIN 在真实浏览器（宿主 `18000` + 真实 B3 + 真实题库）里跑通了覆盖度视图，并发现**同屏常驻**的
`coverage-declared-contract` 横幅写着"B3 尚未实现…没有对着运行中的 B3 跑过"，而面板**正在渲染 B3 的
实时计数**。这不是功能错，而是界面对用户说了不成立的话——本项目最看重的那一类问题。

### 1. 根因：静态文案断言另一个模块的实现进度

那条横幅当初是对的（写它时 B3 确实未实现），但它是**无条件静态文案**、**没有过期机制**，
于是代码一落地就变成假话。同族问题**不止一处**：B1 为四个"契约已声明"页面都挂了同类横幅。

**先探测真实路由是否存在**（无令牌 `curl`，只读；按各模块的校验优先级，`401`/`405` = 路由存在）：

| 探测 | 读数 | 结论 |
|---|---|---|
| `GET /api/v1/questions/coverage` | `422` | B3 路由存在（缺必需 `course_id` → 请求校验优先） |
| `GET /api/v1/intake/quarantines` | `401` | B2 路由存在（鉴权优先） |
| `GET /api/v1/evaluation-runs/quality-summary` | `401` | 路由存在 |
| `GET /api/v1/evaluation-runs/{id}/appeal-pack` | `401` | 路由存在 |
| `GET /api/v1/reference-verifications` | `405` | 路由存在（POST-only） |

⇒ **四条横幅全部是假话**，不止覆盖度那一条。

### 2. 处置（明确选择：**删除 + 数据驱动**，不是改成另一句静态断言）

- **删除**四处常驻横幅（`coverage-declared-contract`、`declared-contract-quarantine`、
  `declared-contract-appeal`、`declared-contract-quality`）与两条 i18n 词条
  （`common.declaredContract` / `common.declaredContractB3`）。
- **端点不可用改由数据驱动**：只有服务端**真的**返回 `404` 时才出现一条信息态，
  措辞是"本部署的这次请求取不到这个端点"，**不再提任何模块的实现进度**（testid 也从
  `coverage-not-implemented` 改名为 `coverage-endpoint-absent`，让名字本身不再断言进度）。
- **不改成"B3 已实现"**：那种新静态断言会以同样的方式再次过期。**关于"对方实现到哪一步"的断言
  不属于界面**；证据边界写在 `docs/` 里。
- 测试侧把四处旧断言改成**反向断言**（`queryByTestId(...)` 为 `null`），并新增一条用例专门断言
  面板里不出现"尚未实现 / 未实现 / 契约夹具"字样——**用测试钉住"界面不做进度断言"这条规则**。

### 3. 真实浏览器复验（Playwright，`http://127.0.0.1:18000/app`）

**两次都做了**：第一次在文案修正后（产物 `index-BcGBkSL5.js`，页头显示"契约 0.15.0"），
第二次在 `0.16.0` 类型再生成 + 最终构建后（产物 `index-CBI1o-qO.js`，页头"契约 0.16.0"）——
两次读数逐项相同，因此下面这份证据对应的是**最终交付的产物**。

选课程 `CS-OS-2026 · 操作系统（Unix 作业）` 后的逐项读数：

```text
bundle                  = /app/assets/index-CBI1o-qO.js      （最终产物）
header                  = 契约 0.16.0
coverage-total-value    = 102
coverage-tagged-value   = 4
coverage-untagged-value = 98
tag rows                = file-systems 3, virtual-memory 3, unix 1   （同数并列按 tag 升序，前端未重排）
identity                = "合计校验：已标注 4 + 未标注 98 = 102，与 `total_questions` 一致。契约里可校验的恒等式只有这一条。"
hasDeclaredContractBanner  = false      ← 假话横幅不再出现
hasEndpointAbsentBanner    = false      ← 端点可用时，数据驱动的那条也不出现
panel text 含"尚未实现"    = false
```

与 MAIN 的独立 SQL（`tags=[('file-systems',3),('virtual-memory',3),('unix',1)]`）逐项一致。其余页面：

| 页面 | 浏览器读数 |
|---|---|
| `/app/intake/quarantines` | 渲染"当前筛选下没有隔离记录"（空列表，**不是**错误）⇒ B2 该端点可用；无横幅、无"尚未实现" |
| `/app/quality` | 渲染筛选面板与"请先填写题目版本 ID"（无版本不发请求）；无横幅、无"尚未实现" |
| `/app/questions/<id>/edit` | 验证面板正常渲染（按钮 + 门禁说明 + `content_digest`） |
| 证据包面板 | **本轮未在浏览器里打开**（它嵌在成绩下钻里，且报告缺失时不渲染）；只有组件测试 + 路由探测证据 |

### 4. `503` 的结论**过宽**：已改为中性标题 + 原样 detail

`governance.md` 把 `503` 定义为"依赖不可用"，它同时覆盖**两种**原因：① 本平台没有沙箱；
② **某个上游依赖调用失败**（MAIN 的真实读数正是这一种：标题说"需要 Linux 沙箱环境"，而
`detail` 写着 `B3 request failed: TimeoutError`）。从状态码**无法**区分两者。

- 标题改为中性的 **"这次验证没有完成：平台依赖不可用"**，并新增一条说明：
  **本页不从状态码推断是哪一类依赖**（"没有 Linux 沙箱"只是其中一种可能）。
- **保留"服务端说明：{detail}"那一行**（`data-testid="reference-verify-environment-detail"`）——
  它是这条信息诚实的原因，MAIN 明确要求不要为了排版删掉它。
- 逐用例全 `infrastructure_error` 的那条 200 分支同样改为中性标题（`infrastructure_error`
  也不足以断定缺的是哪一类依赖）。
- "一个是从状态码推出的结论、一个是原始证据"这一点写进了注释：本次修的是**结论不得超出证据**，
  与第 1 条的"假话"性质不同，两者不被当成同一件事。
- **本轮无法取到真实的 `503` 读数**：现在对草稿调用该端点得到的是 `422`（见下）而不是 `503`，
  因此这条只有组件测试（夹具 `503` + `B3 request failed: TimeoutError` 原文）作为证据。

### 5. 真实浏览器发现的**新问题**：这条链在真实环境里走不通（已登记缺口 12）

对一道**草稿**（`CHG-0017 probe: title after the correction`，question_id `90001a8f-…`）点击
"请求平台验证参考答案"，约 2.5 s 内真实 B2 返回 **`422`**：

```text
the version has no evaluation package yet (B3 compiles one only for a published version),
so its reference answer cannot be verified against the cases
```

面板把它渲染成通用错误态并**原样带出 detail + `request_id`**（没有改写成任何结论，这一处不是假话）。
但它暴露一个**跨模块语义**问题：`CHG-0019`/`ADR-0015` 的意图是"**发布前**验证"，而 B2 要求该版本先有
评测包、B3 **只为已发布版本**编译评测包；B1 的已发布版本又按设计不渲染该面板（门已经过了）。
⇒ **草稿被 `422` 挡住、已发布版本没有入口，"先验证再发布"目前没有可走通的路径。**
B1 的处置：按钮、三态与门禁都照契约实现，**不**自行倒转流程、**不**为这个 `422` 编造专门的状态或文案；
已作为**缺口 12** 登记，请 MAIN 裁决（B3 是否为草稿编译评测包，或这条链的语义需要重定义）。

### 6. 一处**没有**改的地方（避免顺手扩大范围）

`adapter-not-readable` 横幅**保留**：`CHG-0025` 尚未落地，那条说明仍然准确（它是关于**本页行为**的
陈述，不是关于别的模块进度的断言）；`0.16.0` 接入 `draft-input` 读出口之后再撤（见"缺口 10"）。

## 与既有阶段的关系

- 本阶段推翻并替换了 `phase-02 §11 第 1 条`（"UI 出题没有通往发布的路径"）：那条记录当时是真实的，
  现在由 `ADR-0015` 与 `CHG-0019`/`CHG-0023` 解掉。`docs/README.md` 与 `README.md` 的"已知限制"
  已同步改写，**不是**在旧结论旁边再挂一条新结论。
- `phase-04` 记录的是覆盖度视图（`CHG-0020`）；本轮在它之上又删除了那段当时刻意保留的防御分支
  （`CHG-0022` 让契约说出了它一直保证的事实）。
- **验收后的裁决已回写并已部分落地**（同一阶段，2026-09-20）：`CHG-0026`（参考解答的 `409` 语义，
  B1 不消费）、`CHG-0027`（`reason_code` 的 `enum` + "可归属那对值继续手写"的取舍）与
  `CHG-0028`（评测包/存证对草稿开放，正面解决缺口 12）已并入 **`0.16.0`**（摘要 `372cfcab…`）；
  B1 按新版本重新生成类型、门禁全绿，`ReasonCode` 从生成类型派生那一手**因此真的开始生效**。
  `CHG-0025`（`adapter` 的读出口）**未随批落地**，故 B1 保持"常驻说明"现状。
  在此之前 B1 **没有**提前按未落地的契约定型（只做了一步纯类型准备）。
