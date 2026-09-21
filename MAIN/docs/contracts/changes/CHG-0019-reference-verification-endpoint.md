# CHG-0019 参考解答验证的对外接口：解除出题发布死结

- **级别**：P1 增补（一个内部端点 + 一个 schema）
- **状态**：已生效（契约）；**实现已落地，但目的未达成**——B2 侧已实现（`api/reference_routes.py`、49 项测试），
  然而 `CHG-0028` 缺失的前置条件让"验证草稿 → 存证 → 发布"**在真实环境依然不可能**，§7.2/7.3/7.4 因此未执行。
  **见 §2.8。**
- **契约版本**：0.14.0 → **0.15.0**
- **依据**：[ADR-0015](../../adr/ADR-0015-reference-verification-orchestration.md)、`CHG-0008`（发布存证门禁）

## 1. 问题：一条真门禁造成的一个死结

`CHG-0008` 要求 `publish` 前必须有与 `content_digest` 绑定的**参考解答验证存证**，且该门禁是**真的**
（B3 交叉核对摘要，无跳过开关）。它的设计前提是**执行者 ≠ 记录者**：B3 禁止执行任何东西，
跑参考答案的是能执行代码的组件。

后果是 B1 已按规程上报、MAIN 从未裁决的那一条：

> **B1 的出题界面无法完成发布。** 它不能自己声明"我验证过了"（存证退化成自我声明），
> 也不能携带 `serviceToken`（服务凭据不该进浏览器）。教师能写到草稿，然后卡在发布，只能找运维跑脚本。

`phase-01 §11.2` 当时写下的方向是"增加平台侧的验证编排（能执行代码的组件跑、B3 记录）"。
`ADR-0015` 把它裁决为：**B2 执行、B3 记录、教师一键触发**，且复用 B2 **已有的** B3 服务通道，
不引入新凭据、新信任关系或新的跨模块机制。

## 2. 变更内容（实施时执行）

### 2.1 `openapi/b2.yaml` 新增端点

| 端点 | 语义 |
|---|---|
| `POST /api/v1/reference-verifications` | 给定 `question_version_id`，在沙箱按该版本自己声明的适配器与比较策略执行**参考解答**，逐用例比对并返回结论。**不落库** |

- **鉴权**：`bearerAuth` + 该题目的**课程写权限**（与 `publish` 同一套判定）。
- **不是 `serviceToken`**：调用者是教师，不是服务。B2 会在内部**再**用 `serviceToken` 调 B3。

### 2.2 新增 schema `ReferenceVerification`

| 字段 | 类型 | 说明 |
|---|---|---|
| `question_version_id` | Uuid | 被验证的版本 |
| `content_digest` | Sha256 | 与 `CHG-0008` 的存证口径一致；内容一改即失效 |
| `language` | Language | 来自该版本的声明 |
| `adapter_kind` | string | 来自该版本的声明 |
| `cases_total` / `cases_passed` | integer | 计数 |
| `cases` | array | 逐用例：`ordinal`、`outcome`、`failure_category`（可为 null） |
| `verified_at` | Timestamp | |
| `attestation_id` | Uuid \| null | B3 记录成功后的存证 id；记录失败时为 `null` 且 `cases` 仍然返回 |

`cases[].failure_category` 复用 `evaluation-report` 已有的失败类别枚举，不新造词表。

**刻意不含**：期望输出、隐藏用例输入、参考解答源码。教师对自己题目有权看到这些，但这个端点
**不得成为绕过受保护内容访问控制的旁路** —— 它返回的粒度与报告端点的 `view=teacher` 同级。

### 2.3 受影响模块

| 模块 | 改动 |
|---|---|
| **B2** | 新端点；取包（已有）、沙箱执行参考答案（复用 runner）、逐用例比对；成功后用**已有的** `B2_B3_AUTHORIZATION` 调 B3 的 `recordReferenceAttestation`；失败不产生存证 |
| **B3** | **无改动**（端点、鉴权、schema 全部已在 `CHG-0008` 中就位） |
| **B1** | 出题页增加"验证并发布"入口与三态呈现：通过 → 可发布；未通过 → 展示逐用例失败类别；`infrastructure_error` → 明确显示"验证需要 Linux 沙箱环境"（Windows 上必然如此，**不得伪装成功**） |
| **MAIN** | 本 CHG、schema、五份锁与派生声明同步到 0.15.0 |

### 2.4 复用点（实施前已逐处核实，避免实施时再探索）

`ADR-0015` 的全部要点是"复用 B2 **已有的**通道"。以下三处在 2026-09-20 逐处核实存在，
因此本 CHG 在 B2 侧**只新增一个 HTTP 路由与一个编排函数**，不新增基础设施：

| 需要的能力 | 既有实现 | 核实位置 |
|---|---|---|
| 取评测包（隐藏用例与期望输出） | `HttpPackageSource.fetch()` | `integration/adapters.py:111`，URL 拼装在 `:121` |
| B3 服务通道的定位与凭据 | `B3Endpoint`（冻结 dataclass，`authorization` 为 `repr=False`） | `integration/adapters.py:67`；装配在 `runtime.py:410` |
| 沙箱执行 + 逐用例比对 | `runner/package.py`、`runner/comparator.py`、`runner/policy.py` | `runner/` |

> **更正（2026-09-20，实施侧上报后核实）**：本表初稿第一行写的是"取评测包（**含参考答案**、隐藏用例）"。
> **那句话是错的，而且是我未经核实写下的断言**——与 `CHG-0020` §5 的 GIN 断言是同一类错误。
> 实测：`contracts/schemas/evaluation-package.schema.json` 的 `properties` 里**没有**参考解答字段
> （且 `additionalProperties: false`），B3 的编译器签名
> `compile_evaluation_package(snapshot, manifest, cases, fixtures, *, created_at, policy)` **也不接收**它
> ——尽管 `snapshot.reference_answers` 就在手边，编译器是**刻意不输出**。
> **因此本 CHG 按原样没有可实现的语义**：B2 拿不到参考解答，就无法执行、无法逐用例比对。
> 缺口已由 `CHG-0023` 补上（新增 B3 内部 `serviceToken` 端点交付参考解答）。**实施前必须先读它。**

**唯一新增的外部调用面**是给 `B3Endpoint` 加两个同源的调用：GET 参考解答（`CHG-0023`）与
POST `recordReferenceAttestation`（`b3.yaml` 已在 `CHG-0008` 中就位并已声明 `serviceToken`）。
它们与取包共用同一个 `base_url`/`authorization`，因此**没有新增凭据存放位置**。

**必须复用而不是另起一套**的理由：另写一份"执行参考解"的路径会绕过 `runner/policy.py` 的
seccomp / cgroup / 禁网策略，那等于在发布门禁上开一个不受沙箱约束的执行面。

### 2.5 门禁约束：契约与实现必须同一次落地

B2 的 `tests/test_b2_routes.py:764` 断言：

```python
assert set(expected) - set(described) == _PENDING_CONTRACT_PATHS   # 该集合当前为空集
```

因此**先声明端点、稍后实现**会立刻让该断言失败。实施顺序必须是：在**同一次**变更里
同时完成 `b2.yaml` 声明、B2 实现、五份锁重算。若要分两次，则必须把新路径显式登记进
`_PENDING_CONTRACT_PATHS` 并写明理由 —— 但那正是 `CHG-0005` 暴露过的"声明了没人实现"的旧形态，
本 CHG **不采用**。

### 2.6 门禁约束：复用失败类别词表必须同时登记进 `SHARED_VOCABULARIES`

§2.2 的 `cases[].failure_category` 复用 `evaluation-report` 的失败类别枚举。那个枚举的权威是
`contracts/schemas/evaluation-report.schema.json`（`timeout` / `crash` / `output_mismatch` /
`resource_limit` / `missing_output` / `import_error` / `security_blocked` / `infrastructure_error`）。

OpenAPI 无法 `$ref` 进 `schemas/*.json`，所以这**必然是一次重复声明**。为此 `checks.py` 已新增
`check_shared_vocabularies()`（见 `phase-02 §3.13`）：它比对所有**被登记**的重复词表。

**实施时必须做的**：在把该枚举写进 `b2.yaml` 的**同一次变更**里，把这一对登记进
`checks.py` 的 `SHARED_VOCABULARIES`。只写 `b2.yaml` 而不登记，本 CHG 那句"不新造词表"
就**没有工具在保证**——两份副本可以单侧改动而门禁全绿，这正是该门禁存在的理由。

**`null` 的表达方式两处可以不同。** 本项目是 **OpenAPI 3.1**（不是 3.0），它和 JSON Schema 2020-12
对齐，所以 `enum` 里**可以**直接出现 `null`；而 3.1 之前风格的写法用 `anyOf: [ ..., {type: "null"} ]`。
实施时本 CHG 在 `b2.yaml` 里用了 `anyOf` 形式（对生成类型的工具更清晰），报告 schema 里则是把 `null`
列为 `enum` 成员——**两者都对**。`check_shared_vocabularies()` 会先**剥掉 `null` 再比集合**，因此
这两种拼法不会互相误报成分歧；它比对的是**值集本身**，可空性由各自的写法表达，不由本门禁裁决。

### 2.8 **本 CHG 的目的没有达成**：实现已落地，闭环仍走不通（2026-09-20，实施侧在真实 B3 上发现）

B2 实现完本端点后在**真实 B3** 上验证，发现"验证草稿 → 存证 → 发布"**依然不可能**：

| | 规则 | 位置 |
|---|---|---|
| A | `GET .../evaluation-package` 对**草稿**返回 `409` | `B3/domain/packages.py:43-44` |
| B | `recordReferenceAttestation` **只接受草稿** | `B3/domain/attestations.py:82-87` |

两条规则**各自都合理**（B 的理由还写在错误文案里："内容已冻结，新存证无法改变学生收到的东西"），
**合起来矛盾**：验证必须在草稿上，而可执行输入只有已发布版本才有。教师**依旧卡在发布**——
**而那正是本 CHG 存在的唯一理由。**

**我的责任，两处同型**：

1. §2.4 初稿写"取评测包（**含参考答案**…）"——那件东西根本不在包里（`CHG-0023` 已更正）。
2. §2.4 表里"取评测包（隐藏用例与期望输出）| `HttpPackageSource.fetch()`"——**这件东西确实存在，
   但我只核实了代码件存在，没有核实它的前置条件**（只能取**已发布**版本）。

**同一个设计里我犯了两次同型错误，两次都是实施侧撞出来的，不是我自查出来的。**

**处置**：缺口已由 `CHG-0028` 接手（让草稿也能取包 + 收窄 "immutable" 的措辞 + 点名两处 `409`）。
**在本 CHG 的 §7 里，2/3/4 三条在 `CHG-0028` 落地前无法执行**，因此在 §7 中显式标注为"被前置条件阻塞"，
而不是留在那里看起来像"待跑"。

> **一条应当被记住的判断**：核实一个**可复用的组件**存在，与核实它**在当前场景下可用**，是两件事。
> 前者读代码就够，后者必须问"它的前置条件我满足吗"。本 CHG 的复用表只做了前者。

## 3. 安全与隐私检查

- 新端点返回的字段**逐条**在上面列出，全部是计数、摘要或枚举，**没有**期望输出、隐藏用例内容或参考解答源码。
- 调用者必须先通过课程写权限判定；判定在服务端（B1 的按钮可见性不构成边界）。
- B2 对 B3 的调用使用的是**已存在**的服务令牌通道（它本来就用它取 evaluation package），
  因此**没有新增数据出境面**，也没有新增凭据存放位置。

## 4. 资源影响

每次验证起一个沙箱作业。单并发沙箱（资源账本决定）天然限流。**未加显式速率限制**，
记录为已知限制（`ADR-0015` 遗留风险）。

## 5. 需求一致性

不新增 `RQ-*`。它是 `RQ-B-3-20`（按课程维护题库 + 发布）与 `RQ-T-10`（出题工作流）
的**可用性补全**：不在契约里，教师就无法在界面上完成发布，而那是 `RQ-B-3-20` 验收 3 的日常形态。

## 6. 是否破坏兼容

**否。** 新增一个端点与一个 schema，不改任何既有形状。MINOR 升到 0.15.0。

## 7. 验证（实施后执行，未完成前不得声称已通过）

> **2/3/4 被前置条件阻塞**：它们要求"验证草稿 → 存证 → 发布"这条链能跑，而 `CHG-0028` 之前
> **草稿取不到执行输入**（§2.8）。这三条不是"待跑"，是"在 `CHG-0028` 落地前不可能跑"。
> 1/5/6 已由 B2 在实现侧验证（`tests/test_reference_verification.py`，49 项）。

1. 未持有该课程写权限的调用者得到 `403`；学生得到 `403`。
2. 参考答案**通过**时：返回 `cases_passed == cases_total`，且 B3 侧出现一条与该 `content_digest`
   绑定的存证；随后 `publish` 成功。
3. 参考答案**未通过**时：不产生存证，`publish` 仍然 `422`，且响应逐用例说明失败类别。
4. 编辑题目内容后，旧存证失效（`content_digest` 变了），再次 `publish` 被拒。
5. 响应中**不存在**期望输出、隐藏用例输入或参考解答源码（断言键集合）。
6. Windows 环境下返回 `infrastructure_error` 且**不**产生存证、**不**返回伪成功。

## 8. 回滚方式

删除端点与 schema，重新运行 `scripts/relock_contracts.py`，版本回到 0.14.0；
同时把 `ADR-0015` 改为"拒绝"。回滚后 B1 的出题界面重新失去发布路径 —— 那必须**同时**
写回 B1 的已知限制，而不是让界面留一个总是失败的按钮。
