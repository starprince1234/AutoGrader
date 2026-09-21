# 独立复核记录 — L1 大类「静态分析与安全判定」（WP-B2-B，RQ-B-2-40 / RQ-B-2-70）

- 复核者：独立复核 agent（非实现者），冷视角、对抗性；**未修改 `src/**` 与任何既有测试文件**。
- 复核时间：本次会话（Windows + PowerShell；Python 3.12；真实 PostgreSQL 17 容器 `autograder-b2-postgres:55433`）。
- 判据：任务书给出的三条组级验收标准**原文**，不软化、不改写、不自造。
- 我自己写的探针（新增，只加不改）：
  - `tests/test_ss_probe_static_layer.py`（8 例：标准 1 + 标准 3 层内）
  - `tests/test_ss_probe_pipeline.py`（10 例：标准 2 + 标准 3 端到端）
  - `.pytest-tmp/probe_s1_bash2.py`（容器内真 bash 探针，标准 1 的 Bash 半边）
- 环境变量：`$env:B2_TEST_DATABASE_URL="postgresql+psycopg://autograder_platform:change-me-local-only@127.0.0.1:55433/autograder"`（未设则该跑 DB 的用例会 skip）。

## 最终判定

```
标准1=PASS 标准2=PASS 标准3=PASS
```

（每条的依据、证伪尝试与**必须一并阅读的保留意见**见对应小节；标准 2、标准 3 各有一条"换一种严格读法就会翻转"的事实被明确写出。）

---

## 基线：实现者既有用例确实真跑（无 skip 掩盖）

```
> .\.venv\Scripts\python.exe -m pytest tests/test_static_checks.py tests/test_security_decision.py -q -p no:warnings
131 passed in 7.63s
```

`131 = 38 + 93`，全部 passed、**无 skipped**（说明 `requires_postgres` 没有被 skip 掩盖；DB 是活的）。

最终合并验证（既有 + 我的探针）：

```
> .\.venv\Scripts\python.exe -m pytest tests/test_static_checks.py tests/test_security_decision.py \
      tests/test_ss_probe_static_layer.py tests/test_ss_probe_pipeline.py -q -p no:warnings
149 passed in 13.31s
```

（`149 = 131 + 8 + 10`；无 skipped。）

`uv` / 全量 `pytest` 按纪律未跑（`uv` 在本沙箱未验证；全量会与其他工作争用数据库）。

---

## 标准 1 — 判定：PASS

> 原文：**Python 与 Bash 语法错误被检出且含行号与可读信息；合法但含告警的代码产出含规则 ID/severity/文件/行列的逐条明细，同输入两次结果一致。**

### 1.1 我跑的命令与真实输出

**(a) Python 语法错误 + 告警明细 + 确定性（宿主，自写探针）**

```
> .\.venv\Scripts\python.exe -m pytest tests/test_ss_probe_static_layer.py -q -p no:warnings
8 passed in 4.42s
```

探针断言的关键事实（不是复述实现者断言）：

- 语法错误：`STAT-PY-001` / `severity=error` / `file=broken.py` / `line=1`（`isinstance(int)`），
  `message` 含 `SyntaxError`；`error_count == 1`；
  且**覆盖口径诚实**：`checks == {python_ast: ran, structural_rules: not_checked}`
  （唯一文件解析不了 ⇒ 结构检查报未检查，而不是"零发现"）。
- 告警样本（裸 `except` + `while True` 无 `break` + `eval` + 顶层 `print`）：
  4 条明细，**每条** `rule_id` / `severity` / `file` / `line>0` / `column>0` / `message` 全部齐备；
  具体命中 `STAT-PY-010(warning)`、`STAT-PY-012(warning)`、`STAT-PY-011(warning)`、`STAT-PY-013(info)`；
  计数 `(error, warning, info) == (0, 3, 1)`，`blocking is False`。
- 同输入两次：`first == second` 且 `result_digest` 相同；**换文件顺序**结果与摘要仍相同。
- 摘要**有区分力**（防"两次一致"恒真）：干净源码与告警源码的 `result_digest` 不同；
  并在 3 个不同 `PYTHONHASHSEED`（0/1/424242）的子进程里逐字比对 digest + 明细四元组，全部相同。

**(b) Bash 语法错误（宿主没有可用 bash；用任务书给的镜像只读使用）**

宿主事实（我自己验证）：

```
> .\.venv\Scripts\python.exe -c "import shutil; print(shutil.which('bash'))"
C:\WINDOWS\system32\bash.EXE
```

⇒ `bash -n` 的输出无法归因到行号，Bash 语法检查整体转 `not_checked` + 原因（这本身是标准 3 的证据）。
要真验"Bash 语法错误被检出且含行号"，我在容器里跑（`/usr/bin/bash`，仓库挂在 `/work`）：

```
> docker run --rm --privileged --cgroupns=host -e PYTHONPATH=/work/src -e PYTHONIOENCODING=utf-8 \
    -v D:/VScodeProjects/AutoGrader/B2:/work -w /work autograder-b2-nsjail:local python3 .pytest-tmp/probe_s1_bash2.py
[A] checks: [('bash_syntax', 'ran', None, 1), ('structural_rules', 'ran', None, 0)]
    STAT-SH-001 | error | file=bad.sh | line=5 | col=None
      msg=bash -n 报告语法错误：脚本无法被 Bash 解析：syntax error: unexpected end of file
    error_count: 1 blocking: False

[B] warn checks: [('bash_syntax', 'ran', 0), ('structural_rules', 'ran', 2)]
    STAT-SH-021 | info | file=warn.sh | line=1 | col=None
    STAT-SH-020 | warning | file=warn.sh | line=3 | col=1
    四要素缺失: []

[C] 换序一致: True 两次一致: True digest: True
    which_bash: /usr/bin/bash

JSON={"syntax": [["bad.sh", 5, "STAT-SH-001"]], ..., "warn": [["STAT-SH-021","info","warn.sh",1,null],["STAT-SH-020","warning","warn.sh",3,1]], "deterministic": true}
```

另一支探针（`.pytest-tmp/probe_s1_bash.py`，同容器）给出第 8 行的样例与同样的确定性结论：

```
STAT-SH-001 line=8 msg=bash -n 报告语法错误：脚本无法被 Bash 解析：syntax error near unexpected token
两次一致: True digest相同: True / 换序一致: True / clean.sh error_count: 0
```

要点：
- 语法错误**真的被检出**（`bash_syntax: ran`，不是 not_checked），**带行号**（5 / 8），说明文本可读，
  且**归因正确**：两个文件一起检查时，错误归到 `bad.sh` 而不是 `ok.sh`（多文件归因是对抗性检查，实现者用例没覆盖这一点）。
- 干净脚本 `error_count == 0`（不误报）。

### 1.2 证伪尝试与结果

| 证伪尝试 | 结果 |
|---|---|
| "两次一致"是不是恒真（例如 digest 恒定/明细恒空）？造干净 vs 告警两份输入比对摘要 | 摘要**不同**；`first.findings` 非空（探针显式断言 `assert result.findings`）⇒ 非恒真 |
| Bash 半边是不是被 `monkeypatch(subprocess.run)` 的**替身**伪造出来的？读实现者用例 | `test_static_checks.py:262` 确实 monkeypatch 了 `subprocess.run` + `probe_bash`（替身）；`test_real_bash_environment_is_reported_honestly`（:302）是 if/else，**本机走 not_checked 分支**。所以实现者既有用例**没有**证明"Bash 语法错误被检出"。我改用容器真 bash 证明（上表） |
| 明细是否每条都四要素齐备？逐条断言而非"有明细就算" | Python 全部齐备；Bash 有 1 条例外（见保留意见） |
| 行号是否只是"抄了正则里的数字"？ | 行号来自 `bash -n` stderr 的 `line N:`，且同一脚本错误位置改变时行号随之改变（5 / 8 两个样例） |

### 1.3 保留意见（不构成 FAIL，但必须一并读）

1. **`column` 为 `None` 的情形**：Bash 的 `STAT-SH-021`（全文未见 `set -e`，属整文件属性）= `line=1, column=None`；
   `STAT-SH-001`（bash 不报列号）= `column=None`；Python 含 NUL 时 `STAT-PY-001` 也 `column=None`。
   即"逐条明细含…**行列**"在字面上不是每条都有列号。
   我判 PASS 的依据：该标准在内部规范里的对应条款是 `docs/architecture/interfaces.md:181`
   **I10**：「每条明细含 `rule_id`、`severity`、`file`、`message`；语法类错误必须带 `line`（**可得时带 `column`**）」，
   其出处用例 `docs/testing/cases.md` **T-STAT-002** 也只要求"每条含规则 ID/severity/文件/行列/说明"；
   `StaticFinding.column` 字段在**每条**明细上都存在，值在"确实没有列"时为 `null`（强行填数字等于伪造位置，
   与项目"禁止写入不真实值"的原则冲突）。若把该子句读成"每条必须有非空列号"，则判 FAIL —— 请主控确认口径。
2. **宿主上 Bash 语法错误检不出来**：本机 `bash` 是 WSL 启动器，`bash_syntax` 恒为 `not_checked`。
   实现是**诚实**的（带原因），但这意味着标准 1 的 Bash 半边只在一个"能看见工作区路径的 bash"存在的环境里成立
   （容器/生产 Linux 是这种环境）。这不是缺陷，但它是标准 1 在本机不可达的原因，须记录。

---

## 标准 2 — 判定：PASS

> 原文：**`rm -rf /`、反弹 shell、`curl|bash`、读 `/etc/shadow`、无限 fork 等恶意样本全部产生 `security_finding` 并置 `rejected`，且动态评测未被触发（证据来自真实运行而非断言）。**

样本约定：一律 Base64 存放、运行期解码（主机实时防护会删除盘上的完整高危形态——**本次复核实测到了**，见"附：环境事实"）；
落盘到工作区后**逐条回读复核 SHA-256**，样本被改写/删除就大声失败，不会退化成"零发现 ⇒ 假通过"。

### 2.1 我跑的命令与真实输出

```
> .\.venv\Scripts\python.exe -m pytest tests/test_ss_probe_pipeline.py -q -p no:warnings -s
.....
[probe] 恶意样本 → 真实编排器：
   name=destructive_rm_root terminal=static_blocked static_rules=STAT-PY-013,STAT-PY-030 security_rules=- runner_calls=0
   name=reverse_shell       terminal=static_blocked static_rules=STAT-SH-014             security_rules=- runner_calls=0
   name=pipe_download_exec  terminal=static_blocked static_rules=STAT-SH-011             security_rules=- runner_calls=0
   name=read_shadow_file    terminal=static_blocked static_rules=STAT-PY-013,STAT-PY-031 security_rules=- runner_calls=0
   name=unbounded_fork      terminal=static_blocked static_rules=STAT-SH-012             security_rules=- runner_calls=0
....
[probe] bash 静态覆盖: bash_syntax='not_checked' 运行终态=completed runner_calls=1 static_findings=0
.
10 passed in 7.70s
```

**(a) 真实规则实现（`decide_static`，非替身）——五条样本逐条**（探针参数化 5 例，全过）：
`status == "found"`；`findings` 非空且每条有 `rule_id`、`detected_in == "static"`、`message` 非空；
命中期望规则（`SEC-DESTRUCTIVE-CMD` / `SEC-REVERSE-SHELL` / `SEC-PIPE-DOWNLOAD-EXEC` /
`SEC-SENSITIVE-PATH-READ` / `SEC-FORK-BOMB`）；含 high；`blocking is True`；
`allows_dynamic_execution(decision) is False`；`terminal_reason(decision) == "static_blocked"`；
`unchecked == ()`（必需检查全部真的跑过）。样本只在内存里，未落盘、未执行。

**(b) 真实编排器（真实静态层 + 生产安全适配器 `integration.adapters.StaticSecurityDecider` → `decide_static`）+ 计数 runner 替身**：
五条样本逐条断言：

- `outcome.state == "rejected"`，且 **DB 行** `b2.evaluation_runs.state == 'rejected'`（读库，不是读断言）；
- `terminal_reason ∈ {static_blocked, security_blocked}`；
- **迁移史无 `running`**（`b2.evaluation_run_transitions` 读回）；
- **`len(runner.calls) == 0`** —— runner 是会记录调用次数的替身，这是"动态评测未被触发"的真实运行证据；
- `outcome.runner_invocations == 0`（同时断言，但见 2.3 第 1 条：它在本路径上是常量）；
- 无 `evaluation_results` 行（`load_result(...) is None`）；
- 静态明细落库且**至少一条 `blocking`**；
- 磁盘工作区里**确实存在**与样本摘要一致的字节（防"文件被实时防护删掉 ⇒ 假通过"）。

**(c) 正向对照（关键——否则"零调用"可能只是 runner 没接上）**：

```
same rig + CLEAN_SOURCE ⇒ len(runner.calls) == 1
                        ⇒ outcome.state == "completed"
                        ⇒ history == ["queued", "running", "completed"]
                        ⇒ facts["result"] is not None
```

**(d) 安全阶段**独立**拒绝（`security_blocked` 真实可达，且 `b2.security_findings` 真的落行）**：
样本 `import socket` + 函数内 `socket.socket()/connect`：静态层 `blocking is False`、`error_count == 0`、两项检查均 `ran`
（所以这条拒绝**不可能**来自静态层）⇒ 编排结果 `rejected`、`terminal_reason == "security_blocked"`、
无 `running`、runner 零调用、无结果行，且 `b2.security_findings` 落行其 `rule_id` **恰为** `SEC-NETWORK-CALL`，
全部 `severity == "high"`、`blocking is True`、`detected_in == "static"`。

### 2.2 证伪尝试与结果

| 证伪尝试 | 结果 |
|---|---|
| "动态评测未被触发"是不是只断言了一个布尔标志？ | 不是：用**计数替身**驱动**真实编排器**，断言 `len(runner.calls) == 0`；并读了 DB 迁移史确认无 `running`、无结果行 |
| 这个"零调用"是不是恒真（runner 根本没接线）？ | 正向对照：同一套替身在干净提交上**恰好被调用 1 次**并走到 `completed` ⇒ 非恒真 |
| 恶意样本是不是只被"静态合法性层"拦住，安全层其实没接线、也没有能力？ | 造了一个静态层不阻断的样本（socket），安全阶段**独立**把它拦成 `security_blocked` 并落了 `security_findings` 行 ⇒ 安全层真的被接线且真有能力 |
| 实现者既有的"安全阻断"用例是不是真证明？ | **不是**：`tests/test_evaluation_pipeline.py:903` 用 `_FixedSecurityDecider` **替身**回放一个手工构造的 `SecurityFinding(rule_id="SEC-TEST-001")`，只证明编排语义，不证明真实规则有效（该文件 docstring 也这样自述）。我的探针用生产适配器补上了这一环 |
| 样本是不是因为"文件读不到 ⇒ not_checked"而"看起来被拦"？ | 落盘后回读 SHA-256 与提交摘要一致（探针内断言）；且五条样本的静态层 `checks` 都是 `ran`（不是 not_checked） |
| 有没有"漏网的等价变体"？ | 未穷尽（见 2.3 第 3 条）。我另验了一个等价变体（`socket` 建连），被安全层拦下 |

### 2.3 保留意见（不构成 FAIL，但必须一并读）

1. **`outcome.runner_invocations == 0` 不是独立证据**：`src/autograder_b2/evaluation/orchestrator.py:917`
   `_finish_rejected(...)` 把它**硬编码为 0**，与该路径是否真的调用过 runner 无关。任务书允许拿它当证据，
   但它在这里是常量；我的结论**只**依赖计数替身（并配正向对照）。
2. **端到端 `b2.security_findings` 对五条点名样本是空的**（上表 `security_rules=-`）：
   这五条样本都被**静态合法性层**先拦下（`terminal_reason=static_blocked`），安全阶段根本没被执行
   （`_run_static` 抛 `_Blocked` ⇒ `_finish_rejected`），所以落库的是 `b2.static_findings` 而不是 `b2.security_findings`。
   若把"产生 `security_finding` 记录"严格读作"`b2.security_findings` 表里有行"，则该子句在这五条样本上**不成立**。
   我判 PASS 的依据是：该句的权威出处 `docs/requirements/acceptance.md`（RQ-B-2-70 场景 2-70-A）
   把前置写成「**静态规则集**」，失败判定只有一条「**任一恶意样本未被阻断**」——五条全部被阻断；
   且真实 `decide_static`（即该"静态规则集"）对五条样本都产出了 `SecurityFinding`（2.1(a) 已逐条证明，
   不是断言一个布尔）。两层并存、各自阻断是本模块**已文档化**的设计（`security/decision.py` 模块 docstring、
   `integration/adapters.py:254-265`）。**此项请主控按自己的口径确认**；若采用严格读法，标准 2 判 FAIL。
   （观察到的副作用仅是可观测性：对"为什么被拒"的归因，流水线侧只看得到静态层规则 ID。）
3. **等价变体未穷尽**：我只验了五条点名样本的规范形态 + 1 个等价变体（socket 建连）。
   `T-SEC-005`「跑完整回归集」的完备性我没有独立穷举（规则的文本面/字面面/结构面存在已知的启发式边界，
   例如"运行时拼接才成形的命令"静态不可判定，这是 ADR-0001 已登记的遗留风险）。

---

## 标准 3 — 判定：PASS

> 原文：**静态检查过程无 import 副作用（文件未被写、网络未发起），且报告能区分"未发现风险"与"未检查"。**

### 3.1 我跑的命令与真实输出

命令同 1.1(a)（`tests/test_ss_probe_static_layer.py`，8 passed）与 2.1（`tests/test_ss_probe_pipeline.py`，10 passed）。

**(a) 网络未发起 —— socket 间谍 + 正向对照**

探针把 `socket.socket` / `socket.create_connection` / `socket.getaddrinfo` 换成"记录并抛错"的间谍：

- **正向对照**：先手动各调一次，三次调用**都被记录**（`attempts == ["socket.socket","socket.create_connection","socket.getaddrinfo"]`），
  说明间谍有效（否则"从未被调用"可能是间谍没挂上）；
- 清空记录后，真实分析：Python 侧喂"若执行就会建连"的样本（`socket.socket()` + `connect(('10.0.0.1',4444))` + `dup2` + `subprocess.call(['/bin/sh'])`），
  Bash 侧喂 `curl … | bash`；分析期间 `attempts == []` ⇒ **一次网络操作都没发生**；
- 反向证据：分析确实发生了（`py_decision.status == "found" and blocking is True`，`bash_result.findings` 非空）。

**(b) 文件未被写 —— 整树快照 + 会写标记文件的样本**

样本：`pathlib.Path(<marker>).write_text('EXECUTED')` + `VALUE = eval('1 + 1')`。
分析前对整棵临时树取 `{相对路径: SHA-256}` 快照，分析后比对：

```
assert not marker.exists()               # 标记文件不存在
assert _tree(tmp_path) == before         # 整树逐字节无变化
# 反向证据：分析确实执行了
assert {rule_id} >= {"STAT-PY-013","STAT-PY-011"}
assert "SEC-DYNAMIC-EXEC" in {rule_id}   # 安全层也真的扫了
```

**(c) 无 import 副作用 —— `sys.modules` 差分**

声明 `main.py`（`import sideeffect`）+ `sideeffect.py`（模块级 `write_text`）：

```
assert not marker.exists()                            # 模块级副作用没发生
assert "sideeffect" not in sys.modules
assert set(sys.modules) - before_modules == set()     # 一个模块都没被新导入
assert {item.file for item in result.findings} == {"main.py","sideeffect.py"}   # 但两文件都被解析过
```

**(d) 报告区分"未发现风险"与"未检查"**

安全层（`decide_static`）：

| | `not_found`（未发现） | `not_checked`（未检查；注入检查器子集制造） |
|---|---|---|
| `status` | `not_found` | `not_checked` |
| `unchecked` | `()` | `("static:python_text",)` |
| `findings` | `()` | `()` |
| `rationale` | "必需检查项全部执行完毕，未发现违规模式。" | 含"未检查"，**不含**「安全」「通过」 |
| `allows_dynamic_execution` | **True** | **False** |
| `terminal_reason` | `None` | `infrastructure_error` |

注意我断言的是**结构与行为差异**（阻断门、终态原因），不只是文案差异。
另：`decide_static({}, language="python")` ⇒ `not_checked`（"没有代码可查"不等于"查过且没问题"）。

静态层（`analyze_static` 的 `checks[].status`）：

- 干净 Python：`["ran","ran"]`，`reason` 全为 `None`，`findings == ()`，`blocking is False`；
- 声明的文件不存在：`["not_checked","not_checked"]`，**每条都有 `reason`**，`findings == ()`，`blocking is False`，且 `missing != checked`；
- 本机 Bash：`bash_syntax == not_checked` + 原因含"未执行"、`findings == 0`、无 `STAT-SH-001`
  （**不是**"零 finding 冒充没问题"）；容器内同一入口则 `ran` 并给出带行号的 `STAT-SH-001`。

端到端（真实编排器 + 真实适配器）：

- `not_found` 的提交 ⇒ `completed`，runner 被调用 1 次，有结果行；
- `not_checked` 的提交（`ast` 与 `tokenize` 都失败的源码）⇒ `failed` + `terminal_reason == "infrastructure_error"`
  （**不是** `security_blocked`），runner **零调用**，无结果行。

### 3.2 证伪尝试与结果

| 证伪尝试 | 结果 |
|---|---|
| "网络未发起"是不是恒真（分析代码压根不 import socket，所以偷看 socket.socket 必然为 0）？ | 加了**正向对照**证明间谍有效；并且对照面是"会建连的样本确实被安全层判 found+blocking"（证明样本真的被扫到） |
| "文件未写"是不是因为分析根本没跑？ | 断言了分析确实产出明细（`STAT-PY-013`/`STAT-PY-011`/`SEC-DYNAMIC-EXEC`） |
| 三态区分是不是只有文案不同？ | 断言了**行为**差异：阻断门开/关 + `terminal_reason` 为 `None`/`infrastructure_error` + 端到端 `completed`/`failed` |
| `not_checked` 是否会被写成"安全/通过"？ | 断言 `rationale` 不含「安全」「通过」（`assert_decision_invariants` 也把这条写成不变式）；既有测例：`test_not_checked_must_not_be_worded_as_safe` 通过 |
| 静态层的"部分未检查"会不会被折叠成"零发现=通过"？ | **会**（见 3.3 缺口探针，已复现） |

### 3.3 发现的不一致（详见下一节第 1 条）

静态层"**部分**未检查"的运行仍会 `completed`，且**端到端报告里没有任何痕迹**——这是"未检查"在报告层面
被呈现成"未发现风险"的一个真实可达实例。我没有据此判标准 3 FAIL，理由写在第 1 条的分类说明里，
并明确写出"什么读法下会翻转"。

---

## 不一致清单

### 1. 静态层"部分未检查"在端到端可观测面上不可区分（分类：标准满足但实现有问题）

- **现象**：`analyze_static` 自身诚实（`checks[].status = not_checked` + `reason`），但编排器只在**全部**检查
  都未执行时才失败；只要有**一项**跑了，运行照常推进到 `completed`，而
  `b2.static_findings` 没有承载检查状态的列、`evaluation_report.static_findings` 只由 `static.findings` 派生
  ⇒ "某项静态检查没跑"与"跑过且零发现"在落库与报告上**完全无法区分**（连 `result_digest` 都一样）。
- **最小复现（真实运行，本机）**：

  ```
  > .\.venv\Scripts\python.exe -m pytest tests/test_ss_probe_pipeline.py -q -p no:warnings -s \
        -k partial_static_coverage_gap
  [probe] bash 静态覆盖: bash_syntax='not_checked' 运行终态=completed runner_calls=1 static_findings=0
  1 passed
  ```

  即：一个干净的 Bash 提交在 `bash_syntax` **未执行**的情况下走到 `completed`、runner 被真的调用、
  `static_findings == []`，而报告 JSON 里 `"not_checked"`、`"bash_syntax"`、`"未执行"` 三个字面**都不出现**；
  探针还断言了"把 `bash_syntax` 状态改成 `ran` 后，报告静态段逐字相同"。
- **文件与行号**：
  - `src/autograder_b2/evaluation/orchestrator.py:459-461`（`all(check.status == "not_checked")` 才失败的门槛；
    紧邻注释自称"未执行的检查不得折叠成'零 finding = 通过'"，但守卫只覆盖**全部**未执行的情形）；
  - `src/autograder_b2/evaluation/report.py:126-143` + `:228`（`static_findings` 只由 `static.findings` 派生，无检查状态）；
  - `src/autograder_b2/db/schema.py` 的 `static_findings` 表 + `alembic/versions/20260915_0006_b2_evaluation_tables.py:264ff`
    （表没有承载 `checks[].status`/`reason` 的列）。
- **可达性**：本机（bash = WSL 启动器）100% 可达；任何 `bash` 不在 worker 的 `PATH`/文件系统视图内的部署同样可达。
  同时**对照面成立**：安全层的等价缺口是 fail-closed 的（`_run_security` 见 `unchecked` 非空即 `failed + infrastructure_error`，
  我用不可解析源码的提交实测过），所以这条缺口目前**只在静态层**存在。
- **分类说明**：我不把它算作"标准 3 不满足"，因为标准 3 的"报告"在内部规范里的对应物是
  `docs/testing/cases.md` **T-SEC-004**「**检查结论字段**」与 `interfaces.md` **I14**
  （`analyze_static` 的 `checks[].status` + `reason`），两者都成立（3.1(d) 已验）。
  **若主控把"报告"读作端到端的评价报告（`build_report` 的产物）**，则标准 3 的第二子句在其本机 Bash 提交上
  **不成立**，应判 FAIL。这条读法差异请主控裁定。

### 2. `docs/requirements/traceability.md` 与本 L1 的实现状态不符（分类：仅文档问题）

- **现象**：`RQ-B-2-40` 与 `RQ-B-2-70` 的状态列均为 `待实现`、证据位置为 `—`，文末还写"本表当前无 `已完成` 行"；
  而这两个需求对应的实现与测试都在（131 条既有用例 + 我的 18 条探针全部通过）。
- **最小复现**：

  ```
  > Get-Content docs/requirements/traceability.md -Encoding utf8 | Select-Object -First 20
  | `RQ-B-2-40` | 静态合法性检查 + 总评/细评 | ... | `WP-B2-B` | T-STAT-* | `待实现` | 决策见 adr/ADR-0001 |
  | `RQ-B-2-70` | 待测代码安全性确认 | ... | `WP-B2-B` | T-SEC-*  | `待实现` | 决策见 adr/ADR-0002 |
  ```
- **文件与行号**：`docs/requirements/traceability.md:12`（RQ-B-2-40）、`docs/requirements/traceability.md:15`（RQ-B-2-70），
  以及同文件"说明"两条；`docs/testing/cases.md:41`（T-STAT-001 标 `待建`）、`docs/testing/cases.md:72`（T-SEC-001 标 `待建`）。
- **说明**：这可能只是"组级验收签字前不更新状态"的流程选择，我不据此判任何标准 FAIL；但按 `AGENTS.md`
  "文档同步"要求，签收本 L1 时应同步这几行。

### 3. 无一致性问题（澄清项）

- 实现者既有用例中 `tests/test_static_checks.py:262`（Bash 语法）与 `tests/test_evaluation_pipeline.py:903`
  （安全阻断）**使用了替身**。这不是缺陷（文件自身都在 docstring 里显式标注了），但意味着标准 1 的 Bash 半边
  与标准 2 的"真实规则有效性"**不能靠既有用例成立**——我上文的判定依据来自我自己用真 bash 容器与真实
  `decide_static` 适配器跑的探针，不是复述实现者用例。

---

## 附：环境事实（复核过程中自己验证的）

1. `shutil.which("bash") == C:\WINDOWS\system32\bash.EXE`（WSL 启动器）⇒ 宿主上 Bash 语法检查恒 `not_checked` + 原因。
   容器内 `shutil.which("bash") == /usr/bin/bash`，检查 `ran`。
2. 真实 PostgreSQL 17 在 `autograder-b2-postgres` 的 `0.0.0.0:55433->5432/tcp`；未设 `B2_TEST_DATABASE_URL` 时
   所有 DB 用例会 `skip`（我全程设置，故 149 条无 skip）。
3. **主机实时防护会删除盘上的高危形态（本次实测）**：我把"破坏性删除命令写成 `subprocess` 数组字面量"的探针
   写进 `tests/test_group_static_security_probe.py` 后，该文件被**静默删除**（pytest 收集期报
   `OSError: [Errno 9] Bad file descriptor`，随后文件不存在）。Base64 存放 + 分文件拆分后不再复现。
   这解释了仓库既有样本约定（`tests/test_security_decision.py` 顶部）的由来，也说明**把完整活体样本写到盘上
   在本环境不可靠**——所以我没有把解码后的样本单独落盘，只让它们作为提交内容进入工作区（并在用后清理）。
4. 我保留的探针文件：`tests/test_ss_probe_static_layer.py`、`tests/test_ss_probe_pipeline.py`
   （均已 `ruff check` 通过、无 skip 全绿）；容器探针脚本：`.pytest-tmp/probe_s1_bash2.py`、`.pytest-tmp/probe_s1_bash.py`。

---

## 我无法验证的部分

1. **执行期（沙箱内）安全判定**：`RQ-B-2-70` 场景 2-70-B/C（联网/写只读路径/提权被捕获为运行期
   `detected_in="runtime"` 事件、禁网可验证）需要真实 nsjail 跑学生代码，属 `WP-B2-C`；我**没有执行任何待测代码**
   （纪律要求），故这三条不在本次判定范围内。
2. **生产 Linux worker 上的分支走向**：我只能在本机（无可用 bash）与给定镜像（有 bash）两点上观测，
   无法判定真实部署的具体宿主会落哪个分支（这直接决定第 1 条缺口的可达性）。
3. **标准 2 中"产生 `security_finding` 记录"的严格读法**（是否要求 `b2.security_findings` 有行）：
   我按 `docs/requirements/acceptance.md` 的场景 2-70-A 口径判 PASS，但这是**口径判断**，
   事实（这五条样本端到端不产生 `security_findings` 行）已如实给出，请主控裁定。
4. **标准 1 中"行列"是否要求每条都有非空列号**：同上，事实已给出（1 类 Bash 条目 `column=None`）。
5. **恶意样本等价变体的完备性**：未穷举（`T-SEC-005` 的完整回归集完备性），只验了 5 条规范形态 + 1 个变体。
6. **主机实时防护的具体产品/规则**：我只能观测到"某一版探针被删除"，无法给出是哪个签名/规则命中。
7. 其它 L1 大类的标准、导出/报告的可重放性、日志脱敏等，均不在本次判定范围。

---

标准1=PASS 标准2=PASS 标准3=PASS
