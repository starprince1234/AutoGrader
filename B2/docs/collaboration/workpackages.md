# B2 工作包拆分与认领（2~3 人并行）

> 本文件必须与 [../../../MAIN/docs/workstreams/B2.md](../../../MAIN/docs/workstreams/B2.md) 第 5/6 节一致。拆分为 3 条泳道，建议 2~3 人：A、B 可完全并行，C 依赖 B 的"阻断判定"接口签名（先冻结签名即可并行）。

## 工作包总览

| 工作包 ID | 泳道 | 覆盖 RQ | 目标一句话 | 预估工作量 | 建议认领人数 |
|---|---|---|---|---|---|
| `WP-B2-A` | 接收与存储 | `RQ-B-2-10/20/30` | 提交 HTTP 端点 + 信封校验 + 内容寻址存储 + IMAP 适配器 | 中 | 1 |
| `WP-B2-B` | 静态合法性与安全性 | `RQ-B-2-40/70` | Python/Bash 静态检查 + 安全规则 + 阻断门 | 中 | 1 |
| `WP-B2-C` | 动态执行与评价输出 | `RQ-B-2-50/60/80/99` | nsjail runner + 比较器 + 报告/导出 + 日志 | 大 | 1~2 |
| `WP-B2-D` | 教学评语轨与评分参考 | `RQ-T-20` / `RQ-T-30` | 评语生成编排 + 参考分严格解析与边界判定 + 四列存储 + 评语端点 | 中 | 1 |

> `WP-B2-D` 覆盖的是**基线之外的自选增量**，因此不出现在 `MAIN` 的 32 条 `RQ-B-*` 基线检查里
> （`RQ-T-*` 由 `checks.py` 的 `team_expected` 单独登记）。定义权威在
> [../../../MAIN/docs/workstreams/B2.md](../../../MAIN/docs/workstreams/B2.md) 第 5 节 §`WP-B2-D`；
> 本表此前引用了该编号而 MAIN 尚未定义它，**已由 MAIN 补上定义**，此处按同一口径镜像（编号是
> MAIN 的权威，模块不自行发明）。

## 并行前提（必须先冻结的接口）

| 冻结项 | 谁冻结 | 冻结后约束 | 状态 |
|---|---|---|---|
| 内部模型 `SubmissionEnvelope`（归一化后） | A（与 MAIN 契约对齐后自定内部形态） | B/C 只依赖其公开字段，不得改结构 | 已冻结（规格 + 代码，[../architecture/interfaces.md](../architecture/interfaces.md) 第 2 节） |
| `run_case(package, submission) -> CaseEvidence` 签名 | C（先冻结） | A/B 不得改，C 在冻结后可并行 | 已冻结（规格 + 代码，[../architecture/interfaces.md](../architecture/interfaces.md) 第 5 节） |
| 静态检查结果 → 阻断判定 的接口签名 | B | C 的 runner 依赖"是否允许动态运行"的判定 | 已冻结（`StaticAnalysisResult`/`SecurityDecision`，[../architecture/interfaces.md](../architecture/interfaces.md) 第 3/4 节） |
| 报告结构（受 `evaluation-report` schema 约束） | C | 不可自行改，改动走 MAIN 变更规程 | 契约已锁定 |
| 事件 outbox 实现 | C（共享） | A 发布 `submission.accepted` 依赖 | 已冻结（规格 + 代码，[../architecture/interfaces.md](../architecture/interfaces.md) 第 6 节） |

## 必须串行的点

1. `b2` schema 初始迁移（一人先建基线表，其他人基于它追加）。
2. `run_case` 内部接口签名（冻结后才允许三泳道并行）。
3. 报告最终结构（受契约约束，不可自行改）。
4. 契约变更（走 [../../../MAIN/docs/contracts/change-protocol.md](../../../MAIN/docs/contracts/change-protocol.md)）。
5. 事件 outbox 实现（共享，一人实现）。

## 共享文件冲突预防

`plugin.py`、`alembic/`、错误码目录为共享文件；`plugin.py` 的 descriptor 变更只能由一人执行并同步 `module.yaml`。

---

## WP-B2-A 接收与存储

- **覆盖 RQ**：`RQ-B-2-10`、`RQ-B-2-20`、`RQ-B-2-30`
- **目标**：实现 `POST /api/v1/submissions`、内容寻址存储层、元数据表、IMAP 收取适配器、隔离区、邮件检查点。
- **输入**：MAIN `submission-envelope` schema；B4 JWT（可先用契约夹具与本地测试密钥）；B3 `question_version_id`（仅标识）。
- **产出**：提交端点与信封校验、SHA-256 核对、幂等、作用域校验、`submission`/`artifact`/`email_checkpoint` 表迁移、IMAP 适配器、隔离区、`submission.accepted` 事件发布。
- **依赖**：MAIN `submission-envelope` schema（已锁定）；outbox（C 冻结后）；B4 JWT 校验（可先用夹具）。
- **验收**：`RQ-B-2-10` 的 6 项 + `RQ-B-2-20` 的 6 项 + `RQ-B-2-30` 的 5 项（见 [../requirements/acceptance.md](../requirements/acceptance.md)）。
- **风险**：邮件来源不可控 → 隔离机制必须先于自动匹配实现。

## WP-B2-B 静态合法性与安全性

- **覆盖 RQ**：`RQ-B-2-40`、`RQ-B-2-70`
- **目标**：Python AST 检查器、Bash 语法/结构化检查、规则目录、安全规则集、`security_finding` 持久化、静态→动态阻断门。
- **输入**：evaluation package 中声明的语言与检查策略；MAIN 语言枚举（`RQ-B-0-10`）。
- **产出**：静态检查器、规则目录（规则 ID + severity + 说明）、安全规则集、`security_finding` 表迁移、阻断判定接口。
- **依赖**：MAIN 语言枚举（已锁定）；沙箱策略（见 [ADR-0002](../adr/ADR-0002-sandbox-execution-policy.md)）。
- **验收**：`RQ-B-2-40` 的 5 项 + `RQ-B-2-70` 的 5 项，含恶意样本回归集。
- **风险**：静态检查必须无 import 副作用，且高危阻断必须在进入 runner 前生效。

## WP-B2-C 动态执行与评价输出

- **覆盖 RQ**：`RQ-B-2-50`、`RQ-B-2-60`、`RQ-B-2-80`、`RQ-B-2-99`
- **目标**：nsjail 策略、三类 adapter 执行器对接、结果比较器、资源限制与进程树清理、证据与报告生成、数据库持久化、导出、结构化日志。
- **输入**：B3 evaluation package 结构与三类 adapter manifest（**B2 最关键的跨模块依赖**）。
- **产出**：nsjail 策略（参数数组）、三种 adapter 对接、比较器、`evaluation_run`/`evaluation_result` 表迁移、报告器、导出器、结构化日志器。
- **依赖**：B3 package 结构（已锁定）；`run_case` 签名（自冻结）；B 的阻断判定签名。
- **验收**：`RQ-B-2-50` 的 6 项 + `RQ-B-2-60` 的 5 项 + `RQ-B-2-80` 的 5 项 + `RQ-B-2-99` 的 4 项。
- **风险**：nsjail 只能在 Linux 验收；Windows 上先交付"可测的比较器与报告"作为可交付部分，不得整体阻塞。

## WP-B2-D 教学评语轨与评分参考

- **覆盖 RQ**：`RQ-T-20`、`RQ-T-30`（**基线之外的自选增量**，不计入 9 条 `RQ-B-2-*`）
- **目标**：评语生成的编排（provider 适配、prompt 版本化、显式免责标注）、`advisory_score` 的**严格解析**
  与边界判定、四列存储与表级 CHECK、`GET`/`POST /api/v1/evaluation-runs/{id}/commentary` 的对外出口。
- **输入**：`CHG-0011`（评语轨的对外接口）、`CHG-0018`（评分参考）、`ADR-0009`（AI 边界）与 `ADR-0014`
  （双轨与评分参考的六项可执行分离断言）；外部 provider 由部署配置，**默认关闭时不得出网**。
- **产出**：`commentary/` 包（白名单取证、版本化 prompt、可配置 provider、严格解析、边界判定）、
  `evaluation_commentaries` 表（迁移 0010 + 0014 的四列参考分）、两个端点。
- **依赖**：B2 自己的证据表（只读）；模型 provider（可选，默认没有）。
- **验收**：`CHG-0018` §7 的五条 + `CHG-0011` 的实现侧断言。**其中两条是否定断言，价值最高**：
  ① 参考分**不得**出现在报告、成绩、导出、任何事件或 `result_digest` 的任何位置（它是子对象
  `commentary.advisory_score`，访问路径本身就在说"它不是分"）；② 删掉评语行之后**重放**，
  分数与 `result_digest` 逐字不变——参考分必须是纯附加的。
- **风险**：**模型输出非确定**，越界、畸形、模型自带 `max_score` 这些路径**无法靠真实模型稳定触发**，
  只能用传输替身覆盖。因此必须如实区分"真实 provider 跑过的"与"只在替身下验证的"，不得混同。
  另有一条环境级限制：Windows 上沙箱不可用 ⇒ 没有 `completed` 运行 ⇒ 没有 `case_evidence` ⇒
  **真实 `advisory_score` 无法在集成环境产生**（`max_score` 由证据权重和填入，无证据即无从填）；
  **不得**为了演示而伪造一条 completed 运行。

## WP-B2-E 参考解答验证的执行侧（`CHG-0019` / `CHG-0023`，**不新增 RQ 编号**）

- **覆盖**：`RQ-B-3-20`（发布门禁）与 `RQ-T-10`（出题工作流）的**可用性补全**；不新增 `RQ-*`。
- **目标**：`POST /api/v1/reference-verifications` —— 给定 `question_version_id`，在沙箱按该版本自己声明的
  适配器与比较策略执行**参考解答**、逐用例比对，全部通过才用**已有的** B3 服务通道请 B3 记录存证。
- **输入**：`CHG-0023` 的 `GET .../reference-answer`（B3 内部、`serviceToken`）、`CHG-0008` 的
  `POST .../attestations`、`ADR-0015` 的编排裁决。
- **产出**：`reference/` 包（B3 两个调用 + 一次性工作区 + 编排）、`api/reference_routes.py`、
  `module.yaml` 与 `plugin.py` 的第五条路由前缀。
- **依赖**：`runner.run_case`（复用，**不得**另起执行路径）、`PackageSource`（复用取包端口）、
  B3 的 `reference-answer` 端点（**B3 侧实现中**）。
- **验收**：`CHG-0019` §7 的六条。**第 2/3/4 条依赖真实 B3 与 Linux 沙箱**，本机只能覆盖到分支替身
  与"无沙箱 ⇒ 503 且零存证"；如实区分，见 [../phases/active/phase-07-reference-verification.md](../phases/active/phase-07-reference-verification.md)。
- **风险**：① 课程维度的授权判定**做不了**（题目→课程是 B3 的事实），当前只判到"staff + 至少一门课"；
  ② B3 的 `evaluation-package` 对**草稿**返回 `409`，而存证又只在草稿上被接受 ⇒ "验证草稿再发布"这条
  端到端路径在当前 B3 上**跑不通**（已上报，见 phase-07 与 `api/contract.md`）。

---

## 认领表（初始留空）

| 工作包 | 认领人 | 状态 | 预计完成 |
|---|---|---|---|
| `WP-B2-A` | （空） | 待认领 | — |
| `WP-B2-B` | （空） | 待认领 | — |
| `WP-B2-C` | （空） | 待认领 | — |
| `WP-B2-D` | （空） | 待认领 | — |
| `WP-B2-E` | （空） | 待认领 | — |
