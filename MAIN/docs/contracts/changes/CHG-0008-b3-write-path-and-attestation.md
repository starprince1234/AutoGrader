# CHG-0008 B3 的写路径补全：`tags`、`as_new_version`、参考解答验证存证

- **级别**：P1 增补（`tags`、`as_new_version`）+ P2 语义（`publish` 新增存证前置）
- **状态**：已生效（2026-09-18 判定）
- **契约版本**：0.2.0 → **0.3.0**

## 1. 问题

三处都是"契约没写，于是能力不可达"或"能力不可达，于是质量无保障"：

### 1.1 `tags` 的写路径结构性缺失（`additionalProperties: false` 且无 `tags`）

`tags` 在三个地方都存在——`b3` 的列、B3 的 `QuestionPublic` DTO、以及契约的 `QuestionPublic`——
**唯独 `QuestionDraftInput` 没有这个字段**。该 schema 是 `additionalProperties: false` 的，
于是：

- 读路径声明了一个**结构上不可能产生**的值；
- 库里 106 道题**标签全空**，且没有任何客户端能设置它们。

这不是"标签功能没人用"，是"标签功能不可能被使用"。

### 1.2 `as_new_version` 已实现但未声明

`B3/src/autograder_b3/api/router.py` 的 `update_question_endpoint` 早就接受 `as_new_version: bool`，
用于"改已发布题目时产生新版本而不是原地编辑"。契约里没有这个参数，而契约的 `summary` 又写着
"Editing a published version returns 409" —— **契约与实现对同一行为的描述相反**，且生成的客户端
根本无法调用它。这是 `CHG-B3-0002` 的同类问题（实现先行、声明缺失）。

### 1.3 没有任何机制检查"参考答案真的能产出期望输出"

这是本变更里最重要的一条，也是审计把它排为 B3 最强创新点的原因：

- 一道题是否**可解**，取决于它的参考答案能否在它自己的比较策略下产出期望输出；
- 全平台唯一知道这件事的东西是 `MAIN/scripts/verify_question_bank.py`（它真的逐题执行），
  但它**跑完不留任何痕迹**——没有存证、没有落库、没有产物文件；它自己的 docstring 就承认
  "nothing in the platform catches a broken question before a student sees it"；
- 于是**参考答案从没跑过、或者跑过之后题干/用例被改过**的题目，可以正常发布并到达学生。

B3 不能执行任何东西（`B3/AGENTS.md`：我禁止：执行学生代码；对参考答案同样成立），
所以 B3 的正确角色是**只记录 + 只校验**。

## 2. 变更内容

### 2.1 `QuestionDraftInput.tags`（P1 增补）

```yaml
tags:
  type: array
  maxItems: 20
  uniqueItems: true
  items: {type: string, minLength: 1, maxLength: 40, pattern: '^[a-z0-9][a-z0-9-]*$'}
```

规范化形式为小写加连字符；**词表本身归 B3**（在 B3 内校验），契约只固定形状。
`QuestionPublic.tags` 已存在，无需改动。

### 2.2 `updateQuestionDraft` 声明 `as_new_version`（P1 增补）

作为 query 参数声明（`type: boolean, default: false`），并把 `summary` 改成与实现一致的表述：
已发布版本**不是不能改，而是不能原地改**——`as_new_version=true` 时产生新版本，旧版本保持不可变。
同时给该 operation 补上 `200` 的响应 schema（`QuestionVersionPublic`），此前只有一句 `description`。

### 2.3 新增存证端点与 `publish` 前置（P2 语义）

**新端点**：`POST /api/v1/question-versions/{question_version_id}/attestations`
（`tags: [internal]`、`security: [{serviceToken: []}]`）

| 元素 | 内容 |
|---|---|
| 请求 `ReferenceAttestationInput` | `content_digest`（必填）、`verifier`、`cases_total`、`cases_passed`、`verified_at?`、`notes?` |
| 响应 `201 ReferenceAttestationPublic` | `attestation_id`、`question_version_id`、`content_digest`、`verifier`、`cases_total`、`cases_passed`、`verified_at` |

**`publish` 新增前置**：必须存在一条 `content_digest` 与**版本重新计算的内容摘要**相等的存证。
不匹配 → `409`；缺失 → `422` 并在消息里指明修复方式。

**两个关键设计点，都不是细节：**

1. **client 声明的摘要不被信任。** B3 自己重算版本内容摘要再比对，与
   `assert_fixture_binding` 对 fixture 摘要的做法完全一致。这样存证**不可能被挪用到别的内容上**。
2. **执行者与记录者分离。** 执行的是 MAIN 侧的题库校验器（它真的跑参考答案），记录的是 B3。
   让 B3 自己跑参考答案会立刻违反"B3 不执行任何东西"这条边界，也会让"存证"退化成自我声明。

内容一改，摘要即变，存证自动失效——**不需要额外的"过期"逻辑，也不允许加一个跳过校验的开关**
（本项目禁止兼容层与降级路径）。

## 3. 是否破坏兼容

| 变更 | 破坏性 |
|---|---|
| `QuestionDraftInput.tags` | 否（可选新增字段） |
| `as_new_version` 参数 | 否（可选参数，默认值与实现一致） |
| `updateQuestionDraft` 的 200 schema | 否（此前无 schema） |
| 存证端点 | 否（新增） |
| **`publish` 新增存证前置** | **是，且是有意的**：没有存证的版本将**无法发布**。这是本变更的目的——"参考答案跑过"从此是发布条件，而不是一句期望 |

版本号按 MINOR 升到 0.3.0。**不提供"先发布后补存证"的路径**：那正是要修的问题。

## 4. 受影响模块与需要做的改动

| 模块 | 改动 |
|---|---|
| **MAIN** | 契约 + 四份 `contracts.lock` + 四份 `module.yaml` 的 `contract_version`（0.3.0，本次已同步）；题库脚本需新增"产出存证"的能力，并在导入时写入存证 |
| **B3** | `tags` 的写入与校验；声明并核对 `as_new_version`；存证表 + 端点 + `publish` 前置；`updateQuestionDraft` 的响应 schema；`RQ-B-3-30` 验收 5 的分页与实测 |
| **B1** | 重新生成 API 类型；题目表单可提交 `tags`；可显示"已验证"与版本修订入口（无契约变更） |
| **B2** | 无改动。**注意**：`publish` 变严不影响已发布的 103 个版本，但新题目必须先过校验器 |

## 5. 验证

```powershell
cd MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

本次已实测：

- 严格 YAML 门禁**确实拦下**了一次重复键（`b3.yaml: duplicated mapping key 'tags'`）——说明
  "契约必须严格可解析"这条门禁有效，不是摆设；
- 契约版本同步门禁**两次**拦下"锁/清单版本落后于契约"（第一次是在 CHG-0006 之后，
  这正是本批次顺手修掉的门禁漏洞）；
- 三条门禁在 `0.3.0` / `e943d0d7d461…` 下全绿，四份锁与四份 manifest 一致。
- contract_live_check（契约 schema 校验线上真实响应）在 0.2.0 下为 4/4；0.3.0 新增的
  `tags`/存证在 B3 实现落地后需要重跑，**目前尚未验证**——这一点如实记录，不得写成已通过。

## 6. 回滚方式

回退本批次 → 重新运行 `scripts/relock_contracts.py` → 四份锁与四份 `module.yaml` 回到 0.2.0。
不得只回退 `publish` 的存证前置而保留端点（那会留下一个无人使用的端点）。
