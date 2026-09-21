# CHG-0012 逐用例证据补全：资源实测、文件证据、确定性解释

- **级别**：P1 增补（`case_evidence` 新增三个**可空**字段）
- **状态**：已生效（2026-09-18 判定）
- **契约版本**：0.6.0 → **0.7.0**
- **依据**：`ADR-0005`（可解释/可重放）、`ADR-0010`（隔离可证明）、创新清单簇 B

## 1. 问题：三处"算过就丢"与"声称但无实现"

审计与实测发现三件事，都是**证据链断在半路**：

1. **资源账本只有配置值，没有实测值。** 逐用例的资源使用从未落进证据，于是"1 vCPU / 512 MB"
   只是**限额声明**。`ADR-0010` 决策 3 要求把 cgroup 的 `memory.peak` / `cpu.stat` / `pids.peak`
   回读落证据 —— 但 `case_evidence` 里没有字段可放（`additionalProperties: false`）。
2. **逐用例文件摘要算完即丢。** B2 的 `FixtureOutcome` 已经带着 expected/actual 的 sha256，
   而 `build_evidence` **只拿它挑一段文本**，摘要既不进 `CaseEvidence`、也不进 `result_digest`、也不进报告。
   后果：`ADR-0005` 第 3 条只兑现一半，而 `RQ-B-0-30` 验收 2（"被修改文件清单与内容摘要"）
   **四个模块里没有任何一个实现**。
3. **报告没有面向人的解释。** 结构化字段齐全，但学生/教师看不到"这个用例为什么没过"的一句话说明。
   `ADR-0005` 的后果节点本来就允许**结构化规则说明文本**作为缓解措施 —— 缺的是字段。

## 2. 变更内容

`evaluation-report.schema.json` 的 `case_evidence.items` 新增三个字段，**全部可空**：

| 字段 | 内容 | 为什么可空 |
|---|---|---|
| `resource` | `memory_peak_bytes` / `cpu_user_seconds` / `cpu_system_seconds` / `pids_peak` / `wall_seconds` / `limit_hit` | 沙箱可能**能强制**限额却**无法回读**峰值。"强制了但没测量"与"测出来很小"是两件不同的事；用 0 表示"没测量"会被读成"没占用" |
| `artifacts` | `expected_sha256`（受保护）/ `actual_sha256` / `created` / `modified` / `deleted`（每项含 `path`、`content_sha256`、`size_bytes`） | 非文件类题型没有产物 |
| `explanation` | 由**固定规则**从本对象内的证据推出的说明文本 | —— |

### 2.1 一条必须写明的边界：`explanation` 不是模型输出

schema 描述里写死了这句：**它是规则推导的、可重放的，因此可以作为报告的一部分**；
而模型生成的教学评语走**另一条轨**（`ADR-0009` / `RQ-T-20`），**永不进入这里**。

这不是靠约定：本变更验证时实测过 —— **在 `case_evidence` 里加一个模型输出字段会被 schema 直接拒绝**
（`additionalProperties: false`）。"模型输出进不了报告"是结构性事实，不是一句承诺。

### 2.2 `artifacts` 的受保护面

`expected_sha256` 标为 **PROTECTED**：摘要不是内容，但它是一个**可检验的预言机**（猜到文件内容
即可验证），因此只允许在**可见用例**上出现。`created`/`modified`/`deleted` 各自上限 200 条 ——
失控的用例不得产出无界报告。

## 3. 是否破坏兼容

**否。** 三个字段均可选；既有报告不含它们时仍然合法。MINOR 升到 0.7.0。

## 4. 受影响模块与需要做的改动

| 模块 | 改动 |
|---|---|
| **B2** | 执行后回读 cgroup 峰值；把 `FixtureOutcome` 的摘要与文件变更清单落进证据；实现规则式 `explanation`。**不得**把模型输出写进这三个字段 |
| **B1** | 呈现资源、文件证据与解释；`limit_hit` 要显示成"触发了哪一类限额"而不是一个枚举值 |
| **B5** | 其一致性报告顺带覆盖新字段（无需专门改动） |
| **MAIN** | 本变更；五份锁、五份 module.yaml、四个描述符（B1 无 Python 描述符）同步 0.7.0 |

## 5. 验证

```powershell
cd MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

本变更特有的验证（**已执行**）：

1. schema 自身通过 `Draft202012Validator.check_schema`；
2. 一份带 `resource`/`artifacts`/`explanation` 的报告实例**通过校验**；
3. **在 `case_evidence` 中插入模型输出字段被拒绝** —— 即 §2.1 的边界是结构性的。

实现侧验证（**未完成，不得声称已通过**）：真实执行后 cgroup 峰值落库、文件变更清单与摘要出现在报告里、
`explanation` 是纯规则且同输入同输出。

## 6. 回滚方式

删除三个字段与 `$defs` 中的 `case_resource`/`case_artifacts`/`file_change_list`，重新运行
`scripts/relock_contracts.py`。回滚会使 `ADR-0010` 决策 3 失去落点、`RQ-B-0-30` 验收 2 重新变成
无人实现，因此必须同步修改 `ADR-0010` 与 `rq-catalog.md`，不能只删字段。
