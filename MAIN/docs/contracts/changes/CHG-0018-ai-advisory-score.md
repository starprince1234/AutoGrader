# CHG-0018 AI 评分参考：`EvaluationCommentary.advisory_score`

- **级别**：P2 语义增补（新增一个可空子对象与一个 schema，无既有形状改变）
- **状态**：已生效（2026-09-20 判定）
- **契约版本**：0.12.0 → **0.13.0**
- **依据**：[ADR-0014](../../adr/ADR-0014-ai-advisory-score.md)、`rq-catalog.md` §5B 的 `RQ-T-30`

## 1. 问题：一个被刻意挡住的形状，现在需要一个受控的入口

`CHG-0011` 为 `EvaluationCommentary` 写了整段描述，说明它**刻意没有 `score` 字段**：

> if either existed, a consumer would eventually treat the commentary as a grading input, which is
> exactly the outcome `ADR-0005` worried about and `ADR-0009` had to design around.

负责人要求增加"AI 可以提供评分参考"。这条约束因此不能靠"不建模"来维持 —— 不建模它，它只会以
不可审计的方式出现在自由文本里（评语里写一句"大致能得 8 分"完全没有结构可断言）。正确做法是
**给它一个形状，并让"它不是分"成为可执行的断言**，而不是让它消失。

## 2. 变更内容

### 2.1 新增 schema `AdvisoryScore`

| 字段 | 类型 | 说明 |
|---|---|---|
| `score` | number | 模型建议的分数 |
| `max_score` | number (>0) | 满分。**由 B2 从结构化证据填入，不由模型生成** —— 满分是可复算的事实，让模型产出它只会引入一个可能错、且错了看不出来的数 |
| `rationale` | string (1..2000) | 依据。必须说明这个建议是基于证据里的什么 |
| `confidence` | enum `low`/`medium`/`high` | 模型对这个建议的自评 |

`additionalProperties: false`，四个字段全部必填。

### 2.2 `EvaluationCommentary` 新增可空子对象 `advisory_score`

它是**子对象**而不是平级数字，这一条是本次变更的核心设计：消费者必须写
`commentary.advisory_score.score`，**路径本身在代码里就说明了它不是权威分**。

命名用 `advisory`（顾问性的）而不是 `reference`（参考），因为本项目里 `reference_answer` /
`reference_attestations` 已经占用"reference"；用它会让"参考分"与"参考答案"在同一份文档里指两件事。

`disclaimers` 增加一条针对分数参考的强制说明。`advisory_score` 为 null 时该标注**仍然存在**。

### 2.3 `EvaluationCommentary` 的描述改写

原句"刻意没有 score 字段"必须**正面回应**而非删除。新描述要写清：字段现在存在，但它是顾问性的；
判据在 `ADR-0014` 决策 3 的六项可执行断言，而不是散文承诺。

## 3. 与分数轨的分离（本次变更的全部要害）

`advisory_score` 及其任何派生值**不得**出现在：

- `evaluation_results.score` / `is_pass` / `result_digest`
- `case_evidence` 的任何字段
- `evaluation-report.schema.json` 的任何字段
- 五项绑定摘要
- `/api/v1/grades` 与 `/api/v1/grades/export`
- 任何事件 payload

**输入白名单与评语轨完全相同**（`ADR-0009` 决策 2 不变）：同一次生成、同一份结构化证据，
同样永不读学生源码、参考答案、隐藏用例、期望输出。

## 4. 是否有意保留的缺口

**是，两处**：

- **没有"只生成参考分、不生成评语"的开关**。它们是同一次生成的两个产物，共用一个 provider 配置。
  新增开关会引入一个状态组合（有分无评语），而这个组合没有任何使用者能说清它意味着什么。
- **学生可见性没有接口开关**。沿用 `ADR-0009` 决策 5：它是课程策略、默认关闭，由部署方决定，
  不通过 query 参数表达（那会被误读成"学生可以自己打开"）。

## 5. 是否破坏兼容

**否。** 新增一个可空属性与一个 schema；`advisory_score` 缺省为 `null`，因此既有消费者读到的
对象形状不变（`additionalProperties: false` 的响应在新增字段后仍然合法）。MINOR 升到 0.13.0。

`prompt_version` 会推进：生成从"输出一段文字"变为"输出一个 JSON 对象"。解析失败归为
`status: failed`，**不得**退化成"把整段响应当评语" —— 那会把一个格式错误变成一次静默降级。

## 6. 受影响模块与需要做的改动

| 模块 | 改动 |
|---|---|
| **B2** | prompt 改为要求 JSON 对象；解析出 `text` + `advisory_score`；`max_score` 由证据填入；`score` 越界（负数或 > `max_score`）判为生成失败而**不截断**；落库新列；响应返回新字段。**必须有一条测试断言 `advisory_score` 进不了分数、`case_evidence`、`result_digest` 与导出** |
| **B1** | 展示参考分：与权威分**并列**且样式不得让参考分显得更权威；原样显示 `disclaimers`；未配置 provider 时不显示 |
| **MAIN** | 本变更；五份锁、五份 `module.yaml`、四个描述符同步 0.13.0；`rq-catalog.md` 新增 `RQ-T-30` |

## 7. 验证

```powershell
cd MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

实现落地后才能做（**未完成前不得声称已通过**）：

1. **参考分不是分**：断言 `evaluation-report` schema 拒绝含 `advisory_score` 的实例；
   断言 `/api/v1/grades` 与导出响应里不存在该键。
2. **删掉评语不影响分数**：清空评语表后重放同一提交，分数、逐项证据与 `result_digest` 完全相同。
3. **默认关闭**：未配置 provider 时 `GET` 返回 `unavailable`、`POST` 返回 `409`，无任何出网调用。
4. **越界即失败**：模型返回 `score > max_score` 或负数时，`status` 为 `failed`，
   **不是**被截断到合法区间。
5. **`max_score` 来自证据**：断言它等于证据里各用例权重之和，而不是模型返回的值。

## 8. 回滚方式

删除 `AdvisoryScore` 与 `EvaluationCommentary.advisory_score`，重新运行 `scripts/relock_contracts.py`，
版本回到 0.12.0；同时把 `ADR-0014` 状态改为"拒绝"并撤销 `RQ-T-30`，而不是只删字段。
B2 的列可以保留（回滚契约不需要回滚数据），但**不得**保留任何读它的代码路径。
