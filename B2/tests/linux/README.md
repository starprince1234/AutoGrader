# Linux nsjail 验收（`tests/linux/`）

本目录的用例在**真实 Linux 内核**上实测沙箱隔离与资源限制是否生效。完整构建与运行手册（镜像、特权容器、
挂载、常见故障）在 [../../verify/linux-nsjail/README.md](../../verify/linux-nsjail/README.md)；
本文件只写"这里有什么、怎么跑、哪些项有平台限制"。

## 1. 它证明什么

本目录有**两套互补**的验收，都必须在 Linux（容器）里跑：

| 文件 | 证明什么 | 是否导入 `src/**` |
|---|---|---|
| [test_nsjail_acceptance.py](test_nsjail_acceptance.py) | 隔离与限制**逐项**生效（自带最小策略）：禁网/只读根/非 root/内存/CPU/pids/输出/墙钟/进程树；并只读校验 runner 生成的配置被真 nsjail 接受 | **否**（刻意独立） |
| [test_nsjail_runner_acceptance.py](test_nsjail_runner_acceptance.py) | **真实 runner 执行路径**：`run_case` + `PosixNsjailLauncher` 在真 nsjail 里跑学生代码；恶意/超限样本被拦并归类；超时零残留；真实编排层得到终态与分数、同摘要重放同分同摘要 | **是**（真实接线） |

判定口径只有三种取值：`通过` / `失败` / `无法执行`，**全部来自真实执行的退出码 / 信号 / stdout /
`/proc` 观测**；拿不到证据一律 fail 并写明原因——绝不把"配置里写了"当成"限制生效"。

### 1.1 隔离矩阵（`test_nsjail_acceptance.py`）

验收项清单（`_REQUIRED_ITEMS`）共 11 项（表中另有第 7 项自检，不属于 `_REQUIRED_ITEMS`，故表列 12 行）：

| 项 | 内容 |
|---|---|
| 1 | nsjail 启动并跑完脚本 |
| 2 | 禁网可验证（外网/内网均不可达，且失败被记录） |
| 3 | 只读根（写系统路径失败） |
| 4 | 非 root（含外层 `/proc` 复核，防"假非 root"） |
| 5a | 内存上限生效（`rlimit_as`） |
| 5b | CPU 时间上限生效 |
| 5c | 进程数上限挡住 fork bomb（`rlimit_nproc` + `rlimit_nproc_type=VALUE`） |
| 5d | 输出上限（读取侧截断；nsjail 3.4 的 `config.proto` 无 stdout/stderr 大小选项） |
| 5e | 墙钟超时 |
| 6 | 超时清理**整个进程树**（不残留、不跳用例） |
| I2 | `nsjail --log` 把沙箱自有诊断与学生流**物理分开**（沙箱侧归因的可信边界） |
| 7 | 每一项都留下"命令 + 输出 + 判定"（自检：缺证据即失败） |

另有 1 项**平台限制项**（`_LIMITATION_ITEMS`，要求"留下实测结论"而非"必须通过"）：

- `5a2 cgroup memory.max 杀进程（环境限制）`：本环境的内核**记账但不杀**（`memory.max` 超限不触发
  OOM-kill），因此该用例记为 `无法执行` 并 **xfail**。若在某内核上它 XPASS，那正是"限制不再存在"的信号，
  应把该项移出 `_LIMITATION_ITEMS` 并把结论改写为 `通过`。

集成项（读取 runner 产物的用例，**只读**、不修改 runner）：

- `test_i1_runner_generated_config_is_accepted_by_real_nsjail`——用 runner 生成的策略去跑真 nsjail，
  用来校准字段名与 seccomp 允许清单。
- `test_i2_nsjail_log_separates_diagnostics_from_student_streams`——用生产同一组函数
  （`render_nsjail_config` + `build_argv`）跑两种真实情形，证明 `--log` 的分离成立：学生字节不落日志、
  nsjail 自身失败的 `[E]`/`[F]` 诊断不落被采集的进程流（stderr 0 字节）。

## 2. 怎么跑

**方式一：原生 Linux + 已装 nsjail**

```bash
pytest tests/linux/test_nsjail_acceptance.py -v -s
```

**方式二：Windows 宿主经特权 Linux 容器**（本模块实际使用的方式）

```powershell
docker build -f verify/linux-nsjail/Dockerfile -t autograder-b2-nsjail:local .
$env:AUTOGRADER_B2_NSJAIL_VIA_DOCKER = '1'      # 走 docker 驱动
$env:AUTOGRADER_B2_NSJAIL_IMAGE = 'autograder-b2-nsjail:local'   # 可省略，默认即此镜像
uv run pytest tests/linux/test_nsjail_acceptance.py -v -s
```

驱动模式只用一个**固定参数数组**调用 `docker run`（`shell=False`，无 shell 字符串拼接）；容器内跑的是
**同一份**用例，因此两侧结论可比。

**最近一次实测（2026-09-16）**：`uv run pytest tests/linux/test_nsjail_acceptance.py -q -s` →
**`1 passed, 14 skipped in 25.02s`**，宿主侧读到的容器内矩阵为 `matrix_items=14 missing=[] not_passed={}`。
那 14 行的构成（按 `_record(...)` 的实际标签）：**10 个必需项**（1/2/3/4/5a/5b/5c/5d/5e/6）+
**I2**（也在 `_REQUIRED_ITEMS` 里） + **5a2**（限制项） + **I1**（集成项） + **7**（自检项） = 14；
`1 passed` 是驱动用例本身，其余 14 条在本机按平台 skip。
**注意**：这条入口曾经**必然失败**——I2 的命令串含真实换行而 `_render_matrix` 只展平输出列，宿主侧按行解析
读不到该项（`missing=['I2 …']`）。矩阵的契约是"一行一项"，命令列也必须展平（已修，见
[../../docs/phases/active/phase-01-implementation.md](../../docs/phases/active/phase-01-implementation.md) 缺口 37）。

## 3. 与 B2 主测试套件的关系

- `tests/linux/**` **不在** `uv run pytest` 的默认收集范围内（需要 Linux 内核 + nsjail；Windows 宿主上
  只能经上面的容器方式跑）。
- Windows 主机上 nsjail 不可用 ⇒ runner **拒绝执行**（`SandboxUnavailableError`），
  **绝不降级为裸进程执行**；该行为由 `tests/test_integration_adapters.py`（T-INT-007）钉住。
- 结论与未执行项同步登记在 [../../docs/phases/active/phase-01-implementation.md](../../docs/phases/active/phase-01-implementation.md)
  的"验证记录"。

## 4. 最近一次实测结论（2026-09-16，容器方式）

- 验收项：**13 项通过 + 1 项「无法执行」**（`5a2`，原因见上；独立复核者在容器内重跑得
  `13 passed, 1 skipped, 1 xfailed`）。

### 4.1 真实 runner 验收（`test_nsjail_runner_acceptance.py`）

最近一次实测（2026-09-16，特权容器 + `--init` + 临时库）：**5 passed, 1 skipped**。

| 用例 | 实测证据 |
|---|---|
| `i2` 合法提交经真实 runner | `passed=True failure_category=None exit_code=0`，实际 stdout `'42\n'`（`duration_ms` 是**易变读数**，各轮实测 313/332/387ms，不作为判据） |
| `i3` 恶意/超限样本被拦并归类 | 禁网 → `security_blocked` + `exit_code=159`(SIGSYS) + 违规 `runner.seccomp`；超时 → `timeout`/`wall`；输出洪泛 → `resource_limit`/`output`；fork bomb → 程序观测到 `FORK_BLOCKED BlockingIOError`；内存申请 → 失败且非零退出（归类限制见下） |
| `i4` 超时后无残留 | `存活 nsjail=[]`、`引用本工作区的存活进程=[]`、`僵尸=[]`（需 `--init` 收尸） |
| `i5` 真实编排 + 真沙箱 | 同一提交两次 → `completed` / `score=1.0` / **同一** `result_digest`（两个 run 之间逐字节相同；具体值随提交/包绑定与环境变化，各轮实测 `3c60673b…fd8af`、`a26838c9ab…ca055`，**不比字面值**） |
| `i8` 学生不能伪造沙箱侧归因 | 学生打印 `nsjail: out of memory` / `nsjail: operation not permitted` → 归类仍是学生侧 `output_mismatch`、`resource_limit_hit=None`、无 `runner.resource_limit` 违规；从沙箱内往日志路径追加写入被拒（`FORGED_LOG_WRITE=DENIED errno=…`） |
| `i7` 沙箱不可用时拒绝执行 | 容器内 **skip**（沙箱可用）；该负面路径由 Windows 侧真实 `run_case` 与 T-INT-007 覆盖 |

**沙箱侧归因的边界（红队证伪 → 已修复）**：修复前 nsjail 自有诊断与学生 stderr **同写一份文件**，
标记匹配只是 `"nsjail" in line` 的启发式 ⇒ 学生打印一行 `nsjail: out of memory` 即能让
`case_evidence.failure_category` 落成 `resource_limit`（红队真跑并落库验证过）。现在 `build_argv` 必填
`--log`，nsjail 诊断只写那份**工作区之外**的宿主自有日志（沙箱内被遮蔽为只读空 tmpfs：学生读不到也
写不进），归类只读 `RunFacts.sandbox_log_text`，学生流不参与归因——伪造路径**不可达**而非"没被触发"
（分离本身由集成项 `I2` 真跑证明，伪造不可行由 `i8` 真跑钉住，两者都会在矩阵里留证）。

**如实记录的限制（红队实测修正）**：`rlimit_as` 用尽时内核不写日志、nsjail 也无诊断行，因此**内存耗尽**
在缺少沙箱侧诊断时无法与学生自身的失败区分（容器内实测归类为 `output_mismatch`）。生产配置
`log_level: ERROR` 下，正常运行与三类真实限制的 `--log` 文件均为 **0 字节**；只有 nsjail 自身报错
（如 execve 失败）才留下 `[E]`/`[F]` 行 ⇒ 限制标记依旧只作为**已判定失败**的补充说明，不得用于
"这是不是沙箱限制"。内存限制的 jail 级强制由 `test_05a_memory_limit_is_enforced` 实测证明；**运行期沙箱违规目前没有持久化落点**
（见 phase-01 遗留项）。

**未覆盖**：`run_case` 在真实 Linux 沙箱内执行学生代码这条路径**已由本文件覆盖**；仍未覆盖的是
"沙箱不可用 ⇒ 拒绝执行"的容器内验证（见上表 i7）。
- 已知边界（均为实测得到，不是推测）：nsjail 3.4 无 stdout/stderr 大小选项（输出上限只能读取侧强制）；
  `rlimit_nproc_type` 默认 `SOFT`，只写 `rlimit_nproc` 不生效；`uidmap.outside_id` 为空串表示"当前 uid"，
  nsjail 以 root 运行时会形成"假非 root"（故有外层 `/proc` 复核）。
