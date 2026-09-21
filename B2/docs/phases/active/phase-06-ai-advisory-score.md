# Phase 06 AI 评分参考（`RQ-T-30` / `ADR-0014` / `CHG-0018`）：生成与存储

- **范围**：B2 侧**生成与存储**。B1 的展示侧已完成且契约已冻结，本批**不改 B1**。
- **契约**：`MAIN/contracts/openapi/b2.yaml` 的 `EvaluationCommentary.advisory_score` 与 `AdvisoryScore`
  （版本以 `module.yaml: contract_version` 为准，本文件不写死字面值）。
- **裁决**：[ADR-0014](../../../../MAIN/docs/adr/ADR-0014-ai-advisory-score.md) 六条决策；
  变更 [CHG-0018](../../../../MAIN/docs/contracts/changes/CHG-0018-ai-advisory-score.md)（§7 五条验证）。
- **需求**：[RQ-T-30](../../../../MAIN/docs/requirements/rq-catalog.md) 九条验收。

## 改了什么

| 落点 | 文件 | 内容 |
|---|---|---|
| 迁移 | `alembic/versions/20260915_0014_b2_advisory_score.py` | 评语表加四列 + 重写 shape CHECK + 三条参考分约束（**可逆**，见下） |
| 映像 | `src/autograder_b2/db/schema.py:305-327`（四列）、`:19`（`EXPECTED_SCHEMA_REVISION`） | 与 DDL 逐列对齐（含顺序） |
| 清单 | `module.yaml:13` | `migration_head` 推进到 `20260915_0014`（**只改这一行**） |
| prompt | `src/autograder_b2/commentary/prompt.py` | `PROMPT_VERSION` `b2-commentary/1` → `/2`；系统提示改为"只输出一个 JSON 对象"并写明满分/越界纪律；`DISCLAIMERS` 增加第三条（针对分数参考） |
| 解析 | `src/autograder_b2/commentary/parsing.py`（**新增**） | `parse_generation(raw) -> Generation(text, advisory)`；形状违例一律抛 `CommentaryResponseError`（**不退化**成纯文本评语） |
| provider | `src/autograder_b2/commentary/provider.py` | 只负责从响应信封里取 content（长度/形状判定移交 `parsing`）；不再对 JSON 做文本归一 |
| 满分来源 | `src/autograder_b2/commentary/scope.py:128-141, 213-220` | `CommentaryEvidence.case_weight_total`：对 `b2.case_evidence.weight` **SUM**（与 `evaluation/scoring.py` 的满分同源同值） |
| 边界与落库 | `src/autograder_b2/commentary/service.py:96-186` | `resolve_advisory(proposal, max_score=...)`：满分只有这一个入口；越界/无用例权重 ⇒ `AdvisoryScoreRejected` ⇒ 整次 `failed` |
| 存储 | `src/autograder_b2/commentary/store.py` | `AdvisoryScore` 记录类型；`CommentaryRecord.advisory`；`as_contract` 输出 `advisory_score`；`begin/finish/fail` 各自处理四列（重新生成先清空） |
| 导出 | `src/autograder_b2/commentary/__init__.py` | 新模块与新符号的导出；包 docstring 改写为四条硬边界 |

**没有**改：`MAIN/contracts/**`、`B2/contracts.lock`、`B2/pyproject.toml`、`plugin.py` 的
`descriptor.contract_version`、路由响应结构（它由 `store.as_contract` 决定）、`scope.INPUT_SCOPE`
（`ADR-0014` 决策 4：白名单与评语轨**完全相同**，一个字段都没放宽）。

## 六条决策的落点（逐条）

| `ADR-0014` | 实现 | 断言 |
|---|---|---|
| 1 子对象而非平级数字 | 契约已声明；`as_contract` 只放 `advisory_score`，从不放平级 `score` | `test_commentary_responses_validate_against_the_contract_schema`（路径必须走子对象） |
| 2 `max_score` 由 B2 填入 | `evidence.case_weight_total` → `resolve_advisory`；模型给同名键 ⇒ `CommentaryResponseError` ⇒ `failed` | T-ADV-007 / T-ADV-008 |
| 3 与分数轨物理分离 | 只写 `b2.evaluation_commentaries`；`advisory` 形状的列只此一张表 | T-ADV-001/002/003/004 |
| 4 白名单完全相同 | `INPUT_SCOPE` 未变；`input_scope` 随 provenance 返回并与声明逐项相等 | `test_collected_evidence_reads_exactly_the_declared_whitelist`、T-CMT-007 |
| 5 显式标注 | `DISCLAIMERS` 第三条；`unavailable` 与"这次没有参考分"两种情形都返回 | T-ADV-005/010 |
| 6 可重新生成、不改变权威值 | 覆盖同一行（四列一并清空重写）；三次运行比对分数与 `result_digest` | T-CMT-008、T-ADV-004 |

## 验证（真实执行）

### 1. 迁移三步可逆（**只在 B2 测试实例 55433 上执行**，未触碰 55432）

```powershell
cd B2
$env:DATABASE_URL = "postgresql+psycopg://autograder_platform:***@127.0.0.1:55433/autograder"
uv run alembic upgrade head      # 0013 -> 0014
uv run alembic downgrade -1      # 回到 0013
uv run alembic upgrade head      # 再到 0014
uv run alembic heads
uv run alembic upgrade head --sql
```

每一步都配一个只读结构探针（`information_schema.columns` + `pg_constraint`），原始读数：

```text
### step 0: probe before
revision=20260915_0013
（无 advisory_* 列，无 advisory 约束）

### step 1: upgrade head -> probe
revision=20260915_0014
col: advisory_score numeric null=YES
col: advisory_max_score numeric null=YES
col: advisory_rationale text null=YES
col: advisory_confidence text null=YES
con: ck_evaluation_commentaries_advisory_bounds
con: ck_evaluation_commentaries_advisory_confidence
con: ck_evaluation_commentaries_advisory_rationale

### step 2: downgrade -1 -> probe
20260915_0013
revision=20260915_0013
（advisory_* 列与三条约束均消失）
shape=CHECK ((((status = 'pending'::text) AND (text IS NULL) AND (failure_reason IS NULL) AND (generated_at IS NULL)) OR ((status = 'ready'::text) AND (text IS NOT NULL) AND (failure_reason IS NULL) AND (generated_at IS NOT NULL)) OR ((status = 'failed'::text) AND (text IS NULL) AND (failure_reason IS NOT NULL) AND (generated_at IS NOT NULL))))
   ← 与 0010 的原文逐字相同：shape CHECK 被**还原**，不是被删掉

### step 3: upgrade head -> probe
20260915_0014 (head)
revision=20260915_0014
（四列与三条约束全部回来）
```

head 上的两条新约束定义（同一实例，只读）：

```text
shape=CHECK ((((status = 'pending'::text) AND (text IS NULL) AND (failure_reason IS NULL) AND (generated_at IS NULL) AND (advisory_score IS NULL) AND (advisory_max_score IS NULL) AND (advisory_rationale IS NULL) AND (advisory_confidence IS NULL)) OR ((status = 'ready'::text) AND (text IS NOT NULL) AND (failure_reason IS NULL) AND (generated_at IS NOT NULL) AND (((advisory_score IS NULL) AND (advisory_max_score IS NULL) AND (advisory_rationale IS NULL) AND (advisory_confidence IS NULL)) OR ((advisory_score IS NOT NULL) AND (advisory_max_score IS NOT NULL) AND (advisory_rationale IS NOT NULL) AND (advisory_confidence IS NOT NULL)))) OR ((status = 'failed'::text) AND (text IS NULL) AND (failure_reason IS NOT NULL) AND (generated_at IS NOT NULL) AND (advisory_score IS NULL) AND (advisory_max_score IS NULL) AND (advisory_rationale IS NULL) AND (advisory_confidence IS NULL))))
bounds=CHECK (((advisory_score IS NULL) OR ((advisory_max_score > (0)::numeric) AND ((advisory_score >= (0)::numeric) AND (advisory_score <= advisory_max_score)))))
```

**离线模式（`--sql`）也走通**（1117 → 1130 行，新增的正是 0014 的 DDL）：

```text
$ uv run alembic upgrade head --sql   # -> exit 0，1130 行
ALTER TABLE b2.evaluation_commentaries ADD COLUMN advisory_score NUMERIC;
ALTER TABLE b2.evaluation_commentaries ADD COLUMN advisory_max_score NUMERIC;
ALTER TABLE b2.evaluation_commentaries ADD COLUMN advisory_rationale TEXT;
ALTER TABLE b2.evaluation_commentaries ADD COLUMN advisory_confidence TEXT;
ALTER TABLE b2.evaluation_commentaries DROP CONSTRAINT ck_evaluation_commentaries_shape;
ALTER TABLE b2.evaluation_commentaries ADD CONSTRAINT ck_evaluation_commentaries_shape CHECK (...);
ALTER TABLE b2.evaluation_commentaries ADD CONSTRAINT ck_evaluation_commentaries_advisory_bounds CHECK (advisory_score IS NULL OR (advisory_max_score > 0 AND advisory_score BETWEEN 0 AND advisory_max_score));
ALTER TABLE b2.evaluation_commentaries ADD CONSTRAINT ck_evaluation_commentaries_advisory_confidence CHECK (advisory_confidence IS NULL OR advisory_confidence IN ('low', 'medium', 'high'));
ALTER TABLE b2.evaluation_commentaries ADD CONSTRAINT ck_evaluation_commentaries_advisory_rationale CHECK (advisory_rationale IS NULL OR char_length(advisory_rationale) BETWEEN 1 AND 2000);
```

### 2. `ruff check .`（原始输出）

```text
All checks passed!
```

### 3. `pytest`（原始 summary 行）

```powershell
$env:B2_TEST_DATABASE_URL = "postgresql+psycopg://autograder_platform:***@localhost:55433/autograder"
$env:B2_TEST_NATS_URL = "nats://localhost:54223"
uv run pytest
```

SDK 状态：**已刷新**（本轮先跑了
`uv sync --all-groups --reinstall-package autograder-platform`，输出 `Installed 1 package`）。
基线与最终两次全量运行的原始 summary 行：

```text
本批改动前（工作区起点）：4 failed, 1214 passed, 9 skipped, 2 xfailed
本批第一次（修 0012 相对修订假设之前）：4 failed, 1232 passed, 9 skipped, 2 xfailed, 2 warnings in 381.86s (0:06:21)
本批最终：3 failed, 1233 passed, 9 skipped, 2 xfailed, 2 warnings in 382.98s (0:06:22)
```

失败条数在 3 与 4 之间不是不稳定代码，而是上面那对**既有 flaky** 用例（本次只失败了其中一条）；
逐条根因见下"环境读数"。收集总数：**1247** = 1233 passed + 3 failed + 9 skipped + 2 xfailed
（本批前为 1229，差额 **+18** 全部来自 T-ADV 的 9 个用例函数 / 18 个用例项）。

> **后续（已解决）**：本批记录的那 3～4 条既有失败已由 MAIN 指定的整改批次修掉（四条**都是用例缺陷**，
> 产品代码零行为变更），最新读数为 **`1298 passed, 9 skipped, 2 xfailed`（收集 1309，退出码 0）**；
> 逐条根因/改法/前后读数与"不是缺 tie-breaker"的订正见
> [phase-08-known-failure-remediation.md](phase-08-known-failure-remediation.md)。

### 4. 真实 provider 调用（集成环境已配置 DeepSeek）

用**生产代码路径**（`CommentaryService` + `HttpCommentaryProvider` + 真 `UrllibTransport`）对
`.env.integration` 里的 `deepseek` / `deepseek-flash` 发了一次真实请求；证据来自真实 PostgreSQL
（临时库迁移到 head + 一次真实编排），prompt 由白名单载荷渲染，解析与边界判定走生产代码。
探针脚本是一次性的（位于 gitignore 的 `.pytest-tmp/real_provider_call.py`，复用
`tests/test_commentary_track.py` 的同一批端口替身），**不属于交付内容**。

请求/响应结构（**不含任何凭据**）：

```text
# orchestration: state=completed score=6.0 run=2e167f49-4bd4-4328-9695-77d00b6da10e
# provider request: url=https://api.deepseek.com/chat/completions
# provider request: payload keys=['messages', 'model', 'stream'] prompt_chars=1847
# provider response: HTTP 200, envelope_chars=5280
# provider raw content (模型输出，原样) >>>
{"commentary":"本次评测共 2 个用例……需结合失败用例的输入输出进一步定位。","advisory_score":{"score":6.0,"rationale":"两个用例权重合计 10.0，用例 0 权重 6.0 且通过，用例 1 权重 4.0 且因 output_mismatch 未通过。……","confidence":"medium"}}
# <<< provider raw content end
# elapsed_ms=7787 ok=True failure=None
# input_scope == INPUT_SCOPE: True
# stored row: ('ready', Decimal('6.0'), Decimal('10.0'), 'medium', 'b2-commentary/2')
# case weights: ['6.0', '4.0']
```

契约响应体（`advisory_score` 一段逐字）：

```text
"status": "ready",
"advisory_score": {
  "score": 6.0,
  "max_score": 10.0,
  "rationale": "两个用例权重合计 10.0，……",
  "confidence": "medium"
},
"provenance": {"provider": "deepseek", "model": "deepseek-flash", "prompt_version": "b2-commentary/2", "input_scope": [21 项 == INPUT_SCOPE]},
"disclaimers": [三条，第三条即参考分标注]
```

三点可核对的读数：① 模型**没有**给出 `max_score`，而落库的 `advisory_max_score = 10.0` 正是
`6.0 + 4.0`（用例权重之和）；② `prompt_version` 已是 `b2-commentary/2`；③ 真实响应一次通过解析，
`status=ready`，耗时 **7787 ms**（`B2_COMMENTARY_TIMEOUT_SECONDS=120` 之内）。

### 5. 新增/更新的用例

`tests/test_commentary_track.py` **新增 9 个用例函数 = 18 个用例项**（两条参数化：越界 5 组、畸形响应 6 组）：

- T-ADV-001..011（逐条见 [../../testing/cases.md](../../testing/cases.md) 的"AI 评分参考"一节），
  其中 T-ADV-003/011 是**纯结构**用例（不挂 `requires_postgres`，无库环境里也真的在跑）。
- 更新 4 条既有用例：T-CMT-004（重放时评语行带上参考分四列）、T-CMT-007（断言
  `prompt_version == b2-commentary/2` 且 prompt 含 JSON 协议与"不要给出满分"）、
  T-CMT-008（重新生成覆盖参考分）、T-CMT-010（**含参考分**的响应体也逐字过契约 schema）。
- 结构用例改名并加固：`test_commentary_table_has_no_score_and_no_evidence_column` →
  `test_commentary_table_carries_only_the_advisory_score_columns`（表里现在确实有分数形状的列，
  因此判据改成"只有 `advisory_*` 那一组，权威分数轨的列名一个都不在"）。

收集总数（本树）：**1247** 条 = 1233 passed + 3 failed + 9 skipped + 2 xfailed（本批前 1229，差额 +18）。

## 环境读数：既有失败与本批暴露的一条

本批最终全量运行的失败集合（**逐条列出，不合并**）：

| # | 用例 | 与本批的关系 |
|---|---|---|
| 1 | `test_grades_query_export.py::test_cursor_pagination_is_complete_and_disjoint` | **既有**：迁移 0012 的毫秒四舍五入让 `_seed_pages` 的 `0/120/350` µs 折成同一毫秒，排序退化 ⇒ 随机 UUID 决定成败（flaky，与 `::test_pagination_is_keyset_not_offset` 是同一对，实测两者不同时失败） |
| 2 | `test_grades_query_export.py::test_xlsx_timestamp_truncates_like_the_api_not_rounds` | **既有**：期望截断 `.123999 → .123`，库列四舍五入到 `.124`（确定性失败） |
| 3 | `test_migrations.py::test_every_timestamp_column_has_millisecond_precision` | **既有**：`import alembic.versions` 在本仓库解析到 site-packages 的 Alembic（`ModuleNotFoundError`；确定性失败） |

**本批暴露并已修掉的一条**（原先不在"既有 4 条"里，是**新迁移引入的**，因此必须修而不是登记）：
`test_migrations.py::test_standard_remediation_round_trip_is_reversible_and_idempotent` 用
`downgrade -1` **两次**来回到 `0012` 之前的状态，即隐含假设"head 只比 `0011` 高两级"。
0014 一加进来，两次 `-1` 只到 `0012`，于是断言读到的仍是已整改结构（`pk_*` 而不是 `*_pkey`）而失败。
修法是把回滚目标写成**修订号** `20260915_0011`（用例要验的是"0012 的 downgrade 还原它之前的结构"，
与它上面还有几个修订无关），并在 docstring 里写明这次更正的原因；断言本身一条都没放宽。
单独复跑：`2 passed, 21 deselected in 11.70s`（连同 `test_fresh_database_needs_no_correction_...`）。

**未顺手修**的仍是上表 1–3：它们的根因与本批无关，且"修 flaky 的判据"要改成绩分页用例的期望——
那是另一个决定（详见 [phase-05](phase-05-quarantine-and-quality-surfaces.md) 的"未执行/未验收"第 1 条）。

> **后续（已解决）**：上表 1–3 已由后续整改批次修掉，最新读数
> **`1298 passed, 9 skipped, 2 xfailed`（收集 1309，退出码 0）**；逐条根因/改法/前后读数见
> [phase-08-known-failure-remediation.md](phase-08-known-failure-remediation.md)。**上表第 1 条的行文需订正**：
> 不是"排序退化"，而是用例按**种子顺序**断言、而声明顺序是全序 `(created_at ASC, submission_id ASC)`——
> `submission_id` 一直都在做 tie-breaker（12 次连续运行的确定性实测见 phase-08）。

## 未执行 / 未验收（**不写成通过**）

1. **B1 侧的并列展示**：本批只证明 B2 的响应形状与语义。B1 已完成展示侧（`advisory_score` 低权重并列、
   `null` 不渲染、反向断言成绩轨道模块不含 `advisory`），但**浏览器端到端未做**——属 B1 的工作包。
2. **未通过运行中的集成 host（18000）发起 `/commentary` 请求**：那需要一份教师 JWT 与一个真实 run，
   且运行中的 host 进程早于本批构建（其 `/commentary` 不带 `advisory_score`，见 B1 的 phase-03 记录）。
   真实 provider 调用是**直接走生产代码路径**做的（见上），未重启任何服务。
3. **真实 provider 只跑了一次**：模型输出是非确定性的，一次读数只能证明"这条路径真的能通"，
   不能证明协议在真实模型上的**通过率**。越界、畸形响应、模型自带 `max_score` 这些路径
   用传输替身覆盖（真实模型无法被要求返回特定的坏响应）。
4. **判决与越界语义没有在真实模型上触发过**：`AdvisoryScoreRejected` 只在替身下被验证过。
5. **本模块未触碰 `55432` 集成库**：迁移与探针都在 B2 测试实例（`55433`）与临时库上执行。

## 已上报的契约 / 实现边界问题（**B2 未自行裁决**）

1. **模型自带 `max_score` 时怎么办，契约没有规定**。`ADR-0014` 决策 2 只说"由 B2 填入、不由模型生成"。
   本实现取**最严**的一种：`advisory_score` 对象里出现 `max_score` ⇒ 整次生成 `failed`
   （理由：静默忽略一个模型给的满分，等于让"模型说了什么"和"系统用了什么"分叉，而这正是本 ADR 要防的事）。
   请 MAIN 确认这是否是要写进契约的措辞；若是，`AdvisoryScore` 的描述里可以补一句
   "unknown properties are rejected"。
2. **模型协议违规返回 `503`**：契约的 `POST` 只声明了 `409` 与 `503`，没有 `422`。
   本实现把"provider 不可用 / 响应形状不符 / 参考分越界"统一归到 `503` + 指名原因，
   并把该条评语落成 `status: failed`（随后 `GET` 读得到）。语义上"503 = 一个必需依赖不可用"
   覆盖"模型没按协议回答"是拉伸的——但新增状态码属契约变更，B2 不自行发明。
3. **`AdvisoryScore.score` 无上下界声明**：`score <= max_score` 这条不变式在 JSON Schema 里表达不了，
   契约把拒绝逻辑留给实现（本实现落在服务层 + 库级 CHECK）。这是契约的合理留白，非缺陷，此处如实记录。

另发现一处**与本批无关的既有文档缺陷**（未顺手修改）：`docs/api/contract.md` 的
`## 1b. 教学评语轨…` 标题出现了两次（第 40 行与第 51 行），第 40 行那个下面挂的是成绩端点的契约缺口
说明，看起来是一次复制粘贴遗留。
