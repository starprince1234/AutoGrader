# 组级复核（L1 recheck）：动态评测与报告导出

- 复核者：独立组级复核者（冷视角、对抗式）；只读被测文件，探针全部写在 `.pytest-tmp/review_probe/`。
- 工作目录：`D:\VScodeProjects\AutoGrader\B2`；环境：Windows + PowerShell + 真实 PostgreSQL(55433) + Docker。
- 被复核对象：大类「动态评测与报告导出」（`evaluation/**`、`runner/**`、`api/{evaluation_routes,grades_routes}.py`、
  `grades/**`、`logging/**`、`events/**`）。
- 结论口径：**只写实际跑过的东西**；没跑成的写明原因。探针的 oracle 是脚本自己打印的 `PASS/FAIL` 与
  `FAILURES = [...]` 清单，不是"看起来对"。
- **快照**：本复核针对 2026-09-16 17:15 之前的源码快照（最后一个被复核源文件 `api/grades_routes.py`
  mtime = 17:15:15；`evaluation/orchestrator.py` = 17:04:27；`events/dispatcher.py` = 15:48:00）；
  探针运行时间窗 17:22–17:36，全部晚于该快照 ⇒ 证据与快照一致。
- 未修改任何被测文件：本会话的写操作全部在 `.pytest-tmp/` 内（`.gitignore:5` 忽略 `.pytest-tmp/`）；
  `src/**/__pycache__/*.pyc` 是解释器运行副产物（`.gitignore:2` 忽略 `__pycache__/`）。

---

## 0. 基线复算（自己跑）

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:B2_TEST_DATABASE_URL="postgresql+psycopg://autograder_platform:change-me-local-only@localhost:55433/autograder"
uv run ruff check .
uv run pytest -q -p no:warnings
```

实测输出：

```
All checks passed!                      # ruff（exit 0）
1124 passed, 3 skipped, 1 xfailed in 161.74s (0:02:41)
```

与任务书给出的基线（`1124 passed, 3 skipped, 1 xfailed`）**逐字一致**，ruff 全绿。基线无偏差。

复核用的独立探针（全部在 `.pytest-tmp/review_probe/`，自建临时库、自造 UUID/权重/分数、自写
HTTP 服务器与 JSON Schema 校验器，不复用 `tests/` 夹具）：

| 探针 | 覆盖 |
|---|---|
| `probe_lib.py` | 共享库：临时库、种数据、自己的端口替身、自己的 jsonschema 校验器、**标准库 asyncio 写的 HTTP/1.1 服务器**、原始套接字 SSE 客户端、生产日志捕获 |
| `probe_replay.py` | 标准 ①：重放一致性 + 反证 + 合计对齐 + 手工核算 + 自己的 schema 校验 |
| `probe_sandbox.py` | 标准 ②：Windows 拒绝 + 零进程创建的审计证据 + AST 进程创建点扫描 + 端到端归类 |
| `probe_sse.py` | 标准 ③：真实 HTTP 上的 SSE 单调/终态/重连 + 帧/日志/事件三处不泄漏 |
| `probe_export.py` | 标准 ③：导出转义/确定性/不泄漏 + 路由与渲染器字节一致 + 403/422 |
| `probe_cases.py` | 一致性：`cases.md` ↔ `tests/**` 交叉核对、登记清单抽查 |
| `probe_invariants.py` | 一致性：终态成因配对强制、标识口径六处一致 |
| `probe_events_order.py` / `probe_publish_order.py` / `probe_order_flip.py` / `probe_order_risk.py` | 一致性 → 新缺陷取证：事件发布顺序 |

---

## 1. 标准 ①「重放与可追溯」→ **PASS**

命令（每条约 60–90 s，含建库 + `alembic upgrade head`）：

```powershell
uv run python .pytest-tmp/review_probe/probe_replay.py      # 末行 FAILURES = []
```

自造数据：3 个用例，权重 `2.5 / 3.5 / 4.0`，仅序号 0、2 通过 ⇒ 期望 `6.5 / 10.0`；绑定摘要全部由
复核者自己的字符串派生（`sha256("recheck-env")` 等），运行标识由 `uuid4` 生成。

### 1.1 相同五项绑定摘要 ⇒ 同分数、同 `result_digest`

```
run#1 id=cc68dfc9-… state=completed score=6.5 digest=37b233f0401f7fbdf5d52fe4e69ed789a5079684abb65e5ec57d9ff70326e1b3
run#2 id=b54c8a77-… state=completed score=6.5 digest=37b233f0401f7fbdf5d52fe4e69ed789a5079684abb65e5ec57d9ff70326e1b3
[PASS] A2 相同五项绑定摘要 ⇒ 相同分数            :: 6.5 vs 6.5
[PASS] A3 相同五项绑定摘要 ⇒ 相同 result_digest
[PASS] A4 两次运行实例标识不同（digest 不是单次运行实例的产物）
[PASS] A5 分数与手工核算一致（2.5 + 0 + 4.0 = 6.5）
```

### 1.2 易变字段确实被排除（不是"两次跑得快所以碰巧一样"）

第三次运行把**时钟起点**推到 2044-12-31、把逐用例 `duration_ms` 改成 987654：

```
run#3 generated_at=2045-01-01T00:00:01.750Z duration_ms=[987654, 987654, 987654] digest=37b233f0…
run#1 generated_at=2031-05-06T07:08:11.750Z duration_ms=[11, 11, 11]
[PASS] A'1 generated_at / duration_ms 确实不同（前提成立）
[PASS] A'2 易变字段不同但 result_digest 相同（排除生效）
```

对应实现：`src/autograder_b2/evaluation/report.py:52` `VOLATILE_REPORT_FIELDS = ("generated_at",
"result_digest", "evaluation_run_id")`、`:54` `VOLATILE_CASE_FIELDS = ("duration_ms",)`、`:252-269`
`_digest_projection`。

### 1.3 反证：改动任一绑定项 ⇒ `result_digest` 必变

```
[PASS] B 改动 environment_digest      ⇒ 607e3fa3… vs 37b233f0…
[PASS] B 改动 submission_digest       ⇒ b60e27c7… vs 37b233f0…
[PASS] B 改动 package_digest          ⇒ 10cbee96… vs 37b233f0…
[PASS] B 改动 runner_policy_digest    ⇒ 34a43b2f… vs 37b233f0…
[PASS] B 改动 question_version_binding⇒ cf5dc18f… vs 37b233f0…
```

五项绑定摘要**逐项**都有区分力（`report.py:76` 的 `question_version_binding` 也在投影内）。

### 1.4 逐项之和 == 总分（三路对齐）+ 手工核算

```
报告逐项 awarded=['2.5', '0.0', '4.0'] sum=6.5 overall.score=6.5
total_check.sum_of_awards=6.5 matches_total=True max_score=10.0
DB b2.case_evidence = [(0,'11c12124-…','2.5',True,'2.5'), (1,'b4f21ffa-…','3.5',False,'0'), (2,'268b0055-…','4.0',True,'4.0')]
evaluation_results row = [('6.5','10.0',False,'37b233f0…','37b233f0…')]
[PASS] C1 报告逐项之和 == overall.score
[PASS] C4 报告 awarded 与数据库 awarded 逐项一致
[PASS] C7 evaluation_results.result_digest == report.result_digest == 返回值
```

抽 2 条手工核算（`D1`/`D3`）：`ordinal=0 passed=True weight=2.5 ⇒ 手工 2.5 == 报告 2.5`；
`ordinal=2 passed=True weight=4.0 ⇒ 手工 4.0 == 报告 4.0`。两条的 `case_id` 均在自造的 package
用例集合内（`D1b`/`D3b`），静态明细每条带 `rule_id`（本次为 `STAT-PY-013`），安全结论三态原样
（`not_found`）。评分依据：`scoring.py:191` `awarded = weight if item.passed else ZERO`、
`scoring.py:208-210` 与 `report.py:210-216`（报告侧**独立**再加总一遍）。

### 1.5 用 MAIN 的 JSON Schema + **自己的**校验器校验真实报告

```
jsonschema format checkers active=['uuid'] missing=['date-time']
own-schema errors (0): []
[PASS] E1 真实报告通过 MAIN evaluation-report.schema.json（自己的校验器）
[PASS] E2 校验器有区分力：删掉 total_check 必须报错
[PASS] E3 校验器抓住 matches_total != true（const）
[PASS] E4 校验器抓住绑定摘要形态非法（pattern）
```

校验器是 `jsonschema.Draft202012Validator + FormatChecker`，直接读
`../MAIN/contracts/schemas/evaluation-report.schema.json`，**不 import** `autograder_b2.contracts`。
E2–E4 三处故意破坏证明它不是"永远通过"。

> **如实标注**：本机 jsonschema 没有 `date-time` 检查器，因此 `generated_at` 的**格式**不是由
> schema 校验的。我另外独立核对了它的形态：`2031-05-06T07:08:11.750Z` 既能被
> `datetime.fromisoformat` 解析，也匹配 `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$`。
> 这是"我没能给 schema 校验器补上 date-time 检查器"的限制，不是实现缺陷。

**标准 ① 判定：PASS**（重放一致、反证有效、逐项可复算、报告过契约 schema）。

---

## 2. 标准 ②「沙箱与降级」→ **PASS**

### 2.1 Linux / 真 nsjail / cgroup v2（Docker 驱动，两种跑法都跑了）

```powershell
$env:AUTOGRADER_B2_NSJAIL_VIA_DOCKER='1'; $env:AUTOGRADER_B2_NSJAIL_IMAGE='autograder-b2-nsjail:local'
uv run pytest tests/linux/test_nsjail_acceptance.py -v -s     # 宿主 docker 驱动
# 另跑一次容器内原生模式（拿到逐项矩阵）：
docker run --rm --privileged --cgroupns=host -e PYTHONPATH=/work/src -v D:/VScodeProjects/AutoGrader/B2:/work \
  -w /work autograder-b2-nsjail:local python3 -m pytest tests/linux/test_nsjail_acceptance.py -v -s --no-header -p no:cacheprovider
```

**矩阵数值（我自己跑的容器原生模式）**：

```
12 passed, 1 skipped, 1 xfailed in 23.56s
tests/...::test_01_nsjail_starts_and_runs_python                          PASSED
tests/...::test_02_network_is_disabled                                    PASSED
tests/...::test_03_root_filesystem_is_read_only                           PASSED
tests/...::test_04_jailed_process_is_not_root                             PASSED
tests/...::test_05a_memory_limit_is_enforced                              PASSED
tests/...::test_05a2_cgroup_v2_memory_max_kills                           XFAIL
tests/...::test_05b_cpu_time_limit_is_enforced                            PASSED
tests/...::test_05c_pids_limit_blocks_fork_bomb                           PASSED
tests/...::test_05d_output_limit_truncates                                PASSED
tests/...::test_05e_wall_clock_timeout_kills                              PASSED
tests/...::test_06_timeout_cleans_whole_process_tree                      PASSED
tests/...::test_07_every_item_reports_command_output_and_verdict          PASSED
tests/...::test_i1_runner_generated_config_is_accepted_by_real_nsjail     PASSED
tests/...::test_docker_driver_runs_acceptance_in_privileged_linux_container SKIPPED（容器内已是 Linux）
```

宿主 docker 驱动模式输出（容器内矩阵被折叠进一行）：

```
container_returncode=0 | matrix_items=13 missing=[] | not_passed={}
limitations={'5a2 cgroup memory.max 杀进程（环境限制）': '无法执行'}
integration={'I1 runner 配置跑真 nsjail（集成项）': '通过'}
```

逐项判定（容器输出里的矩阵，原样摘录关键单元格）：

- **禁网可验证**：`2 禁网可验证` → `通过`；证据
  `tcp_1.1.1.1:80=FAIL OSError errno=101 msg=[Errno 101] Network is unreachable |
   dns_example.com=FAIL gaierror errno=-3 | udp_8.8.8.8:53=FAIL errno=101 | IFACES=['lo']`。
- **资源超限被归类**：
  `5a 内存上限生效` → `通过`（`rlimit_as=128MiB`，`MEM_BLOCKED MemoryError allocated=112MiB`，`rc=4`）；
  `5b CPU 时间上限生效` → `通过`（`returncode=137`，`terminated with signal: SIGKILL (9)`，`elapsed=3.04s`）；
  `5c 进程数上限挡住 fork bomb` → `通过`（`FORK_BLOCKED children=15 errno=11(Resource temporarily unavailable)
   jail_proc_count=16`）；`5d 输出上限（截断）` → `通过`（`读取上限=4096B 实收=4096B truncated=True`）；
  `5e 墙钟超时` → `通过`（`returncode=137`，`run time >= time limit (3 >= 3)`）。
- **超时后整个进程树无残留**：`6 超时清理整个进程树` → `通过`；证据
  `[对照·无沙箱] 杀父进程后残留=[44]（证明检测手段有效） | [实测·nsjail] 超时前=[] 超时后=[]
   elapsed=3.00s returncode=137 … pid=46 terminated with signal: SIGKILL (9), (PIDs left: 0)`。
  对照项（无沙箱确实残留 pid 44）使"[]"不是空转证据。
- **唯一非通过项**：`5a2 cgroup memory.max 杀进程（环境限制）` 判定 `无法执行`（`xfail`）；原因见
  `tests/linux/README.md` §1：本环境内核记账但不 OOM-kill，`rlimit_as` 先命中（`rc=4`）。

### 2.2 本机（Windows，缺 nsjail / cgroup v2）拒绝执行 + 无裸进程降级路径

```powershell
uv run python .pytest-tmp/review_probe/probe_sandbox.py     # 末行 FAILURES = []
```

探测项逐项数值（`probe_sandbox` 输出）：

```
probe[platform]      ok=False :: platform=win32（nsjail 仅 Linux 可用；本平台一律判定不可执行，绝不降级）
probe[cpu_arch]      ok=True  :: machine=x86_64 seccomp_arch=x86_64 match=True
probe[nsjail]        ok=False :: nsjail=/usr/bin/nsjail exists+executable=False
probe[cgroup_v2]     ok=False :: /sys/fs/cgroup/cgroup.controllers unreadable
probe[user_namespace] ok=False :: platform is not linux
available=False
```

真调用（**生产默认参数**，不传 `probe`/`launcher` 接缝；真 package 由契约 JSON 解析得到、
真 `runner.run_case`）：

```
run_case raised: SandboxUnavailableError: sandbox unavailable: platform: …; nsjail: …; cgroup_v2: …; user_namespace: …
audit spawn events during run_case: []          # sys.addaudithook 记录 subprocess.Popen/os.system/os.exec*/os.fork*
workspace before=['main.py'] after=['main.py']  # 前置判定发生在任何进程与任何写入之前
[PASS] B1 真 run_case 在缺沙箱平台上抛 SandboxUnavailableError
[PASS] B3 调用期间零进程创建审计事件（无裸进程执行路径） :: 0 events
[PASS] B4 工作区未被写入/未被改动
PosixNsjailLauncher.start on Windows raised: SandboxUnavailableError: nsjail requires a POSIX host (os.name=nt); refusing to execute
[PASS] B5 生产启动器在非 POSIX 平台自拒（双重保险）
[PASS] B7 launcher_for 无环境变量开关（只有 nsjail 启动器）
```

进程创建调用点**自己用 AST 全量扫描** `src/**`（不靠 grep）：

```
AST spawn call sites in src/** (2):
  src\autograder_b2\static\bash_checker.py:240 -> subprocess.run     # bash -n <workspace 脚本>
  src\autograder_b2\static\bash_checker.py:94  -> subprocess.run     # bash --version 探测
AST spawn **references**（含作为默认工厂传递）(1):
  src\autograder_b2\runner\run_case.py:243 -> subprocess.Popen       # 默认启动器工厂
```

逐点说明输入来源：

| 调用点 | 输入来源 | 是否执行学生代码 |
|---|---|---|
| `runner/run_case.py:243`（`subprocess.Popen`，默认值 → `PosixNsjailLauncher.start`，`:273` 调 `self._popen_factory`） | `argv` 由 `runner/policy.py:616` `build_argv` 生成，形态固定 `[nsjail, --config, <cfg>, --, <interpreter>, <script>, *args]`；interpreter 只能取自策略、script 必须是 `/workspace/...`；`shell=False`（`run_case.py:281`）、`start_new_session=True` | 是——**但只在 nsjail 内**，且非 POSIX / 探测不通过时在 `run_case.py:713-715` 抛异常，进程从未创建 |
| `static/bash_checker.py:94` | 固定 `[candidate, "--version"]`（`shutil.which("bash")` 的结果），只探测解释器可用性 | 否（不读学生文件） |
| `static/bash_checker.py:240` | `[bash_path, "-n", str(script_absolute)]`，`-n` 只做语法解析、不执行；脚本路径来自工作区内的服务端物化文件 | 否（`bash -n` 不执行） |

其余 `subprocess`/`os.system` 命中全是**安全规则文本/正则**（`security/patterns.py`、`static/python_checker.py`、
`static/rules.py` 的字符串模式与消息），不是调用点。

**端到端归类**（真 runner 接真编排器，`integration.adapters.RunnerCaseRunner`）：

```
E2E outcome: state=failed stage=terminal terminal_reason=infrastructure_error score=None report=None
run transition history = [(1,None,'queued',''), (2,'queued','running','静态与安全检查通过，进入沙箱执行。'),
                          (3,'running','failed','沙箱不可用：本次评测未能执行，这不是学生代码的问题。')]
progress frames = [1..6 progress, (7,'failed','沙箱不可用：…')]
evaluation_runs row = [('failed','terminal','infrastructure_error',True,0)]
[PASS] C1 终态是 failed      [PASS] C4 数据库无 evaluation_results 行（不落分数）
[PASS] C2 失败原因是 infrastructure_error（不赖学生）
[PASS] C5 终态帧恰好一个且是 failed
[PASS] C7 编排全程零进程创建审计事件（真 runner 也没裸跑）
```

**标准 ② 判定：PASS**。

> **残留风险（记入第 3 节新发现 #4）**：Linux 验收证明的是 **nsjail 隔离本身**（自带策略）+
> runner 生成的配置能被真 nsjail 接受（I1）。**`runner.run_case()` / `PosixNsjailLauncher.start`
> 的真实 Linux 执行路径（真实启动、真实采集输出、真实归类）没有任何测试跑过**：
> `tests/linux/test_nsjail_acceptance.py:1152-1178` 只 `import render_nsjail_config / build_argv`，
> `tests/test_runner_core.py:3-6` 自述"在 Windows 上运行、不是隔离强度"。这两处**声明**了边界，
> 但没有进 `phase-01` 的 `G-*` 编号清单。

---

## 3. 标准 ③「SSE / 导出 / 不泄漏」→ **PASS**

### 3.1 SSE：真实 HTTP（自己写的标准库 HTTP/1.1 服务器 + 原始套接字客户端）

```powershell
uv run python .pytest-tmp/review_probe/probe_sse.py     # 末行 FAILURES = []
```

不依赖 `fastapi.testclient`：探针用 `asyncio.start_server` 在 `127.0.0.1:0` 起真套接字，自己实现
请求解析与 chunked 响应；客户端用 `socket` 直接读并**逐帧记录到达时刻**。

```
HTTP status line: HTTP/1.1 200 X
response headers: {'content-type': 'text/event-stream; charset=utf-8', 'transfer-encoding': 'chunked', 'cache-control': 'no-store'}
connect 前已落库帧数=2 终态帧=[]          # 连接时终态尚未落库（流式到达的前提）
SSE frames (12): id/event 依次 1..11 = progress, 12 = completed
[PASS] A1 sequence 严格单调递增 :: [1,2,3,4,5,6,7,8,9,10,11,12]
[PASS] A2 sequence 连续无缺号（从 1 开始）
[PASS] A3 id 字段 == sequence
首帧→末帧到达间隔 = 2.047s（runner 每用例 sleep 0.6s）
[PASS] A4 帧是**流式**到达的（末帧比首帧晚 ≥1.0s）
[PASS] B1 终态事件恰好一个 :: ['completed']
[PASS] B2 终态帧是最后一帧
[PASS] B3 终态之后连接关闭且无任何剩余字节 :: clean=True leftover=b''
[PASS] B4 completed 帧带 score/is_pass 且 terminal_reason 为空 :: {"event":"completed","score":6.5,"is_pass":false,"terminal_reason":null}
[PASS] E1 帧字段集 == 契约 EvaluationStreamFrame（无额外字段） :: 多出=[] 缺少=[]
[PASS] E2 非终态帧不得携带 score/is_pass
```

三种终态各自成因可辨（同一条流水线、不同注入）：

```
failed 场景帧:   [(1..6,'progress',None), (7,'failed','infrastructure_error')]
[PASS] C1 沙箱不可用 ⇒ 终态事件 failed 且唯一
[PASS] C2 failed 帧带 terminal_reason=infrastructure_error 且无分数
rejected 场景帧: [(1..5,'progress',None), (6,'rejected','security_blocked')]
[PASS] C3 安全阻断 ⇒ 终态事件 rejected 且唯一（与 failed 可辨）
[PASS] C4 rejected 帧带 terminal_reason=security_blocked 且无分数
failed 运行是否进入 running: True；rejected 运行是否进入 running: False
[PASS] C5 rejected 运行**从未**进入 running（静态/安全阻断发生在沙箱之前）
```

重连语义（`Last-Event-ID`）：

```
从 sequence>7 重连得到 [8, 9, 10, 11, 12]
[PASS] D2 中途重连不重复不遗漏（恰为 mid 之后的全部帧）
末帧后重连：耗时=30.44s 帧数=0 clean_close=True
[PASS] D1 Last-Event-ID=末帧序号 ⇒ 0 帧（不重复）
  [观察] 终态已写过时，重连仍空等 idle_timeout(30s) 才关流   ← 见新发现 #3
```

### 3.2 不泄漏：帧 / 日志 / 事件 payload / 导出 四处独立扫描

注入物：源码标记 `RECHECK_SECRET_STUDENT_SOURCE_9f3a`、隐藏期望输出标记
`RECHECK_HIDDEN_EXPECTED_OUTPUT_7c1b`、凭据 `Bearer sk-recheckPROBE0123456789abcdef`（含裸值）、
绝对路径 `D:\VScodeProjects\AutoGrader\B2\.pytest-tmp\secret-internal-root`。日志用**生产渲染路径**
捕获（`build_logger` + `JsonLogFormatter` → 单行 JSON，共 21 行）。

```
[PASS] F1 SSE 帧不含 源码标记 / 隐藏用例期望输出标记 / 凭据 / 凭据裸值 / 内部绝对路径  （5 项均 0 次）
[PASS] F2 日志流不含 源码标记 / 隐藏期望标记 / 凭据 / 凭据裸值 / 内部绝对路径 / 工作区绝对路径 （6 项均 0 次）
outbox 事件数 = 21；subjects = ['autograder.v1.evaluation.completed', '.failed', '.progress', '.started']
事件 payload 顶层键集合 = ['cases_done','cases_total','environment_digest','evaluation_run_id',
 'failure_category','package_digest','question_version_id','result_digest','runner_policy_digest',
 'score','sequence','stage','submission_id','terminal_reason']
[PASS] F3 事件 payload 不含 上述 6 类标记（均 0 次）
[PASS] F4 学生视图省略全部用例的期望输出（fail-closed）
[PASS] F5 学生报告不含隐藏用例标记
[PASS] F6 教师视图保留逐用例期望输出（对照）
[PASS] F7 裁剪后的学生报告仍通过契约 schema
```

### 3.3 导出：转义 / 确定性 / 不泄漏 / 口径一致（真实 HTTP 导出端点）

```powershell
uv run python .pytest-tmp/review_probe/probe_export.py  # 末行 FAILURES = []
```

自己造的恶意单元格（6 个触发字符 × 7 个字符串列 = 42 个单元格）：

```
CSV 第 1 行数据 = ["'=1+2+cmd|' /C calc'!A0", "'=1+2+cmd|' /C calc'!A0", …]
CSV 未转义单元格数 = 0（6 个触发字符 × 7 个字符串列全部转义）
XLSX 公式单元格数 = 0；未转义单元格数 = 0
[PASS] A1 CSV 带 UTF-8 BOM    [PASS] A2 CSV 用 CRLF（RFC 4180）
[PASS] A3 CSV 全部字符串单元格已转义（'= + - @ TAB CR 开头）
[PASS] A4 转义幂等（再次调用不叠加前缀）
[PASS] A5 XLSX 无任何公式单元格（data_type != 'f'）    [PASS] A6 XLSX 字符串单元格均以 ' 开头
[PASS] B1 CSV 渲染两次逐字节相同      [PASS] B2 XLSX 渲染两次逐字节相同
```

真实端点（`GET /api/v1/grades/export`，教师令牌，真库 + 真评测结果）：

```
GET …/export?course_id=…&format=csv -> 200 media=text/csv; charset=utf-8
  disposition=attachment; filename="grades-<course>.csv" bytes=669
completed 行 score=2.5 max=2.5 state=completed ；未完成行 score='' state=queued
[PASS] C1b 导出媒体类型为 text/csv 且带 attachment 文件名
[PASS] D2 completed 行 score == 报告里的分数 :: 2.5 vs 2.5
[PASS] D3 未完成运行的 score 是**空**而不是 0（未完成 ≠ 0 分） :: ''
[PASS] D5 导出与查询逐字段一致（同口径） :: []
XLSX 导出 -> 200 bytes=5632
教师报告 -> 200；含隐藏期望标记 = True；含源码标记 = True      ← 对照：标记确实进了系统
[PASS] C2 导出字节不含 源码标记 / 隐藏期望输出 / 凭据 / 裸凭据 / 内部绝对路径 / 工作区路径 / 用例 ID（CSV=0 XLSX=0）
[PASS] C3 对照成立：教师报告里确实含隐藏期望输出与源码标记（标记真的进了系统）
[PASS] C5 越权课序导出 → 403     [PASS] C6 非法 format → 422
[PASS] D6 路由发出的 XLSX 字节 == 服务层渲染字节（路由没另造编码） :: 5632 vs 5632; rows=2
[PASS] D4b 学号/姓名列全为空（B2 不自造 B4 数据）
```

**标准 ③ 判定：PASS**。

> **如实标注（已在登记清单内）**：转义防线位于**渲染层**；B2 库里没有可承载触发字符的列
> （`student_number`/`display_name` 恒 `null`，其余文本列是 UUID/枚举且有 CHECK），因此"恶意单元格"
> 只能由我在渲染层注入。这一点在 `docs/phases/active/phase-01-implementation.md:243` 已登记，
> 我核对与代码一致（不是新偏差）。

---

## 4. 组内一致性 / 覆盖性

### 4.1 `cases.md` ↔ `tests/**` 交叉核对

```powershell
uv run python .pytest-tmp/review_probe/probe_cases.py   # 末行 FAILURES = []
```

```
tests/** 中 test_* 函数总数 = 732
cases.md 中标『存在』的表格行数 = 65 ；可机器核对的引用数 = 34（不一致 0）
[PASS] J1 cases.md 中标『存在』且写了测试名的引用全部真实存在 :: []
[PASS] J2 组内 7 类关键断言在测试文件里都有具名用例 :: 7/7
```

抽样（ID ↔ 测试名 ↔ 文件，全部用 AST 核名，不靠 grep）：

| 用例 ID | 参考 | 测试名 | 文件 |
|---|---|---|---|
| B2-GRD-01 | 2-60 | `test_grades_pages_and_both_export_formats_agree_field_by_field` | `tests/test_b2_routes.py` |
| B2-GRD-02 | 2-60 | `test_cursor_pagination_is_complete_and_disjoint` | `tests/test_grades_query_export.py` |
| B2-GRD-05 | 2-60 | `test_unfinished_runs_are_null_score_with_state_and_never_zero` | `tests/test_b2_routes.py` |
| T-LOG-001..004 | 2-99 | `test_log_lines_are_json_with_mandatory_fields` / `…correlation_id_links_every_stage` / `…broker_unavailable_has_log_and_status_and_is_not_swallowed` 等 9 个 | `tests/test_logging_pipeline.py` |
| T-INT-005 | 2-60 | `test_opaque_handles_are_the_parsed_objects_themselves`（读了断言：`view.adapter_handle is view.handle.adapter`） | `tests/test_integration_adapters.py:174` |
| T-INT-007 | 2-50 | `test_runner_adapter_never_degrades_to_a_bare_process`（读了断言：`pytest.raises(SandboxUnavailableError)`） | `tests/test_integration_adapters.py:389` |
| T-INT-008 | 2-50 | `test_prepare_materialises_exact_bytes` + `test_each_case_gets_its_own_workspace` | `tests/test_integration_adapters.py:435/453` |
| T-INT-009 | 2-30 | `test_release_removes_only_inside_the_root` + `test_workspace_root_must_not_overlap_the_cas_root` | `tests/test_integration_adapters.py:473/495` |
| T-INT-011 | 2-70 | `test_security_decider_blocks_a_high_risk_sample` + `…opens_the_gate_for_a_clean_sample` | `tests/test_integration_adapters.py:356/367` |
| 另有 | — | `test_report_passes_main_contract_and_total_check`、`test_replay_same_bindings_same_awards_and_evidence`、`test_event_stream_is_monotonic_and_ends_at_the_terminal_frame`、`test_formula_injection_is_escaped_in_csv_and_xlsx`、`test_sandbox_unavailable_fails_with_infrastructure_error`、`test_frames_never_carry_source_and_match_contract_shape`、`test_outbox_events_only_carry_identifiers_counts_and_digests` | 7 类精确命中 7/7 |

**未见 ID ↔ 测试名 不一致**。唯一可指摘的是**文档完备性**：65 行"存在"里有 35 行只写
`**存在**`（无测试名），机器无法核对（`T-INT-001..011`、`T-PERSIST-003/004`、`T-PLUGIN-001` 等）；
我按内容手工映射了其中 5 条（上表）并与测试断言逐条对齐，结论一致。→ 低严重度文档项（新发现 #6）。

### 4.2 五份组内关键不变量是否互相矛盾

```powershell
uv run python .pytest-tmp/review_probe/probe_invariants.py   # FAILURES = ['K1a:未强制配对']
uv run python .pytest-tmp/review_probe/probe_events_order.py # 见下
uv run python .pytest-tmp/review_probe/probe_publish_order.py
```

**(a) 「静态阻断 ⇒ rejected」与「技术失败 ⇒ failed」是否被混用** ——
**实现未混用**（证据：`rejected` 从未进入 `running`；`failed` 进入 `running`；
`orchestrator.py:573` 静态阻断 → `_Blocked(static_blocked)`；`:577` 静态"未检查" → `InfrastructureError`；
`:621`/`:623` 安全侧同理）。**但配对本身没有任何强制层**：

```
K1a 写入 rejected+infrastructure_error 结果: raised=None db_row=[('rejected','infrastructure_error')]
[FAIL] K1a 跨域配对被强制拒绝（期望：应被拒）
[PASS] K1b completed 带失败原因被拒（TerminalReasonMismatch）
[PASS] K2 第二个终态帧被拒（TerminalFrameExists）      # 终态帧唯一性**有**强制
```

根因：`store._require_terminal_reason`（`store.py:107-130`）只校验"终态必须有 `terminal_reason`
且取值在四种枚举内"，不校验 state↔reason 配对；`progress._validate_frame`（`progress.py:196-198`）同样。
迁移 0007 的两条 CHECK（`ck_evaluation_runs_terminal_consistency` / `ck_progress_events_terminal_frames`）
也只约束"completed 必须 NULL、其余终态必须非空"。→ **新发现 #2**。

**(b) `result_digest` 构成是否排除 `generated_at`/耗时/运行标识** —— 已排除，见 §1.2（PASS）。

**(c) 日志/事件/导出三处对同一标识的口径是否一致** —— 一致：

```
run_id = b8e53a39-2a4d-4b34-834d-eee7ded3249c
  DB / 报告 JSON / SSE 帧取值集合 / 事件 payload：全部 {b8e53a39-…}
  日志出现次数 = 9 ；导出出现次数 = 1（评测运行ID 列）
[PASS] K3 数据库 / 报告 / SSE / 事件 / 日志 / 导出 六处标识字形一致
[PASS] K3b 标识为标准小写连字符 UUID 字形
```

**(d) 事件顺序不变量** —— **声称与实测不一致（新发现 #1，见下）**。
另：`rejected` 无终态领域事件这一点是**成立**的（`probe_events_order`：rejected 运行事件集合
= `started + progress×3`，无 `completed`/`failed`；`completed`/`failed` 各自恰一个且在**最后**）：

```
[PASS] L2[completed] 终态事件恰好一个且是最后一个 :: count=1 last=…evaluation.completed
[PASS] L2[failed]   终态事件恰好一个且是最后一个 :: count=1 last=…evaluation.failed
[PASS] L2[rejected] 无任何终态领域事件（契约无 evaluation.rejected）
[PASS] L3[*] progress 事件的 sequence 单调递增
[PASS] L4[*] 事件 payload 带各自契约要求的键（started→package_digest 等）
```

---

## 5. 新缺陷清单（不在登记清单内的）

### #1【中】事件发布顺序不变量只在约一半的运行上成立

**声称**：`dispatcher.py:9-13`「**顺序**：严格按 `enqueued_at`（同刻用 `event_id`）取行、按同序串行
投递，绝不重排……发布顺序（`started → progress → completed|failed`）由写入端按 `enqueued_at` 保证」；
`orchestrator.py:22-26`「outbox 的 `started` 在拿到 package 摘要之后入队，因此事件顺序天然满足
`started -> progress -> completed|failed`」。

**事实**（可复算）：

```powershell
uv run python .pytest-tmp/review_probe/probe_publish_order.py   # FAILURES = []
uv run python .pytest-tmp/review_probe/probe_order_risk.py      # FAILURES = []
uv run python .pytest-tmp/review_probe/probe_order_flip.py      # FAILURES = []
uv run python .pytest-tmp/review_probe/probe_events_order.py    # 跑 4 次看翻转
```

1. `orchestrator._fetch_package` 在**同一事务、同一 `occurred_at`** 里入队两条事件
   （`orchestrator.py:471-500`）：`started`（dedup `{run}:started`）与 `progress`（dedup `{run}:progress:3`）。
   实测库中两行 `enqueued_at` **逐微秒相同**：

```
started   dedup=…:started    occurred=2036-01-01 00:00:00.500000 enqueued=2036-01-01 00:00:00.500000 event_id=3fd7731e…
progress  dedup=…:progress:3 occurred=2036-01-01 00:00:00.500000 enqueued=2036-01-01 00:00:00.500000 event_id=677a1854…
```

2. `NatsOutboxDispatcher.pending_rows` 按 `(enqueued_at, event_id)` 排序（`dispatcher.py:175`），
   于是同刻这一对的顺序**完全由 `event_id` 决定**；而 `event_id = uuid5(NAMESPACE_URL, f"b2-evaluation:{dedup_key}")`
   （`orchestrator.py:1178`），dedup key 里含**每次运行都不同的 uuid4**。
3. 大样本统计（生产同一派生规则，20000 个随机运行标识）：

```
样本 20000：`progress:3` 排在 `started` **之前**的比例 = 10050/20000 = 50.2%
```

4. 端到端复现（选一个已知翻转的运行标识，用生产 `build_event`+`enqueue` 入队，用生产
   `NatsOutboxDispatcher` + 假 bus 派发；不碰真实 NATS）：

```
P1 前提成立：两条事件的 enqueued_at 逐微秒相同 :: {'2037-06-01 12:00:00.500000'}
**实际投递顺序** = ['progress', 'started']
```

5. 真跑 4 次（每次 3 个场景）× `(enqueued_at, event_id)` 取行顺序，观察到的首事件：

```
run1: completed=started   failed=started   rejected=started
run2: completed=progress  failed=started   rejected=started
run3: completed=started   failed=started   rejected=progress
run4: completed=progress  failed=progress  rejected=progress      # 12 次观测里 5 次 started-first、7 次 progress-first
```

**影响**：消费者会**随机地**先收到 `progress` 再收到 `started`；"事件顺序"既不可复算也不可依赖
（同一份代码、同一逻辑，换个运行标识就变）。对按 `started` 建运行上下文的订阅方是实打实的错误源。
**登记状态**：`G-EVT-6` 只登记了"无乱序到达专项用例"，没有登记这个**写入端排序规则本身不保证顺序**。

**改法（最小）**：给 `b2.outbox_events` 加一个单调插入序号（`bigserial`，随迁移引入），
`pending_rows` 改为 `ORDER BY enqueue_seq`；或让 `started` 与 `progress:3` 的 `enqueued_at`
严格错开（写入端为同一事务内的多次入队递增一个微秒），使 `enqueued_at` 真的构成全序。

### #2【中】终态状态与 `terminal_reason` 的配对没有任何强制层

```
K1a 写入 rejected+infrastructure_error 结果: raised=None db_row=[('rejected','infrastructure_error')]
```

- `store._require_terminal_reason`（`store.py:107-130`）与 `progress._validate_frame`
  （`progress.py:196-198`）都只校验"取值 ∈ `TERMINAL_REASONS`"；
- 迁移 `20260915_0007` 的两条 CHECK 只约束"`completed` 必须 NULL、其余终态必须非空"；
- 因此 `rejected + infrastructure_error`、`failed + static_blocked` 这类**语义混用**可以被写进库
  （业务码今天不会这么写——但这是"碰巧对"，不是"不可能错"）。

**改法**：在 `_require_terminal_reason` 加配对规则（`rejected ⇒ {static_blocked, security_blocked}`、
`failed ⇒ {infrastructure_error, internal_error}`），并同步一条 CHECK；或按 CHG 流程请 MAIN 裁定
是否把配对写进契约（当前契约 `EvaluationStreamFrame.terminal_reason` 只给了枚举，配对是文字描述）。

### #3【低】终态已写过时，`Last-Event-ID` 重连仍空等 `idle_timeout`（30 s）才关流

```
末帧后重连：耗时=30.44s 帧数=0 clean_close=True
```

`progress.frame_stream`（`progress.py:377-420`）只有当它**产出**终态帧时才 `return`；
以末帧序号重连时 `read_frames` 返回空，于是必须等满 `idle_timeout=30.0`。
客户端无法从"连接是否立即结束"判断运行已终结（只能靠 30 s 超时）。
**改法**：`frame_stream` 在 `after > 0` 且库中已有终态帧时立即结束（先查 `terminal_frame(...)`）。

### #4【低】`run_case` 的真实 Linux 执行路径无任何验收证据（边界已声明，但未进 `G-*` 登记）

- `tests/linux/test_nsjail_acceptance.py:8-12` 自述自带策略、不导入 `runner/**`；唯一的集成项
  `test_i1_...`（`:1152-1178`）只 `import render_nsjail_config / build_argv` 并把生成的配置交给真 nsjail；
- `tests/test_runner_core.py:3-6` 自述"在 Windows 上运行、**没有**在真实 Linux + nsjail + cgroup v2 上验证"。
- 结论：`PosixNsjailLauncher.start` → 输出采集 → `classify_case_outcome` 这条**生产执行路径**没有
  任何 Linux 级证据（当前只有注入式单测 + 配置被真 nsjail 接受）。
- **改法**：在 `tests/linux/` 下加一个"真 `run_case` 跑真 nsjail"的集成项（可用同一容器），
  或在 `phase-01` 的 `G-*` 里把这条登记为显式缺口。

### #5【低·文档】`phase-01:74`「矩阵 13/13」与逐项判定不一致

`docs/phases/active/phase-01-implementation.md:74` 写「Linux/nsjail 验收（容器内）… 矩阵 13/13；
`5a2` … 记 `xfail`」，同一行内自相矛盾（13 项里 5a2 是"无法执行"）。实测：矩阵 13 行 =
**12 通过 + 1 无法执行 + 0 失败**。`tests/linux/README.md` §1 的写法是准确的（10 必过项 + 1 限制项 + I1）。
**改法**：把 74 行改为"矩阵 12 通过 / 1 无法执行（5a2，环境限制）"。

### #6【低·文档】两处陈述与现状不符

1. `src/autograder_b2/runner/policy.py:17-18` 仍写「**未在真实 Linux + nsjail + cgroup v2 上验证**……
   seccomp（kafel）语句与宿主 cgroup v2 语义须由 Linux 验收工作项实测校准」，而同一文件
   `:666-673`、`:699-708` 明确写着这些形态是"依据 Linux 验收实测（真 nsjail 3.4 + kafel）修正"的，
   且 `test_i1` 已在真 nsjail 上跑通 runner 生成的配置。模块 docstring 已陈旧。
2. `docs/testing/cases.md` 65 行"存在"里有 35 行未写测试名（见 §4.1），ID ↔ 测试名 无法机器核对。

---

## 6. 与登记清单的一致性核对（抽查）

| 登记项 | 我的核对 | 结论 |
|---|---|---|
| `G-EVT-1..8` | 抽查事件 payload 键集（实测 14 个键，与 `events/payload.py:151-186` 一致）、rejected 无终态事件、事件大小/驱动缺口属 MAIN 侧 | 与登记一致 |
| `G-GRD-1`（契约侧缺口） | 实测导出端点 403/422 行为与 `grades_routes.py:14-22` 的自述一致 | 一致 |
| `G-GRD-2`（导出全量入内存） | `grades/export.py:260-268` 确实 `list(...)` 全量；属资源风险且已登记 | 一致 |
| `G-GRD-5`（XLSX 把单元格内 CR 规范化） | 我的转义探针只断言"以 `'` 开头/无公式元素"，未声称 CR 原样保留 | 一致（未证伪） |
| `G-LOG-2` | 条目跨 3 行已读完：grades 已补、`evaluation_routes`/`intake/api` 尚未补；实测 `evaluation_routes._not_available` 确实无日志 | **一致**（我最初只看首行时误判为矛盾，读全后纠正） |
| `G-LOG-1`（DB 整体不可达未覆盖） | 未复跑（需长超时探活），登记已声明 | 未复核 |
| 导出转义可达性边界 | 与 `phase-01:243` 的登记一致 | 一致 |
| `5a2` xfail | 与 `phase-01:74`、`tests/linux/README.md` §1 一致 | 一致 |

未复核项（如实列出）：`G-EVT-5/7/8`（消费端 lag/驱动/事件大小上限于本轮组级标准无关）、
`G-LOG-1`（需把运行时指向无监听端口、探活超时长）、`verify/linux-nsjail/README.md` 的失败尝试记录。

---

## 7. 一句话总评 + 必办

**总评**：三条组级标准**全部 PASS**（重放一致且有反证、Linux 真 nsjail 隔离与降级拒绝都有实测数值、
SSE/导出/不泄漏四处零命中），实现里"不降级、不四舍五入、不把未检查当通过"的硬规则在真实路径上都成立；
但**事件发布顺序这一族不变量只在一半的运行上成立**，属于必须修的排序缺陷。

**一行级必办**：给 `b2.outbox_events` 增加单调插入序号并让 `dispatcher.pending_rows` 按它排序
（或在写入端为同事务内的多次入队严格错开 `enqueued_at`），使文档声称的
`started → progress → completed|failed` 成为真正的不变量；同时在 `store._require_terminal_reason`
补上 `state ↔ terminal_reason` 配对校验。
