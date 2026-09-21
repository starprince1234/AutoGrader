# B3 文档索引（唯一入口）

本文件是 B3 文档的唯一索引。**不在下表 / 状态表内的正式文档视为治理缺陷**；新增 / 移动 / 归档文档必须同步更新本文件。

> 相对路径约定：B3 内部用相对路径；引用 MAIN 用 `../../MAIN/...`（从本 `docs/` 层看；从模块根 `B3/` 看则是 `../MAIN/...`）。本仓库假设 **B3 与 MAIN 位于同一父目录**（`contracts.lock` 的 `source: ../MAIN/contracts` 即此约定）。

## 权威表

| 主题 | 权威位置 | 本模块文档作用 |
|---|---|---|
| 需求编号 / 原文 / 验收 | `../../MAIN/docs/requirements/rq-catalog.md` | 只引用编号，写实现级验收细则（`requirements/`） |
| HTTP / 事件 / JSON 字段 | `../../MAIN/contracts/**` | 只写"我提供/我消费哪些端点与事件"（`api/`） |
| 契约版本与摘要 | `contracts.lock`（B3 根） | 引用，不写死字面值 |
| 物理表结构 / DDL | `alembic/versions/**` | 写表意图、约束、生命周期、权限（`database/schema.md`） |
| 路由 / 事件 / 能力声明 | `module.yaml`（B3 根） | 摘要，以 `module.yaml` 为准 |
| 跨模块边界与不可逆决策 | `../../MAIN/docs/adr/**` | 引用编号；本模块决策见 `adr/` |
| 阶段完成证据 | `phases/` | 必须写真实执行过的命令与结果 |
| 资源账本 / 安全门禁 | `../../MAIN/docs/architecture/**`、`../../MAIN/docs/testing/**` | 落地为 `testing/`、`boundaries.md` 检查项 |
| B3 内部冻结的接口 | `collaboration/frozen-interfaces.md` + `adr/ADR-0004/0005` | 冻结清单；改 ADR → 改代码 → 同步清单 |
| 与 MAIN 的契约缺口 | `contracts/changes/CHG-B3-*` | 只记录差异与请求裁决，**不改** `MAIN/contracts/**` |

## 状态表

状态词仅用：`骨架` / `待实现` / `已完成` / `阻塞`。基线日期 2026-09-20（[Phase 10](phases/active/phase-10-draft-package-and-current-pointers.md)；同日早些时候为 [Phase 09](phases/active/phase-09-reference-answer-channel.md)、[Phase 08](phases/active/phase-08-omission-preserves.md) 与 [Phase 07](phases/active/phase-07-question-tag-coverage.md)）。

| 文档 / 能力 | 状态 | 最后验证方式 |
|---|---|---|
| 插件注册（descriptor / health） | `已完成` | `tests/test_plugin.py`（8 例） |
| `b3` schema：17 张业务表 + 5 个触发器 + 角色授权 | `已完成` | `tests/test_migrations.py`（17 例，含空库迁移与权限拒绝）、`alembic heads` → `20260915_0004 (head)` |
| RQ-B-3-10 题目聚合（题干/图片/参考答案/用例） | `已完成` | `tests/test_questions_api.py`（80 例）、`tests/test_dto_boundary.py`（8 例） |
| RQ-B-3-20 题库维护 + 不可变版本 + assignment | `已完成` | `tests/test_versions.py`（31 例，应用层 409 + 触发器 23001） |
| RQ-B-3-20 发布前置参考答案凭证（CHG-0008） | `已完成` | `tests/test_versions.py`、`tests/test_questions_api.py`（无凭证 422 / 摘要不符 409 / 改内容自动失效） |
| RQ-B-3-20 标签写路径（CHG-0008） | `已完成` | `tests/test_questions_api.py`（往返 + 9 类非法值 422） |
| RQ-B-3-30 去标识化统计（去重/重放/查询） | `已完成`（**验收 5 未达标**：窗口路径 10 万量级 23.8 s） | `tests/test_statistics.py`（39 例）、`tests/test_auth.py`（23 例）、`scripts/benchmark_statistics.py` |
| RQ-B-3-30 消费健康面（空闲 vs 拒收） | `已完成` | `tests/test_statistics.py`、`tests/test_consumer_cli.py`（4 例） |
| RQ-B-3-40 stdin/argv adapter | `已完成` | `tests/test_adapters.py`（46 例，含 MAIN schema 校验） |
| RQ-B-3-50 file I/O adapter | `已完成`（文本差异粒度受限 → `CHG-B3-0001`） | 同上 |
| RQ-B-3-60 callable adapter | `已完成` | 同上（含扩展点断言） |
| RQ-B-3-99 结构化脱敏日志 | `已完成` | `tests/test_logging.py`（23 例） |
| `RQ-T-10` 出题期模型建议（CHG-0011） | `已完成`（provider 默认关闭） | `tests/test_questions_api.py`（未配置 `409` 指名缺项、角色 `403`/`401`、payload 无受保护内容、调用不改题、回包越界即拒） |
| 诊断端点 `GET /questions/{id}/diagnostics`（CHG-0013） | `已完成`（**仅 HTTP 契约级验证**，未经真实宿主端到端） | `tests/test_questions_api.py`（4 例：形状、四条规则与 severity、鉴权分支、空 findings 的含义）+ `tests/test_adapters.py`（规则单测 5 例） |
| 修订闭环读写出口 `revision_note`/`revision_diff`（CHG-0013） | `已完成`（**仅 HTTP 契约级验证**） | `tests/test_versions.py`（键集合读契约断言、首版不可比较、无 flag 即 422）、`tests/test_questions_api.py`（往返 + 422 分支） |
| 标签检索 `GET /questions?tags=`（CHG-0013） | `已完成`（**仅 HTTP 契约级验证**） | `tests/test_questions_api.py`（交集等式 + 6 类非法查询标签 422） |
| RQ-B-3-20 标签覆盖度 `GET /questions/coverage`（CHG-0020） | `已完成`（**仅 HTTP 契约级验证**，未经真实宿主端到端） | `tests/test_questions_api.py`（7 例：合计恒等式 + 显式否定 `sum(tags)+untagged`、跨课程 403 与学生仅已发布、与列表可见集合逐标签一致、只有计数、空标签库、路由顺序、鉴权/必填参数）+ `MAIN/contracts` 的键集合核对 |
| RQ-B-3-20 草稿写入的省略语义（CHG-0017） | `已完成`（**真实 composition host + 真实 JWT + 真实题库**读数） | `tests/test_versions.py`（6 例）+ `tests/test_questions_api.py`（3 例）；修复前后读数见 [Phase 08](phases/active/phase-08-omission-preserves.md) §4（`422 body.cases` → `200`）与 §4.1（宿主上摘要逐字不变、`tagged` +1） |
| RQ-B-3-20 参考解答交付通道（CHG-0023） | `已完成`（**真实 composition host + 真实 JWT + 真实题库**读数；B2 侧消费未做）。两类 `409` 已由 `CHG-0026` 确认保留并逐条点名；情形 ② 的文案在 Phase 10 收敛为"只点名缺失的摘要、不断言原因" | `tests/test_questions_api.py`（6 例，含两类 `409` 的**镜像 `detail`** 断言）+ `tests/test_logging.py`（1 例）；真实读数见 [Phase 09](phases/active/phase-09-reference-answer-channel.md) §4 与 [Phase 10](phases/active/phase-10-draft-package-and-current-pointers.md) §4.2（两个 `409` 的 `detail` 并排、可区分） |
| RQ-B-3-20 草稿也可取执行输入（CHG-0028） | `已完成`（**真实 composition host + 真实 JWT + 真实题库**读数；B2 的"执行"那半未跑——无 nsjail） | `tests/test_questions_api.py`（4 例：草稿 `200`、`created_at` 语义、已发布路径仍比对摘要、零用例 `409`、闭环）；读数见 [Phase 10](phases/active/phase-10-draft-package-and-current-pointers.md) §4 |
| 工具链（ruff / pytest / pytest-cov / jsonschema / httpx / cryptography） | `已完成` | `uv run ruff check .` → All checks passed!；`uv run pytest` → `393 passed, 6 warnings in 53.93s` |
| 表规范对齐（`RQ-B-4-30` / E16）：主键命名 + `timestamptz(3)` | **库 `已完成`；B4 的迁移文本门禁 `未达成`** | 库内直接断言：17 个 `pk_<table>`、15 列 precision=3，`downgrade -1`→`upgrade head` 往返通过；`--scope migrations` 对 B3 仍报 32 条（该扫描器不建模 `ALTER TABLE`），见 [Phase 05](phases/active/phase-05-table-standard-alignment.md) |
| B2 runner 端到端联调 | `待实现`（跨模块） | 未验证；acceptance 标注 ⚠ B2 联调 |
| NATS durable consumer 真实运行 | `待实现`（需 B4 提供 NATS） | 仅消息处理语义与拒收路径已测；健康面只能区分空闲与拒收，**不证明链路跑过** |
| 新出口在真实宿主上的端到端调用 | `待实现`（跨模块） | `CHG-0013`/`CHG-0020` 只有 `TestClient` + 真实 PostgreSQL；`CHG-0017`、`CHG-0023`、`CHG-0028` **已在真实 composition host + 真实 JWT 上取过读数**（Phase 08 §4.1、Phase 09 §4、Phase 10 §4）；仍未跑的是 **B1 客户端**、**B2 对这些端点的消费**，以及 **B2 真的执行参考解答**（无 nsjail） |
| 契约一致性（events 方向） | `阻塞`（SDK 缺口） | ADR-0003 已记录，见 `architecture/overview.md` |
| 图片/fixture 端点与创建题目的 `course_id` | `已完成`（**未成文**） | 落在既有前缀内实现 → `contracts/changes/CHG-B3-0002` |
| ~~`ComparisonDiagnostics.severity` 的契约笔误~~ | **已撤销（`CHG-0016` §5 驳回）** | 曾记为"契约 description 与 enum 不一致"。MAIN 全仓检索确认**契约不存在这个笔误**：所有 `severity` 枚举都是 `[error, warning, info]`，`ComparisonDiagnostics` 的 description 根本没有列举取值。真实经过是**实现偏离了契约**（代码里多出一个未被声明的 `note`），已改实现对齐。本行保留是为了不让后来者再去"修"一个本来就正确的声明 |

## 文档导航

- 需求：`requirements/scope.md` · `traceability.md` · `acceptance.md`
- 架构：`architecture/overview.md` · `components.md` · `models.md` · `boundaries.md`
- 接口：`api/contract.md` · `api/events.md`
- 数据库：`database/schema.md` · `migrations.md`
- 测试：`testing/strategy.md` · `cases.md`
- 决策：`adr/README.md`（ADR-0001~0005）
- 契约缺口：`contracts/changes/CHG-B3-0001` · `CHG-B3-0002` · `CHG-B3-0003`
- 冻结接口：`collaboration/frozen-interfaces.md`
- 运行：`runbooks/local-development.md` · `troubleshooting.md`
- 阶段：`phases/active/phase-01-question-bank-and-packages.md`（题库与 package 实现）·
  `phases/active/phase-02-contract-alignment-and-suite.md`（EdDSA / 版本题干 DTO 对齐）·
  `phases/active/phase-03-quality-attestation-and-bounded-statistics.md`（凭证 / 标签 / 有界统计与
  10 万量级实测 `331 passed`）·
  `phases/active/phase-04-quality-surfaces.md`（CHG-0013 三个出口落地）·
  `phases/active/phase-05-table-standard-alignment.md`（表规范对齐 E16，库已合规、静态门禁未归零）·
  `phases/active/phase-06-package-digest-scope.md`（包摘要作用域修正与一次被推翻的根因判断）·
  `phases/active/phase-07-question-tag-coverage.md`（CHG-0020 标签覆盖度，含 `CHG-0020` §5 措辞与实测 plan 不符的记录）·
  `phases/active/phase-08-omission-preserves.md`（CHG-0017 省略即保留落地，含真实 HTTP 的修复前后读数与两条移交 MAIN 的观察）·
  `phases/active/phase-09-reference-answer-channel.md`（CHG-0023 参考解答交付通道，含真实 composition host 上 §8 的七条读数）·
  `phases/active/phase-10-draft-package-and-current-pointers.md`（**当前阶段**：CHG-0028 草稿也可取执行输入，含两处"指向过时机制"的文案修正与真实读数）·
  `phases/archive/README.md`
- 协作：`collaboration/onboarding.md` · `workpackages.md` · `interfaces.md` · `definition-of-done.md` · `glossary.md`
- 知识：`knowledge/README.md`（+ `2026-09-15-main-checks-gates.md`）· `research/README.md`（旧材料说明，仅 B3 需要）
