# B2 迁移策略（Migrations）

## Alembic 约定

- **版本表名**：`alembic_version_b2`（独立版本表，见 `../../alembic.ini` 与 `alembic/env.py` 的 `version_table` 参数）。
- **版本表 schema**：`b2`（与迁移目标 schema 一致，`env.py` 已配置 `version_table_schema="b2"`）。
- **命名格式**：`YYYYMMDD_NNNN_b2_<short-description>.py`（现有基线 `20260915_0001_b2_schema_baseline.py`）。
- **一次迁移一件事**：一个迁移文件只做一项结构变更（建一张表 / 加一列 / 改一约束）。
- **可回滚性**：`upgrade()` 必须配 `downgrade()`；无法回滚的变更须在迁移文件 docstring 明确说明原因。
- **不手改库**：所有结构变更都必须有对应迁移文件（RQ-B-4-30）。

## 现有迁移与 head

| revision | 内容 | 说明 |
|---|---|---|
| `20260915_0001` | `CREATE SCHEMA IF NOT EXISTS b2`（upgrade）/ `DROP SCHEMA IF EXISTS b2`（downgrade） | 基线迁移，无业务表 |
| `20260915_0002` | 接收与存储业务表 | `submissions`/`artifacts`/`submission_files`/`email_checkpoints`/`intake_quarantines`（WP-B2-A） |
| `20260915_0003` | 孤儿回收审计表 | ADR-0003：回收只删**无引用**内容，且必须有审计记录 |
| `20260915_0004` | 事件 outbox 表 | 先写 outbox 再发（at-least-once）；DLQ 不静默 |
| `20260915_0005` | 提交状态迁移史 | 每次迁移都有记录；失败迁移保留错误类别与脱敏原因 |
| `20260915_0006` | 评测链业务表 | 运行、进度、证据、报告、判决、防重 |
| `20260915_0007` | 修正 0006 两条过度收紧的 CHECK | `completed` 是成功态，不应被迫携带终态原因 |
| `20260915_0008` | 提交 provenance 6 列 | 持久化冻结信封的 `correlation_id` 与 intake provenance |
| `20260915_0009` | outbox 插入序号 + 终态原因配对 | 把两条"声称的不变量"变成**可强制**的不变量 |
| `20260915_0010` | 评语轨存储表 | `evaluation_commentaries`（`RQ-T-20`/`ADR-0009`）：非确定性模型输出有自己的位置，删除它不影响可重放性 |
| `20260915_0011` | 运行表补三态安全结论列 | `evaluation_runs.security_status`（`found`/`not_found`/`not_checked`，NULL＝没走到安全判定）：评语轨白名单里"三态安全结论"的**唯一**存储落点 |
| `20260915_0012` | 表标准整改（`RQ-B-4-30` / 缺陷 E16） | 18 个主键约束改名为 `pk_<table>` + 7 个 `created_at`/`updated_at` 列收窄到 `timestamptz(3)` |
| `20260915_0013` | 隔离区人工归属落点（`CHG-0014`） | `intake_quarantines` 加 `resolved`/`resolved_student_id`/`resolution_note` + `ck_intake_quarantines_resolution`（`resolved = (resolved_student_id IS NOT NULL)`） |
| `20260915_0014` | AI 评分参考的存储落点（`RQ-T-30` / `ADR-0014` / `CHG-0018`） | `evaluation_commentaries` 加 `advisory_score`/`advisory_max_score`/`advisory_rationale`/`advisory_confidence` 四列；**重写** `ck_evaluation_commentaries_shape`（把四列纳入状态配对）；新增 `ck_evaluation_commentaries_advisory_bounds`（`0 <= score <= max_score` 且 `max_score > 0`）、`..._advisory_confidence`、`..._advisory_rationale`（当前 head） |

- 当前 head = **`20260915_0014`**，三处一致（`uv run alembic heads` 实测为
  `20260915_0014 (head)`）：`module.yaml: database.migration_head`、`db/schema.py: EXPECTED_SCHEMA_REVISION`、
  Alembic 自身。**注意**：`EXPECTED_SCHEMA_REVISION` 是 readiness 判据，因此**部署时必须先跑
  `uv run alembic upgrade head`**——库停在上一个 revision 时评测/成绩/评语路由会按设计返回 503
  （如实报"库结构版本与本构建不符"，而不是带着旧结构继续服务）。
- **0014 为什么加在评语表里而不开新表**：契约把参考分定成 `EvaluationCommentary` 的**子对象**，
  且 `CHG-0018` §4 明确没有"只生成参考分"的开关——两者是**同一次生成**的产物。开第二张表会造出
  "有分无评语"这种没有语义的状态组合。四列与 `text` 同生同死，由 shape CHECK 强制。
- **0014 为什么要把 `0 <= score <= max_score` 写进库**：`ADR-0014` 决策 6 规定越界即**整次生成失败**，
  因此越界值从来不该存在。这条 CHECK 让"服务层的判断"与"库里的行"不可能分叉，
  并且它是**拒绝**而不是截断——截断会把"模型说了什么"悄悄改成"我们允许它说什么"。
- **0014 的数据后果**：纯加列，**不改动任何既有行**（历史评语在新四列上都是 `NULL`，如实读成
  "那次生成没有参考分"——那些生成发生在参考分存在之前）。`downgrade()` 删四列、删三条新约束，
  并把 shape CHECK 还原成 0010 的逐字原文；**已生成的参考分会随之丢失**，降级后必须把这件事
  写进运维记录。（契约侧回滚还要删 `AdvisoryScore` 并重新 relock，见 `CHG-0018` §8。）
- **0013 为什么必须新增一列而不是复用自由文本**：契约 `IntakeQuarantineView` 要求返回
  `resolved`/`resolved_student_id`/`resolution_note`，而 `b2.intake_quarantines` 从建成起只写不读、
  没有任何"处理状态"。把归属塞进 `detail` 会让"这封信被谁认领过"变成不可查询的事实；
  而 `resolved = (resolved_student_id IS NOT NULL)` 这条 CHECK 把"已归属 ⇔ 有归属对象"钉成单一事实——
  少了它，"已处理但处理给谁不知道"这种行可以落库。`resolution_note` 不参与该约束（契约把 `note` 定为可选）。
- **0013 的数据后果**：纯加列，**不改动任何既有行**；`resolved` 的默认值 `false` 让 0002 以来写下的
  每一条隔离记录都如实读成"未处理"（那正是它们当时的真实状态）。`downgrade()` 会**丢失已记录的归属**
  （三列被删）——降级后必须把"隔离记录重新变成不可辨认"写进运维记录，而不只是删列。
- **0011 为什么是"补列"而不是"让下游去猜"**：`not_found`（跑了、没发现）与 `not_checked`（根本没跑）
  在 `security_findings` 里都是"零行"；从行数推断会把后者报成前者，正是本项目明令禁止的
  "把未检查写成安全"。从报告 JSON 里读也一样不行——那份文档同时承载隐藏用例的期望输出。
- 0011 只增一个**可空**列且不回填：历史行保持 NULL（那时确实没有这一列的事实），不猜测、不伪造。
- **0012 为什么是"再补一个修订"而不是改历史迁移**：`0002`–`0011` 已应用到集成库（`55432`，
  内含已播种演示数据）。改写已应用的修订会让 `alembic_version` 与实际结构反向漂移。
  因此 0012 只做向前修正：主键名与列精度都在**执行时**现查（`pg_constraint` /
  `information_schema.columns`），不硬编码 Postgres 的隐式名 `<table>_pkey`，两个方向都可重复执行；
  写成 `DO $$ … $$` 是为了让**离线模式**（`alembic upgrade head --sql`，没有可用连接）与在线模式共用同一份实现。
- **0012 的数据后果（不是 no-op）**：`timestamptz` → `timestamptz(3)` 会**四舍五入**（不是截断）到毫秒：
  实测 `'…00.9999+00'::timestamptz::timestamptz(3)` 得到 `…01`（**进位到下一秒**）。
  这些列不参与分数/摘要/绑定计算，因此不影响可重放性；`downgrade` 只还原**结构**，
  已经丢掉的亚毫秒位不会回来。
- 业务表**已全部创建**（上表 0002–0006），并由真实 PostgreSQL 上的用例验收到列与约束级别
  （`tests/test_migrations.py`、`tests/test_evaluation_pipeline.py`、`tests/test_storage_cas.py` 等）。
  **更正**：本文件此前写"业务表尚未创建，属 `待实现`"、"`migration_head: baseline`"，均为过期表述
  （2026-09-15 之后的迁移未同步）；本轮已按实现刷新。
- **已知缺陷（登记为 phase-01 缺口第 36 条）**：基线 0001 的 `downgrade` 是
  `DROP SCHEMA IF EXISTS b2`（**无 `CASCADE`**），因此只要 `b2` 里已有对象，`alembic downgrade base`
  必然失败（`DependentObjectsStillExist`）。该事实当前由
  `tests/test_migrations.py::test_downgrade_base_is_blocked_by_known_baseline_defect` 以 `xfail(strict=True)`
  钉住；修法（把 0001 的 downgrade 改 `CASCADE` 或先删对象）待做。

## head 与 `module.yaml` 一致性

- `module.yaml: database.migration_head` 必须与 Alembic 当前 head 一致，MAIN 集成检查会校验（RQ-B-4-30 验收 #3）。
- 每次新增迁移后，同步更新 `module.yaml: database.migration_head` 为新的 head revision。

## 生成与执行命令

```powershell
# 生成新迁移（autogenerate 仅作草稿，需人工审阅）
uv run alembic revision --autogenerate -m "b2 add <table>"

# 应用迁移到 head
uv run alembic upgrade head

# 回滚一步
uv run alembic downgrade -1

# 查看当前 head 与历史
uv run alembic heads
uv run alembic history
```

> `uv` **实测可用**（2026-09-16：`uv 0.10.2`；`uv run alembic upgrade head` / `current` / `heads` 均实跑）；
> 真实执行证据见 [../phases/active/phase-01-implementation.md](../phases/active/phase-01-implementation.md) 的"验证记录"。
> 任何未执行的命令必须在该文件里写明"未执行 + 原因"，**不得伪报通过**。

## 迁移与并发开发

- `alembic/` 是共享文件，多人并行开发时由一人先建基线，其他人基于基线追加（见 [../collaboration/workpackages.md](../collaboration/workpackages.md) 串行点）。
- 分支冲突以"按时间顺序重排 revision 链"解决，禁止手工合并两个 head（多 head 视为缺陷）。
