# B2 文档索引（唯一入口）

这是 B2 文档体系的**唯一索引**。不在本索引中的正式文档视为治理缺陷；新增/移动/归档文档必须同步更新本页两张表。

## 权威表（Single Source of Truth）

| 主题 | 权威文件 | 模块文档作用 |
|---|---|---|
| 需求编号、原文、验收 | `../../MAIN/docs/requirements/rq-catalog.md` 第 3 节 | 摘要引用编号，写实现级验收细则 |
| 契约版本与摘要 | `../contracts.lock` | 引用，不写死字面值 |
| HTTP / 事件 / JSON 字段 | `../../MAIN/contracts/**` | 只写"我提供/我消费"，禁止手抄字段 |
| 路由/事件/能力声明 | `../module.yaml` | 摘要，以 `module.yaml` 为准 |
| 物理表结构（DDL） | `../alembic/versions/**` | 写表意图、约束、生命周期 |
| 跨模块边界与不可逆决策 | `../../MAIN/docs/adr/**` | 引用 ADR 编号 |
| 统一标识与绑定摘要 | `../../MAIN/docs/api/identifiers.md` | 摘要引用 |
| 阶段完成证据 | `phases/active/`、`phases/archive/` | 写真实命令与结果 |
| 资源账本与安全门禁 | `../../MAIN/docs/architecture/**`、`../../MAIN/docs/testing/**` | 落地为 B2 检查项 |

## 状态表

状态词仅用：`骨架` / `待实现` / `已完成` / `阻塞`。

> **状态表的读法（必读）**：本表已于 **2026-09-16 按实际实现刷新**，判词口径同
> [requirements/traceability.md](requirements/traceability.md)（`已完成` = 可运行实现 + 真实依赖测试 + 阶段证据）。
> 证据指向 [phases/active/phase-01-implementation.md](phases/active/phase-01-implementation.md) 与
> [phases/active/reviews/](phases/active/reviews/)（独立复核/红队记录原文，共 **12** 份，逐份列名以免"索引无遗漏"
> 只靠目录兜底：`REVIEW_L1_reception_storage.md`、`REVIEW_L1_static_security.md`、`REVIEW_L1_orchestration.md`、
> `REVIEW_L1_orchestration_RECHECK.md`、`REVIEW_GROUP_L1_recheck.md`、`REVIEW_GROUP_L1_recheck_verdict2.md`、
> `REVIEW_GRADES_RECHECK.md`、`REVIEW_EVENTS_RECHECK.md`、`REVIEW_EVENTS_RECHECK_ROUND2.md`、
> `REVIEW_EVENTS_RECHECK_ROUND3.md`、`REVIEW_LINUX_SANDBOX_redteam.md`、
> `REVIEW_LINUX_SANDBOX_redteam_round2.md`）。
> **未验收项不因 `已完成` 而消失**：真实 IMAP/TLS 仅夹具、Linux/nsjail 走容器化验收（`5a2` xfail）、
> 按 `correlation_id` 的全链路检索无专项用例、跨进程并发上限未测——逐条见 phase-01。
> **另有一类必须写明的读数**：本树当前 `uv run pytest`（SDK 已按 `--reinstall-package` 刷新）是
> **`1298 passed, 9 skipped, 2 xfailed`（收集 1309 条，退出码 0，全绿）**——本模块此前的**四条既有失败
> 已全部修掉**（[phases/active/phase-08-known-failure-remediation.md](phases/active/phase-08-known-failure-remediation.md) 逐条给了根因、归属
> （四条**都是用例缺陷**，产品代码只改了两处 docstring 的措辞准确性、零行为变更）、改法与单条前后读数）。
> 因此 `check_test_evidence()` 的"B2 恒非零"阻塞**在套件侧已消失**：B2 自己**没有**写入
> `MAIN/.runtime/evidence/B2.json`（那条 `exit=1` 的诚实记录原样保留），由 MAIN 在重启窗口后统一重测。
> 历史读数（供对照，均含当时未修的四条失败）：`3 failed, 1294 passed, 9 skipped, 2 xfailed`（收集 1308；
> `RQ-T-30` 那批 +18、`CHG-0019` 那批 +61）、上一批 `3 failed, 1233 passed, 9 skipped, 2 xfailed`、
> 更早 `4 failed, 1214 passed`。失败条数在 3 与 4 之间摆动的差异来自**一对 flaky 的成绩分页用例**——
> 根因不是"缺 tie-breaker"（`submission_id` 就是 tie-breaker），而是用例期望与 `0012` 迁移的毫秒
> 四舍五入冲突；第 4 条是 `import alembic.versions` 在本仓库解析到 site-packages 的 Alembic
> （**不是收集期错误**：`--collect-only` 得 23 条，只有 1 条在运行期失败）。逐条见 phase-08。
> 同时：**任何基线数字都必须注明 SDK 是否已刷新**
> （`uv sync` 不会因路径依赖版本号未变而重装；见 phase-05 末尾"环境事实"）。

| 文档 / 能力 | 状态 | 最后验证方式 |
|---|---|---|
| 插件注册（`B2Module` + entry point） | `已完成` | `tests/test_plugin.py`（descriptor 与 `module.yaml` 对账见下条） |
| `POST /api/v1/submissions` 接收 | `已完成` | [`tests/test_intake_api.py`](../tests/test_intake_api.py)、[`tests/test_submission_loader.py`](../tests/test_submission_loader.py)；组级复核 [reviews/REVIEW_L1_reception_storage.md](phases/active/reviews/REVIEW_L1_reception_storage.md) |
| `GET /api/v1/evaluation-runs/{id}/events`（SSE） | `已完成` | [`tests/test_b2_routes.py`](../tests/test_b2_routes.py)（断言 `text/event-stream` 与断线语义） |
| 内容寻址存储 + 元数据（RQ-B-2-30） | `已完成` | `tests/test_storage_cas.py`、`tests/test_storage_gc.py`（含**保留策略可配置**：`B2_ARTIFACT_RETENTION_DAYS` 的窗口内保留 / 过窗口回收 / 被引用内容不受影响）；同上组级复核 |
| 静态检查器（RQ-B-2-40） | `已完成` | `tests/test_static_checks.py`；[reviews/REVIEW_L1_static_security.md](phases/active/reviews/REVIEW_L1_static_security.md)（三条标准 PASS） |
| 安全判定 + 沙箱（RQ-B-2-70） | `已完成` | `tests/test_security_decision.py`、`tests/test_ss_probe_*.py`；**执行期违规的运行级升格**（`rejected/security_blocked` + 运行期 finding 落库 + 不给分）见 `tests/test_evaluation_pipeline.py::test_runtime_sandbox_violation_rejects_the_run_instead_of_scoring_it`；同上复核记录 |
| 评测 worker（推进评测 + 排空 outbox） | `已完成` | `tests/test_worker.py`（真实 PostgreSQL + 真实 JetStream：HTTP 接收 → outbox → worker 一轮 → 读回事件）；启动方式与重试节奏见 [runbooks/local-development.md](runbooks/local-development.md) §5b |
| nsjail runner + 比较器（RQ-B-2-50） | `已完成` | `tests/test_runner_core.py`；Linux 验收见 [../tests/linux/README.md](../tests/linux/README.md)：隔离矩阵 **13 通过 + 1「无法执行」**（`5a2` xfail）+ **真实 runner 验收 5 passed, 1 skipped**（合法提交经真实 nsjail `passed/exit 0`（`duration_ms` 为易变读数，不作判据）、五个恶意样本各自被拦并归类、超时零残留、真实编排两次同 `result_digest`、学生伪造沙箱侧归因失败且写不进沙箱日志）。Windows 不可执行项清单与恢复操作见 phase-01 第 28 条（状态 `active`） |
| 报告生成 / 导出（RQ-B-2-60 / 2-80） | `已完成` | 成绩查询/导出：`tests/test_grades_query_export.py` + `tests/test_b2_routes.py`，独立复核 [reviews/REVIEW_GRADES_RECHECK.md](phases/active/reviews/REVIEW_GRADES_RECHECK.md)（A/B/C 全 PASS）；报告：`tests/test_evaluation_pipeline.py`。**原"导出产物不确定（墙钟进 docProps/ZIP 戳）"的基线告警已在本工作项修掉并有回归用例**（见 phase-01 第 9 条） |
| 邮件接收 IMAP（RQ-B-2-20） | `已完成` | `tests/test_email_intake.py`、`tests/test_email_intake_acceptance.py`；**真实 IMAP/TLS 未验收（仅夹具）**，见 phase-01"未执行"列 |
| 结构化日志（RQ-B-2-99） | `已完成` | `src/autograder_b2/logging/`（白名单准入 + 脱敏 + 上限）+ `tests/test_logging_redaction.py` + `tests/test_logging_pipeline.py`（字段 / 脱敏 / 单一 `correlation_id` 串联六阶段 / 沙箱·DB·NATS 三条依赖故障各有日志与状态）；事件侧用例 `T-EVT-011/012/014/017/020` |
| Alembic 迁移 | `已完成` | 迁移链至 `20260915_0014`（与 `module.yaml: migration_head`、`db/schema.py: EXPECTED_SCHEMA_REVISION` 三方一致）；离线全链 `alembic upgrade head --sql` 通过；三步可逆（upgrade → downgrade -1 → upgrade）在 B2 测试实例上实测；`tests/test_migrations.py` |
| `descriptor.events` 与 `module.yaml` 一致性 | `已完成`（原记 `阻塞`） | `tests/test_plugin.py::test_descriptor_events_equal_module_yaml_publishes_and_subscribes`（T-EVT-019）：6 个 subject = 5 publishes + 1 subscribes；SDK 扁平元组不区分方向的限制仍在（MAIN ADR-0003） |
| 事件 outbox / inbox（RQ-B-2-10/2-60/2-70/2-99） | `已完成` | 用例 `T-EVT-001..022`；独立复核（含缺陷复现与三轮复裁）见 [reviews/REVIEW_EVENTS_RECHECK.md](phases/active/reviews/REVIEW_EVENTS_RECHECK.md)；契约偏差与缺口登记见 phase-01 第 1～8 条。**发布侧驱动前提（2026-09-18 更正）**：outbox 只由 `autograder_b2.worker` 排空（此前该循环漏了 `await`，事件只进不出），宿主不调用 worker；订阅侧 `InboxConsumer` 仍只作为库存在 |
| 教学评语轨（RQ-T-20，非确定性、不参与判分） | `已完成` | `tests/test_commentary_track.py`（10 条）：白名单字段集 == `INPUT_SCOPE`（结构性）、受保护内容零命中、清空评语表后重放分数与逐项证据逐字相同、未配置时**零出网调用**；契约 `CHG-0011`，裁决 `ADR-0009`。**两处白名单证据面读不到**（题目公开元数据 B2 不落库；三态安全结论由迁移 0011 补落点），见 [phases/active/phase-03-commentary-and-per-case-evidence.md](phases/active/phase-03-commentary-and-per-case-evidence.md) |
| 逐用例资源读数 / 文件证据 / 规则式解释（CHG-0012） | `已完成` | `tests/test_case_evidence_details.py`（13 条）：读不到就是 `null` 而不是 0、文件变更清单含内容摘要、解释同输入同输出且只含行号/路径/计数、`expected_sha256` 在学生视图置空。**仅 Linux 可验收的部分未验收**：真实 nsjail 下 cgroup 读数的实际取值（见 phase-03 与 `tests/linux/README.md`） |
| AI 评分参考（`RQ-T-30` / `ADR-0014` / `CHG-0018`，生成与存储侧） | `已完成` | `tests/test_commentary_track.py` 的 T-ADV-001..011（11 条）：报告 schema 拒收、成绩/导出/事件零命中、`advisory` 列只存在于评语表、删表重放逐字相同、`max_score` == 证据权重之和（模型自带满分 ⇒ `failed`）、五种越界值 ⇒ `failed` 且四列全空（**不截断**）、六种畸形响应 ⇒ `failed`（不退化成纯文本）、参考分标注恒在。迁移 `20260915_0014` 三步可逆与**真实 DeepSeek 调用**读数见 [phases/active/phase-06-ai-advisory-score.md](phases/active/phase-06-ai-advisory-score.md)。**未验收**：B1 侧"并列展示"的浏览器端到端（属 B1） |
| 参考解答验证（`CHG-0019` + `CHG-0023`，发布门禁的执行入口） | `已完成`（**两处受限**） | `tests/test_reference_verification.py`（53 项，T-REF-001..031）+ `tests/test_integration_adapters.py`（含 T-REF-032/033）：键集合与契约逐字一致、`failure_category` 复用报告词表、参考解答正文在脱敏名单、本包不可达进程创建路径、**服务层拿不到存储**、`401`/`403` 矩阵、全通过 ⇒ 计数与 `verified_at`/`content_digest` 与存证逐字一致、未通过 ⇒ **零存证**、沙箱违规 ⇒ 零存证、记录失败 ⇒ `null` 但 `cases` 照旧、无沙箱 ⇒ `503`（含一条走**真实** `run_case` 与真实沙箱探测的用例）、**逐表零行 + 零残留目录**、装配共用同一个 `B3Endpoint` 与同一个并发闸门。**真实端口发现并修掉两个缺陷**：① 自调用死锁（阻塞调用必须在 `run_in_threadpool` 里下线；修复前 `503 TimeoutError` **10.11s** → 修复后 `503 沙箱不可用` **0.17s**）；② `409` 的原因必须**转述** B3 而不是自造（草稿 `422` **0.12s**，`detail` 是 B3 原话）。**两处受限**：① 课程维度的授权做不了（题目→课程是 B3 的事实）——另一门课的教师当前放行，有记录用例；② "验证草稿 → 存证 → 发布"在真实 B3 上仍走不通（`CHG-0028` 归 B3），`CHG-0019` §7.2 后半 / §7.3 后半 / §7.4 **未执行**。逐条见 [phases/active/phase-07-reference-verification.md](phases/active/phase-07-reference-verification.md) |
| 沙箱自证（canary，`ADR-0010` 决策 2） | `已完成`（本机只能 `not_checked`） | `python -m autograder_b2.canary`；`tests/test_case_evidence_details.py` 的 T-CANARY-001..003（无 nsjail ⇒ 六项全 `not_checked`、**不创建任何进程**、退出码 3）。真实内核上的跑批（T-CANARY-004）**未执行** |
| 隔离区复核 + 申诉证据包 + 题目质量汇总（`CHG-0014` / `CHG-0015` / 行级课程范围 `0.19.0`） | `已完成` | `tests/test_intake_api.py`（T-QUAR-001..015：列表过滤/倒序游标分页/鉴权矩阵/**归属不触发评测**/内容类拒绝不可归属/重复归属 409/契约长度上限/**跨课程隔离矩阵**/行级过滤先于分页/查询参数校验/越权归属 403/管理员处置未关联行）与 `tests/test_matching_scope.py` + `tests/test_quarantine_scope_sql.py`（scope 只来自规则候选共识；谓词在 SQL 里且在 `LIMIT` 前，**无需数据库**）与 `tests/test_b2_routes.py`（B2-QS-01..06：字面路由不被吞掉、**只含计数**、三态不合并且完整划分、`unmeasured_runs` 可见；B2-APL-01..06：**两处强制脱敏**、`result_digest` 可复算、学生视图不扩大）；迁移 `20260915_0013` 三步可逆实测见 [phases/active/phase-05-quarantine-and-quality-surfaces.md](phases/active/phase-05-quarantine-and-quality-surfaces.md)，`0015` 见 [phases/active/phase-10-quarantine-row-scope.md](phases/active/phase-10-quarantine-row-scope.md)。**契约缺口现状**：隔离区课程作用域已修复；质量聚合仍只判角色（题目→课程属 B3，P13 前端先经 B3 校验版本）；归属不闭环与名单成员关系不可复核仍登记在案，见 [api/contract.md](api/contract.md) 第 1e 节 |
| 分项健康 / readiness（含失败计数语义） | `已完成` | `tests/test_b2_routes.py` 第 8 节（三态聚合 + T-HLT-01：`record_failure()` **只如实累计、不翻转 `healthy`**，detail 措辞含"累计/不参与健康判定"）与 `tests/test_reference_verification.py`（T-HLT-02/03：已文档化的平台条件**不**计入，未预期依赖故障**必须**计入）；判定规则在 `src/autograder_b2/runtime.py` 的"失败计数与健康判定的关系"，运维读法见 [runbooks/local-development.md](runbooks/local-development.md) §6a；整改来源与前后读数见 [phases/active/phase-09-readiness-failure-counter.md](phases/active/phase-09-readiness-failure-counter.md) |

## 文档地图

- 需求：[requirements/scope.md](requirements/scope.md)、[requirements/traceability.md](requirements/traceability.md)、[requirements/acceptance.md](requirements/acceptance.md)
- 架构：[architecture/overview.md](architecture/overview.md)、[architecture/components.md](architecture/components.md)、[architecture/models.md](architecture/models.md)、[architecture/boundaries.md](architecture/boundaries.md)、[architecture/interfaces.md](architecture/interfaces.md)
- 接口：[api/contract.md](api/contract.md)、[api/events.md](api/events.md)
- 数据库：[database/schema.md](database/schema.md)、[database/migrations.md](database/migrations.md)
- 测试：[testing/strategy.md](testing/strategy.md)、[testing/cases.md](testing/cases.md)
- 决策：[adr/README.md](adr/README.md)
- 运行：[runbooks/local-development.md](runbooks/local-development.md)、[runbooks/troubleshooting.md](runbooks/troubleshooting.md)
- 阶段：[phases/active/phase-00-initialization.md](phases/active/phase-00-initialization.md)、[phases/active/phase-01-implementation.md](phases/active/phase-01-implementation.md)（实现阶段进行中记录 + 缺口登记）、[phases/active/phase-02-test-instance-and-suite.md](phases/active/phase-02-test-instance-and-suite.md)（测试实例文件化 + 套件实跑读数）、[phases/active/phase-03-commentary-and-per-case-evidence.md](phases/active/phase-03-commentary-and-per-case-evidence.md)（评语轨 + 逐用例证据补全 + 沙箱自证；含**契约不足与未验收项**）、[phases/active/phase-04-table-standard-remediation.md](phases/active/phase-04-table-standard-remediation.md)（表标准整改 E16）、[phases/active/phase-05-quarantine-and-quality-surfaces.md](phases/active/phase-05-quarantine-and-quality-surfaces.md)（`CHG-0014`/`CHG-0015` 四个端点 + 迁移 0013；含**三处契约缺口**与既有失败的环境读数）、[phases/active/phase-06-ai-advisory-score.md](phases/active/phase-06-ai-advisory-score.md)（`RQ-T-30`/`CHG-0018`：参考分的生成与存储 + 迁移 0014；含**真实 DeepSeek 调用读数**与未验收项）、[phases/active/phase-07-reference-verification.md](phases/active/phase-07-reference-verification.md)（`CHG-0019`/`CHG-0023`：参考解答验证端点；含**两处受限**与逐条未执行项）、[phases/active/phase-08-known-failure-remediation.md](phases/active/phase-08-known-failure-remediation.md)（四条既有失败清零：逐条根因/**全是用例缺陷**的归属论证/改法与前后读数；含"分页并非缺 tie-breaker"的实测订正）、[phases/active/phase-09-readiness-failure-counter.md](phases/active/phase-09-readiness-failure-counter.md)（readiness 失败计数语义：注释与行为矛盾的裁决、计数**只如实累计不翻转健康**、平台条件不计入；含变异反证与全量读数）、[phases/active/phase-10-quarantine-row-scope.md](phases/active/phase-10-quarantine-row-scope.md)（P13 行级课程范围：scope 只来自匹配规则候选共识、迁移 `0015`、SQL 谓词先于分页、resolve 行锁重查；含真实执行命令与未验证项）、[phases/active/phase-11-quarantine-decision-trail.md](phases/active/phase-11-quarantine-decision-trail.md)（0016：邮件摘要/驳回/处理人时间留痕；含主题边界、降级风险与未验证项）、[phases/active/reviews/](phases/active/reviews/)（独立复核记录原文：L1-2 接收与存储、L1-3 静态与安全、编排、事件）、[phases/archive/README.md](phases/archive/README.md)
- 协作：[collaboration/onboarding.md](collaboration/onboarding.md)、[collaboration/workpackages.md](collaboration/workpackages.md)、[collaboration/interfaces.md](collaboration/interfaces.md)、[collaboration/definition-of-done.md](collaboration/definition-of-done.md)、[collaboration/glossary.md](collaboration/glossary.md)
- 知识：[knowledge/README.md](knowledge/README.md)
