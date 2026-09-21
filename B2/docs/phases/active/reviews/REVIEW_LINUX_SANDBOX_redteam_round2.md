# 红队复审记录（第 2 轮）：B2 · Linux 沙箱验收 —— 沙箱日志分离 + 归因只读宿主自有日志

- 裁决者：红队（冷视角）。**未修改任何被测文件**；全部探针写入 `.pytest-tmp/`。
- 被审对象：第 1 轮我打回的"归因面不可伪造"结论的**结构性修复**（`--log` 流分离 + `RunFacts.sandbox_log_text` + `_scan_sandbox_log` + i8）。
- 环境：Windows + Docker Desktop；容器 `autograder-b2-nsjail:local`（Linux/WSL2 内核、nsjail 3.4 在 `/usr/local/bin/nsjail`）。本轮临时库 `b2_rt2_e8ce86af`（`alembic upgrade head` → `20260915_0009`），未启停任何常驻容器。

---

## 0. 结论

| 项 | 裁决 |
|---|---|
| 标准 1（真实 nsjail 内合法提交得终态与分数） | **pass** |
| 标准 2（五类恶意超限被拦并归类；超时无残留） | **pass** |
| 标准 3（Windows 不可执行清单/原因/恢复操作入档，状态如实 `active`） | **pass**（清单已移到**第 28 条**，全部引用同步） |
| 本轮修复（日志分离 + 归因只读宿主自有日志 + i8） | **pass**（我独立复核了 5 个追问点，含 fd 继承维度） |
| 我第 1 轮的 D1 | **已修好**（我第 1 轮的真跑反例现在不再成立：学生一行文本改写归类不再发生） |
| 我第 1 轮的 D2–D8 | **逐条复核，均已修**（见 §4）；D9 未修但**如实登记**为第 27 条 `G-L1-7`（可接受，越界项） |
| 新缺口 | §6（F1 中 / F2 中低 / F3–F6 低 / F7 观察）——**与上面三条标准是否通过无关**，不据此判 reject |

---

## 1. 复算命令与关键读数

宿主（与盘档声称逐字一致）：

```powershell
$env:B2_TEST_DATABASE_URL="postgresql+psycopg://autograder_platform:change-me-local-only@localhost:55433/autograder"
uv run ruff check .            # → All checks passed!
uv run pytest -q -p no:warnings
# → 1134 passed, 9 skipped, 1 xfailed in 157.50s
```

容器（两套交付套件 + 我自写探针）：

```text
tests/linux/test_nsjail_acceptance.py        → 13 passed, 1 skipped, 1 xfailed in 24.07s
tests/linux/test_nsjail_runner_acceptance.py →  5 passed, 1 skipped in 20.33s
.pytest-tmp/redteam_std1_runner.py           → 全部断言通过（合法提交 passed=True/exit 0、
                                               编排 completed/score=1.0、重放同 result_digest=434f809d…、
                                               DB 行一致；两条反证仍失败）
.pytest-tmp/redteam_std2_hostile.py          → 全部断言通过（五类读数见 §3）
.pytest-tmp/rt2_log_boundary.py              → 全部断言通过（新边界四项，见 §2）
.pytest-tmp/rt2_infra_failure.py             → 见 §6 F1
.pytest-tmp/rt2_log_leak.py                  → 归类产物不含日志原文/宿主路径
```

（第 1 轮的 `redteam_fix2_classify.py` 因 `normalize_nsjail_log` 被删而无法再导入——已被本轮探针取代；如实记录。）

---

## 2. 本轮修复的独立结论：**pass**（附我自己的证据，不采信其自述）

探针 `.pytest-tmp/rt2_log_boundary.py`（生产签名 `run_case` + 真 nsjail；为在删除前扣字节，用 monkeypatch 包住 `run_case._unlink_quietly` 做快照——只替换内存引用，不改仓库文件）。

### 2.1 追问点 1：没有行过滤之后，日志是否真的学生不可达（路径 + fd 两个维度）

学生程序（最坏假设：**它已经知道了宿主日志绝对路径**）在沙箱内实测：

```text
[obs] append LOG -> OSError errno=30 Read-only file system OSError
[obs] read LOG -> OSError（读不到沙箱自有诊断）
[obs] write RUNDIR/新文件 -> OSError（工作区之外一律写不进）
[obs] FD PROBE: fd0 -> <…>.stdin WRITE FAILED errno=9 | fd1 -> <…>.stdout WROTE 46 bytes |
               fd2 -> <…>.stderr WROTE 46 bytes | fd3 -> unreadable errno=2 | fd4 -> unreadable errno=2 …
[obs] A 日志快照：0B 含学生标记=False
[obs] A 归类：cat='output_mismatch' limit=None violations=[]
```

- **路径维度**：append 被拒（我实测 `errno=30` EROFS；交付 i8 实测 `errno=13` EACCES——两者都是拒绝，差异只是先撞上哪一层只读挂载）。
- **fd 维度**（我在第 1 轮补测、他们随后也自测过，我**独立复现**）：沙箱内子进程只看到 fd 0/1/2（自己的 stdin/stdout/stderr），**没有**继承 nsjail 的日志 fd（fd≥3 全部 `errno=2`）。学生用 `os.write` 写满 24 个 fd 后，日志快照仍是 **0B 且不含学生标记**。
- **结论**：伪造路径不是"没触发"，而是**结构性不可达**。`--log_fd`/`--pass_fd` 这条路也不通：argv 与 config 都由 B2 生成（`build_argv` 形态固定，`policy` 来自 package 的 `security_policy`，学生改不了），实测也没有继承 fd。

### 2.2 追问点 2：陈旧字节

预置 55B 伪造日志（`nsjail: out of memory, killing child <标记>`）后再跑同一工作区：

```text
[snap] …/B-stale.nsjail.log -> 55B        # 启动前的 unlink 钩子先看到我预置的字节（证明 unlink 在启动前发生）
[snap] …/B-stale.nsjail.log -> 0B         # 本轮 nsjail 写的字节
[obs] B 归类：cat='output_mismatch' limit=None；快照=0B 含预置标记=False
```

即"本轮扫描只认本轮字节"成立（`run_case.py:843` 的启动前 unlink + 读后即删）。

### 2.3 追问点 3：日志不进证据/不落库

- 代码面：`sandbox_log_text` 的**唯一**消费者是 `_scan_sandbox_log`（`run_case.py:446/548`），返回的是**结构化键**（`memory`/`cpu`/`processes`/`files`/`sandbox_violation`），不是原文；`RunFacts` 不进证据，`evidence_digest_of` 只吃 `CaseEvidence` 字段。
- 实测：把"含宿主路径 + 诊断句"的日志喂进归类，产物只有键：

```text
[obs] message='sandbox reported a runtime violation: sandbox_violation'
[obs] findings=[('sandbox_violation', 'high', 'runner.sandbox_log', 'sandbox reported a runtime violation: sandbox_violation')]
[obs] 日志原文里的宿主路径是否进入归类产物：False
[obs] 日志原文里的诊断句是否进入归类产物：False
```

- 文件面：`finally` 里与 stdin/stdout/stderr 一同 unlink（`run_case.py:877-879`），我实测运行结束后日志文件**不存在**。

### 2.4 追问点 4：i8 的 `FORGED_LOG_WRITE=DENIED` 是否可能"因为路径不存在而恒真"

- 我复核了 i8 源码（`tests/linux/test_nsjail_runner_acceptance.py:525-555`）：它断言的是"出现 `FORGED_LOG_WRITE=DENIED`"，函数层确实**无法**区分 ENOENT/EACCES/EROFS——**他们的自我说明是诚实的**。
- 但我用独立证据把这条断言补全了：① 该路径在沙箱内先撞上只读层，实测 `errno=30`（EROFS），不是"父目录不存在"；② 同一程序对**合法的可写路径**（chmod 后 0o777 的工作区）写入**成功**（§6 F2 的对照组 `WRITE_OK`）⇒ 拒绝来自沙箱边界而非探针本身坏掉；③ 日志快照不含学生标记；④ 无继承 fd。四条合起来，i8 的结论成立。
- 建议（可选、低优先）：把断言从"任何 OSError"收紧为 `errno in (13, 30)`，或补一条 `os.access(log_path, os.W_OK) is False`；并在 docstring 里把"ADD 到路径不存在也满足 DENIED"这一点写明（他们的消息里已说明，代码注释里没有）。

### 2.5 追问点 5：i3 forkbomb 现在归 `output_mismatch`/`limit=None` —— 更诚实还是检测退化？

**判定：更诚实，不是退化。** 证据：

- 旧代码那条 `resource_limit/processes` 只能由**文本标记**触发；而文本标记的来源在前一轮被我证明是**学生可写**的（我第 1 轮用它把 `case_evidence` 落成 `resource_limit`）。
- 真实环境里 nsjail **不自带**这种诊断：我直接用生产配置跑真 nsjail，CPU（`rc=137`）、内存（`rc=9`）、文件大小（`rc=120`）三类真实限制下 nsjail 日志**均为 0B**；fork bomb 的 EAGAIN 是**学生进程自己**观测到的（`FORK_SUCCESS=15 LIMIT_HIT=(15, 11, 'Resource temporarily unavailable')`）。
- 强制证据仍在，只是换了承载方：沙箱内 15 次 fork 后 EAGAIN（上限 16），**沙箱外同一程序 200 次全成功**（`FORK_SUCCESS=200 LIMIT_HIT=None`）⇒ pids 限制真的生效；矩阵 `test_05c` 另有 jail 级证明。
- 代价如实：该样本的 `resource_limit_hit` 现在是 `None`，"pids 上限被命中"这件事**只存在于学生自己的 stdout**里，平台侧不再据此归因——这与"学生文本不可作归因来源"是同一条边界，取舍正确。

---

## 3. 三条标准（当前代码）

标准 1/2 用我自带的探针（与上一轮同一份，代码未动）在**新代码**上重跑：

```text
# 标准 1（生产签名 run_case + 真 nsjail；沙箱内自报 euid/pid 进入期望输出）
[PASS] A2 合法提交通过 / 退出码 0 / stdout 摘要 == sha256(期望) / 沙箱内 euid=65534
[PASS] A3 期望改错 ⇒ output_mismatch      [PASS] A4 非零退出 ⇒ 失败
[obs] 编排 run#0/#1 state=completed score=1.0 digest=434f809da51cb21f… 两次相同；DB 行一致

# 标准 2（五类自写样本 + 沙箱外对照 + 残留扫描；PID1=docker-init）
[obs] N 禁网: cat='security_blocked' exit=159（SIGSYS）  对照（沙箱外）NET_OK 1.1.1.1:80 / DNS 解析成功
[obs] T 超时: cat='timeout' limit='wall' exit=-9 duration_ms=5169；存活 nsjail=[] / 本工作区存活进程=[] / 僵尸=[]
[obs] F fork bomb: cat='output_mismatch' limit=None；沙箱内 FORK_SUCCESS=15→EAGAIN；沙箱外对照 200/None
[obs] M 内存: exit=9 cat='output_mismatch'（MEM_MEMORYERROR allocated_mib=48，rlimit_as=64MiB）
[obs] O 输出洪泛: cat='resource_limit' limit='output'（另有 rlimit_fsize 佐证）
```

- 标准 1 **pass**；标准 2 **pass**（五类都真实被拦/被挡并留下可核对的观测量；超时后无存活残留；`--init` 与否的僵尸差异已在第 1 轮实测并写入文档）。
- 标准 3 **pass**：Windows 不可执行清单现为**第 28 条**（`docs/phases/active/phase-01-implementation.md:277-286`），5 行齐全、状态如实 `active`；所有引用同步到第 28 条（`docs/README.md:38`、`traceability.md:21`、`phase-01:74`、`policy.py:24`）。第 1 轮我指出的第 27 条引用错误已随重编号消解。

---

## 4. 我第 1 轮清单的处置复核（D1–D9）

| 项 | 我的复核 | 证据 |
|---|---|---|
| D1 归因可伪造 | **已修好** | 学生流伪造 → `output_mismatch`（纯函数 + 容器真跑 + i8）；日志里的同串才命中（`test_student_text_cannot_forge_a_limit_attribution`，`tests/test_runner_core.py:2369-2392`） |
| D2 探测把目录当可执行 | **已修** | `policy.py:453`：`if candidate and check(candidate) and not os.path.isdir(candidate)` |
| D3 README `rlimit_nproc_type=HARD` | **已修** | `tests/linux/README.md:31` 现为 `VALUE` |
| D4 README "10 项"与 11 行 | **已修** | `tests/linux/README.md:21` 改写为"11 项 + 第 7 项自检不属 `_REQUIRED_ITEMS`，表列 12 行"；矩阵读数同步为 13 |
| D5 cases.md "无僵尸"强于断言 | **已修** | `docs/testing/cases.md:58` 现写"僵尸不在断言内，仅在容器无 `--init` 时打印记录" |
| D6 policy.py 顶部过期表述 | **已修** | `policy.py:17-24` 重写为"已验收 + 5 passed, 1 skipped"+ 指向第 24/26 条 |
| D7 `__all__` 漏两项 | **已修** | `policy.py:1160/1187` |
| D8 交叉引用应指第 26 条 | **已修** | `phase-01:225` 现为"归类限制见第 26 条" |
| D9 运行期违规无落点 | **未修，但如实登记** | `phase-01:272-276` 新增第 27 条 `G-L1-7`，写明"越出本小类"与修法方向 ⇒ 可接受 |

---

## 5. 我不同意/需注意的两点（不构成 reject）

1. **i8 的写拒绝断言偏弱**（见 §2.4）：它无法区分"路径不存在"与"写入被拒"。结论仍成立是因为我补了独立证据；但作为回归断言，建议收紧 errno 或补 `os.access`。
2. **`test_sandbox_log_must_live_outside_the_writable_workspace` 名不符实**（见 §6 F3）：它只校验"日志与配置同目录"，而"工作区之外"是**调用方**（`_execute_case` 把两者都放在 `workspace.parent`）的不变量。名字与 docstring 会让人误以为 `build_argv` 能挡住"把 config 放进工作区"的调用方。

---

## 6. 新缺口（与"本小类是否通过"分开；按严重度）

**F1（中）宿主侧沙箱建不起来时，学生被记成 `output_mismatch`（学生代码从未运行）**
- 实测（`.pytest-tmp/rt2_infra_failure.py`，`probe_sandbox.available=True` 但 nsjail 建沙箱失败）：
  - 变体 A（`docker run` 无 `--privileged`，cgroup 建不起来）：`cat='output_mismatch' exit=255 stdout=''`，日志 `Could not apply '+memory' to cgroup.subtree_control … / Couldn't setup parent cgroup (cgroupv2)`；违规 `[]`。
  - 变体 B（`--privileged --cgroupns=host --read-only`，挂载失败）：`cat='output_mismatch' exit=255`，日志 `Couldn't mount '/workspace' / [F] Launching child process failed`；违规 `[]`。
- 后果：`case_evidence.failure_category='output_mismatch'`（学生侧）落库，而学生代码**一行都没跑**；`_exit_code_failure` 里专为 255 写的那条分支（`run_case.py:572-579`，`crash` + "nsjail or the child terminated abnormally"）在 stdout 比较之后，**不可达**。
- **与本轮改动无关（非回归）**：探针同时打印"旧过滤（要求行内含 `nsjail`）会命中的 needle = None"，即旧实现同样归成 `output_mismatch`。
- 建议：在输出比较之前加一条结构化判定（例如 `exit==255 且 stdout/stderr 双空 且日志非空 ⇒ infrastructure_error`），或把 255 分支提到比较之前；并补一个"沙箱建不起来"的用例钉住它。

**F2（中低）生产工作区不放开权限 ⇒ 服务以 root 运行时学生写不进自己的工作区**
- 实测 A/B（真 nsjail，容器内以 root 运行 ⇒ 被囚进程外层 uid=65534）：
  - 默认 `0o755`（**生产 `WorkspaceProvider.prepare` 的做法**，`integration/adapters.py:220-243` 无 `chmod`）：`DIR=/workspace mode=0o755 wok=False / WRITE_FAILED errno=13 Permission denied`
  - `chmod 0o777`（`policy.create_case_workspace` 的做法，`policy.py:702-715`）：`WRITE_OK`
- 关键事实：做 0o777 的那个函数**在生产里无人调用**（全仓仅有测试引用：`grep create_case_workspace` ⇒ `policy.py` 定义 + `__init__` 导出 + `tests/test_runner_core.py:2311`）。生产走 `WorkspaceProvider.prepare`（不 chmod）或直接吃调用方给的 `submission.workspace`。
- 影响面：服务以**非 root** 运行时被囚进程外层 uid = 服务 uid（`policy.uid_maps`），工作区归服务所有 ⇒ 可写，没问题；服务以 **root** 运行（容器化部署常见）时 ⇒ 学生**无法产出任何文件**，`file`/fixture 输出类用例全灭。交付验收的样本只写 stdout（`LEGAL_PROGRAM`/五个恶意样本），所以这条路径**没有验收覆盖**。
- 建议：把"沙箱内 uid 可写工作区"变成显式不变量（`WorkspaceProvider.prepare` 后 chmod，或 `run_case` 前置断言 `os.access(workspace, W_OK|X_OK)`），并补一个"学生在沙箱内写文件并产出期望文件"的验收样本。

**F3（低）`test_sandbox_log_must_live_outside_the_writable_workspace` 名不符实**（`tests/test_runner_core.py:969-978`）：它传入 `config_path=CONFIG`、`sandbox_log_path=/tmp/other/…`，断言的是"必须与配置同目录"。`build_argv` 拿不到宿主工作区路径，**无法**校验"工作区之外"；该不变量由 `_execute_case`（两者都放 `workspace.parent`）保证。建议改名或在 `run_case` 里断言 `sandbox_log_path.parent == workspace.parent`。

**F4（低）残留过期读数**：`verify/linux-nsjail/README.md:126` 仍写"本轮实测结果：**4 passed, 1 skipped**"，而本轮实测（我复算）与其余全部文档均为 `5 passed, 1 skipped`（`docs/README.md:38`、`traceability.md:21`、`phase-01:74/89`、`tests/linux/README.md:88`、`policy.py:22`）。该文件本轮被改过（18:54），但这一行没跟上。

**F5（低）第 24 条仍写 4 passed 且无"上轮读数"标注**（`phase-01:220`）：第 26 条与第 74 行已更新为 5 passed，同一文件里两个不同读数并列而无说明。建议补一句"（该轮读数；本轮为 `5 passed, 1 skipped`，见第 26 条）"。

**F6（低）`policy.py:23` 的交叉引用**：把证据指向"第 24/26/27 条"，但第 27 条现在是 `G-L1-7`（运行期违规落库），与 run_case 的 Linux 路径证据无关；应指第 24/26 条（Windows 清单在第 28 条，下一行已提）。

**F7（观察）边界依赖两处"当前恰好为真"的约定**：① argv 里没有 `--pass_fd`/`--log_fd`（否则学生可能拿到可写的日志 fd）；② config 与 log 都在工作区之外（由调用方保证，见 F3）。两者目前都成立且被实测覆盖，但都不是 `build_argv` 能自证的不变量。建议在 `build_argv`/`run_case` 加显式断言（例如拒绝 `--pass_fd` 形态的扩展、断言 `sandbox_log_path.parent == workspace.parent`），免得将来被静默破坏。

---

## 7. 未执行/未验证（如实记录）

1. 容器内"沙箱不可用 ⇒ 拒绝执行"：**未执行**（容器里沙箱可用；交付 i7 在此处 skip，我复核了其 skip 条件）。
2. 我**没有**构造出"nsjail 报错文本里含 `operation not permitted`"的真实失败（两次真实基础设施失败分别是 cgroup 与 mount 失败，文本都不含 `VIOLATION_MARKERS` 的任何 needle，旧/新过滤都不命中）。因此"基础设施失败被误判成学生 `security_blocked`"这一假设**未被证实**，我只报告被证实的 F1（误归成学生 `output_mismatch`）。
3. 第 1 轮的 `redteam_zombie_noinit.py` 本轮未重跑（超时清理与收尸路径未被本轮改动触及；第 1 轮读数：`--init` 僵尸 `[]` vs 无 `--init` 稳定 1 个 `<defunct>`）。
4. 临时库 `b2_rt2_e8ce86af` 未删除（供复算；清理命令：`docker exec autograder-b2-postgres psql -U autograder_platform -d postgres -c 'DROP DATABASE "b2_rt2_e8ce86af";'`）。

## 8. 本轮探针清单（`.pytest-tmp/`，未改被测文件）

| 文件 | 用途 |
|---|---|
| `rt2_log_boundary.py` | 日志可达性（路径+fd）、陈旧字节、归因来源边界、工作区可写性 A/B |
| `rt2_infra_failure.py` | 宿主侧建沙箱失败时的归类（F1） |
| `rt2_log_leak.py` | 日志原文/宿主路径是否漏进归类产物 |
| `redteam_std1_runner.py`（复用） | 标准 1：合法提交 + 反证 + 编排层重放 |
| `redteam_std2_hostile.py`（复用） | 标准 2：五类样本 + 对照 + 残留 |
