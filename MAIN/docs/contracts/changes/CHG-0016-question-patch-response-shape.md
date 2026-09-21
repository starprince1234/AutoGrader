# CHG-0016 `updateQuestionDraft` 的响应声明与实现不符

- **级别**：P2 语义（改正错误的响应声明，线缆行为不变）
- **状态**：已生效（2026-09-18 判定）
- **契约版本**：0.10.0 → **0.11.0**
- **依据**：`RQ-B-3-20`；B3 在 CHG-0013 落地过程中上报；同 URL 的 `getQuestion` 声明

## 1. 问题：同一个 URL 声明了两种实体形状，而其中一种是错的

`PATCH /api/v1/questions/{question_id}`（`updateQuestionDraft`）把 200 响应声明为
`QuestionVersionPublic`，而**同一个路径**的 `GET`（`getQuestion`）声明为 `QuestionPublic`。
实现自 Phase 01 起两条路径都返回 `QuestionPublic`。

判定哪一边是权威，不能看"谁改起来省事"，而要看声明是否**可表达**。这里有一个决定性的结构事实：

| | 可写（`QuestionDraftInput`） | 可读 |
|---|---|---|
| `title` | ✅ 且**必填** | `QuestionPublic` 有；`QuestionVersionPublic` **没有** |
| `tags` | ✅（CHG-0008 加的写路径） | `QuestionPublic` 有；`QuestionVersionPublic` **没有** |

`QuestionVersionPublic` 是 `additionalProperties: false`。因此按原声明，客户端可以 `PATCH` 一个
`title` 与一组 `tags`，然后收到一个**结构上无法包含它们**的响应体。**一个无法表达"请求刚刚改动的
字段"的响应 schema 就是错的**，无需再看别的证据。契约是错的一边，实现是权威的一边。

## 2. 这个错误已经造成了客户端的真实缺陷（不只是潜在风险）

B1 的出题写路径（`RQ-T-10` 的 D6 出题面）按生成的类型消费该响应：

```ts
// B1/src/features/authoring/useAuthoring.ts:67-84
}): Promise<QuestionVersionPublic> => {
  const version = await apiRequest<QuestionVersionPublic>(`/api/v1/questions/${...}`, { method: "PATCH", ... });
  log({ event: "question_draft_updated",
        question_version_id: version.question_version_id,   // 服务端从不返回该字段
        version: version.version,                           // 服务端从不返回该字段
        status: version.status, ... });
```

`question_version_id` 与 `version` 在 `QuestionPublic` 上都不存在，因此这两项在**运行期恒为
`undefined`**，写进审计日志。而因为生成的类型说的是 `QuestionVersionPublic`，TypeScript **无法**
发现它 —— 一个错误的契约声明把编译期本可捕获的错误变成了运行期的静默错误。这正是"契约是唯一权威"
这条规则要防的事，也说明该缺陷的代价不是文档不整齐。

## 3. 变更内容

`b3.yaml` 的 `updateQuestionDraft` 响应：

```diff
       responses:
         '200':
           description: Draft updated.
           content:
             application/json:
-              schema: {$ref: '#/components/schemas/QuestionVersionPublic'}
+              schema: {$ref: '#/components/schemas/QuestionPublic'}
```

只改这一处引用。`QuestionVersionPublic` 本身**不动**——它对
`/api/v1/question-versions/{question_version_id}` 系列仍然是正确的形状，那里才是版本实体的家。
`QuestionPublic` 也不动：它已含 `versions[]`（`QuestionVersionSummary`，含
`question_version_id`），因此"改动产生了哪个新版本"依然可发现，只是要通过正确的字段。

**不做的事**：不给响应加"返回哪种形状"的开关，也不加 `oneOf`。两种形状并列会让每个消费者都必须
分支处理，而其中一支永远不会出现——那是把契约错误转嫁给所有客户端。

## 4. 受影响模块与需要做的改动

| 模块 | 改动 |
|---|---|
| **B3** | 无需改动。实现已经是正确的形状，本变更只是让声明追上它 |
| **B1** | **需要改**：`useAuthoring.ts` 的 `useUpdateQuestionDraft` 改为消费 `QuestionPublic`；日志里的 `question_version_id` / `version` 从 `versions[0]` 取（新版本在最新位），`status` 保持不变。重新生成 API 类型后，编译器会把现有代码标出来 |
| **MAIN** | 本变更；五份锁、五份 `module.yaml`、**四个描述符**同步 0.11.0（见 §5.1）；新增一条静态门禁 |

## 5. 同时判定为**不成立**的一项（记录以免日后被"修好"）

B3 同时上报"契约自身的 `ComparisonDiagnostics` 描述把 `severity` 的第三个取值写成 `note`，而枚举是
`info`，属契约笔误，需 MAIN 裁决"。**核查结论：该笔误不存在。** 全量检索
`MAIN/contracts/**`（含 `schemas/*.json`）后，`note` 只出现在三处与严重度无关的位置（一条归属说明
字段、一句 "Implementation note" 散文、一句 "Note what is absent" 散文），三处 severity 枚举
**全部**是 `[error, warning, info]`：

```text
b2.yaml:695                    severity: {type: string, enum: [error, warning, info]}
b3.yaml:714                    severity: {type: string, enum: [error, warning, info]}
evaluation-report.schema.json  "severity": {"enum": ["error", "warning", "info"]}
```

B3 据此把实现里的 `Severity.NOTE` 改成 `INFO` **是正确的**（`note` 确实无法序列化），但那是
**实现偏离契约**，不是契约笔误。因此本变更**不修改契约的严重度枚举或描述**。此条记录在此，是为了
防止后人照着"存在一处 note/info 不一致"的说法去改动一个本来就正确的声明。

### 5.1 同步这个版本号时漏掉了四个描述符，因此补了一条门禁

契约版本这一个值在本仓库有 **14 处**拷贝：5 份 `contracts.lock` + 5 份 `module.yaml` +
4 个 `ModuleDescriptor`。本次变更先更新了前两类，**漏掉了第三类**（`B2`–`B5` 的
`src/autograder_bN/plugin.py` 里的 `contract_version=` 字面量）。

漏改的后果被误诊过一次：B5 的一致性套件报 `descriptor.contract_version='0.10.0' but module.yaml
declares '0.11.0'`，看起来像"安装陈旧"，但**重装修不好它**——描述符里的版本是源码字面量，重装只是把
同样过时的字面量再装一遍。**错误归因会让人做一件无效的事，然后在无效之后再次撞上同一个错误。**

已补的门禁：`check_manifests()` 用 `ast` **静态**读每个 `ModuleDescriptor(...)` 的
`contract_version` 并与 `info.version` 比对（B1 无描述符，明确跳过）。**静态**是刻意的——
`check_module_descriptors()` 导入的是**已安装**的包，在开发期间源码与清单都改了而只差重装时，
它比较的两边都来自那份陈旧安装，于是"一致"。变异证明见 `phase-01` §12.11。报错信息明确写出
"**重装修不好这个**"，因为真实排查中正是缺了这句话而被误导。

**考虑并放弃**：让 `relock_contracts.py` 一并写 `module.yaml` 与描述符（一次变更一条命令）。该工具
已经在写模块侧的锁、先例是有的；放弃是因为它要开始**改写模块源码**，而门禁已经能在源码层面立刻抓到
漏改并给出确切处置。若日后采纳，应配 ADR 界定工具对模块源码的写权限。

## 6. 验证

```powershell
cd MAIN
uv run python -m autograder_host.checks
uv run python scripts/relock_contracts.py --check
```

本变更特有的验证（**已执行**）：

1. `b3.yaml` 中 `updateQuestionDraft` 的 200 引用已为 `QuestionPublic`，且 `getQuestion` 与它一致：
   `Select-String b3.yaml -Pattern 'QuestionPublic|QuestionVersionPublic'` 显示 109 行
   （`getQuestion`）与 143 行（`updateQuestionDraft`）指向同一 schema。
2. 五份 `contracts.lock` 的版本与摘要一致，且与实时摘要一致（由 `relock_contracts.py` 重算，不手填）。
3. 门禁 `check_route_declarations`、`check_contract_parses_strictly`、`check_contract_examples` 保持通过。
4. **实现侧反向验证（尚未执行，不得声称已通过）**：B3 的契约测试应断言该端点真实返回的键集合与
   `QuestionPublic` 一致；B1 修好消费方后，`useUpdateQuestionDraft` 的日志字段必须是真实值而不再是
   `undefined`。这两条要在 B1/B3 落地后实测。

## 7. 回滚方式

把该处引用改回 `QuestionVersionPublic`，重新运行 `scripts/relock_contracts.py`，五份锁回到 0.10.0。
回滚会重新引入 B1 的静默 `undefined` 缺陷，因此必须同时在 B1 的追踪表里记回该缺陷，而不是只回滚契约。
