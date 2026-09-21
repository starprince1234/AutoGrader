# 红队裁决记录：B2 · Linux 沙箱验收（工作项三条标准 + 两个修复）

- 裁决者：红队（冷视角），**未修改任何被测文件**；全部探针写入 `.pytest-tmp/`。
- 环境：Windows 10 + PowerShell；Docker Desktop（容器 `autograder-b2-nsjail:local`，内核 `6.18.33.2-microsoft-standard-WSL2`，nsjail 3.4 位于 `/usr/local/bin/nsjail`）；PostgreSQL 55433 / NATS 54223 **未启停**。
- 本轮临时库：`b2_rt_97e74bec`（宿主建库 + `alembic upgrade head` → head `20260915_0009`）。
- 盘档的三条标准原文见任务书；本文件只写**我实测到的**事实。

---

## 0. 一句话结论

| 项 | 裁决 |
|---|---|
| 标准 1（真实 nsjail 内合法提交得到终态与分数） | **pass**（我用自带程序独立复现，含反证） |
| 标准 2（五类恶意/超限被拦截或归类；超时无残留） | **pass**（五类各有我的真实观测量 + 沙箱外对照；残留按存活/僵尸分开核对） |
| 标准 3（Windows 不可执行清单/原因/恢复操作入档，状态如实 active） | **pass**（清单在**第 27 条**，非任务书说的第 28 条；5 行齐全，状态如实） |
| 修复 1（nsjail 路径探测） | **真缺陷确实修好**；发现 1 个低severity缝隙（探测接受**目录**） |
| 修复 2（归类顺序） | 顺序改动本身生效，但**"归因面不可伪造"这一结论不成立**：学生一行 stderr 文本即可把 `output_mismatch` 改写并**落库**为 `resource_limit/memory`（真跑证据见 §5） |

---

## 1. 我跑了什么（可复算命令）

宿主：

```powershell
$env:B2_TEST_DATABASE_URL="postgresql+psycopg://autograder_platform:change-me-local-only@localhost:55433/autograder"
uv run ruff check .            # → All checks passed!
uv run pytest -q -p no:warnings
# → 1132 passed, 8 skipped, 1 xfailed in 181.10s   （与盘档声称逐字一致）
```

容器（真 Linux + 真 nsjail；探针均在 `.pytest-tmp/`，容器内 `-w /work/B2`）：

```powershell
# 我自写的探针（标准 1：合法提交 + 反证 + 真实编排层重放）
docker run --rm --init --privileged --cgroupns=host --add-host=host.docker.internal:host-gateway `
  -e "PYTHONPATH=/work/B2/src:/work/B2/.pytest-tmp/linux-site" -e PYTHONDONTWRITEBYTECODE=1 `
  -e "B2_TEST_DATABASE_URL=postgresql+psycopg://autograder_platform:change-me-local-only@host.docker.internal:55433/b2_rt_97e74bec" `
  -v "D:/VScodeProjects/AutoGrader:/work" -w /work/B2 autograder-b2-nsjail:local `
  python3 .pytest-tmp/redteam_std1_runner.py            # → 全部断言通过 (exit 0)

# 标准 2：五类样本 + 沙箱外对照 + 残留扫描
... python3 .pytest-tmp/redteam_std2_hostile.py          # → 全部断言通过 (exit 0)
... python3 .pytest-tmp/redteam_zombie_noinit.py         # 故意不带 --init
# 修复 1 / 修复 2 / 真实 nsjail 日志扳机 / 伪造归因落库
... python3 .pytest-tmp/redteam_fix1_path.py             # → 2 条 FAIL（见 §4 缺陷 D2）
... python3 .pytest-tmp/redteam_fix2_classify.py         # → 4 条 FAIL（见 §5）
... python3 .pytest-tmp/redteam_nsjail_logs.py           # → exit 0
... python3 .pytest-tmp/redteam_fix2_persist.py          # → exit 0

# 另：把**交付的**两套套件在容器里原样重跑（我只用它们核对宣称的数字，不作为结论依据）
python3 -m pytest tests/linux/test_nsjail_acceptance.py -q --no-header -p no:cacheprovider
# → 12 passed, 1 skipped, 1 xfailed in 24.30s
python3 -m pytest tests/linux/test_nsjail_runner_acceptance.py -q --no-header -p no:cacheprovider
# → 4 passed, 1 skipped in 20.67s
```

**未跑 / 跑不成**：
- 未启停任何容器；未跑 `tests/linux` 的宿主驱动模式（`AUTOGRADER_B2_NSJAIL_VIA_DOCKER=1`）——它只是把同一份容器命令再包一层，我直接跑了容器内那层。
- 我**没有**复现盘档里 `result_digest=3c60673b…fd8af` 这个字面值：该值绑定他们那份提交/package，换输入必然不同。我复现的是**性质**（同提交重放同摘要，见 §3）。
- 我**没有**在容器内验证"沙箱不可用 ⇒ 拒绝执行"（交付的 i7 在容器内 skip；我复核了它会 skip 的条件判断，见 §6-未执行项）。

---

## 2. 标准 1：独立复现（pass）

探针：`.pytest-tmp/redteam_std1_runner.py`（自带学生程序：读两个整数求和，并把**沙箱内 euid/pid** 打进 stdout；期望输出先由一次"未声明期望"的运行校准，再据此声明，最后做反证）。

关键输出（容器，真 nsjail）：

```text
[obs] 校准运行 passed=True exit=0 duration_ms=285 excerpt='SUM=7 euid=65534 pid=1\n'
[obs] 合法提交 passed=True cat=None exit=0 duration_ms=264 limit=None stdout_digest=342e6685a202f57b…
[PASS] A2 合法提交通过（真实 run_case + 真 nsjail）
[PASS] A2 真实退出码 == 0 :: 0
[PASS] A2 stdout 摘要 == sha256(期望输出)（真实 stdout 被逐字比较）
[PASS] A2 沙箱内 euid 就是策略声明的 65534（证明在 nsjail 内执行） :: SUM=7 euid=65534 pid=1
[obs] 反证（期望改错）passed=False cat='output_mismatch' exit=0 msg='exact mismatch (actual 23 vs expected 21 字符)'
[PASS] A3 期望输出改错 ⇒ 必须失败
[obs] 反证（学生 exit 3）passed=False cat='output_mismatch' exit=3
[PASS] A4 非零退出 ⇒ 必须失败
```

判据说明（为什么这不是"断言冒充"）：
- `euid=65534` 且 `pid=1` 是学生程序**在沙箱内自己观测并打印**的，等于证明它真的跑在 nsjail 的 user+PID 命名空间里（策略 `NSJAIL_SANDBOX_UID=65534`，`runner/policy.py:70`）。
- 通过的那一次，期望输出里**含这个 euid 观测值**，而 `evidence.stdout_digest == sha256(期望)` ⇒ "通过"依赖真实进程的真实 stdout。
- 反证两条（期望改错 → `output_mismatch`；学生 exit 3 → 失败）说明通过不是恒真。

## 3. 标准 1：真实编排层（真 PostgreSQL）的终态、分数、重放（pass）

同一探针 B 段（真 `EvaluationOrchestrator` + 真 `RunnerCaseRunner` → 真 `run_case` → 真 nsjail；包来源/工作区物化是替身，与交付 i5 的装配口径一致）：

```text
[obs] run#0 id=8c7ffb7e-… state=completed score=1.0 is_pass=True digest=f043a9bc13b9acf07602e7c11c30b1b2be4fca2360a2098304605f4e7a2a5eff db_state=completed db_score=1.0 evidence_rows=1
[obs] run#1 id=85895797-… state=completed score=1.0 is_pass=True digest=f043a9bc13b9acf07602e7c11c30b1b2be4fca2360a2098304605f4e7a2a5eff db_state=completed db_score=1.0 evidence_rows=1
[PASS] B4 DB evaluation_results 的分数/摘要与返回值一致
[PASS] B5 DB 有 case_evidence 行且 passed=True / exit_code=0
[PASS] B6 重放同一提交 ⇒ 同分同 result_digest
[PASS] B7 重放 ⇒ case_evidence 的 input_digest 与 evidence_digest 逐字节相同
```

即：终态 `completed`、`score=1.0`、`result_digest` 非空且两次逐字节相同，DB 的 `evaluation_results`/`case_evidence` 与返回值一致。

## 4. 标准 2：五类样本 + 残留（pass）

探针：`.pytest-tmp/redteam_std2_hostile.py`（样本全部自写，且每类之后都做一次 `ps -eo pid,ppid,comm,stat,args` 扫描，存活/僵尸分开统计；容器 `PID1=docker-init`）。

| 类别 | 我实测到的真实观测量 | 判定 |
|---|---|---|
| 禁网 | `cat='security_blocked' exit=159`（=SIGSYS）`violations=[('sandbox_violation','runner.seccomp')]`，stdout 为空；**沙箱外对照**同一程序：`NET_OK 1.1.1.1:80 / NET_OK 93.184.216.34:80 / DNS=('104.20.23.154', 80) / IFACES=['eth0','lo']` | 真被拦（seccomp 默认拒绝 `socket`），不是"程序自己没连" |
| 超时 | `cat='timeout' limit='wall' exit=-9`，`duration_ms≈5150`，`msg='…process tree cleanup: signal 9 to process group 13'`；残留 `live_nsjail=[] live_workspace=[] zombies=[]` | 通过 |
| fork bomb | 沙箱内 `FORK_SUCCESS=15 LIMIT_HIT=(15, 11, 'Resource temporarily unavailable')`（EAGAIN，正好撞上 `max_processes=16`）；**沙箱外对照**：`FORK_SUCCESS=200 LIMIT_HIT=None` | 限制真生效 |
| 内存 | `MEM_MEMORYERROR allocated_mib=48`（策略 `max_memory_bytes=64MiB` → `rlimit_as=64`），`exit=9` | 真被挡；**归类**为 `output_mismatch`（学生侧），与交付文档的"如实限制"一致 |
| 输出洪泛 | `cat='resource_limit' limit='output' exit=1 duration_ms=391`，摘录 2000 字符（有界） | 通过（另有 jail 级 `rlimit_fsize` 佐证：见 §5.3） |

**残留**（标准 2 的要害）：
- 带 `--init`：`PID1='docker-init'`，全部样本后 `存活 nsjail=[]、引用本工作区的存活进程=[]、僵尸=[]`。
- **不带** `--init`（`.pytest-tmp/redteam_zombie_noinit.py`）：`PID1='python3'`，超时样本后稳定存在 **1 个僵尸** `8 1 python3 ZNs [python3] <defunct>`（延迟 0/1/3s 三次扫描一致）。
  ⇒ 文档"`--init` 是必要的（否则被杀进程以僵尸形式留下）"（`verify/linux-nsjail/README.md:127-128`）**独立复现成立**；该僵尸是**已终止未回收**，不引用工作区、不占资源，交付 i4 只断言"存活"进程（`tests/linux/test_nsjail_runner_acceptance.py:293-313`）在两种容器形态下都不会误判。

## 5. 两个修复的独立结论

### 5.1 修复 1：nsjail 路径探测（真缺陷确已修好；1 个低severity缝隙）

探针 `.pytest-tmp/redteam_fix1_path.py`（Linux 真文件系统）：

```text
[obs] 候选位置实测存在性/可执行：{'/usr/bin/nsjail': (False, False), '/usr/local/bin/nsjail': (True, True), …}
[PASS] 1 声明路径可用时优先用它 :: '/usr/local/bin/nsjail'
[PASS] 2 声明路径不存在时回退到标准源码安装位置 :: '/usr/local/bin/nsjail'
[obs] probe 判定 available=True nsjail_check='nsjail=/usr/local/bin/nsjail exists+executable=True'
[obs] build_argv[0]='/usr/local/bin/nsjail'
[PASS] 3 探测报出的路径 == build_argv 实际启动的路径
[PASS] 4 reason 里列出全部被搜索的位置 :: nsjail=/nonexistent/nsjail exists+executable=False（已按候选位置查找：…7 个位置…）
[PASS] 5 注入式：声明与候选都可执行 ⇒ 声明胜出 / 只有候选可执行 ⇒ 用它
[FAIL] 6 目录不得被当成可用的 nsjail（应返回 None） :: resolve 返回了 '…/nsjail-is-a-directory'
[obs] 该策略下 probe_sandbox.available=True reason=None checks=[('platform',True),('cpu_arch',True),('nsjail',True),('cgroup_v2',True),('user_namespace',True)]
[obs] 用该路径启动的真实异常：PermissionError: [Errno 13] Permission denied: '…/nsjail-is-a-directory'
```

- 修复方向正确且**探测与启动确实用同一个绝对路径**（`resolve_nsjail_path` 同时被 `probe_sandbox`（`policy.py:1007`）与 `build_argv`（`policy.py:665`）调用；生产路径两边都用默认 `_default_is_executable`）。
- 我还另测了 `nsjail_path=""` 这种空声明：`os.access("", X_OK)=False`（容器实测）⇒ 会正确回退到候选，**没有**该缝隙。
- 缝隙（**新缺陷 D2**）：`_default_is_executable`（`policy.py:961-962`）用 `exists()+X_OK`，**目录会判为可执行**（`os.access(dir, X_OK)` 为真）。于是"探测说可用、启动必然失败"，且失败形态是 `PermissionError` ⇒ 进 `run_case` 的兜底分支变成 `InternalEvaluationError`（`run_case.py:806-811`），而不是"沙箱不可用"这条明确的拒绝路径。

### 5.2 修复 2：归类顺序（"归因面不可伪造"不成立，**这条结论必须撤回或改成真的**）

探针 `.pytest-tmp/redteam_fix2_classify.py`（上半段纯函数、下半段容器内真跑）：

```text
[PASS] A nsjail 诊断（memory/processes/cpu）优先于输出比较 ⇒ resource_limit     # 改动本身生效
[PASS] A 学生程序打印 MemoryError（无 nsjail 字样）⇒ 仍 output_mismatch
[obs] 学生打印 'nsjail: out of memory' → cat='resource_limit' hit='memory'      # 伪造命中
[obs] 学生打印 '[E][2026-…][1] nsjail: memory limit exceeded' → cat='resource_limit' hit='memory'
[obs] 学生打印 'nsjail: operation not permitted (seccomp denied)' → cat='security_blocked'
[FAIL] B 伪造面：…不得改写成系统侧 resource_limit :: 4/4 条被改写成 resource_limit
[FAIL] B 伪造面：…不得改写成 security_blocked :: cat='security_blocked'

===== C. 容器内真跑：学生把伪造行写进真实 stderr =====
[obs] 伪造限制字样 → cat='resource_limit' limit='memory' exit=0 violations=[('resource_limit','runner.resource_limit')] msg='process exhausted a sandbox limit (item: memory)'
[obs] 伪造违规字样 → cat='security_blocked' violations=[('sandbox_violation','runner.sandbox_log')]
[obs] 仅打印 'MemoryError: cannot allocate memory' → cat='output_mismatch'   # 无 "nsjail" 字样时确实是学生侧
```

并且经**真实编排层 + 真 PostgreSQL**落库（`.pytest-tmp/redteam_fix2_persist.py`）：

```text
[obs] 伪造 nsjail 限制字样: run_state=completed score=0 case_evidence.failure_category='resource_limit' resource_limit_hit='memory' awarded=0 message='process exhausted a sandbox limit (item: memory)'
[obs] 诚实打印 MemoryError: run_state=completed score=0 case_evidence.failure_category='output_mismatch' resource_limit_hit=None awarded=0
```

机理（行号）：`_scan_markers`（`run_case.py:322-331`）的"防伪"过滤只是 **`"nsjail" in line.lower()`**；而学生程序的 stderr 与 nsjail 自己的日志**写进同一个文件**（`_execute_case` 只把 nsjail 的 stderr 重定向到 `stderr_path`，学生进程继承同一个 fd，`run_case.py:826-851`），所以这一行文本完全由被测方掌握。`classify_case_outcome` 在 `run_case.py:449-465` 用它做归类。

后果（精确到落点，不夸大）：
- 被改写并**持久化**的是 `b2.case_evidence.failure_category` / `resource_limit_hit` / `message`（`store.py:613-624`）以及报告/进度帧里的用例失败类别；
- **分数不受影响**（`awarded` 恒 0）、**运行终态不受影响**（编排层只把 `infrastructure_error` 升格为技术故障，`orchestrator.py:733-741`）；运行期 finding 目前也没进 `security_findings`（`store.record_security_findings` 只喂静态 decision，`orchestrator.py:600`；`security/decision.py:488 record_runtime_findings` 无生产调用方）；
- 但"被限制掉 ⇒ 平台侧归因"的公平性目标被反向利用：**学生自己一行文本就能把学生失败记成平台/沙箱限制**（内存那一类尤其刺眼——文档刚说"内存耗尽无法与自身失败区分"，一行 `nsjail: out of memory` 就"区分"了）。

因此以下三处结论**不成立**，需撤回或改成真的：
- `src/autograder_b2/runner/run_case.py:88-92`（"归因面必须不可由被测方伪造"）
- `tests/test_runner_core.py:2315-2326` 用例名/断言口径（该用例样本把 `nsjail` 与 `MemoryError` 放在**不同行**，所以恰好通过；把两者合成一行即失败——见下）
- `docs/phases/active/phase-01-implementation.md:245-247`、`tests/linux/README.md:94-96`

最小反例（我在纯函数层复现，等价于把该用例的样本改成一行）：

```text
classify_case_outcome(RunFacts(exit_code=1, stderr_text="nsjail: out of memory\n", stdout_comparison=(≠"42\n")))
→ failure_category='resource_limit', resource_limit_hit='memory'
```

### 5.3 附带事实：修复 2 的"正面分支"在真实环境里**没观测到扳机**

探针 `.pytest-tmp/redteam_nsjail_logs.py`（直接用 runner 生成的配置跑真 nsjail，stderr 落盘后原样打印）：

```text
[obs] cpu:    rc=137 stdout='CPU_START'                nsjail stderr 行数=0（落盘 0B）
[obs] memory: rc=9   stdout='MEM_MEMORYERROR allocated_mib=48'   nsjail stderr 行数=0（落盘 0B）
[obs] fsize:  rc=120（学生侧 'OSError: [Errno 27] File too large'，落盘 1048576B）nsjail stderr 行数=6，但全是学生 traceback，无 nsjail 诊断行
```

即：CPU/内存/文件大小三类真实限制下，nsjail **自己的**诊断行（`LIMIT_MARKERS` 依赖的东西）在我这三次真实执行里**一条都没有**；能触发 `resource_limit` 归类的只有伪造文本。这不是"限制没生效"（限制都生效了，见 §4），而是说明该修复的**收益侧没有实测证据**、**风险侧有**。（交付的 `test_nsjail_limit_diagnostic_beats_output_mismatch` 是合成字符串的单元用例，不构成真实扳机证据。）

## 6. 标准 3 与文档/数字一致性（pass + 若干文档问题）

- 清单位置：**第 27 条**（`docs/phases/active/phase-01-implementation.md:253-263`）——任务书说的"第 28 条"是**第 28 条另有所指**（`state↔terminal_reason` 配对）。`docs/README.md:38` 与 `docs/requirements/traceability.md:21` 都正确指向第 27 条。
- 5 行清单均含"项 / 无法执行的原因 / 恢复验证操作"，状态如实为 `active`（文档在 `docs/phases/active/`，且每行写明"未通过、也未撤销"）；没有把"未执行"写成"通过"：
  - 第 1 行"已在本机 Docker 完成"——我复核为真（§1 两条命令 12 passed/4 passed）。
  - 第 5 行明确写"容器内沙箱可用 ⇒ 拒绝路径未验"——与我复核的 i7 逻辑一致（`tests/linux/test_nsjail_runner_acceptance.py:545-549`：`availability.available` 为真即 skip）。
- 数字一致性（逐字核对，均相符）：`docs/README.md:38`、`traceability.md:21`、`phase-01:74/83-88`、`tests/linux/README.md:76-99` 都说"隔离矩阵 12 通过 + 1「无法执行」"与"真实 runner 验收 4 passed, 1 skipped"；我重跑容器两套套件得 `12 passed, 1 skipped, 1 xfailed` 与 `4 passed, 1 skipped`，宿主全量 `1132 passed, 8 skipped, 1 xfailed`——与盘档逐字一致。
- `docs/testing/cases.md:57-59` 的 T-RUN-008/009/010 均标"**存在**"并指向正确文件/用例名。

文档问题（不影响三条标准判定，但需修）：

- **D3**：`tests/linux/README.md:31` 写 5c 用 `rlimit_nproc` + `rlimit_nproc_type=HARD`，而实际代码/其余文档都是 `VALUE`（`tests/linux/test_nsjail_acceptance.py:322-323`、`src/autograder_b2/runner/policy.py:844-845`）。
- **D4**：`tests/linux/README.md:21`"验收项清单（`_REQUIRED_ITEMS`）共 10 项"，紧随的表却列了 11 行（含不在 `_REQUIRED_ITEMS` 里的"7"自检项，`test_nsjail_acceptance.py:1094-1110`），与 §4"12 项通过"叠加易误读。
- **D5**：`docs/testing/cases.md:58` 的 T-RUN-009 期望结果含"无僵尸"，但 i4 只断言**存活**进程（僵尸仅打印，`test_nsjail_runner_acceptance.py:296-318`）——期望结果强于断言。
- **D6（残留过期表述）**：`src/autograder_b2/runner/policy.py:20-23` 仍写"**仍缺的验收**：`run_case` 走真实 Linux 沙箱执行的那条路径本身没有验收证据（…`tests/test_runner_core.py` 注明在 Windows 上跑）"，与第 24 条"已结案"和新用例直接矛盾（我逐条确认这条路径已有证据，见 §2/§3）。同文件 `:17-19` 的矩阵数字仍准确。
- **D7（低）**：`policy.py:1118-1169` 的 `__all__` 未收录新增的 `NSJAIL_CANDIDATE_PATHS` / `resolve_nsjail_path`（测试直接 import，功能无影响，但公共面清单与实际不一致）。
- **D8（低，越界但相关）**：`phase-01:224` 的交叉引用"（归类限制见第 **28** 条）"应为**第 26 条**（第 26 条才是这条归类修复；第 28 条是 `state↔terminal_reason`）。
- **D9（低，越界）**：运行期沙箱违规没有持久化落点——`b2.case_evidence` 无违规列（`db/schema.py:263-285`）、`store.record_case_evidence` 不写违规（`store.py:606-628`）、`security_findings` 只由静态 decision 写入（`orchestrator.py:600`），`record_runtime_findings`（`security/decision.py:488`）无生产调用方。故 §4 里我真实测到的 `runner.seccomp` 违规只存在于测试进程内存与 `evidence_digest` 里。不属本工作项三条标准，建议单独登记。

## 7. 未执行/未验证（如实记录）

1. 容器内"沙箱不可用 ⇒ 拒绝执行"：**未执行**（容器里沙箱可用；交付 i7 在此处 skip，我复核了 skip 条件但没构造缺 nsjail 环境）。
2. 宿主驱动模式（`AUTOGRADER_B2_NSJAIL_VIA_DOCKER=1`）：**未执行**（等价于我已跑的容器内命令）。
3. 盘档 `result_digest=3c60673b…fd8af` 字面值：**未能复现**（绑定他们的提交；我复现的是"同提交重放同摘要"性质）。
4. cgroup `memory.max` 杀进程（`5a2`）：**未单独复现**，沿用容器内 `xfail` 记录；我实测到的是 `rlimit_as` 生效（48MiB 处 MemoryError）。
5. 跨进程并发上限、真实 IMAP/TLS：与本工作项无关，未跑。
6. 我创建的临时库 `b2_rt_97e74bec` 未删除（供复核复算；如需清理：`docker exec autograder-b2-postgres psql -U autograder_platform -d postgres -c 'DROP DATABASE "b2_rt_97e74bec";'`）。

## 8. 探针清单（均在 `.pytest-tmp/`，未改任何被测文件）

| 文件 | 用途 |
|---|---|
| `redteam_std1_runner.py` | 标准 1：合法提交 + 两条反证 + 真实编排层分数/摘要/重放 |
| `redteam_std2_hostile.py` | 标准 2：五类样本 + 沙箱外对照 + 残留扫描 |
| `redteam_zombie_noinit.py` | 不带 `--init` 的僵尸对照 |
| `redteam_fix1_path.py` | 修复 1：声明/回退/不可用/探测与启动同路径/缝隙搜索 |
| `redteam_fix2_classify.py` | 修复 2：顺序生效 + 伪造（纯函数 + 容器真跑） |
| `redteam_fix2_persist.py` | 修复 2：伪造归因经真实编排层落库的结果 |
| `redteam_nsjail_logs.py` | 真实 nsjail 自有诊断行是否存在（修复 2 正面分支的扳机） |
