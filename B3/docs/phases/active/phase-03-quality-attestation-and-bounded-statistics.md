# Phase 03 质量凭证、可检索标签与有界统计

> **后续说明（2026-09-18，Phase 04）**：本文件下方的"验证记录"是 **Phase 03 当时的真实情况**（`345 passed`、
> `0.6.0` 契约基线、两项能力无出口），按"阶段记录不回头改写"的原则保留原文。
> 当前分支的状态以 [Phase 04](phase-04-quality-surfaces.md) 为准。
> 本文件仍未归档（归档门禁见 [../archive/README.md](../archive/README.md)）。

## 目标与范围
本轮把四项**平台里确实缺失**的能力从"靠人记得"变成"平台保证"，并补一条从未被使用的运维面：

1. **参考答案自证**（审计 I1）：`publish` 要求一份与版本内容**逐字节绑定**的验证凭证；
2. **标签写路径**（审计 I2）：`QuestionPublic.tags` 一直存在，但没有任何客户端能写入；
3. **有界统计**（审计 I3 / `RQ-B-3-30` 验收 5）：窗口查询把整窗载入内存，且没有任何实测；
4. **统计链路活着证明**（审计 I3）：`event_inbox = 0` 与 `question_version_stats = 0` 无法区分
   "还没有作答"与"事件全被拒收"。

另有两项纯确定性能力落地（审计 I5 诊断规则、审计 I4 修订闭环），它们的**出口**需要契约变更，本轮只实现
规则引擎与落库，见 [未完成](#未完成--阻塞)。

契约基线：`contracts.lock` → **`0.6.0`**，摘要 `d1d29b6cfe9c2bb9a35e964143d25b978d957d6ea8ff1947a43181f53c245119`
（本轮开始时是 `0.3.0`；期间 MAIN 落地了 CHG-0009~0011，`contracts.lock`/`module.yaml`/`plugin.py` 的
`contract_version` 由协调方更新。B3 按 `AGENTS.md` 第 3 条用 `checks.py` 的算法独立重算摘要确认一致。
任务简报里先给的是 `e943d0d7…`（0.3.0 之前的中间态），**与磁盘上的锁不一致**，以锁 + 实时契约文件为准。）

范围外：`MAIN/contracts/**`（未改）、其他模块、任何执行（B3 不执行参考答案，更不执行学生代码）。

## 输入与决策

| # | 输入 / 现象 | 决策 |
|---|---|---|
| 1 | 唯一验证器 `MAIN/scripts/verify_question_bank.py` 执行后**不留痕迹** | 新增 `b3.reference_attestations`，由外部执行器上报、B3 只**记录 + 强制**；B3 自身不运行任何东西 |
| 2 | 声明的摘要不可信（与 `CHG-B3-0002` 同一条原则） | 上报的 `content_digest` **只做交叉核对**：B3 用与服务端完全相同的路径重算，不符即 `409` |
| 3 | 外部验证器拿不到它该上报的摘要（草稿的 `content_digest` 一直是 `NULL`，只有发布时才写） | 草稿内容每次写入后**重算并持久化** `content_digest`，使其可经 `QuestionVersionPublic.content_digest` 读到——否则"绑定到重算摘要"这条要求无法被任何外部方满足 |
| 4 | 编辑内容必须自动使凭证失效 | 不需要任何失效逻辑：摘要变了，凭证自然不再匹配；**不提供**任何跳过/覆盖开关 |
| 5 | `tags` 三处齐备（契约读、DTO、列）但写路径结构性缺失 | `QuestionDraftInput.tags` → 校验 → jsonb；**拒绝**而非重写；词表即契约正则，不引入受控词表 |
| 6 | `inbox_rows_in_window` 用 `list(...)` 无 `limit` | 改为 keyset 分页（每页 1000 行）+ 硬上限 100 万行（超出 `422`，不静默截断）；新增 `(question_version_id, occurred_at, event_id)` 复合索引 |
| 7 | 统计端点无分页，`QuestionStatistics` 是单个聚合对象 | **不改**契约形状：窗口查询改为有界扫描，不引入新的分页参数 |
| 8 | 被拒事件不写 inbox，故"全被拒"与"没事件"在库里完全一样 | 新增 `b3.consumption_counters`（按 outcome 计数），消费器**每条路径**都计数；健康面复用 B4 的 `HealthReport` 约定 |
| 9 | 比较策略只做合法性校验，不做适配性判断 | 新增 `compile/diagnostics.py`（4 条规则，纯函数、零 AI），每条规则必须指向真实发生过的故障 |
| 10 | `versions_per_question = 1.00`，修订路径从未被真实使用 | `as_new_version` 分支时计算**结构化差异**并落库，附自动生成的修订理由（契约暂无可提交字段） |
| 11 | CHG-0011 落地 `RQ-T-10`：新增 `POST /api/v1/questions/{id}/authoring-assistance` | 实现 provider **默认关闭**的出题辅助：授权先于配置（不泄漏"本部署配没配 provider"）、出站 payload 按受保护词汇**当场断言**、回包越界即整份拒绝 |

## 完成条件

1. 没有与当前内容匹配的凭证时，`publish` 必须被拒，且错误信息指出补救动作。
2. 凭证只能被外部执行器写入，且必须通过服务认证。
3. 标签可读写；非法标签 `422`，不被静默改写。
4. 窗口统计扫描有界，且在同一量级下有真实数字。
5. 运维能区分"没有事件"与"事件被拒收"。
6. 套件转绿且 `ruff` 干净；新增能力都有测试。

## 验证（真实执行）

```powershell
cd B3
uv run ruff check .
uv run pytest -q
uv run alembic upgrade head

# 10 万事件量级实测（写入模块本地库 b3 schema）
uv run python scripts/benchmark_statistics.py --events 100000 --versions 100 --repeats 3
```

### 结果

```text
uv run ruff check .            → All checks passed!
uv run pytest -q               → 345 passed, 6 warnings in 34.56s
uv run alembic upgrade head    → Running upgrade 20260915_0002 -> 20260915_0003（exit 0）
uv run alembic heads           → 20260915_0003 (head)
uv run alembic current         → 20260915_0003 (head)
```

### 10 万事件量级实测（`RQ-B-3-30` 验收 5）

方法：向模块本地库 `b3.event_inbox` 直接插入 100,000 条**去标识化**事件（100 个版本，30 天均匀分布，
payload 与真实 `evaluation.completed` 同形），然后对三条读取路径各测 5 次，取中位数；峰值内存用
`tracemalloc` 统计（只计 Python 侧分配，即 ORM 对象，属**下界**）。

| 路径 | 中位耗时 | 峰值内存 | 说明 |
|---|---|---|---|
| `preaggregated`（无时间窗） | **0.044 s** | **0.66 MiB** | 读 `question_version_stats` + 两张 bucket 表；仪表盘走的就是这条 |
| `windowed_bounded`（`from`/`to`，1000 行/页） | **23.764 s** | **5.10 MiB** | 本次改动的路径 |
| `windowed_one_statement`（同窗口，单条无界语句 = 改动前的形状） | 8.327 s | **256.59 MiB** | **仅存在于基准脚本中**，作为对照基线 |

- **分页有效**：峰值内存 **256.59 MiB → 5.10 MiB（50.3×）**，与窗口宽度解耦。
- **预聚合有效**：10 万事件下 0.044 s / 0.66 MiB。
- **窗口路径不达标**：23.8 s 属于"不可用于交互式查询"。诚实结论是**验收 5 只满足一半**，
  因此 [traceability.md](../../requirements/traceability.md) 的状态词加了限定，而不是继续记 `已完成`。
  未做的补救（属后续工作）：把窗口求和下推到 SQL、或按时间桶预聚合——两者都不需要改契约。

原始输出：[`evidence/phase-03-statistics-benchmark.txt`](evidence/phase-03-statistics-benchmark.txt)。

> 注意：基准脚本会 **truncate 模块本地库的 `b3` 事件与统计表**并写入 10 万行。跑完请再执行一次
> `uv run pytest -q`（会话夹具会 drop `b3` 后从零迁移），以免本地库残留基准数据。

## 附：本轮新增/变更的对外可见面

| 面 | 变化 | 契约状态 |
|---|---|---|
| `POST /api/v1/question-versions/{id}/attestations` | 新增端点（`tags: [internal]`，`serviceToken`） | 已成文（CHG-0008） |
| `publish` 的前置条件 | 新增"必须有匹配 `content_digest` 的凭证" | 已成文（CHG-0008 描述已更新） |
| `QuestionDraftInput.tags` | 写路径实现 | 已成文（CHG-0008） |
| `PATCH /questions/{id}?as_new_version` | 已是实现；现在会同时落修订差异与理由 | 已成文（CHG-0008） |
| 健康面（B4 readiness 报告里的 B3 detail；`consumer_cli --health`） | 新增消费状态文案与 JSON | B3 内部，B4 已声明的 `HealthReport.detail` 承载 |
| `POST /api/v1/questions/{id}/authoring-assistance` | 新增端点（出题期模型建议，provider 默认关闭） | 已成文（CHG-0011） |
| 修订理由 / 修订差异的**读写出口** | 落库 + 测试，**无出口** | **未成文**（见下） |
| 比较策略诊断的**教师可见出口** | 规则引擎 + 测试，**无出口** | **未成文**（见下） |

## 未完成 / 阻塞

- 无 `阻塞` 项。
- **两项能力已实现但无处触达**（不伪报为可用）→ **2026-09-18 已解除，见 [Phase 04](phase-04-quality-surfaces.md)**：
  MAIN 以 `CHG-0013` 补齐了出口，本阶段记录的缺口不再是缺口，原始记述按"阶段记录不回头改写"保留：
  1. `revision_note`（自动生成的修订理由）与 `revision_diff`（结构化差异）已写入
     `b3.question_versions`，序列化形状经断言不含任何受保护词汇；但契约的
     `QuestionDraftInput` 没有可提交的字段、`QuestionVersionPublic` 没有可读取的字段
     （该 schema `additionalProperties: false`）。**缺的是契约**：`QuestionDraftInput.revision_note`
     （可选 string）与 `QuestionVersionPublic.revision_diff`（可选 object）。
  2. 比较策略诊断（`CMP-001..004`）已实现并被测试覆盖，`analyse_comparison` 是纯函数；
     但没有教师可见出口。**缺的是契约**：`GET /api/v1/questions/{id}/diagnostics`（返回规则 id +
     severity + 可读说明，**不含期望输出正文**），或 `QuestionVersionPublic.diagnostics` 字段。
     `publish` 上挂 `warning` 级提示也需契约先声明语义。
  > Phase 04 的落地结果与本节有一处**不一致**，如实记录：本阶段写的是"严重度 + 可读说明"，
  > 而实现里的 `Severity` 有一个契约未声明的取值 `note`（契约 enum 为 `error|warning|info`）。
  > Phase 04 已把该取值改为 `info`，即**改实现对齐契约**。
  > （曾把这一点描述为"契约 description 中的笔误"，`CHG-0016` §5 已驳回：契约不存在该笔误。）
  > 另外：`revision_diff` 的**存储形状**在 Phase 04 被改为契约形状（删除旧的内部结构），
  > 因此本阶段"序列化形状经断言不含任何受保护词汇"这一结论仍然成立，但断言的键集合已变。
- **`RQ-B-3-30` 验收 5 未达标**（窗口路径 23.8 s），已如实写进 traceability 与 acceptance。
- 跨模块未联调部分按 B3 `AGENTS.md` 要求继续保持未完成表述：B2 runner 端到端、NATS 真实
  durable consumer / DLQ（本环境无 NATS）、B4 真实 JWKS 的线上轮换、Linux 冷启动。
  本轮的健康面**只证明"能把拒绝与空闲区分开"**，不证明真实 NATS 链路曾跑到过——`event_inbox = 0`
  在真实环境里依然是当前状态。
- **出题辅助没有对真实模型服务做过端到端调用**：provider 默认关闭，套件用注入的测试 provider 验证
  边界（出站 payload 无受保护内容、回包越界即拒、调用不改题）。`HttpAuthoringProvider` 的真实
  HTTP 路径只在本环境构造过请求体，**没有对任何真实端点发出过请求**——这一点不写成"已验证"。
- **`MAIN/scripts/verify_question_bank.py` 没有实现 `--emit-attestation`**（该文件属 MAIN，B3 未改）。
  当前状态下发布门禁是**真的**（无凭证即拒发），但凭证必须由 MAIN 侧验证器上报；
  在 MAIN 落地该开关之前，新题目需要手工调用一次内部端点，或由 MAIN 补上这一环。
  这是**跨模块缺口，已上报**，B3 不做自我声明式的替代路径（那会让凭证失去意义）。
- `MAIN/contracts/**` 与 `contracts.lock` 由 MAIN 修改与重新锁定；B3 未改这两个位置。
  **注意**：B3 是 `MAIN` 的兄弟目录，本轮对 `MAIN/contracts` 只读。
