# CHG-0025 草稿内容的读出口：`QuestionDraftInput` 没有对应的读形状

- **级别**：P1 增补（一个新端点 + 一个 schema）
- **状态**：已采纳，**实施排期**（不随 `0.15.0`：该版本**已被在飞实现消费**，见 §7）
- **契约版本**：未改契约（实施时升至 **`0.17.0`**；**不并入 `0.16.0`**，理由见 §7——那一批全是"把已成立的事实写成声明"，而本 CHG 是**新增一个能力**）
- **依据**：`CHG-0017`（写路径的省略语义）、`RQ-B-3-20`；B1 在实现出题界面时按规程上报

## 1. 问题：写形状有必填字段，而没有任何读形状能取回它们

`QuestionDraftInput` 的 `required` 是 **`[title, stem_markdown, language, adapter]`**（`CHG-0017` 一字未改）。
其中 `adapter` 的读出口**不存在**：

| 字段 | 读得到吗 |
|---|---|
| `title`、`stem_markdown`、`language` | 是（`QuestionPublic` / `QuestionVersionPublic`） |
| `adapter.kind` | 是（`adapter_kind`） |
| `adapter.max_files`、`max_total_bytes` | 是 |
| **`adapter.args`、`adapter.stdin`、`adapter.expect_exit_code`、`adapter.comparison`、`adapter.files`、`adapter.callable`** | **否——一处都没有** |
| `tags` | 是（`QuestionPublic.tags`） |

而 `AdapterManifest` 里**没有任何一个字段是 `required` 的**（只有 `kind` 必填，而 `kind` 读得到）。
这就是缺口的确切形状：**必须重发的东西，恰好是读不回来的东西。**

### 1.1 后果不是"脚本不方便"，是**数据损坏**

B1 的实现**必然重新构造** manifest：它把**表单里当下的值**当 manifest 发出去。
- 同一会话内（刚在出题页填过并保存的题）确实保留了用户输入；
- **但打开一道在 UI 之外创建的题目时，那些输入框是空的**，保存会用一份**不完整的 manifest
  覆盖已存的那份**——静默地丢掉 `args`/`stdin`/`comparison`/`files`。

纯 API 客户端受同一个缺口影响。**"能工作"与"能正确工作"在这里是不同的判断**：
B1 的界面能跑、测试全绿，而这个路径会静默改坏数据——**前端测试发现不了它，因为前端正好是那个会重新构造的客户端。**

## 2. 变更内容：给写形状配一个读形状

### 2.1 `openapi/b3.yaml` 新增端点

| 端点 | 语义 |
|---|---|
| `GET /api/v1/questions/{question_id}/draft-input` | 返回**当前草稿的、与 `QuestionDraftInput` 同形的**可编辑内容，供写路径原样回发 |

- **鉴权**：`bearerAuth` + 该题目的**课程写权限**（与 `PATCH`/`POST` 同一套判定）。
  **不是**所有能读题目的人：学生读不到它。
- 响应即 `QuestionDraftInput` **减去两个受保护字段**（见下），因此"取回什么就得回发什么"成为字面事实。

### 2.2 新增 schema `QuestionDraftInputView`

`title`、`stem_markdown`、`language`、`adapter`、`tags`、`max_files`、`max_total_bytes`。

**刻意不含 `cases` 与 `reference_answer`。** 那不是遗漏，是本设计的要点：
- `QuestionDraftInput` 的正文写明这两个字段"are never echoed back through a public DTO"；
- `CHG-0017` 已经让它们**可以省略**，所以写路径**不需要**读回它们。
- 若把它们放进这个响应，就等于把"受保护内容的往返"重新变成常规动作——而那正是 `CHG-0017` 要消除的。

**也不含 `revision_note`**：它是"为什么产生这一版"的写入参数，不是草稿的当前状态。

**刻意不含**隐藏用例的期望输出、fixture 内容。它们有自己的通道；本端点只补**缺的那一件**。

### 2.3 `adapter.files[].content_sha256` 保留在响应里，并写明代价

`QuestionDraftInput.adapter.files[]` 的 `required` 是 `[path, content_sha256]`，所以**不返回摘要就无法回发**。
因此响应**包含**它，但把已知代价写进契约：

> `content_sha256` 是**摘要不是内容**，但短内容的摘要是可爆破的。因此本端点要求**课程写权限**，
> 不向学生开放。**这是一处被明确接受的残余风险**，不是被忽略的：另一种选择（不返回摘要）
> 会让 `kind=file` 的写路径**永远无法回发**，而那会把一个可用性缺口换成一个静默改坏数据的缺陷——
> 两者都比"有写权限的人能对一个他本来就能替换的夹具做摘要爆破"更糟。
> （`kind=file` 的**夹具字节**另有约束：只能经 `internal` + `serviceToken` 的 fixtures 端点上传，
> 摘要由服务端算，因此本端点不提供、也不需要提供字节。）

## 3. 为什么不是"让 B1 自己缓存"

已明确**否决**"客户端缓存本会话提交过的 manifest"：
- 它变成**每个客户端各自维护的一份服务端状态影子副本**，于是立刻产生"谁的副本才是对的"；
- 每新增一个客户端就要再实现一遍，且**未缓存过的题目仍然会静默降级**——缺口只是被推后，不是被补上；
- 它没有契约可校验，因此没有任何门禁能发现它错了。

**缺口的正确位置是契约，不是每个调用方的内存。**

## 4. 安全与隐私检查

- 新增响应**不含**任何受保护内容（`cases`/`reference_answer`/期望输出/fixture 字节一律不含）。
- 调用方收窄到**课程写权限**；学生与无权限的教职员读不到。判定在服务端。
- 唯一新增面是 `adapter` 的声明性字段（`args`/`stdin`/`expect_exit_code`/`comparison`/`callable`）
  与 `files[].content_sha256`；前者的可见性与"题目描述本身"同级，后者的代价见 §2.3。
- `comparison` 是**服务端策略**，暴露它不会让学生影响它（`AdapterManifest.comparison` 的正文已写明
  "Student code must not be able to influence it"）。

## 5. 资源影响

无。一次单行读取（草稿与其 adapter 声明），无新表、无新索引、无新依赖。

## 6. 需求一致性

不新增 `RQ-*`。是 `RQ-B-3-20`（按课程维护题库）的**可用性补全**：
在没有它之前，"改一道题"这件事对**任何不自己保存过 manifest 的调用方**都会静默改坏数据。

## 7. 是否破坏兼容与版本策略

**否。** 新增一个端点与一个 schema，不改任何既有形状。

**为什么不并入 `0.15.0`**：`0.15.0` 现在**正在被实现消费**（B2 在实现 `CHG-0019` 的端点、
B3 在实现 `CHG-0023` 的端点）。**就地修订一个已被消费的版本是不诚实的**——实现者测试的是摘要 A，
而契约会变成同版本号的摘要 B，此后没有任何东西能区分两者。

**随后由 `0.16.0` 改排 `0.17.0`（本轮更新）**：原定 `0.16.0`，但 `0.16.0` **也已被消费**——B3 的实现
已针对它落地 `CHG-0028`，并以它的摘要记录套件证据（`372cfcabad2a…`），`B1`/`B3`/`B4`/`B5` 四份证据
全部绑在该摘要上。同一条判据第二次生效："是否可以就地修订"看的是**有没有实现已经针对它写过代码**，
不是"它是不是刚发布"。

**本批次一并带上（新增）**：`openapi/b3.yaml` 里 `getEvaluationPackage` 描述草稿 `created_at` 的那句
"**不是** `published_at`，**草稿没有它**"必须改准——存储层只要求"离开草稿后必须有 `published_at`"，
**不保证草稿没有**它，因此"这里不读 `published_at`"必须是**代码**的保证而非**数据**的。依据与实测见
`CHG-0028` §5 后的精确化注。**单独修这一句就会改变摘要**，从而让上面四份证据同时失效，所以它只能与
下一个版本号一起走。

**本批次一并带上（第二项，2026-09-20 新增）**：`openapi/b2.yaml` 的 `static_rule_hits` 描述
（`:827`）写的是 *"Which static rules **actually fired**, and how often. A rule that never fires on any
submission is either useless or mis-specified -- that is the point of surfacing it."* ——
**这两句互相拉扯**：前半句隐含"只列命中过的"，后半句恰恰要求"把从不命中的也摆出来"。

**实测三方不一致**（见 `docs/phases/active/phase-02-*.md` §3.47）：

| 来源 | 行为/说法 |
|---|---|
| B2 `evaluation/quality.py::_rule_hits` | docstring 明写"**以 B2 规则目录逐条为准（未命中即 0）**"，**故意**返回完整冻结目录（这正是后半句"surfacing it"的落地） |
| B1 质量页 | 小标题"静态规则命中（**只含命中过的规则**）"+ 说明"契约没有给出规则目录……B1 拿不到规则全集"——而它渲染的正是那份含 0 的 15 条目录 |
| 契约 | 两句要求相反，无法据此判定谁对 |

**收紧方向**：改成"**冻结目录逐条给出计数，未命中即 `0`**"，并把"从不命中"那句作为**明确要求**保留
（它是这条数据存在的理由）。这样 B1 那一侧的标签就有了唯一正确的读法。**与第一项同理**：单独改它会改变
摘要并使五份证据失效，只能与下一个版本号一起走。

> 这条是我在本轮学到的：`0.15.0` 此前被修订过三次（`CHG-0023`、`CHG-0024`、以及 `CHG-0021` 的补写），
> 那三次都成立，因为**当时没有任何实现消费它**。**"是否可以就地修订"的判据是"有没有实现已经针对它写过代码"，
> 不是"它是不是刚发布"。**

## 8. 验证（实施后执行，未完成前不得声称已通过）

1. 有课程写权限的调用方 → `200`，且 `adapter` 的六个字段与库里存的 `adapter_manifest` **逐字段相等**。
2. **往返**：把响应里除了 `tags` 之外的东西原样作为 `PATCH` 的 body 发回（省略 `cases`/`reference_answer`）
   → `200`，且 `revision_diff` 的 `case_set_changed=false`、`reference_answer_digest_before == after`、
   **`adapter_kind_changed=false`、`size_limits_changed=false`**。
   **这一条是本 CHG 存在的全部理由**：往返之后 adapter 未被改动。
3. **反向**：不做第 2 步、只发 `{title, tags, ...}` 而**不带** `adapter` → 契约允许的 `422`
   （`adapter` 是必填）。用来钉住"必须回发它"这个事实。
4. 学生 / 无该课程写权限者 → `403`（不是 `404`，不用 404 掩盖）。
5. 响应**不含** `cases`、`reference_answer`、任何 `expected_stdout`。
6. `kind=file` 的题目：响应的 `files[]` 含 `path`/`content_sha256`/`mount`，且**不含字节**。

## 9. 摘要

由 `checks.py` 计算（不在此复制字面值，避免漂移）。

## 10. 回滚方式

删除端点与 schema，重新运行 `scripts/relock_contracts.py`。**回滚后必须把 §1.1 的数据损坏路径
重新登记回 `phase-02` 与 B1 的 `interfaces.md`**，而不是让它退回成"B1 加了一条横幅说明"了事。
