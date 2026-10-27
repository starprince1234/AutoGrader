# B2 需求追踪表（Traceability）

需求权威：`../../../MAIN/docs/requirements/rq-catalog.md`。本表覆盖 B2 的 9 条 `RQ-B-2-*` 需求
（编号精确匹配，无遗漏、无多余），以及落到本模块的**自选需求** `RQ-T-20`（`ADR-0009` 的评语轨）
与 `RQ-T-30`（`ADR-0014` 的 AI 评分参考）、以及**跨模块需求** `RQ-B-0-30` 验收 2
（文件类题目的证据，审计发现此前无人实现）。

状态词仅用：`骨架` / `待实现` / `已完成` / `阻塞`（判词口径见 `MAIN/docs/collaboration/module-doc-spec.md` 铁律二：`已完成` = 可运行实现 + 通过真实测试 + **阶段证据**）。

> **状态列的读法（必读）**：下表状态列已于 **2026-09-16 按实际实现逐行刷新**，判词口径 = `MAIN/docs/collaboration/module-doc-spec.md`
> 铁律二（`已完成` = 可运行实现 + 通过真实测试 + **阶段证据**）。每行的"证据位置"指向
> [../phases/active/phase-01-implementation.md](../phases/active/phase-01-implementation.md)（实现阶段的真实命令与结果、
> 以及 `G-EVT-*`/`N*` 缺口登记）与 [../phases/active/reviews/](../phases/active/reviews/)（独立复核记录原文）。
> **已知未验收部分写在 phase-01 的"验证记录 / 未执行项"与"风险与遗留项"，不隐藏**：真实 IMAP/TLS 仅夹具、
> Linux/nsjail 走容器化验收且平台限制项 `5a2` 记 xfail、跨进程并发上限未测、"DB 整体不可达"形态的日志用例
> 因探活长超时未做（改以两种真实可复现形态覆盖）。**状态为 `已完成` 不代表上述未验收项已通过。**

| RQ | 需求摘要 | 本模块实现方式 | 关联工作包 | 关联测试 | 状态 | 证据位置 |
|---|---|---|---|---|---|---|
| `RQ-B-2-10` | API 接收 B1 待测代码 | FastAPI 提交端点 + `submission-envelope` JSON Schema 校验 + 摘要核对 + 幂等 + 作用域校验 + `submission.accepted` 事件 | `WP-B2-A` | T-RECV-*、T-EVT-010/019 | `已完成` | `tests/test_intake_api.py`、`tests/test_submission_query.py`；组级复核 [reviews/REVIEW_L1_reception_storage.md](../phases/active/reviews/REVIEW_L1_reception_storage.md)（三条标准 PASS）。**验收 5（接收成功后发布事件）现在有端到端证据**：`tests/test_worker.py::test_accepted_submission_is_evaluated_and_published_end_to_end`（真实 HTTP 接收 → outbox → worker 一轮 → 真实 JetStream 读回同一条事件）与 `::test_run_once_publishes_pending_events_and_marks_them_published`；**未验收**：验收 4"不产生第二次评测"仍无统计 `evaluation_runs` 的专项用例（`awaiting_evaluation` 的 `NOT EXISTS` 只有选择面断言） |
| `RQ-B-2-20` | 电子邮件接收待测代码 | IMAP 适配器归一化为同一 `SubmissionEnvelope`；唯一匹配失败进隔离区；检查点幂等 | `WP-B2-A` | T-MAIL-* | `已完成` | `tests/test_email_intake.py`、`tests/test_email_intake_acceptance.py`；同上组级复核。**真实 IMAP/TLS 未验收（仅夹具）**，见 phase-01"未执行"列 |
| `RQ-B-2-30` | 存储管理待测代码 | 内容寻址存储（SHA-256 去重）+ 元数据 + **可配置保留策略** + 孤儿回收审计 | `WP-B2-A` | T-STORE-*、T-INT-008..010 | `已完成` | `tests/test_storage_cas.py`、`tests/test_storage_gc.py`；同上组级复核；决策见 [adr/ADR-0003](../adr/ADR-0003-content-addressed-submission-storage.md)。**验收 5 已实现（2026-09-18）**：`B2_ARTIFACT_RETENTION_DAYS`（默认 `0`＝一失去引用即可回收）由回收器在同一 `DELETE` 语句内与引用条件一起判定，窗口内不删也不审计、过窗口照常回收、被引用内容任何窗口下都不是候选人（`tests/test_storage_gc.py` 四条）。语义与范围见 [../database/schema.md](../database/schema.md) 的"保留策略"节 |
| `RQ-B-2-40` | 静态合法性检查 + 总评/细评 | Python `ast` / Bash `bash -n` + 结构化规则；逐条规则含 severity+位置；高危阻断动态运行 | `WP-B2-B` | T-STAT-* | `已完成` | `tests/test_static_checks.py`；组级复核 [reviews/REVIEW_L1_static_security.md](../phases/active/reviews/REVIEW_L1_static_security.md)（三条标准 PASS） |
| `RQ-B-2-50` | 动态运行准确性 + 总评/细评 | nsjail runner + 逐用例比较；资源限制与进程树清理；固定参数数组；**逐用例资源实测读数** | `WP-B2-C` | T-RUN-*、T-INT-007、T-EVID-001..003 | `已完成` | `tests/test_runner_core.py`、`tests/test_evaluation_pipeline.py`、`tests/test_case_evidence_details.py`（`CHG-0012` 的 `resource`：读不到就是 `null` 而不是 0、`cpu.stat` 解析、nsjail cgroup 定位规则、资源读数不进摘要）；Linux 验收 [../../tests/linux/README.md](../../tests/linux/README.md)：隔离矩阵 **13 通过 + 1「无法执行」**（`5a2` xfail）+ **真实 runner 验收 `5 passed, 1 skipped`**（T-RUN-008/009/010/011：合法提交经真实 nsjail 通过、五个恶意样本被拦并归类、超时零残留、真实编排两次同 `result_digest`、学生伪造沙箱侧归因失败且写不进沙箱自有日志）；编排复核 [reviews/REVIEW_L1_orchestration.md](../phases/active/reviews/REVIEW_L1_orchestration.md) + `_RECHECK`。**未验收**：容器内"沙箱不可用 ⇒ 拒绝执行"（容器里沙箱可用）、跨进程并发上限、**以及真实 nsjail 下 `resource` 的实际读数**（见下条） |
| `RQ-B-0-30`（验收 2） | 文件类题目的评分证据含"被修改文件清单 + 内容摘要" | `case_evidence[].artifacts`：`created`/`modified`/`deleted` 三张清单（工作区相对路径、内容摘要、字节数）+ 单一产出文件的期望/实际摘要 | `WP-B2-C` | T-EVID-004..006 | `已完成` | `tests/test_case_evidence_details.py`（清单内容与摘要、解释器缓存不进清单、上限截断如实标记、`expected_sha256` 在学生视图置空）；报告侧 `tests/test_evaluation_pipeline.py`。**这条此前「四个模块里没有任何一个实现」**（`MAIN/.runtime/innovation/B2.md` §1.3 D），本批补上 |
| `RQ-B-2-60` | 过程与结果入库 + 按格式导出 | `evaluation_run`/`evaluation_result` 持久化 + 状态迁移史 + XLSX/CSV 导出（防公式注入） | `WP-B2-C` | T-PERSIST-003/004、B2-GRD-01..12 | `已完成` | 成绩查询/导出：`tests/test_grades_query_export.py`、`tests/test_b2_routes.py`；独立复核 [reviews/REVIEW_GRADES_RECHECK.md](../phases/active/reviews/REVIEW_GRADES_RECHECK.md)（A/B/C 全 PASS）；持久化与状态迁移见 `tests/test_evaluation_pipeline.py` |
| `RQ-B-2-70` | 待测代码安全性确认 | 静态高危模式 + **执行期**沙箱违规 → `security_finding`（`detected_in ∈ {static, runtime}`）；违规 ⇒ `rejected`（安全）与 `failed`（技术）严格区分；恶意样本回归集 | `WP-B2-B` | T-SEC-* | `已完成` | `tests/test_security_decision.py`、`tests/test_ss_probe_*.py`、`tests/test_evaluation_pipeline.py`（**验收 2 运行期一侧，2026-09-18 补齐**：`::test_runtime_sandbox_violation_rejects_the_run_instead_of_scoring_it` —— 违规 ⇒ `running → rejected + security_blocked`、运行期 finding 落 `b2.security_findings`、无结果行/无报告、后续用例不执行、不发布 `completed`/`failed`；此前违规用例只被当成学生 0 分，评测仍走到 `completed`，与本节原文及 `architecture/interfaces.md` §4.4/§5.4 双向矛盾）；决策见 [adr/ADR-0002](../adr/ADR-0002-sandbox-execution-policy.md)。**未验收**：真实沙箱里"写只读路径/提权"被捕获为**运行期**事件（仅 Linux 容器，且当前只测到禁网）、验收 5"证据来自真实执行"的措辞已按两段样本改正（见 [testing/cases.md](../testing/cases.md) T-SEC-005） |
| `RQ-B-2-80` | 生成答案代码评价结果（总评+细评） | 报告生成（总评 + 静态/安全/逐用例细节 + 绑定摘要）；每分可追溯；可重放；**逐用例的规则式解释文本** | `WP-B2-C` | T-REPORT-*、B2-GRD-09、T-EVID-007/008 | `已完成` | `tests/test_evaluation_pipeline.py`（报告只在 `completed` 后存在、绑定摘要可重放）；`tests/test_case_evidence_details.py`（`CHG-0012` 的 `explanation`：五种失败形态各有说明、同输入同输出、只含行号/路径/计数、且**只由摘要稳定字段推出**——否则"同绑定 ⇒ 同 `result_digest`"会从后门失效）；导出侧不泄漏受保护内容见 B2-GRD-09；绑定摘要见 [../../../MAIN/docs/api/identifiers.md](../../../MAIN/docs/api/identifiers.md) |
| `RQ-B-2-99` | 运行日志 | 结构化 JSON 日志 + `producer=B2` + `correlation_id` 全链路串联 + 脱敏 | `WP-B2-C` | T-LOG-001..004、T-EVT-011/012/014/017/020 | `已完成` | `src/autograder_b2/logging/`（白名单准入 + 脱敏 + 行/字段上限）+ `tests/test_logging_redaction.py` + `tests/test_logging_pipeline.py`（字段 / 脱敏 / 单一 `correlation_id` 串联六阶段 / 沙箱·DB·NATS 三条依赖故障各有日志与状态）；用例见 [testing/cases.md](../testing/cases.md) |
| `RQ-T-20`（自选） | 评测评语轨（非确定性，不参与判分） | 独立评语轨：字段级白名单取证 → 版本化 prompt（`b2-commentary/2`）→ 可配置 provider（**默认关闭**）→ 自己的表与端点；`input_scope` 随 provenance 返回；响应是 JSON 对象（`text` + 可空 `advisory_score`） | `WP-B2-D` | T-CMT-001..010、T-ADV-005/006/011 | `已完成` | `tests/test_commentary_track.py`：白名单字段集 == `INPUT_SCOPE`（结构性）、受保护标记零命中（证据与 prompt）、import 闭包不含源码访问器、把访问器换成会抛异常的替身后生成仍成功、未配置时**零出网调用**（传输替身断言）、409 指名缺项、清空评语表后重放分数与逐项证据逐字相同（**该行的评语现在带参考分四列**）、把 `generate` 换成抛异常的替身后评测照常完成、provider 失败只标记该条评语、重新生成覆盖且不改分数、响应体逐字过契约 schema。契约依据 [../../../MAIN/docs/contracts/changes/CHG-0011-ai-assistance-endpoints.md](../../../MAIN/docs/contracts/changes/CHG-0011-ai-assistance-endpoints.md)、裁决 [../../../MAIN/docs/adr/ADR-0009-ai-assistance-two-track.md](../../../MAIN/docs/adr/ADR-0009-ai-assistance-two-track.md)。**2026-09-20 协议变更（`CHG-0018` §5）**：prompt 由"一段文字"改为"一个 JSON 对象"，版本推进到 `b2-commentary/2`；解析失败归 `failed`，不退化为纯文本。**两处白名单证据面读不到**（题目的公开元数据 B2 从不落库；三态安全结论由迁移 0011 补上落点），见 [../phases/active/phase-03-commentary-and-per-case-evidence.md](../phases/active/phase-03-commentary-and-per-case-evidence.md) 的"契约不足"一节 |
| `RQ-T-30`（自选） | AI 评分参考（顾问性，与权威分数物理分离） | 与评语**同一次生成、同一份白名单证据**：响应协议推进为 JSON 对象（`b2-commentary/2`），解析出 `text` + 可空 `advisory_score`；`max_score` 由 `b2.case_evidence.weight` 求和填入；`score` 越界（负或 > 满分）⇒ 整次生成 `failed` 而**不截断**；形状不符 ⇒ `failed`（**不**退化成纯文本评语）；落库为评语表四列（迁移 0014） | `WP-B2-D` | T-ADV-001..011、T-CMT-004/006/007/008/010（更新） | `已完成` | `tests/test_commentary_track.py`：`evaluation-report` schema 拒收带 `advisory_score` 的实例（真实报告 + 落库报告）、`/grades`/导出/事件 payload 与报告里零命中、`advisory` 形状的列只存在于评语表（结构性）、参考分行存在或被删都不改变重放出的分数与 `result_digest`、`max_score` == 各用例权重之和且等于分数轨满分、模型自带 `max_score` ⇒ `failed`、五种越界值 ⇒ `failed` 且四列全空、六种畸形响应 ⇒ `failed` 且不退化、参考分标注在参考分为 null 时仍存在。契约依据 [../../../MAIN/docs/contracts/changes/CHG-0018-ai-advisory-score.md](../../../MAIN/docs/contracts/changes/CHG-0018-ai-advisory-score.md)、裁决 [../../../MAIN/docs/adr/ADR-0014-ai-advisory-score.md](../../../MAIN/docs/adr/ADR-0014-ai-advisory-score.md)；迁移与真实 provider 读数见 [../phases/active/phase-06-ai-advisory-score.md](../phases/active/phase-06-ai-advisory-score.md)。**未验收**：B1 侧"并列展示"的浏览器端到端（属 B1） |
| `RQ-B-2-70`（沙箱自证） | 隔离可被证明（`ADR-0010` 决策 2） | `python -m autograder_b2.canary`：六项必须失败的金丝雀作业，复用生产执行路径，输出三态逐项结论 | `WP-B2-C` | T-CANARY-001..004 | `已完成`（**Windows 上只能是 `not_checked`**） | `tests/test_case_evidence_details.py`（无 nsjail ⇒ 全部 `not_checked` 且**不创建任何进程**、退出码 3 而不是 0、三态判定与"无法归因"分支、金丝雀源码可解析且两个标记俱全）。**Linux 侧未执行**：真实内核上的金丝雀跑批属 Linux 验收项 |

## 契约变更的实现状态（`CHG-0014` / `CHG-0015`，**不新增 RQ 编号**）

这两条契约变更（0.9.0 / 0.10.0 生效）在契约里写下了四个端点，而 B2 长期**未实现**——表在写、读出口
不存在。四个端点已于 2026-09-20 实现并有真实依赖测试；它们不改本表上方的 RQ 判词，因此**不新增行**
（本表"编号精确匹配、无遗漏、无多余"这条不变量保持成立），证据集中列在这里：

| 端点 | 状态 | 证据 | 实现落点 |
|---|---|---|---|
| `GET /api/v1/intake/quarantines` | `已完成` | `tests/test_intake_api.py`（列表字段与写入侧一一对应、`reason_code`/`resolved` 过滤、倒序游标分页不重不漏、坏游标/坏 limit 422、契约长度上限、未迁移库 503、**跨课程隔离矩阵**（staff 只看到本课序行、越界 query 403、未关联行仅管理员可见）、行级过滤先于分页、查询参数校验）；`tests/test_matching_scope.py`（scope 只来自规则候选共识）；`tests/test_quarantine_scope_sql.py`（谓词在 SQL 里且在 `LIMIT` 之前） | `intake/quarantine.py` + `intake/api.py` + `intake/matching.py` + `intake/imap.py` |
| `POST /api/v1/intake/quarantines/{id}/resolve` | `已完成` | 同上（归属写入、**不产生提交/事件/评测**、内容类拒绝一律 422、重复归属 409、未知记录 404、请求体校验 422、**行锁内重查行 scope**：staff 对未关联/跨课记录 403 且不改写，管理员可处置未关联行） | `intake/quarantine.py` + `auth/authorization.py` |
| `GET /api/v1/evaluation-runs/{id}/appeal-pack` | `已完成` | `tests/test_b2_routes.py`（`result_digest` = 报告摘要且可复算、宿主路径零命中 + `redactions` 非空、学生视图不扩大、`view` 语义 403/422、不改写已落库报告、未迁移库 503） | `evaluation/appeal.py` + `api/evaluation_routes.py` |
| `GET /api/v1/evaluation-runs/quality-summary` | `已完成` | 同上（**字面路由不被路径参数吞掉**、只含计数（键名与取值双重扫描）、逐用例/逐规则聚合与库内读数逐条相等、三态不合并且完整划分、`unmeasured_runs` 可见、时间窗与非法参数 422） | `evaluation/quality.py` + `api/evaluation_routes.py` |

**新增存储**：迁移 `20260915_0013`（`intake_quarantines` 的人工归属三列 + 一条 CHECK）与
`20260915_0015`（行级课程范围两列 + 成对 CHECK + 查询索引）。0013 的三步可逆性实测见
[../phases/active/phase-05-quarantine-and-quality-surfaces.md](../phases/active/phase-05-quarantine-and-quality-surfaces.md)；
0015 的 DDL 离线渲染与数据后果见
[../phases/active/phase-10-quarantine-row-scope.md](../phases/active/phase-10-quarantine-row-scope.md)。

**契约缺口状态**：① resolve 声明的"重跑信封/摘要校验"在"隔离记录不存报文"的数据模型下不可判定
（本实现只重跑角色/行 scope + 内容类闸门，见 1e 节）；② **隔离区的课程作用域已由 P13 修复**
（行级 scope 持久化 + SQL 过滤 + 行锁重查），但**质量聚合仍只判角色**（题目→课程属 B3，本轮不新增
跨模块通道；P13 前端先经 B3 校验版本可见性）；③ 归属只提供 `student_id`，不足以重建提交信封，
因此"归属后交给正常提交路径评测"在当前架构下无法闭环；④ 名单成员关系（学生是否仍在该课序）
属 B4 事实，B2 没有到 B4 名单的通道，resolve 不重查它（客户端每次现取候选，服务端重查行 scope
与调用者权限）。以上都没有为了让它们"看起来成立"而发明数据或路径。

## 契约变更的实现状态（`CHG-0019` / `CHG-0023`，**不新增 RQ 编号**）

`CHG-0019` 的端点不在需求基线里，它是 `RQ-B-3-20`（题库发布）与 `RQ-T-10`（出题工作流）的**可用性补全**：
没有它，教师无法在界面上完成发布。因此它**不新增 `RQ-*`**，判词单独列在这里；工作包记为 `WP-B2-E`
（定义镜像自 [../../../MAIN/docs/workstreams/B2.md](../../../MAIN/docs/workstreams/B2.md)，见
[../collaboration/workpackages.md](../collaboration/workpackages.md)）。

| 端点 | 状态 | 证据 | 实现落点 |
|---|---|---|---|
| `POST /api/v1/reference-verifications`（`CHG-0019`，输入通道由 `CHG-0023` 补上） | `已完成`（**两处受限**，见下） | `tests/test_reference_verification.py`（49 项，T-REF-001..027）：键集合与契约逐字一致、`failure_category` 复用报告词表、参考解答正文在脱敏名单且真的被丢弃、本包不可达任何进程创建路径、服务层拿不到存储、`401`/`403` 矩阵（学生与无作用域 staff）、全通过 ⇒ 计数与 `verified_at`/`content_digest` 与存证**逐字一致**、未通过 ⇒ **零存证**、沙箱违规 ⇒ 零存证且不写 `security_findings`、存证记录失败 ⇒ `null` 但 `cases` 照旧、坏 `201` ⇒ `503`、无沙箱 ⇒ `503`（含一条走**真实**取包解析 + 真实 `run_case` + 真实沙箱探测的用例）、上游 11 组失败各归其位、取包 `404/409/500/403` 分流、**逐表零行 + 零残留目录**、缺陷不伪装成用例失败、装配共用同一个 B3 通道与同一个并发闸门 | `api/reference_routes.py`、`reference/`（`b3.py`/`workspace.py`/`verification.py`）、`auth/authorization.py`、`runtime.py`、`module.yaml` + `plugin.py` 的第五条路由前缀 |

**两处受限（不写成"已完成"的完整形态）**：

1. **课程维度的授权做不了**（缺口 ⑦）：契约要"该题目的课程写权限"，而"题目→课程"是 B3 的事实，
   本端点调 B3 用的又是服务令牌。当前只判到"staff + 至少一个课程作用域"，另一门课的教师**会被放行**
   ——`tests/test_reference_verification.py::test_course_scope_gap_is_recorded_not_hidden` 把它钉成
   可执行的记录。
2. **"验证草稿 → 存证 → 发布"在真实 B3 上仍走不通**（缺口 ⑧）：B3 的 `evaluation-package` 对**草稿**
   返回 `409`（只在已发布版本上编译包），而存证**只接受草稿**（`status != draft` 即被拒）。因此
   `CHG-0019` §7.2 后半 / §7.3 后半 / §7.4 的端到端验收**未执行**（需真实 B3 + Linux 沙箱）；
   B2 侧只把草稿的 `409` 如实映射成 `422` + 指明原因，**不**返回伪成功。修复属 B3 侧。

**新增路由声明**：`module.yaml:7` 与 `plugin.py` 的 `descriptor.routes` 各加
`/api/v1/reference-verifications`（两侧逐项相等由 `tests/test_plugin.py` 保证）。**没有迁移**
（本端点不落库），`contract_version` 保持 MAIN 同步的值未动。

**真实端口验收发现并修掉的两个 B2 侧缺陷**（都由 MAIN 在运行中的 composition host 上实测发现；
原始读数记在 [../phases/active/phase-07-reference-verification.md](../phases/active/phase-07-reference-verification.md) 的 §2c / §2d）：

| # | 缺陷 | 修复 | 读数（真实宿主） |
|---|---|---|---|
| ③ | **自调用死锁**：handler 是 `async def`，而 `verify()` 是同步阻塞调用；它请求的 `B2_B3_BASE_URL` 就是**正在服务本请求的同一个进程** ⇒ 事件循环被占住 ⇒ 自我等待到超时 | `api/reference_routes.py` 用 `await run_in_threadpool(...)` 把整条阻塞调用下线（覆盖链上全部三处回调）；理由与取舍写进注释 | 修复前 `503 B3 request failed: TimeoutError` **10.11s**（MAIN 侧 10.03s）→ 修复后 `503 the sandbox is unavailable…` **0.17s** |
| ④ | **`409` 的原因被自造**：对任何 `409` 都答 "carries no reference answer"，而那次真实原因是"这个版本还没有 `content_digest`"（该版本**有**参考解答）⇒ 一是假话，二是抹平了 B3 本可区分的原因 | `reference/b3.py` 的 `_refusal` 转述 B3 Problem Details 的 `detail`（取不到就明说"没有原因"、不猜）；取包侧经 `PackageUnavailableError.upstream_detail` 同样转述 | 草稿 `422` **0.12s**，`detail` 是 B3 原话；已发布仍 `503` **0.08s** |

两条修复都有回归用例（T-REF-028；T-REF-029..033），其中死锁那条**先红后绿**已实跑。



## 说明

- 工作包与泳道拆分见 [../collaboration/workpackages.md](../collaboration/workpackages.md)，与本表 `关联工作包` 列一一对应。
- 判词依据与**未验收项**：逐行刷新的判词只用了"可运行实现 + 真实依赖测试 + 阶段证据"三项。
  未达成的部分（真实 IMAP、跨进程并发上限、Linux 平台限制项、缺 `pytest-timeout` 导致"挂死而非失败"等）
  逐条登记在 [../phases/active/phase-01-implementation.md](../phases/active/phase-01-implementation.md) 的
  "验证记录（未执行项）"与"风险与遗留项"，**不因本表写 `已完成` 而消失**。
- 契约侧缺口（不影响本表判词，但影响跨模块一致性）见
  `MAIN/docs/contracts/changes/CHG-0001..0005`（均为"提议，待 MAIN 判定"）。
- **事件链路的驱动前提（2026-09-18 更正）**：outbox 只由 `autograder_b2.worker` 排空，宿主不调用它
  ——因此"事件已发布"的前提是 worker 正在运行，而不是"模块已装载"（`GET /health` 的
  `message-broker` 分项会把未发布积压与 DLQ 报出来）。启动方式与重试节奏见
  [../runbooks/local-development.md](../runbooks/local-development.md) §5b。


