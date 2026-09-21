# CHG-0023 参考解答的交付通道：`CHG-0019` 缺一个必需输入

- **级别**：P1 增补（一个内部端点 + 一个 schema）
- **状态**：已生效（契约）；实现排期（B3 端点）
- **契约版本**：0.14.0 → **0.15.0**（**修订**该批：`0.15.0` 尚未被任何模块实现消费，故并入同一批而不另开 `0.16.0`）
- **依据**：[ADR-0015](../../adr/ADR-0015-reference-verification-orchestration.md)、`CHG-0019`（消费方）、`CHG-0008`（参考解答为受保护内容）

## 1. 问题：`CHG-0019` 的端点**没有可实现的语义**

`CHG-0019` 要 B2"在沙箱按该版本自己声明的适配器与比较策略执行**参考解答**，逐用例比对"。B2 在实施时按规程停下上报：
**B3 没有任何契约通道把参考解答交付给 B2。**

已逐条核实（不是转述）：

| # | 事实 | 证据（实测） |
|---|---|---|
| 1 | evaluation package **没有**参考解答字段，且 `additionalProperties: false` | `contracts/schemas/evaluation-package.schema.json`：`properties` 只有 `question_version_id/package_digest/language/adapter/tests/security_policy/max_files/max_total_bytes/created_at` |
| 2 | B3 的编译器**不接收**参考解答 | `B3/src/autograder_b3/compile/compiler.py`：`compile_evaluation_package(snapshot, manifest, cases, fixtures, *, created_at, policy)` —— 签名里没有它；调用方 `domain/packages.py` 也没传。**注意 `snapshot` 里其实有这个数据**（`dto/internal.py` 的 `PublishedVersionSnapshot.reference_answers`），是编译器**刻意不输出** |
| 3 | 唯一读版本的公开 DTO **禁止**回显参考解答 | `b3.yaml`：`reference_answer` … "**never echoed back through a public DTO**"；`QuestionVersionPublic`（`additionalProperties: false`）无该字段 |
| 4 | B3 全部路径里没有第二个内容通道 | 只有 `evaluation-package`（internal + `serviceToken`）与 `attestations`（**只写**） |
| 5 | `b3.reference_answers.body` 只被用来算摘要 | `domain/content.py`、`domain/revisions.py` 的 `reference_answer_digest()` |
| 6 | 参考解答**已经是内容摘要的一部分** | 同上：`content_digest` 覆盖它，因此"用了哪份参考解答"这一点**已经**被摘要绑定 |

**因此 `CHG-0019` §2.4 的那句"取评测包（**含参考答案**、隐藏用例）"是我写下的未经验证的断言**，
与 `CHG-0020` §5 的 GIN 断言是同一类错误。已在该 CHG 中更正并留痕（见其 §2.4 与 §2.7）。

## 2. 为什么不做 `CHG-0019` 的实现方提出的另外两条路

### 2.1 否决 A1：把参考解答塞进 evaluation package

**否决理由不是"改动大"，而是两处真实代价：**

1. **受保护内容的最小权限**。参考解答只有**验证**路径需要；evaluation package 是**每次评测**都要取的输入。
   把它放进包里，等于让每一次评测取包都在网络上多带一份参考解源码，而评测本身**完全不需要它**。
   这是**无谓扩大受保护内容的在途范围**——与"执行者 ≠ 记录者"要收窄的正是同一类暴露面。
2. **它会重新点燃 §2.5.2 的结构性问题**。`package_digest` 由编译产物算出，并被绑定进每一条
   `evaluation_results`。改编译器输出 ⇒ 所有已发布版本的包摘要全部改变 ⇒ 任何"重算并比对"都会
   与已记录的值不符（`phase-02 §2.5.2` 记录过那 103 条摘要为何曾全部陈旧）。
   一次纯粹为了"多带一个字段"的变更，不该触发全库摘要失配。

（补充：package 并不是"纯声明通道"——它的 `tests.items` 已含 `expected_stdout`/`stdin`，
且 `visible_to_student` 默认 `false`。所以 A1 并非在类别上越界。否决它的理由是**权限与摘要代价**，不是"包里不能有受保护内容"。）

### 2.2 否决 C：先实现"除取参考解答以外"的全部

那会让端点**永远无法返回 200**，`CHG-0019` 的验证项 2/3/4 无法验收，且把"输入缺失"做成一个
只有文档能解释的恒久 `503`。`ADR-0015` 里 `503` 特指 Windows 无沙箱；用同一个码掩盖缺输入，
会毁掉 B1 那套三态呈现的判断依据。**部分实现交给下一个人，比不做更糟。**

## 3. 变更内容（A2：新增一个 B3 内部端点）

### 3.1 `openapi/b3.yaml` 新增端点

| 端点 | 语义 |
|---|---|
| `GET /api/v1/question-versions/{question_version_id}/reference-answer` | 返回该版本**自己声明的语言的**参考解答正文。**受保护内容**，仅服务间可读 |

- **鉴权**：`security: [{serviceToken: []}]` —— 与 `getEvaluationPackage` **同一个 scheme**，
  因此**没有新增凭据、没有新增信任关系**。这正是 `ADR-0015` 要的"复用已有服务通道"。
- **只在版本存在但没有参考解答时返回 `409`**：那不是"资源不存在"，而是"这个版本没有可验证的内容"，
  与 `404` 语义必须分开（`governance.md` 硬规则 1）。

### 3.2 新增 schema `ReferenceAnswer`

| 字段 | 类型 | 说明 |
|---|---|---|
| `question_version_id` | Uuid | |
| `content_digest` | Sha256 | 与 `CHG-0008` 的存证口径**同一个摘要**；B2 用它向 B3 存证 |
| `language` | Language | 该版本声明的语言（参考解答按 `(版本, 语言)` 唯一） |
| `adapter_kind` | enum | `[stdin, file, callable]`，**复用** `QuestionVersionPublic` 的既有词表，不新造 |
| `reference_answer` | string, `minLength: 1` | **PROTECTED**。参考解答正文 |

**刻意不含**：隐藏用例的期望输出、`test_cases` 内容、fixture 内容。它们本来就**不**经此通道
（fixture 走 `adapter.files` 的 path+sha256 声明；隐藏用例随 evaluation package 走）。
**这个端点只补上缺的那一件，不顺手把别的受保护内容也搬过来。**

### 3.3 受影响模块

| 模块 | 改动 |
|---|---|
| **B3** | 新增端点：按 `question_version_id` 取该版本的参考解答（`ReferenceAnswer` 已存在于 `b3.reference_answers`，且 `has_reference_answer()` 已在仓库层就位）。服务间鉴权复用 `serviceToken`。**无迁移、无新表** |
| **B2** | `B3Endpoint` 上加一个同源的 GET（与取包共用 `base_url`/`authorization`） |
| **B1** | 无改动 |
| **MAIN** | 本 CHG、`b3.yaml`、六份契约文件的 `info.version`、五份锁 |

## 4. 安全与隐私检查

- 返回内容**是一种受保护内容**（参考解答源码），与 `b3.reference_answers` 在 `PROTECTED_TABLES` 中同级。
- **调用方收窄**：只有 `serviceToken` 持有者（当前只有 B2）可读；教师与学生**不可**调用。
  教师通过出题写路径接触参考解答（`QuestionDraftInput.reference_answer` 是写字段），那是另一条已存在的路径。
- **不新增出境面**：B2 本来就能拿到隐藏用例的期望输出（evaluation package），加上参考解答不扩大它**本来能读写**的集合；
  它扩大的是"在一次评测取包里不必携带的东西"，而那正是本 CHG **避免**的。
- **不得进入契约示例 / fixture / 日志 / 事件**：契约只声明字段与类型，**不放任何真实参考解答正文**
  （MAIN 硬规则 7）。B3 与 B2 都必须把该字段列入脱敏名单。

## 5. 资源影响

无。一次单行读取，无新表、无新索引、无新依赖。

## 6. 需求一致性

不新增 `RQ-*`。它是 `CHG-0019`（`RQ-T-10` 出题工作流与 `RQ-B-3-20` 发布路径的可用性补全）
**不可缺少的前置**：没有它，那条发布路径在契约层面就不成立。

## 7. 是否破坏兼容

**否。** 新增一个端点与一个 schema，不改任何既有形状。并入 `0.15.0`（该版本尚未被任何模块实现消费，
因此修订它比另开 `0.16.0` 更诚实：**不是两次变更，是一次变更没写完**）。

## 8. 验证（实施后执行，未完成前不得声称已通过）

1. `serviceToken` 调用返回 `200`，且 `language` 等于该版本声明的语言。
2. **无令牌 / 用户令牌（含教师）** 调用 → `401` / `403`。**学生与教师都不得读到参考解答**——这是本端点的核心断言。
3. 版本存在但无参考解答 → `409`（**不是** `404`）。
4. 不存在的版本 → `404`。
5. `content_digest` 与 `CHG-0008` 存证的 `content_digest` **逐字相等**（证明两边是同一个摘要口径）。
6. 响应**只有** `ReferenceAnswer` 声明的五个键：没有 `expected_stdout`、没有 `test_cases`、没有 fixture 内容。
7. B3 / B2 的日志与事件中**不出现**参考解答正文（脱敏名单含 `reference_answer`）。

## 9. 摘要

由 `checks.py` 计算（不在此复制字面值，避免漂移）。

## 10. 回滚方式

删除端点与 schema，重新运行 `scripts/relock_contracts.py`。**回滚后必须同时把 `CHG-0019` 标记为
阻塞并写明原因**——不能让它留成一个"已生效但不可实现"的端点，那正是本轮发现的病灶。
