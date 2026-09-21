# Phase 03 评语轨 + 逐用例证据补全 + 沙箱自证（当前阶段）

- **契约基线**：模块 `contracts.lock` 指向的版本（本文件**不写死版本号**，见 `AGENTS.md` 第 3 条）。
- **范围**：`MAIN/.runtime/innovation/B2.md` 的候选 ①②③④（评语轨另属 `ADR-0009` / `RQ-T-20`）。
  改动全部落在 `B2/`：`src/**`、`tests/**`、`docs/**`、`alembic/versions/0010..0011`、
  `module.yaml: database.migration_head`。**未改** `MAIN/contracts/**`、任何 `contracts.lock`、
  `MAIN/**`、`B1/B3/B4/B5/**`、`pyproject.toml`。
- **未做**：`CHG-0014` 的三个端点（隔离区复核两条 + 申诉证据包一条）属另一批工作，
  本批按指示未开工；契约与实现之间的差集由
  `tests/test_b2_routes.py::_PENDING_CONTRACT_PATHS` 显式列名并断言。

## 1. 教学评语轨（`RQ-T-20`，`ADR-0009`）

**做了什么**：`GET`/`POST /api/v1/evaluation-runs/{evaluation_run_id}/commentary` 两个端点；
`src/autograder_b2/commentary/`（`scope` 白名单 / `prompt` / `provider` / `store` / `service`）；
迁移 `0010` 建 `b2.evaluation_commentaries`；`B2_COMMENTARY_*` 配置（**默认一个都不给**）。

四条边界的落地方式（每条都有对应测试，不靠注释）：

1. **输入白名单是安全边界**：取证只用**字段级 SELECT**（`RUN_COLUMNS`/`CASE_COLUMNS`/…），
   `case_evidence` 的两个摘录列与三张表的 `message` 列**根本不在查询里**；
   `INPUT_SCOPE` 由同一批列清单生成，随 `provenance.input_scope` 返回。
2. **不可达**：`FORBIDDEN_EVIDENCE_MODULES` 列出所有能拿到源码/期望输出的模块（CAS、信封装载、
   package、工作区物化、`run_case`），测试断言评语包的 import 闭包与它无交集，并把那些访问器
   换成会抛异常的替身跑通一次真实生成。
3. **不污染分数轨**：评语只写自己的表；清空该表后重放同一提交，分数/`result_digest`/逐项证据逐字相同；
   把 `CommentaryService.generate` 换成抛异常的替身，评测照常完成。
4. **默认关闭**：未配置时 `GET` → `200 + unavailable`、`POST` → `409` 指名缺项，
   且传输替身**一次都没被调用**（不是"调用了但忽略"）。

**刻意的口径选择（写在这里，避免读者误读）**：

- `provenance` 在 `pending`/`ready`/`failed` 三种已落库状态下**都返回**，`unavailable` 时为 `null`。
  契约的 description 是 "Null when nothing was generated"——本实现把它读作"**没有发生过生成尝试**"
  （即 `unavailable`）。另一读法（"没有文本 ⇒ 一律 null"）会让 `failed` 失去唯一的归因渠道：
  schema 里没有别的字段能承载"是哪家 provider 失败的"，而 `input_scope` 也会随之消失。
- 有 provider 但**从未生成**时返回 `404`：契约的 `status` 枚举里没有这一态
  （`unavailable` 的定义是"没有配置 provider"，`pending` 的定义是"已受理未完成"），
  而 `404` 是契约已声明的状态码，`detail` 明说这与"运行不存在"不是一回事。
- provider 失败：**先落 `failed` 再返回 `503`**（原因只含 HTTP 状态码/异常类型，不回显响应正文）。
  该条评语因此可通过随后的 `GET` 看到 `status: failed`，而分数轨一个字节都不动。

## 2. 逐用例资源实测（`CHG-0012` 的 `resource` / `ADR-0010` 决策 3）

`src/autograder_b2/runner/cgroup.py` 读 `memory.peak` / `cpu.stat` / `pids.peak`，
`RunnerPolicy` 的挂载点与 nsjail 的 cgroup 命名规则（`{mount}/NSJAIL.<被囚子进程 pid>`）都取自现有实现。
**每一项独立可空，读不到就是 `null`，绝不写 0**；`wall_seconds` 由 B2 自测（总有值）。

### 一个必须上报的实测事实：nsjail 在子进程被 reap 时就删掉了那个 cgroup

`ADR-0010` 决策 3 的原话是"执行后回读 cgroup 的 `memory.peak`、`cpu.stat`、`pids.peak`"。
按 nsjail 源码（`subproc.cc::reapProc()` → `cgroup2::finishFromParent()` →
`cgroup2.cc::removeCgroup()` → `rmdir(cgroupv2_mount + "/NSJAIL." + pid)`），
**子进程退出的那一刻该目录就被删除**，进程结束后再回读只会读到"目录不存在"。
因此本实现的做法是：

1. **运行期取样**：`CaseResourceSampler` 在沙箱进程存活期间按 `CGROUP_SAMPLE_INTERVAL_SECONDS`
   周期取样并取各项最大值（`memory.peak`/`pids.peak`/`cpu.stat` 都是单调量）；
2. **收尾再读一次**（`stop()` 内）：若目录仍在（换后端或上游行为改变）就拿到真正的最终值；
3. 目录定位用 `/proc/<nsjail_pid>/task/<nsjail_pid>/children` 解析**被囚子进程** pid，
   并要求 `NSJAIL.<pid>` 目录真实存在——**不猜目录名**。

**诚实边界**：取样得到的是"运行期间观察到的最大值"，对单调量而言是真实峰值的**下界**；
间隔越短越接近真值，但永远不会高估。这一点本机（Windows）**无法验证**，
Linux 侧验收必须复测；未复测前不得把 `resource` 当成已验收的能力（见下"未验收"）。

**另一个契约侧的口径**：B2 的 `resource_limit_hit` 取值域是
`memory|cpu|wall|processes|output|files`，而契约 `case_resource.limit_hit` 的枚举是
`memory|cpu|pids|wall|output`（映射见 `runner.cgroup.LIMIT_HIT_BY_RESOURCE_LIMIT`）。
`files` **没有对应取值**：它本来就是"执行后计数判定"，不是"某个被强制执行的限额结束了本用例"，
因此如实映射为 `null`，具体限制项由 `case_evidence.resource_limit_hit` 与 `explanation` 表达。

## 3. 逐用例文件证据（`CHG-0012` 的 `artifacts` / `RQ-B-0-30` 验收 2）

`snapshot_workspace` 记录每个文件的大小与内容摘要，`workspace_changes` 给出
`created`/`modified`/`deleted` 三张清单（工作区相对路径、内容摘要、字节数）。

- **删除的文件**其摘要取自**运行前**快照——内容此刻已不在盘上，事后再算是不可能的。
- **解释器字节码缓存（`__pycache__`/`*.pyc`）不进清单**：`.pyc` 头里带源文件 mtime，
  同一份提交跑两次会得到不同字节，一旦进报告就会让 `result_digest` 永久失去"同绑定 ⇒ 同摘要"的意义。
  `max_files_created` 的**资源口径**仍照旧统计它们（两个口径刻意不共用同一个过滤）。
- **摘要预算** `EVIDENCE_HASH_BUDGET_BYTES`（32 MiB，按排序累加 ⇒ 确定性）：超出预算的文件只记大小、
  摘要为 `null`（"没测"而不是"测出来是空"）。
- **上限**：三张清单各自截断到契约的 200 条，任何一张被截断时 `truncated=True`，
  由 `explanation` 如实说明（`truncated` 是 B2 内部字段，**不进契约**）。
- **多产出文件的取舍**：契约只有一个 `expected_sha256`/`actual_sha256` 槽位，而用例可以声明多个产出文件。
  实现只在**恰好一个**产出文件时填这两个字段；多文件时为 `null`（逐个文件的实际情况仍在三张清单与
  `missing_outputs`/`mismatched_outputs` 里），避免"挑一个填进去"被读成"这就是那个文件"。

## 4. 确定性失败解释（`CHG-0012` 的 `explanation`）

`evaluation/explanation.py::explain_case` 是**纯函数**：输入是同一用例的证据对象，
输出固定规则推导的中文说明。覆盖契约要求的五种形态：输出不符（**指名首个分歧行**——
`ComparisonOutcome.mismatch_line`，是行号不是内容）、非零退出、超时、缺产出文件、安全阻断。

- **模型输出进不了这里**：`case_evidence` 的 schema 是 `additionalProperties: false`，
  而教学评语走另一条轨、存在另一张表——这是结构性事实，不是承诺。
- **只准用摘要稳定字段**：解释会进报告、进而进 `result_digest`，因此它**不得**引用
  `duration_ms` 与 `resource`（两者都被排除在摘要之外）。本实现里没有任何一处这么做，
  有测试断言文本里不出现时长，也有断言"改资源读数不改变报告摘要"。

## 5. 沙箱自证（`ADR-0010` 决策 2）

`python -m autograder_b2.canary`：六项**必须失败**的金丝雀作业（读宿主文件、联网 TCP+DNS、提权、
写只读根、超内存、超进程数）。它**复用生产执行路径**（`runner.run_case._execute_case`：
同一份 nsjail 配置渲染、同一份 `build_argv`、同一套进程树清理与有界输出采集），不是复刻版。

- 三态与安全结论同一套语义：`found`（发现风险）/ `not_found`（被挡住）/ `not_checked`（未执行或无法归因）。
- **退出码**：`0` = 全部挡住；`2` = 发现风险；`3` = 有未检查项。CI 因此**不可能**把"没跑"当成通过。
- **沙箱不可用时不创建任何进程**：直接返回全 `not_checked`（测试用一个"一启动就抛异常"的启动器证明
  它一次都没被调用，且不创建任何金丝雀工作区）。
- **本机（Windows）读数**：六项全部 `not_checked`、退出码 `3`、结论文本显式写"未被证明"。
  这是 `ADR-0010` 后果节点写明的正确结果，不是缺陷。

## 验证（真实执行）

```powershell
cd B2
uv run ruff check .
uv run pytest -q
```

见本文件末尾的"验证记录"。**未执行项**在下一节逐条列出。

## 契约不足与未验收（本批最重要的产出之一）

### A. 白名单里 B2 读不到的证据面

契约把"题目的**公开**元数据（语言、适配器类型、比较策略）"列为评语轨**允许**读取的输入之一。
B2 **从不落库**这三项（`evaluation_runs` 没有这些列，`PackageView` 也不暴露 adapter kind），
因此它们**不在** `input_scope` 里。

- 为什么不在实现侧顺手补上：需要一个题目元数据快照（新列 + `ports.PackageView` 暴露 adapter kind），
  后者是**端口契约**的改动，属需决策的改动，本批不自行发明。
- 为什么这**不**违反验收 1：白名单是**许可**而不是义务。少读是严格更安全的，而 `input_scope`
  如实列出"实际读了什么"——把没读的字段写进白名单才是真正的谎报。

**三态安全结论**原本也读不到（它只活在报告 JSON 里，而报告同时承载隐藏用例的期望输出，
把它载入内存等于把受保护内容放上生成路径；从 `security_findings` 的行数又**推不出**
`not_found` 与 `not_checked` 的区别）。本批用迁移 `0011` 给 `evaluation_runs` 补了一列
`security_status`（安全判定阶段原样写入，含 `not_checked`），因此这一项**已经可读**。

### B. 发现一个既有缺陷：带静态安全发现的提交**无法重放**

`b2.security_findings` 的主键是 finding 自身的内容派生 uuid
（`security/patterns.py` 的 `uuid5(规则,文件,行)`）。因此同一提交第二次评测必然在
`security_findings_pkey` 上撞唯一约束，异常从 `EvaluationOrchestrator.run` 里**直接冒出去**
（既不是终态、也不是失败事件）。`security/findings.py` 的 docstring 把"重复落库冲突"写成有意行为，
但没有回答"第二个 run 该怎么办"。

本批**没有修**（修法有两种、语义不同，属需决策）：① 静态发现按 (运行, finding) 派生主键；
② 写入侧 `ON CONFLICT DO NOTHING`（但那样第二个 run 会"看起来没有任何发现"）。
现状由 `tests/test_commentary_track.py::test_replaying_a_run_with_static_findings_is_blocked`
以 `xfail(strict=True)` 钉住（它现在确实失败，因此 xfail 成立；修好后该用例会因 strict 而提醒改回断言）。

### C. `CHG-0014` 未做

三条端点（隔离区复核两条 + 申诉证据包一条）属另一批。契约与实现的差集由
`tests/test_b2_routes.py::_PENDING_CONTRACT_PATHS` 逐条列名并断言等值。

### D. 未验收（本机做不到，**不写成通过**）

| 项 | 状态 | 原因 |
|---|---|---|
| 真实 nsjail 下 `resource` 的实际取值 | **未验收** | 需要 Linux + nsjail；本机沙箱不可用（评测根本走不到动态阶段） |
| `memory.peak`/`pids.peak` 的可读性（nsjail 是否为 cgroup v2 后端、是否删目录） | **依据上游源码 + 本机合成树验证**，未在真机复测 | 见上文"一个必须上报的实测事实" |
| 金丝雀在真实内核上的跑批（T-CANARY-004） | **未验收** | 同上 |
| 真实 provider（HTTP）连通性 | **未验收** | 测试用传输替身（这也是"未配置时不出网"的断言手段）；真实网络调用必须在部署环境验证 |
| 部署侧迁移 | **待执行** | 升级到本构建前必须 `uv run alembic upgrade head`（`EXPECTED_SCHEMA_REVISION` 变了，库落后时路由按设计 503） |

## 验证记录（真实执行）

见下文"验证记录"一节：命令与原始读数逐字粘贴，未执行的项在上表逐条说明。
