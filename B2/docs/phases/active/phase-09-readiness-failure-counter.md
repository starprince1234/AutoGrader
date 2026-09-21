# Phase 09 readiness 的失败计数语义（注释与行为互相矛盾 + 计数永不干净）

- **起因**：MAIN 在集成期观察运行中宿主的 `/health/ready`，看到 B2 的分项
  `evaluation-chain: ok - 评测/成绩端点报告了 N 次未预期失败`（模块仍 `healthy: true`），
  顺着读代码发现**三件彼此独立**的问题，并把"选哪种修法"留给 B2。**本文记录真实现场、裁决依据、
  改法与前后读数**；MAIN 提供的是证据与受控实验，不是结论。
- **时间线**：观察与受控实验（MAIN）→ B2 定修法并实现（本文件）→ MAIN 重装/重启/重录/真实端口 E2E。
  因此**本文的读数来自 B2 自己的 venv + 测试实例**，运行中宿主的最终文案由 MAIN 在重装重启后复验
  （见文末"未执行"）。

## 1. 现场（MAIN 提供，原样记录）

```text
运行中宿主 detail: evaluation-chain: ok - 评测/成绩端点报告了 N 次未预期失败    （healthy: true）
```

MAIN 的受控实验（只增内存计数器，不写任何存储；**其中两次探测是 MAIN 有意做的**，
`BEFORE` 的那 1 次无法回溯归因，如实记录）：

```text
BEFORE: failures=1
call 1 -> HTTP 503（沙箱不可用）   AFTER: failures=2
call 2 -> HTTP 503（沙箱不可用）   AFTER: failures=3
```

## 2. 三个问题（定性与我在代码里核实到的坐标）

| # | 问题 | 代码坐标（整改前） | 我的核实 |
|---|---|---|---|
| 1 | **注释与行为相反**：9 处调用点注释写"`record_failure()` 计数（健康检查据此报 unavailable）"，但计数非零时 `evaluation_health()` 返回 `HEALTH_OK`；`IntakeRuntime.health()` 同样返回 `True` | `api/evaluation_routes.py:146,477,537`、`api/grades_routes.py:121,171`、`intake/api.py:214,265,320,409`、`runtime.py:303-305`、`intake/runtime.py:95-96` | 逐处读过：**行为是对的，注释是错的**（理由见 §3）。另外我**多找到一处同类**：`events/nats.py:128` 也写"健康检查据此报 unavailable"，而 `broker_health()` 读的是"配置 + outbox 账本"，从不读 `NatsEventBus.last_error` |
| 2 | **计数器单调不降**：`runtime.py:146` 初始 0、`:161` 只 `+= 1`，**永不重置** | `runtime.py` / `intake/runtime.py` 的 `_failures` | 与 MAIN 结论一致。它不是"当前状态"，但旧文案读起来像"此刻仍在坏" |
| 3 | **计入了已文档化的平台条件**：本机没有 nsjail，参考解答验证的必然 `503` 也走 `record_failure()` | `api/reference_routes.py:110-113`（异常类型 `ReferenceVerificationUnavailable` 同时覆盖"沙箱不可用"与"B3 通道不通/上游自相矛盾"） | 核实：`SandboxUnavailableError` 是**平台条件**（`ADR-0004` 禁止降级为裸进程），而同一个异常类型里**混着**真正的未预期故障——这正是它必须被拆开的理由 |

## 3. 裁决（B2 采用的做法与依据）

**结论：计数保留、语义收紧、注释改齐；不翻转 `healthy`、不加衰减、不动契约。**

1. **不翻转 `healthy`（否掉"让代码对齐注释"这条最顺手的路，也即 MAIN 的修法 ① 的翻转版本）**：
   计数**从进程启动起单调不降**，一旦用它翻转标志，这台实例在**第一次瞬时抖动**之后就永久不健康，
   恢复也不再变绿。`runtime.py` 模块 docstring 早已把这种情况定为不可接受——"恒假告警会训练运维忽略
   这个标志——比不报更糟"。就绪判定回答"现在能不能服务"，由每次探测（`database_reason()`）与每次
   请求自己回答；"曾经失败过几次"回答不了这个问题。**因此不能靠翻转来消除矛盾，只能改注释。**
2. **文案如实**（MAIN 修法 ③ 的"如实措辞"分支）：detail 改成
   `评测链可用；自本进程启动以来累计 N 次未预期依赖失败（单调累计、仅诊断：不参与健康判定）`。
   两句话（"累计"+"不参与健康判定"）缺一不可：它挂在一份 `healthy=true` 的报告里，少一句就会被读成
   "此刻仍在坏"。**不做衰减/重置**：窗口长度会是一个凭空发明的参数，而"累计 + 不计入判定"已经消除
   误导，且不需要新配置项。
3. **只计"未预期"，平台条件单独走类型**（MAIN 修法 ②，但**只**限已文档化的那一个条件）：
   新增 `ReferenceSandboxUnavailable(ReferenceVerificationUnavailable)`，在"无沙箱"分支抛出；
   路由先捕它（503 + 零存证，**不计数**），再捕基类（503 + 零存证，**计数**）。
   用**类型**而不是比对错误文本（`config.py:91` 已就"用类型而不是比对错误文本"立过规矩）。
   为什么只排除它：`ADR-0004`/`tests/linux/README.md` 已把"本平台无沙箱"写成**文档化的部署事实**，
   它必然发生且与代码质量无关；而 B3 通道不通、上游自相矛盾、库不可达都是**真正的**未预期故障，
   必须继续累计（否则运维失去"这台进程最近出过事"的唯一直读信号）。
   **HTTP 可观测行为零变化**：两个分支返回同一个 `Problem(503, "service-unavailable", …, str(error))`。
4. **9 + 1 处注释改成事实**：9 处统一改为"`record_failure()`（**只如实累计、不翻转健康判定**，
   理由见该方法 docstring）"；`events/nats.py` 那处改为"状态侧是**本对象**的 `error_count`/`last_error`
   （供诊断；readiness 读的是配置 + outbox 账本，只有 DLQ 非空才算 unavailable）"。
5. **规则只写在两处权威**（避免再次出现"两处各说一套"）：`runtime.py` 模块 docstring 新增一节
   "失败计数与健康判定的关系"，`B2Runtime.record_failure` 与 `IntakeRuntime.record_failure` 的
   docstring 各自写明同一条规则（后者保持英文，与 `intake/**` 既有语言一致——**语言统一仍属 lead 的
   收口项，本批不改**）。

**范围外（明确不做）**：不动 `MAIN/contracts/**` 与 `contracts.lock`（模块健康只有 `healthy` +
自由文本 `detail`，本批全落在 `detail` 里）；不新增第 5 个分项健康项（会让 `healthy` 的四项口径
与验收用例一起变，收益只是把两个事实换个地方重复）；不改沙箱不可用时的 503 语义。

## 4. 改了什么（文件清单）

| 文件 | 改动 |
|---|---|
| `src/autograder_b2/reference/verification.py` | 新增 `ReferenceSandboxUnavailable`（带"为什么单列一个类型"的 docstring）；"无沙箱"分支改抛它 |
| `src/autograder_b2/reference/__init__.py` | 导出新类型 |
| `src/autograder_b2/api/reference_routes.py` | 先捕新类型（不计数）再捕基类（计数）；模块 docstring 第 4 条补充"两种 503 在计数上不同" |
| `src/autograder_b2/runtime.py` | 模块 docstring 新增"失败计数与健康判定的关系"一节；`record_failure` docstring 写明"不翻转 + 平台条件不计入"；`evaluation_health` 文案改为"自本进程启动以来累计…" |
| `src/autograder_b2/intake/runtime.py` | `record_failure` docstring 与 `health()` 文案同口径（英文） |
| `src/autograder_b2/api/{evaluation_routes,grades_routes}.py`、`intake/api.py` | 9 处注释改为事实 |
| `src/autograder_b2/events/nats.py` | 1 处注释改为事实（状态侧是本对象字段，不是 readiness 判据） |
| `tests/test_b2_routes.py` | 新增 `test_recorded_endpoint_failures_are_reported_without_flipping_readiness`（T-HLT-01） |
| `tests/test_reference_verification.py` | 两条沙箱用例各加 `failure_count == 0`（T-HLT-02）；`test_language_disagreement_between_upstreams_is_503` 加 `failure_count == 1`（T-HLT-03，反向半边） |

## 5. 读数（**本机实测**，真实 PostgreSQL 17 于 `127.0.0.1:55433`）

前置（与 runbook 一致，凭据用 `***` 记）：

```powershell
$env:B2_TEST_DATABASE_URL = "postgresql+psycopg://autograder_platform:***@127.0.0.1:55433/autograder"
$env:B2_TEST_NATS_URL     = "nats://127.0.0.1:54223"
```

### 5.1 定向用例

```text
uv run pytest tests/test_b2_routes.py -k "health" -q                        -> 4 passed, 76 deselected in 4.98s
uv run pytest …::test_recorded_endpoint_failures_are_reported_without_flipping_readiness -q
                                                                            -> 1 passed in 3.68s
uv run pytest tests/test_reference_verification.py -k "sandbox or language_disagreement or blocking" -q
                                                                            -> 5 passed, 48 deselected in 3.65s
uv run pytest tests/test_reference_verification.py "…::test_recorded_endpoint_failures_…" -q
                                                                            -> 54 passed in 8.93s
uv run ruff check .                                                         -> All checks passed!
```

**未整改前的对照**（旧文案/旧行为下这几条断言不可能成立，读数性质与 `phase-08` 的"旧断言原样跑"相同）：
`runtime.py` 只返回 `f"评测/成绩端点报告了 {n} 次未预期失败"`、且沙箱分支计数 ⇒ 新断言里的
`"累计" in detail` 与 `failure_count == 0` 两条**必然**为假；即整改是这两条断言的**前因**，而不是
"先写断言再让代码凑"。

### 5.2 反证"新断言有牙"（临时变异 → 跑 → 立刻还原）

```text
变异 A：路由把"无沙箱"也计入 record_failure()
  -> tests/test_reference_verification.py::test_sandbox_unavailable_is_503_with_no_attestation
     FAILED: assert 1 == 0
     tests/test_reference_verification.py::test_on_a_platform_without_a_sandbox_the_real_runner_returns_503
     FAILED（同一条断言）
  -> 还原后：54 passed

变异 B：计数非零时返回 HEALTH_UNAVAILABLE（即"让代码对齐旧注释"）
  -> tests/test_b2_routes.py::test_recorded_endpoint_failures_are_reported_without_flipping_readiness
     FAILED: AssertionError: ('unavailable', '评测链可用；自本进程启动以来累计 2 次未预期依赖失败…')
              assert 'unavailable' == 'ok'
  -> 还原后：1 passed
```

两处变异都**已还原**，最后一次全量运行（§5.3）是在还原后的工作树上做的；还原后再跑
`uv run ruff check .` → `All checks passed!`。

### 5.3 全量

```text
uv run pytest -q   -> 1299 passed, 9 skipped, 2 xfailed, 2 warnings in 407.77s (0:06:47)   [exit 0]
（上一批 1298 passed / 收集 1309；本批 +1 条用例 = T-HLT-01，其余两条是既有用例加断言）
```

### 5.4 运行中宿主的真实文案（**未复验**，属 MAIN 的收口步骤）

本批只在 B2 自己的 venv 里跑测试；运行中的宿主（18000）装的仍是改前的代码。**因此"重装重启后
detail 变成 `评测链可用（…）`、且两次沙箱 503 之后仍是这句话"这条我没有验证**——它需要
MAIN 按其收口序列（重装四模块 → 重启 → 证明进程新鲜 → 重录证据）后复验。本文给出的三条用例
是该结论在测试实例上的等价证据。

## 6. 文档同步

| 文档 | 改了什么 |
|---|---|
| [README.md](../../README.md) | 状态表新增"分项健康 / readiness（含失败计数语义）"行并指向本文；阶段索引补 phase-09 |
| [runbooks/local-development.md](../../runbooks/local-development.md) | 新增 §6a：那句话的含义、为什么不用它翻转、哪些计入/不计入、看到它时的处置顺序；另按 MAIN 的做法给旧读数加**快照说明**（指向 `MAIN/.runtime/evidence/B2.json` 台账，**不改写历史数字**） |
| [runbooks/troubleshooting.md](../../runbooks/troubleshooting.md) | "已实际遇到"表新增两行（计数不会自己变干净；旧措辞会被读成"此刻仍在坏"） |
| [testing/cases.md](../../testing/cases.md) | 新增"readiness 的分项健康与失败计数"一节：T-HLT-01..04；T-LOG-004 的"健康计数"改为"累计失败计数，见 T-HLT-01" |
| [testing/strategy.md](../../testing/strategy.md)、[collaboration/onboarding.md](../../collaboration/onboarding.md) | 旧读数（`1134/1145 passed`、迁移 head 快照）加**快照说明**，明确"不要改写成最新值、当前读数以 MAIN 台账为准" |
| 本文 | 新增 |

## 7. 未做 / 未验证（**如实列出**）

- **运行中宿主的真实文案未复验**（原因与归属见 §5.4）：需要 MAIN 重装并存重启。
- **MAIN 的 `BEFORE: failures=1` 无法回溯归因**：那 1 次是整改前累计的，含义不可考（MAIN 已如实
  披露，我这里不替它编一个原因）。
- **不覆盖"衰减/重置"这条可选修法**：本批明确**不采用**（理由见 §3.2），因此没有"计数会随时间
  回落"的读数——这是设计选择，不是遗漏。
- **计数器仍是进程内的**：跨进程/跨副本聚合不属本批（readiness 本来就是单实例信号）。
