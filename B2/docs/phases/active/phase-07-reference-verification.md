# Phase 07 参考解答验证的执行侧（`CHG-0019` + `CHG-0023` / `ADR-0015`）

- **范围**：B2 侧**只有一个端点**：`POST /api/v1/reference-verifications`（教师一键触发、B2 执行、
  B3 记录）。B1 的出题入口已由 B1 自己完成；本批**不改 B1、不改 B3**。
- **契约**：`MAIN/contracts/openapi/b2.yaml` 的 `verifyReferenceAnswer` / `ReferenceVerification`，
  以及 `b3.yaml` 的 `getReferenceAnswer`（`CHG-0023` 补上的输入通道）与 `recordReferenceAttestation`
  （`CHG-0008` 既有）。版本以 `module.yaml: contract_version` 为准，本文件不写死字面值。
- **裁决**：[ADR-0015](../../../../MAIN/docs/adr/ADR-0015-reference-verification-orchestration.md)；
  变更 [CHG-0019](../../../../MAIN/docs/contracts/changes/CHG-0019-reference-verification-endpoint.md)
  （§7 六条验证）与 [CHG-0023](../../../../MAIN/docs/contracts/changes/CHG-0023-reference-answer-channel.md)。

## 改了什么

| 落点 | 文件 | 内容 |
|---|---|---|
| 路由声明 | `module.yaml:7`、`plugin.py` 的 `descriptor.routes` | 第五条前缀 `/api/v1/reference-verifications`（**只加这一项**；两侧必须逐项相等，B4 registry 会比较） |
| 路由 | `src/autograder_b2/api/reference_routes.py`（新增） | 可用性 → 认证 → 授权 → 请求体 → 调服务 → 映射状态码 || B3 调用 | `src/autograder_b2/reference/b3.py`（新增） | `GET .../reference-answer`（`CHG-0023`）与 `POST .../attestations`（`CHG-0008`），共用同一个 `B3Endpoint` |
| 一次性工作区 | `src/autograder_b2/reference/workspace.py`（新增） | 受保护正文 → 一次性目录（**不经 CAS、不落库**），用完即删 |
| 编排 | `src/autograder_b2/reference/verification.py`（新增） | 取参考解答 → 取包 → `runner.run_case` 逐用例 → 只在全通过时请 B3 记存证 |
| 授权 | `src/autograder_b2/auth/authorization.py` | `require_reference_verification_authority`（staff + 课程作用域；第三层不可判定，已记录） |
| 装配 | `src/autograder_b2/runtime.py` | 与评测链**共用**同一个 `B3Endpoint` 与同一个 `DynamicExecutionGate`；新增 `references` / `references_reason` / `b3_endpoint` |
| 取包错误保真 | `src/autograder_b2/domain/evidence.py`、`integration/adapters.py` | `PackageUnavailableError.status_code`：`404`（版本不存在）与 `409`（版本还没有包）必须能分开，才能映射成不同对外语义 |
| 测试 | `tests/test_reference_verification.py`（新增，**50 个用例项**） | 见 [../../testing/cases.md](../../testing/cases.md) 的 T-REF-* |
| 自调用死锁修复 | `api/reference_routes.py`（`await run_in_threadpool(...)` + 说明取舍的注释与模块 docstring） | MAIN 在真实端口发现的**第三个缺陷**：见 §2c（修复前 `503 TimeoutError` **10.11s** → 修复后 `503 沙箱不可用` **0.17s**） |
| `409` 原因转述修复 | `reference/b3.py`（`_refusal`）、`reference/verification.py`（`_from_package_error`）、`integration/adapters.py` + `domain/evidence.py`（`upstream_detail`） | MAIN 在真实端口发现的**第四个缺陷**：草稿的 `422` 曾断言一个不成立的原因；见 §2d（修复后转述 B3 原话，草稿 `422` **0.12s**、已发布 `503` **0.08s**） |
| 回归用例 | `test_the_blocking_b3_call_runs_off_the_event_loop`、`test_a_reference_answer_refusal_relays_the_upstream_reason`、`test_a_reference_answer_refusal_without_a_reason_does_not_guess`、`test_a_package_refusal_relays_the_upstream_reason`、`tests/test_integration_adapters.py` 的两组参数化用例 | 前四条**先红后绿**均已实跑；取包侧两组用**真 HTTP 服务**返回 Problem Details |

**没有**改：`MAIN/contracts/**`、`contracts.lock`、`pyproject.toml`、`uv.lock`、`alembic/**`（本批无需迁移）、
`plugin.py` 的 `descriptor.contract_version`（保持 MAIN 同步的值）。

## 五条实现级不变量（与 `docs/api/contract.md` §1f 一一对应）

1. **不落库**：`ReferenceVerificationService.__init__` 连引擎都拿不到（结构性），一次全通过的验证跑完后
   `b2` 全部业务表零行、outbox 零事件、工作区目录已删除。
2. **未通过不产生存证**：只有 `cases_passed == cases_total` 才调 `POST .../attestations`；未通过时
   一次调用都不发（传输替身记录为空）。
3. **记录失败 ≠ 报错**：B3 拒绝/不可达 ⇒ `200` + `attestation_id: null` + 逐用例结论照旧；
   但上游声称 `201` 却读不出 `attestation_id` ⇒ `503`（`null` 的契约含义是"**没有**记录"）。
4. **不返回受保护内容**：响应键集合 == 契约声明的九个；参考解答正文、隐藏用例输入、期望输出零命中；
   `reference_answer` 已在 `logging.redaction.FORBIDDEN_FIELD_NAMES` 里（本批用测试钉住，未改名单）。
5. **失败不被伪装**：沙箱不可用 ⇒ `503` 且零存证；上游 `404`→`404`、`409`→`422`、其它→`503`；
   内部一致性缺陷冒泡成 500（不归一成"用例失败"）。

## 验证（真实执行）

### 1. `ruff check .`（原始输出）

```text
All checks passed!
```

### 2. `pytest`（原始 summary 行）

```powershell
$env:B2_TEST_DATABASE_URL = "postgresql+psycopg://autograder_platform:***@localhost:55433/autograder"
$env:B2_TEST_NATS_URL = "nats://localhost:54223"
uv run pytest
```

SDK 状态：**沿用上一批已刷新的 `.venv`**（本批只新增源码与测试，未改依赖；`uv sync` 未再执行，
因为它不会因路径依赖版本号未变而重装——见 phase-05 末尾"环境事实"）。

```text
本批前基线：3 failed, 1233 passed, 9 skipped, 2 xfailed
本批第一次（含死锁修复）：4 failed, 1281 passed, 9 skipped, 2 xfailed, 2 warnings in 389.31s (0:06:29)
本批最终（含死锁修复 + 409 原因转述）：3 failed, 1294 passed, 9 skipped, 2 xfailed, 2 warnings in 397.25s (0:06:37)
```

收集总数 **1308** = 1294 + 3 + 9 + 2（本批前 1247；`test_reference_verification.py` +53 项、
`test_integration_adapters.py` +8 项，合计 +61）。失败集合**全部是既有家族**：

```text
FAILED tests/test_grades_query_export.py::test_xlsx_timestamp_truncates_like_the_api_not_rounds
FAILED tests/test_grades_query_export.py::test_pagination_is_keyset_not_offset
FAILED tests/test_migrations.py::test_every_timestamp_column_has_millisecond_precision
```

三条失败与本批无关（迁移 `0012` 的毫秒四舍五入 + `import alembic.versions` 解析到 site-packages），
其中一条属于**那一对 flaky 用例**（本批两次运行分别失败 2 条与 1 条——这对的实测分布见
[phase-05](phase-05-quarantine-and-quality-surfaces.md) 的"未执行 / 未验收"第 1 条）。

> **后续（已解决）**：上述三条（连同第四条 `test_cursor_pagination_is_complete_and_disjoint`）已由
> MAIN 指定的整改批次全部修掉——**四条都是用例缺陷**，产品代码仅两处 docstring 措辞被订正、零行为变更。
> 本树最新读数 **`1298 passed, 9 skipped, 2 xfailed`（收集 1309，退出码 0）**；逐条根因、前后读数、
> 以及"分页并非缺 tie-breaker"的订正见
> [phase-08-known-failure-remediation.md](phase-08-known-failure-remediation.md)。

> **一次瞬态失败，已查明并**不是**本批的**：本批第一次全量运行里出现了
> `tests/test_plugin.py::test_descriptor_is_contract_bound` 失败。原因是 **MAIN 在我那次运行期间
> 落地了 `0.16.0` 批次**（`MAIN/contracts/openapi/b2.yaml` 19:06:58 改为 0.16.0，`B2/module.yaml` 与
> `plugin.py` 的 `contract_version` 19:07:13 才同步）——断言比较的正是这两者，于是有几十秒的窗口里
> 它是红的。单独重跑 `2 passed`，最终运行里也不再出现。**契约版本与锁现在由 MAIN 同步为
> `0.16.0`（`B2/module.yaml`、`contracts.lock`、`plugin.py` 一致），本批没有改动它们。**

**此前两处红已按预期转绿**（未靠登记"待实现"绕过）：

```text
tests/test_b2_routes.py::test_registered_paths_match_the_contract   1 passed   # _PENDING_CONTRACT_PATHS 仍为空集
tests/test_plugin.py                                                2 passed   # descriptor.routes == module.yaml routes
tests/test_reference_verification.py                               53 passed
tests/test_integration_adapters.py                                 29 passed
```

### 2b. MAIN 侧两条门禁（**在 MAIN 的 venv 上单独跑**，只跑被本批影响的两条）

```powershell
cd MAIN
.\.venv\Scripts\python.exe -c "from autograder_host import checks; checks.check_route_declarations(); print('check_route_declarations: ok'); checks.check_shared_vocabularies(); print('check_shared_vocabularies: ok')"
```

```text
check_route_declarations: ok
check_shared_vocabularies: ok
```

- `check_route_declarations` 由红转绿：`module.yaml:7` 加了 `/api/v1/reference-verifications`，
  契约路径因此落在某个已声明前缀之下。
- `check_shared_vocabularies` 绿：`cases[].failure_category` 的那份副本与 `evaluation-report`
  的值集一致（`CHG-0019` §2.6 要的那道门禁真的在比对）。
- **只跑了这两条**：`checks.py` 的 `main()` 会先跑 `check_module_installations`，而 MAIN 的 venv 里
  B2 是**路径依赖的已安装副本**，本批改动使它陈旧，需要 MAIN 执行
  `uv sync --reinstall-package autograder-b2`（属 MAIN 侧动作，本批未擅自改 MAIN 的环境）。
  完整的 `checks.py` 与 `probe_host_assembly.py` 因此**未由本批执行**，留给 MAIN 的重装与真实端口验收。

### 2c. 真实端口验收：**自调用死锁**（MAIN 实测发现，本批修复）

**症状（MAIN 在运行中的 18000 宿主机上实测）**：`POST /api/v1/reference-verifications` 恒返回
`503 B3 request failed: TimeoutError`，耗时 **10.03s**；同一时刻同一个 B3 内部端点从外部调用**不到 1 秒**，
`GET /api/v1/questions/coverage`（无外呼）0.05s。

**机制**：本端的 handler 是 `async def`，而它调用的 `verify()` 是**同步阻塞**的 HTTP 调用；它请求的
`B2_B3_BASE_URL` 在组合宿主里就是**正在服务本请求的同一个进程**（`MAIN/.env.integration` 实测
`B2_B3_BASE_URL=http://127.0.0.1:18000`，与宿主监听端口相同）。事件循环被这次调用占住 ⇒ 宿主无法服务那个
嵌套请求 ⇒ 自我等待到超时。评测链的取包没暴露它，只因为它跑在**独立的 B2 worker 进程**里，
不在宿主请求路径上——**本端点是 B2 第一个在宿主请求路径内同步回调宿主自身的路径**。

**修复**：`api/reference_routes.py` 的 `result = await run_in_threadpool(runtime.references.verify, ...)`。
选择理由（写在 handler 的注释里）：保留 `async def` 是因为本模块**自己读原始请求体**（`await request.json()`）
来控制 422 的形状，改成 `def` 会把这个控制权交给 FastAPI/Pydantic、`422` 的 `detail` 随之改变；
两者都能解掉死锁，选这一种使**可观测行为（除死锁本身）零变化**、改动面只有一行。
**这一行覆盖链上的全部三处回调**（取参考解答、取 evaluation package、记录存证）——它们都在 `verify()`
内部顺序执行，不存在"修一处漏两处"。

**修复前 / 修复后的真实端口读数**（同一个真实 composition host 应用，**第二个实例**跑在 `127.0.0.1:18081`、
`B2_B3_BASE_URL` 指向它自己 ⇒ 与 18000 实例同构；运行中的 18000 实例全程未被触碰）：

```text
# 修复前（把那一行临时改回直接调用后重装、重启同一个 18081 实例）
health/live: HTTP 200 in 0.12s
auth/session: HTTP 200 in 0.11s token_present=True
reference-verifications: HTTP 503 in 10.11s
body: {"type":".../service-unavailable",...,"detail":"B3 request failed: TimeoutError","instance":"/api/v1/reference-verifications"}

# 修复后（同一个 18081 实例，装上修复后的 B2）
health/live: HTTP 200 in 0.12s
auth/session: HTTP 200 in 0.12s token_present=True
reference-verifications: HTTP 503 in 0.17s
body: {"type":".../service-unavailable",...,"detail":"the sandbox is unavailable on this platform, so the reference answer was not executed (no attestation was recorded)","instance":"/api/v1/reference-verifications"}
```

`10.11s → 0.17s`（约 60×），且 `detail` 从 `TimeoutError` 变成**指名沙箱**——正是本平台（Windows 无 nsjail）
应有的结论，而 `503` 走的是服务层的 `SandboxUnavailableError` 分支，**零存证**。
`0.17s` 还反证了链路真的通了：能走到沙箱探测，说明**取参考解答与取 evaluation package 两次嵌套调用都已成功**
（否则会是 `404`/`422`/`503` 的另外两种 detail）；版本 `34d07765-49ea-4171-997b-dd25693716ff` 是
MAIN 给的已发布、有参考解答、有用例的版本。

**本地回归用例**：`tests/test_reference_verification.py::test_the_blocking_b3_call_runs_off_the_event_loop`
用"中间件记事件循环线程 + B3 传输替身记被调线程，两者必须不同"把它钉成可断言的事实。**实测有效**：
把那一行临时改回直接调用后，该用例立刻失败并给出诊断
（`AssertionError: 阻塞的 B3 调用跑在事件循环线程上：在真实宿主上这里会自我等待到超时`），
改回 `run_in_threadpool` 后通过——**先红后绿**都已实跑。

**这一类缺陷本地覆盖到什么程度（如实区分）**："阻塞调用必须离开事件循环线程"这条**性质**已被本地用例钉住；
但"真实 uvicorn + 真实自调用 → 真的挂到 10s 超时"这个**端到端现象**本地复现不了：B2 的 venv 里没有
`uvicorn`（而本模块不得新增依赖），因此真实端口的自调用验收仍只能由 MAIN 侧兜住——本批的做法是
**用一个真实 host 实例自己跑了一遍**（读数见上），并把命令留在本节末尾，便于重跑。

```powershell
# 第二个真实 host 实例（自调用），测完即停；运行中的 18000 实例不受影响
cd MAIN
Get-Content .env.integration -Encoding utf8 | ForEach-Object { if ($_ -match '=' -and $_ -notmatch '^\s*#') { $n,$v = $_.Split('=',2); Set-Item -Path "env:$($n.Trim())" -Value $v.Trim() } }
$env:B2_B3_BASE_URL = 'http://127.0.0.1:18081'
.\.venv\Scripts\python.exe -m uvicorn autograder_host.main:app --host 127.0.0.1 --port 18081
```

### 2d. 真实端口验收：`409` 的原因必须**转述**，不能自造（MAIN 实测发现，本批修复）

**症状（MAIN 在真实宿主上实测）**：对一个**确实有参考解答**的草稿
（`626fc91c-9f01-4846-95e1-58595dc0aa66`，`reference_answers.language` 与版本声明的语言一致），
B2 返回 `422 detail = "the question version exists but carries no reference answer to verify"`；
而直接问 B3，它的 `409` 说的是别的原因：`this version has no content digest yet, so an answer executed
against it could not be attested: a version needs at least one test case first`。

**两层错误**：① 那句话对这个版本是**假的**（它有参考解答）；② 它把 B3 **可区分**的原因压成了一句自造的
话——而 `409` 至少有三类（草稿没有包 / 没有 `content_digest` / 该语言没有参考解答），
要教师做的事完全不同（先发布 / 先给版本加用例 / 去写答案）。`CHG-0026` 刚要求这两个 `409` 必须可区分
（B3 已做到），B2 这一层不能把它们抹平。

**修复**：
- `reference/b3.py`：`409` 走 `_refusal()` —— 把 B3 Problem Details 的 `detail` **原样转述**，
  前缀只陈述确定的事实；取不到 `detail` 时明说"没有给出原因"，**不猜**。
- `integration/adapters.py` + `domain/evidence.py`：取包那侧的 `PackageUnavailableError` 多带一个
  `upstream_detail`（从 Problem Details 的 `detail` 提取，≤500 字符，**超过即丢弃、不截断**）。
  它与异常**消息**分开：消息照旧只报状态码（日志/事件不塞上游正文），
  只有验证端点的 `422` 才转述它。`reference/verification.py::_from_package_error` 对 `409` 同样转述。
- **删掉**两处自造原因的文案（`carries no reference answer` / `B3 compiles one only for a published
  version`）：前者对"有答案的草稿"是假的，后者对"摘要与发布时记录不符"也是假的。

**真实端口读数**（同一个 18081 自调用实例，修复后的 B2；草稿与已发布各一条）：

```text
########## A. 草稿（626fc91c-9f01-4846-95e1-58595dc0aa66）
health/live: HTTP 200 in 0.12s
auth/session: HTTP 200 in 0.10s token_present=True
reference-verifications: HTTP 422 in 0.12s
body: {"type":".../unprocessable-entity",...,"detail":"B3 refused to serve the reference answer (HTTP 409): this version has no content digest yet, so an answer executed against it could not be attested: a version needs at least one test case first","instance":"/api/v1/reference-verifications"}

########## B. 已发布（34d07765-49ea-4171-997b-dd25693716ff）
health/live: HTTP 200 in 0.03s
auth/session: HTTP 200 in 0.05s token_present=True
reference-verifications: HTTP 503 in 0.08s
body: {"type":".../service-unavailable",...,"detail":"the sandbox is unavailable on this platform, so the reference answer was not executed (no attestation was recorded)",...}
```

A 的 `detail` 是 **B3 自己的原话**（修前那句假话已消失），B 证明死锁修复没有回归（仍然 `0.08s`、
仍然停在沙箱）。两条都**零存证**。

**本地回归用例**（三条，都在替身/本地 HTTP 上跑，不依赖真实宿主）：
`test_a_reference_answer_refusal_relays_the_upstream_reason`（转述 + **反向断言**不得出现
`carries no reference answer`）、`test_a_reference_answer_refusal_without_a_reason_does_not_guess`、
`test_a_package_refusal_relays_the_upstream_reason`；取包侧的提取规则另有两组参数化用例在
`tests/test_integration_adapters.py`（真 HTTP 服务返回 Problem Details：带出 `detail`、且**消息里不出现**它；
非契约形状一律不带出）。

### 2e. MAIN 的交付扫描（`test_evidence.py --module B2`）：记录已产生，但**门禁会红，且不是本批能修的**

MAIN 本轮新增了第四道门禁（`check_test_evidence()`）：每个模块必须有一份**绑定当前契约版本与摘要**的
实测记录，且该记录 `exit_code == 0`。B2 的记录由本模块跑出：

```powershell
cd MAIN
.\.venv\Scripts\python.exe scripts\test_evidence.py --module B2
```

```text
B2: running the suite at contract 0.16.0 (372cfcabad2a…)
  recorded MAIN\.runtime\evidence\B2.json
  exit=1  {'failed': 4, 'passed': 1293, 'skipped': 9, 'xfailed': 2, 'duration_ms': 368670}
    | FAILED tests/test_grades_query_export.py::test_xlsx_timestamp_truncates_like_the_api_not_rounds
    | FAILED tests/test_grades_query_export.py::test_cursor_pagination_is_complete_and_disjoint
    | FAILED tests/test_grades_query_export.py::test_pagination_is_keyset_not_offset
    | FAILED tests/test_migrations.py::test_every_timestamp_column_has_millisecond_precision
    | 4 failed, 1293 passed, 9 skipped, 2 xfailed, 2 warnings in 368.67s (0:06:08)
```

**已上报的冲突（B2 未自行裁决）**：`evidence_problems()` 对 `exit_code != 0` 直接判失败，而 B2 套件的
非零退出**完全来自那几条既有失败**（迁移 `0012` 的毫秒四舍五入与成绩用例期望冲突的两条 —— 其中一条
属那对 flaky 用例 —— 以及 `import alembic.versions` 一条）；它们与本模块最近三批工作无关，
而**修它们要改成绩分页用例的期望**，那是另一个决定（见
[phase-05](phase-05-quarantine-and-quality-surfaces.md) 的"未执行 / 未验收"第 1 条）。
因此在这个冲突解决之前，B2 的 `test evidence` 门禁**不可能转绿**。三条出路都在 MAIN 手上：
① 授权 B2 修那几条（改测试期望或迁移行为，属需决策的改动）；② 让门禁容忍"已登记的既有失败"；
③ 为 B2 记一条显式豁免并写明原因。**本模块没有为了让门禁变绿而放宽任何断言或改任何既有测试的期望。**

> **后续（已解决）**：MAIN 选了出路 ①（指定专门批次修这 4 条，明确禁止放宽断言）。四条**都是用例缺陷**，
> 已修完并实测 `1298 passed, 9 skipped, 2 xfailed`（退出码 0）——套件侧的非零退出已消失。
> B2 **没有**改写 `MAIN/.runtime/evidence/B2.json`（上面那条 `exit=1` 的诚实记录原样保留），
> 由 MAIN 在重启窗口后统一重测。见
> [phase-08-known-failure-remediation.md](phase-08-known-failure-remediation.md)。

`MAIN` 其余与被本批有关的门禁均绿（本批实跑）：`check_route_declarations`、`check_shared_vocabularies`、
`check_module_governance`、`check_document_encoding_and_links`、`check_no_replacement_characters`。
**整套 `checks.py` 未全绿**：`check_module_installations` 报
`autograder_b3: installed copy is stale or divergent (compile/compiler.py, domain/packages.py,
domain/version_content.py, domain/versions.py…)` —— 那是**另一条线正在实现 `CHG-0028`**（草稿取包）
留下的工作树变化，不属本批；本模块**没有**重装 B3（那会把别人未完成的实现装进共享 venv）。

### 3. `CHG-0019` §7 六条的落实程度（**逐条，未做的写明**）

| # | 契约要求的验证 | 本批做到什么 | 未做/不可做的部分 |
|---|---|---|---|
| 7.1 | 无该课程写权限 ⇒ `403`；学生 ⇒ `403` | 学生 `403`、无课程作用域的 staff `403`、无令牌 `401`（T-REF-006/007/008） | **"该题目的课程"这一层做不了**（缺口 ⑦）：题目→课程是 B3 的事实，且本端点调 B3 用服务令牌。另一门课的教师当前被放行，由 T-REF-009 **钉成可执行的记录**，不写成"已收窄" |
| 7.2 | 参考解通过 ⇒ `cases_passed == cases_total`，B3 出现与该 `content_digest` 绑定的存证，随后 `publish` 成功 | 响应、计数、`content_digest`、`verified_at` 与送给 B3 的存证**逐字一致**（T-REF-011）；"全通过才发存证"由 T-REF-013 的反面钉住 | **"随后 `publish` 成功"未验证**：它需要真实 B3 + Linux 沙箱，且被缺口 ⑧ 挡住（见下）。`publish` 是 B3 的端点，本批未调用 |
| 7.3 | 参考解未通过 ⇒ 不产生存证，`publish` 仍 `422`，逐用例给出失败类别 | "零存证 + 逐用例失败类别"（T-REF-013）；沙箱违规的参考答案同样零存证（T-REF-014） | `publish` 的 `422` 未复现（同上，属 B3 + 真实环境） |
| 7.4 | 编辑题目内容后旧存证失效，再次 `publish` 被拒 | **未执行** | 摘要失效是 B3 的 `content_digest` 逻辑（`CHG-0008`），本批没有真实 B3 可用；B2 侧只把 `content_digest` **原样**交给 B3（T-REF-011 断言逐字一致），因此这条属于 B3 侧的验收 |
| 7.5 | 响应中不存在期望输出、隐藏用例输入或参考解答源码（断言键集合） | 键集合逐字断言 + 标记串零命中 + 契约 schema 校验（T-REF-001/003/021） | — |
| 7.6 | Windows 下返回 `infrastructure_error` 且不产生存证、不返回伪成功 | 沙箱不可用 ⇒ `503` + 零存证（T-REF-017）；**并且**有一条走**真实链路**的用例（真实取包解析 + 真实 `run_case` + 真实沙箱探测，只把两个 HTTP 边界换成替身）同样断言 `503` 与零存证（T-REF-026，Windows 专属，Linux 上 skip 并给理由） | 本机无法产出 `200` 的"全通过"真实读数（Windows 无沙箱），因此 §7.2 的"真实通过"只能靠替身覆盖，**不得**声称在真实环境跑过 |

### 4. 与真实 B3 的关系（**如实区分**）

- B3 的 `GET .../reference-answer` 端点**由另一工作项实现**（MAIN 已派给 B3），因此本批**没有**对真实
  端点做过任何调用：分支用传输替身覆盖（`200 / 404 / 409 / 5xx / 网络不可达 / 坏 JSON / 字段类型不对`，
  共 11 组，T-REF-018）。**未对真实 B3 端点验证**。
- B3 的 `POST .../attestations` 同样是替身覆盖（`201 / 409 / 500 / 网络 / 坏 201`，T-REF-015/016）。
- **缺口 ⑧（本批发现的第二个硬缺口，已上报）**：真实 B3 上 `GET .../evaluation-package` 对**草稿**
  返回 `409`（`B3/src/autograder_b3/domain/packages.py` 只在版本已发布时编译包，
  `load_published_snapshot` 还要求 `published_at`），而 `recordReferenceAttestation` **只接受草稿**。
  两者相遇 ⇒ "验证草稿 → 存证 → 发布"在真实环境**仍然走不通**：教师依旧无法在界面上完成发布。
  B2 这一侧把草稿的 `409` 如实映射成 `422` + 指明原因的 `detail`（T-REF-019 用 `409` 参数钉住），
  **不**返回伪成功、也**不**伪造用例数。修复属 B3 侧（让草稿也能取到执行输入），B2 不自行裁决。

## 未执行 / 未验收（**不写成通过**）

1. **真实 B3 的两个端点**：`reference-answer` 未实现（B3 负责）、`attestations` 本批未调用真实实例。
2. **`publish` 与摘要失效（§7.2 后半 / §7.3 后半 / §7.4）**：既需要真实 B3，又被缺口 ⑧ 挡住。
3. **真实沙箱下的"全通过"读数**：本机 Windows 无 nsjail（`ADR-0004` 禁止降级为裸进程），
   因此 `cases_passed == cases_total` 的真实读数只能在 Linux 验收里产生。
4. **课程维度的授权**：见缺口 ⑦，只判到"staff + 至少一门课"，另一门课的教师当前被放行。
5. **B1 侧的一键发布端到端**：属 B1；在缺口 ⑧ 修好之前它必然停在"验证不可用"这一态。
6. **速率限制**：`ADR-0015` 记录为已知限制（教师可反复触发验证、每次起沙箱），本批**未加**显式限流，
   与裁决一致；单并发闸门是唯一既有限流。

## 一处"刻意与评测编排不同"的取值：沙箱违规时跑完全部用例（**已由 MAIN 裁决保留**）

`EvaluationOrchestrator` 在某个用例 `security_blocked` 时**终止整次评测**（要给出运行级 `rejected`），
而本端点在 `security_blocked` 时**继续跑完其余用例**并如实逐条报告。2026-09-20 MAIN 复核后**裁决保留**，
理由（已同步写进 `reference/verification.py` 的模块 docstring，防止下一个人"顺手统一"）：

1. 契约要求"逐用例给出结论"，且 `cases_total` 要描述**该版本的用例总数**——B3 记录存证时按它
   **交叉核对**；提前停止会让 `cases_total` 要么算错、要么含义悄悄变成"跑过的数量"，两者都会让
   一次全通过也拿不到存证；
2. "违规即终止"的原始理由（要给运行级终态、避免继续消耗算力）对一个**只读、不落库、输出就是逐用例列表**
   的验证都不成立；
3. 同一个版本的参考解答对所有用例是**同一份代码、同一个沙箱策略**，继续跑并不比停下更不安全。

钉住这条行为的用例是 `test_security_blocked_case_is_reported_without_persisting`
（同时证明零存证、不写 `security_findings`）。

## 环境事实（写给下一个人）

- **本路径不读库**：`references_unavailable()` 刻意**不**检查 `EXPECTED_SCHEMA_REVISION`——
  它只依赖认证器与 B3 通道。把它接上库版本会让一个与库无关的功能因迁移落后而 503。
- `HttpPackageSource` 仍**不可注入传输**（它直接调 `urllib.request.urlopen`）：T-REF-026 用
  monkeypatch 换掉 `urllib.request.urlopen` 才能让**真实**取包解析链跑起来；新的 B3 调用则按评语轨的
  既有形态做成可注入的 `B3Transport`，因此分支用例不需要网络也不需要 monkeypatch。
