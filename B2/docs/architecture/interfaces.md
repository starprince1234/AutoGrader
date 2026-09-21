# B2 内部接口（冻结）

> **契约权威在 `../../../MAIN/contracts/**`，本文不是契约。** 本文只冻结 **B2 内部形态**：字段、函数签名、不变量与失败语义，并逐项写明它与哪份契约文件存在派生/引用关系。**契约字段定义不在本文复制**（复制会产生漂移，见 [../../../MAIN/docs/contracts/change-protocol.md](../../../MAIN/docs/contracts/change-protocol.md)）；本文出现的枚举取值是内部类型的实现约束，若与契约漂移，**以契约为准**并触发本文变更。

## 0. 冻结声明

| 内部接口 | 冻结形态 | 消费者 | 冻结依据 |
|---|---|---|---|
| `SubmissionEnvelope` | `frozen dataclass`（不可变） | 存储层、静态检查、安全判定、runner | `WP-B2-A`（与 MAIN 契约对齐后自定内部形态） |
| `StaticAnalysisResult` | `frozen dataclass` + 逐条 `StaticFinding` | 安全判定、报告器 | `WP-B2-B`（静态 → 阻断判定的接口签名） |
| `SecurityDecision` | `frozen dataclass`（三态） | 编排层（阻断门）、报告器 | `WP-B2-B`（`WP-B2-C` 的 runner 依赖"是否允许动态运行"） |
| `run_case(package, submission) -> CaseEvidence` | 函数签名 + `CaseEvidence` | 编排层、报告器 | `WP-B2-C`（自冻结，先冻结后三泳道并行） |
| outbox 发布接口 | `OutboxWriter` / `OutboxDispatcher` 协议 + `DomainEvent` | 接收层（`submission.accepted`）、编排层（评测 4 事件） | `WP-B2-C`（共享，一人实现） |

冻结顺序遵循 [../collaboration/workpackages.md](../collaboration/workpackages.md) 第 2 节：**签名先冻结，实现后补齐**；`WP-B2-A` 与 `WP-B2-B` 可完全并行，`WP-B2-C` 在签名冻结后并行。

状态：**已冻结（规格 + 代码）**。代码落点 `src/autograder_b2/domain/`（单点 import 入口 `autograder_b2.domain`），
文档↔代码的"逐字一致"由 `tests/test_frozen_interfaces.py` 以**本文档为基准**断言：字段名序列、字段注解
（经 `ast.unparse` 规范化）、`Literal` 别名取值、Protocol 方法参数，任一方漂移即测试失败。
契约锁只读由 `tests/test_contract_conformance.py` + [../../verify/contract_lock_check.py](../../verify/contract_lock_check.py) 复算核验。
`docs/collaboration/interfaces.md` 与 `docs/collaboration/workpackages.md` 的冻结清单已同步为"已冻结"。

## 1. 契约基线与核验方式

内部接口与契约的一致性**由工具复算**，不靠人工比对：

```powershell
uv run python verify/contract_lock_check.py
```

| 项 | 值（本文件冻结时实测） |
|---|---|
| `contract_version` | `0.1.0` |
| `contracts.lock.digest` | `de8f683496da78438a5cd1e6a12fc2aa30b871c63573ffb49e5e0abe5cc4b617` |
| `MAIN/contracts` 实时摘要（复算） | `de8f683496da78438a5cd1e6a12fc2aa30b871c63573ffb49e5e0abe5cc4b617` |
| 文件数 | 18 |
| 结论 | `MATCH: True`（退出码 0） |

摘要算法与 MAIN 官方实现逐字节一致（`MAIN/host/src/autograder_host/checks.py: contract_digest()`）：相对路径 POSIX 排序 → `update(相对路径)` + `update(b"\0")` + `update(文件字节)` → SHA-256。本工具**只读**，不修改任何契约或锁文件。

## 2. `SubmissionEnvelope`（归一化后）

外部请求形态与邮件来源在**进入业务逻辑前**归一化为同一内部形态；两者走完全相同的校验/存储/评测路径。

### 2.1 签名

```python
@dataclass(frozen=True, slots=True)
class EnvelopeFile:
    path: str              # 白名单校验后的工作区相对路径
    content_sha256: str    # 服务端按字节重算得到的小写 hex
    size_bytes: int
    storage_ref: str       # 服务端按摘要派生的内容寻址引用（非客户端路径）

@dataclass(frozen=True, slots=True)
class ApiProvenance:
    request_id: str

@dataclass(frozen=True, slots=True)
class EmailProvenance:
    mailbox: str           # 服务端配置的邮箱标识，非邮件头
    uidvalidity: int
    uid: int
    message_id: str

IntakeProvenance = ApiProvenance | EmailProvenance

@dataclass(frozen=True, slots=True)
class SubmissionEnvelope:
    submission_id: str
    student_id: str
    course_id: str
    section_id: str
    question_version_id: str
    idempotency_key: str
    intake: Literal["api", "email"]
    provenance: IntakeProvenance
    correlation_id: str
    files: tuple[EnvelopeFile, ...]
    submission_digest: str   # 五项绑定摘要之一：全部文件路径 + 各文件 SHA-256
    received_at: datetime    # UTC，毫秒精度
```

### 2.2 与契约的关系（引用，不是复制）

| 契约来源 | 关系 |
|---|---|
| `MAIN/contracts/schemas/submission-envelope.schema.json` | **外部请求形态**（含内联 `content_base64`）。B2 按它做入口校验，随后**消费掉**文件字节并归一化为本文形态 |
| `MAIN/contracts/openapi/b2.yaml`（`SubmissionAccepted`/`SubmissionView`） | `intake`、`state` 取值的对外呈现依据 |
| `MAIN/contracts/schemas/submission-envelope.schema.json` 的 `path` 模式 | 路径白名单的**契约下限**；B2 的白名单在此基础上更严（见 I2） |

归一化映射（**只在此处发生一次，禁止在别处再做字段改名**）：

| 外部字段 | 内部字段 | 处理 |
|---|---|---|
| `files[].path` | `EnvelopeFile.path` | 白名单校验；不合法即整体拒绝 |
| `files[].content_sha256` | `EnvelopeFile.content_sha256` | **按实际字节重算并核对**；不符即整体拒绝 |
| `files[].content_base64` | —（**不再保留**） | 解码 → 内容寻址写入 → 只留 `storage_ref` 与 `size_bytes` |
| —— | `intake` / `provenance` | 按入口（API / IMAP）填充 |

### 2.3 不变量

- **I1 不可变**：信封为 `frozen`；归一化完成后任何字段不得原地修改，改内容即新摘要、新实体。
- **I2 路径已白名单**：每个 `path` 均满足——非空、长度 ≤200 字符、段数 ≤8、每段匹配 `[A-Za-z0-9_.-]+`、无 `.`/`..` 段、非绝对路径、无反斜杠、无控制字符、无空段；**存储路径由服务端按摘要派生，与客户端输入无关**。
  - 数值上限的代码源：`autograder_b2.domain.MAX_PATH_LENGTH`（200）/ `MAX_PATH_SEGMENTS`（8）；判定函数 `is_allowed_relative_path`。
  - **实测边界（本文件冻结时验证）**：契约的 `path` 模式 `^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$` **接受 `..` 段**——`../a.py`、`../../etc/passwd`、`a/../../b.py` 均**通过** schema 校验（仅绝对路径与反斜杠被模式拦下）。因此本白名单是路径穿越**唯一有效防线**，"契约已校验"绝不等于"路径安全"；该事实由 `tests/test_contract_conformance.py` 断言，若契约模式将来收紧会使该测试失败并提示更新本条。
- **I3 摘要可信**：每个 `content_sha256` 由服务端按实际字节重算得出，不是客户端声明值的转抄。
- **I4 非空且无重名**：`files` 非空，`path` 在信封内唯一。
- **I5 上限已满足**：文件数 ≤ 题目声明的 `max_files`，总字节 ≤ `max_total_bytes`，单文件字节 ≤ 契约单文件上限。
- **I6 无字节**：信封**不携带任何文件内容字节**，因此可安全进入日志与事件（只含标识与摘要）。
- **I7 标识形态**：`submission_id`/`student_id`/`course_id`/`section_id`/`question_version_id`/`correlation_id` 为 UUID v4 小写规范串（见 [../../../MAIN/docs/api/identifiers.md](../../../MAIN/docs/api/identifiers.md)）。
- **I8 稳定摘要**：`submission_digest` 只由「路径 + 文件摘要」的规范化序列决定，与文件顺序、时间、来源无关。
- **I35 可往返（集成期补齐）**：归一化后的信封必须**逐字段可重建**——库里存的字段集合必须足以还原本类型的每一个字段，不允许"入口有、库里没有"。缺字段不是小事：`correlation_id` 是 outbox 事件的必填关联项，`provenance` 是"哪封邮件/哪个请求"的唯一追溯依据；少存一个字段，评测阶段就重建不出信封，而"重建不出来"会被误读成"这条链路没有生产级输入路径"（实测发生过：`b2.submissions` 原先既无 `correlation_id` 也无 provenance 列）。
  - 落地：迁移 `alembic/versions/20260915_0008_b2_submission_provenance.py` 增加 `correlation_id` 与按 `intake` 判形状的 provenance 列；装载器 `autograder_b2.integration.submission_loader.load_submission_envelope` 负责重建，并在装载时**重算 `submission_digest` 自校验**（不一致即拒绝装载——否则"同输入同分"会因装载出的信封与当初写入的不一致而变成谎话）。
  - `received_at` 的持久化名为 `created_at`（同一事实：接收路径以 `envelope.received_at` 同时写入该列），这不是别名，是契约对外的既有列名。

### 2.4 失败语义

校验失败**不产出部分归一化的信封**，而是抛出单一异常，携带稳定 `reason_code`：

| `reason_code` | 触发 | 对外 |
|---|---|---|
| `schema_invalid` | 契约 schema 校验失败（缺必填、未知字段、类型错误） | `422` |
| `path_not_allowed` | 路径白名单不通过（含 `..`/绝对路径/反斜杠/控制字符/超长） | `422` |
| `digest_mismatch` | 重算摘要与声明不符 | `422` |
| `empty_fileset` / `duplicate_path` | 文件集为空 / 路径重名 | `422` |
| `file_too_large` / `total_too_large` | 单文件或总量超限 | `413` |
| `too_many_files` | 文件数超限 | `422` |
| `language_mismatch` | 提交语言与 package 声明不符 | `422` |

**不落库**：以上任一失败都不得在 `b2.submission`/`b2.artifact` 留下任何行。认证/作用域失败（`401`/`403`）发生在归一化之前，且**不得用 `404` 掩盖**。

## 3. `StaticAnalysisResult`

### 3.1 签名

```python
Severity = Literal["error", "warning", "info"]
CheckStatus = Literal["ran", "not_checked"]

@dataclass(frozen=True, slots=True)
class StaticFinding:
    rule_id: str
    severity: Severity
    file: str
    line: int | None
    column: int | None
    message: str        # ≤1000，规则说明文本；绝不含源码行
    blocking: bool      # 该规则是否属阻断类

@dataclass(frozen=True, slots=True)
class CheckOutcome:
    checker: Literal["python_ast", "bash_syntax", "structural_rules"]
    status: CheckStatus
    reason: str | None  # status == "not_checked" 时必填（脱敏原因）
    findings: int

@dataclass(frozen=True, slots=True)
class StaticAnalysisResult:
    language: Literal["python", "bash"]
    checks: tuple[CheckOutcome, ...]
    findings: tuple[StaticFinding, ...]
    blocking: bool
    error_count: int
    warning_count: int
    info_count: int
    result_digest: str
```

### 3.2 与契约的关系

| 契约来源 | 关系 |
|---|---|
| `MAIN/contracts/schemas/evaluation-report.schema.json`（`static_findings`） | 报告侧形态；`severity` 取值与内部一致，`line`/`column`/`blocking` 可选 |
| `MAIN/contracts/schemas/evaluation-package.schema.json`（`language`） | 决定选哪个检查器；与提交不符即 `language_mismatch` |
| [../adr/ADR-0001-static-analysis-tooling.md](../adr/ADR-0001-static-analysis-tooling.md) | 工具选型（`ast` / `bash -n` / 结构化规则） |

### 3.3 不变量

- **I9 不执行待测代码**：检查过程**不 import、不 exec、不编译执行**学生模块；`bash -n` 只解析不执行（且以固定参数数组启动）。
- **I10 位置与说明齐备**：每条明细含 `rule_id`、`severity`、`file`、`message`；语法类错误必须带 `line`（可得时带 `column`）。
- **I11 确定**：`findings` 按 `(file, line, column, rule_id)` 稳定排序，`result_digest` 由其规范化序列计算——同输入两次运行结果与摘要完全相同。
- **I12 不泄漏源码**：`message` 只含规则说明与脱敏后的结构化细节，**不得内联源码行**。
- **I13 阻断判定只来自已执行的检查**：`blocking = any(f.severity == "error" and f.blocking for f in findings)`，且这些 finding 必须来自 `status == "ran"` 的检查。
- **I14 未检查显式化**：任何未实际执行的检查必须以 `status == "not_checked"` + `reason` 呈现；**禁止**把它折叠成"零 finding = 通过"。

### 3.4 失败语义

- 学生代码的语法错误是**发现**（`severity == "error"`），不是异常——静态阶段不因学生输入抛错。
- 检查器自身不可用（如环境无可用 bash 解释器）→ 该 `CheckOutcome.status = "not_checked"` 且 `reason` 必填；结果整体**不得**被当作干净。
- 检查器内部异常 → 转为 `not_checked` + 脱敏 `reason`，不吞掉、不伪装成通过，也不中断整条评测。
- **阻断后果**：`blocking == True` ⇒ 编排层在**进入 runner 之前**把评测置 `rejected`（`static_blocked`）；此路径**不得**产生 `running` 迁移，也**不得**以 0 分呈现。

## 4. `SecurityDecision`

### 4.1 签名

```python
SecurityStatus = Literal["found", "not_found", "not_checked"]
SecurityCategory = Literal[
    "destructive_command", "network_attempt", "privilege_escalation", "sensitive_path_read",
    "fork_bomb", "resource_limit", "archive_bomb", "sandbox_violation",
]
SecuritySeverity = Literal["high", "medium", "low"]
DetectedIn = Literal["static", "runtime"]

@dataclass(frozen=True, slots=True)
class SecurityFinding:
    finding_id: str
    category: SecurityCategory
    severity: SecuritySeverity
    detected_in: DetectedIn
    message: str              # ≤1000，脱敏
    rule_id: str | None       # detected_in == "static" 时指向静态规则

@dataclass(frozen=True, slots=True)
class SecurityDecision:
    status: SecurityStatus
    findings: tuple[SecurityFinding, ...]
    blocking: bool            # 是否阻断动态评测
    covered: tuple[str, ...]  # 实际执行过的检查项标识
    unchecked: tuple[str, ...]# 未执行的检查项标识（不可用/未到达）
    rationale: str            # 脱敏结论文本
```

### 4.2 与契约的关系

| 契约来源 | 关系 |
|---|---|
| `MAIN/contracts/schemas/evaluation-report.schema.json`（`security_conclusion`） | `status` 与 `category`/`severity`/`detected_in` 的对外形态 |
| [../../../MAIN/docs/architecture/security.md](../../../MAIN/docs/architecture/security.md) | 三态要求与安全门禁（`G-SEC-*`） |
| [../adr/ADR-0002-sandbox-execution-policy.md](../adr/ADR-0002-sandbox-execution-policy.md) | 执行期违规的归类与 `rejected` 语义 |

### 4.3 不变量（三态是本接口的全部意义）

- **I15 三态互斥且完备**：
  - `found` ⟺ 至少一条 finding，且其来自**已执行**的检查；
  - `not_found` ⟺ `unchecked` 为空（所有必需检查都实际执行过）且 `findings` 为空；
  - `not_checked` ⟺ `unchecked` 非空（有必需检查没跑）。
- **I16 未检查 ≠ 安全**：`not_checked` 在任何输出（报告、事件、日志、导出、UI）中**禁止**表述为"安全/通过/未发现问题"；只能表述为"未完成检查"并给出原因。
- **I17 阻断唯一来源**：`blocking = (status == "found" and any(f.severity == "high" for f in findings))`；`blocking == True` 时编排层置 `rejected` 并**不得**进入 runner。
- **I18 可区分 `rejected` 与 `failed`**：安全发现导致 `rejected`（安全原因）；检查不可用导致的"未检查"随运行的技术性失败（`failed`）呈现，**绝不**把安全拒绝记成技术失败，也绝不把技术失败记成安全拒绝。
- **I19 脱敏**：finding 文本不含源码、沙箱内部绝对路径、隐藏用例内容。

### 4.4 失败语义

- 静态阶段发现高危 → `blocking`，评测 `rejected`（`terminal_reason = "static_blocked"`）。
- 执行期捕获违规（联网尝试、写只读路径、提权、资源滥用）→ `detected_in = "runtime"` 的 finding，
  评测 `rejected`（`terminal_reason = "security_blocked"`）。落地细节（此前只在文档里、实现里缺失，
  见 [../phases/active/phase-02-test-instance-and-suite.md](../phases/active/phase-02-test-instance-and-suite.md)）：
  runner 以用例证据的 `failure_category = "security_blocked"` + `sandbox_violations` 表达，
  **编排层**据此把运行从 `running` 升格为 `rejected`、把违规明细写进 `b2.security_findings`
  （`detected_in = "runtime"`）、**不产生分数**（无 `evaluation_results` 行、无报告）并停止执行后续用例。
  `rejected` 没有领域事件（拒绝经 SSE 终态帧送达），因此这条路径既不发布 `evaluation.completed`，
  也不发布 `evaluation.failed`——它**不是**技术故障。
- 沙箱不可用（缺 nsjail / cgroup v2 / 非特权用户命名空间）⇒ 动态检查未执行 → 该部分进入 `unchecked`，运行终态 `failed`（`terminal_reason = "infrastructure_error"`），**绝不**退化为无沙箱执行后再报"安全"。

## 5. `run_case(package, submission) -> CaseEvidence`

一次调用执行**一个**用例；逐用例循环由编排层负责（可按用例持久化进度）。

### 5.1 签名

```python
FailureCategory = Literal[
    "timeout", "crash", "output_mismatch", "resource_limit",
    "missing_output", "import_error", "security_blocked", "infrastructure_error",
]

@dataclass(frozen=True, slots=True)
class SubmissionRunInput:
    submission_id: str
    evaluation_run_id: str
    correlation_id: str
    workspace: Path                     # 服务端生成的本用例独立工作区
    files: tuple[EnvelopeFile, ...]
    case: PackageCase                   # 本次选定的用例（来自 package）
    adapter: PackageAdapter             # kind ∈ {stdin, file, callable}
    policy: RunnerPolicy                # 沙箱策略（含其规范化摘要）

@dataclass(frozen=True, slots=True)
class CaseResource:
    memory_peak_bytes: int | None = None
    cpu_user_seconds: float | None = None
    cpu_system_seconds: float | None = None
    pids_peak: int | None = None
    wall_seconds: float | None = None
    limit_hit: str | None = None

@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    content_sha256: str | None
    size_bytes: int | None

@dataclass(frozen=True, slots=True)
class CaseArtifacts:
    expected_sha256: str | None = None
    actual_sha256: str | None = None
    created: tuple[FileChange, ...] = ()
    modified: tuple[FileChange, ...] = ()
    deleted: tuple[FileChange, ...] = ()
    truncated: bool = False

@dataclass(frozen=True, slots=True)
class CaseEvidence:
    case_id: str
    ordinal: int
    weight: float
    passed: bool
    failure_category: FailureCategory | None
    input_digest: str
    stdout_digest: str
    stderr_digest: str
    actual_output_excerpt: str | None      # ≤2000 字符，按输出上限截断
    expected_output_excerpt: str | None    # 受保护：隐藏用例在学生视图省略
    exit_code: int | None
    duration_ms: int | None
    resource_limit_hit: str | None         # 具体限制项：memory|cpu|wall|processes|output|files
    sandbox_violations: tuple[SecurityFinding, ...]
    message: str | None                    # ≤1000，脱敏
    mismatch_line: int | None = field(default=None, kw_only=True)
    missing_outputs: tuple[str, ...] = field(default=(), kw_only=True)
    mismatched_outputs: tuple[str, ...] = field(default=(), kw_only=True)
    resource: CaseResource | None = field(default=None, kw_only=True)
    artifacts: CaseArtifacts | None = field(default=None, kw_only=True)
    evidence_digest: str

def run_case(package: EvaluationPackage, submission: SubmissionRunInput) -> CaseEvidence: ...
```

> 参数命名与冻结文本一致：第一参数是 B2 内部解析后的 **evaluation package**（不可变，含全部用例与策略），第二参数是**本次用例所需的提交侧输入**（提交身份 + 工作区 + 选定用例 + 关联 ID）。用例选择因此在第二参数内显式可见，而不是靠隐式全局状态。
>
> **`CHG-0012` 补全的五个字段**（都在 `message` 之后、`evidence_digest` 之前；`kw_only` + 默认值，
> 因此既有构造点不必逐个补参数）：
>
> - `mismatch_line`：首个不一致的**行号**（1 起）。它是行号而不是内容——解释可以说"第 3 行不同"，
>   而期望输出本身仍受保护。
> - `missing_outputs` / `mismatched_outputs`：声明了却没产出、以及产出了但摘要不符的文件路径。
> - `resource`：cgroup v2 资源读数（`ADR-0010` 决策 3 的落点）。**每一项独立可空，读不到就是
>   `None`，绝不写 0**；`None` 的对象表示本用例没有真的执行（前置失败），不是"读数不可得"。
> - `artifacts`：文件级证据（`RQ-B-0-30` 验收 2）。`expected_sha256` 是受保护值，
>   学生视图按"未显式声明可见 ⇒ 不可见"的口径把它置空（见 `evaluation.report.for_student`）。

### 5.2 与契约的关系

| 契约来源 | 关系 |
|---|---|
| `MAIN/contracts/schemas/evaluation-package.schema.json` | `tests`（`case_id`/`ordinal`/`weight`/`visible_to_student`/`stdin`/`args`/`expected_*`）、`adapter`（`kind`/`args`/`comparison`/`files`/`callable`）、`security_policy` |
| `MAIN/contracts/schemas/evaluation-report.schema.json`（`case_evidence`） | 证据的对外形态与 `failure_category` 取值 |
| [../adr/ADR-0002-sandbox-execution-policy.md](../adr/ADR-0002-sandbox-execution-policy.md) | 沙箱策略、进程树清理、超限归类 |

### 5.3 不变量

- **I20 一例一证**：每个用例产出且仅产出一条证据；`ordinal` 在运行内唯一，顺序取自 package 声明。
- **I21 策略来自 package**：比较策略（`exact` / `ignore_trailing_whitespace` / `numeric_tolerance` / `ignored`）**只从 package 读取**，提交内容无法影响；`args` 以数组传递，不进入 shell。
- **I22 工作区隔离**：每个用例使用服务端生成的独立临时工作区，运行结束后清理；**不复用**上一用例或上一运行的工作区。
- **I23 截断**：`actual_output_excerpt`/`expected_output_excerpt`/`message` 按上限截断（≤2000/≤1000 字符）；完整输出只留摘要（`*_digest`），不落库原文。
- **I24 无源码**：证据对象不含任何提交文件内容。
- **I25 确定性**：`evidence_digest` 由证据字段规范化计算；**相同五项绑定摘要 ⇒ 相同证据摘要**。
  排除项只有两个，且都是"环境读数"而非"输入事实"：`duration_ms`（墙钟）与 `resource`
  （cgroup 内存/CPU/进程峰值）。纳入它们会让"同绑定摘要 ⇒ 同证据摘要"在真实 Linux 上**永远不成立**。
  反过来，`artifacts`（逐文件内容摘要）、`mismatch_line`、`missing_outputs`/`mismatched_outputs`
  **参与**摘要——它们是确定性事实，纳入后摘要对"产出文件被改动"这类差异才有区分力。
- **I26 `passed` 的充要条件**：比较策略判定相等，且无 `timeout`、无 `resource_limit_hit`、无 `sandbox_violations`，且退出码满足 package 声明。
- **I27 隐藏用例保护**：`visible_to_student == false` 的用例，其输入与 `expected_output_excerpt` 不得进入学生可见视图（报告生成时按视图裁剪，见下文第 8 节）。
- **I28 不跳过**：单用例失败或超时不中断其余用例，也不伪造其通过。

### 5.4 失败语义

| 情形 | 行为 |
|---|---|
| 超时 / 崩溃 / 输出不符 / 资源超限 / 缺输出 / import 失败 | **返回** `passed=False` + 对应 `failure_category`（学生侧结果，不抛异常） |
| 执行期安全违规 | 返回 `passed=False` + `security_blocked`，并在 `sandbox_violations` 携带 finding；编排层据 I17 置 `rejected`（从 `running` 出发、与静态阻断同为"你不该这么写"），并把 finding 写入 `b2.security_findings`（`detected_in = "runtime"`）；**该运行不产生分数、不发布 `completed`/`failed` 事件** |
| **沙箱不可用**（nsjail / cgroup v2 / 非特权用户命名空间缺失） | **抛 `SandboxUnavailableError`，绝不执行任何未隔离进程**；调用方置 `failed` + `infrastructure_error` |
| package 不可获取 / 不可解析 | 抛 `PackageUnavailableError` → `failed` + `infrastructure_error` |
| 内部异常 | 抛 `InternalEvaluationError` → `failed` + `internal_error`；异常不得被吞、不得伪装成用例失败或通过 |

超时路径必须**清理整个进程树**（独立会话/进程组 + 强制终止，并以 cgroup 进程数上限兜底），运行结束后不残留进程。

## 6. outbox 发布接口

### 6.1 签名

```python
@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: str
    event_type: str               # autograder.v1.*
    schema_version: int
    occurred_at: datetime         # UTC，毫秒精度
    correlation_id: str
    causation_id: str | None
    producer: str                 # 恒为 "B2"
    subject: str                  # NATS subject，与 event_type 同名
    payload: Mapping[str, object] # 只有标识与去标识化值

@dataclass(frozen=True, slots=True)
class DispatchReport:
    selected: int
    published: int
    failed: int
    dead_lettered: int
    last_error: str | None        # 脱敏

class OutboxWriter(Protocol):
    def enqueue(self, session, event: DomainEvent) -> None: ...

class OutboxDispatcher(Protocol):
    def dispatch(self, limit: int) -> DispatchReport: ...
```

### 6.2 与契约的关系

| 契约来源 | 关系 |
|---|---|
| `MAIN/contracts/schemas/event-envelope.schema.json` | 事件 envelope 的权威结构；`DomainEvent` 序列化后必须通过该 schema 校验 |
| `MAIN/contracts/asyncapi/events.yaml` | 5 个发布 subject 与 1 个订阅 subject、投递语义与消费者 |
| [../api/events.md](../api/events.md) | B2 的发布/订阅关系、去重键、重试与 DLQ 约定 |

### 6.3 不变量

- **I29 先写后发**：事件先与业务变更**同事务**写入 outbox，再由 dispatcher 发网络；业务已提交而 NATS 不可用时，事件仍留在 outbox 待重投——**不得**把事件发布变成业务事务的一部分，也**不得**用同步跨模块调用替代事件。
- **I30 at-least-once + 去重键 `event_id`**：允许重复投递；生产端按 `idempotency_key` 保证同一提交不重复入队 `submission.accepted`，消费端按 `event_id` 去重（inbox），重复投递为 no-op。
- **I31 顺序**：同一 `evaluation_run_id` 的 `started → progress → completed | failed` 由 B2 保证（进度帧 `sequence` 严格单调）；跨实体不假设全局顺序。
- **I32 payload 结构性禁止**：源码、参考答案、隐藏用例及其期望输出、凭据/token/cookie、内部存储路径与主机端口、超范围 PII。允许：各类 UUID、`intake`、`stage`、`sequence`、计数、分数、摘要。
- **I33 DLQ 不静默**：超过投递上限的消息进入 DLQ 表并可查询/人工重放，**不得**丢弃、也不得只写日志了事。
- **I34 不发布未持久化的结论**：`evaluation.completed` 只在报告与结果**已持久化之后**发布，消费者不可能先于报告读到事件。

### 6.4 失败语义

| 情形 | 行为 |
|---|---|
| NATS 不可用 | 业务事务不回滚；`dispatch` 返回 `failed > 0` 与脱敏 `last_error`，行保持未发布并递增尝试次数；日志与 readiness 如实反映，不吞异常。**失败日志按归因分派事件名**：连接/网络类为 `messaging.broker_unavailable`（含 `dependency=message_broker`/`status=unavailable`/`stage=dispatch`），而"我们自己的 payload/envelope 不合契约"为 `events.outbox.payload_rejected`（`error_type` 为 `PayloadRejected`/`EventEnvelopeInvalid`，**不带** dependency 字段）——两类混报会让内部缺陷去触发 MQ 告警 |
| 单事件序列化失败（不该发生） | 计入 `failed` 并保留行，绝不静默跳过 |
| 投递次数达上限 | 移入 DLQ（含 subject、脱敏原因、尝试次数） |
| 消费重复 `event_id` | inbox 去重命中 → no-op，不产生第二次业务效果 |

## 7. 横切不变量：三态不混同

`rejected`（安全/静态被拒）、`failed`（技术失败）、`completed` 且低分（学生代码不达标）在**数据模型、报告、事件、SSE、导出**中都不允许互相替代：

| 终态 | 触发（`terminal_reason`） | 报告 `state` | `score` | 允许/禁止的表述 |
|---|---|---|---|---|
| `completed` | 全部用例执行完成 | `completed` | 可为 0 | 0 分是**学生结果**，不是系统失败 |
| `rejected` | `static_blocked` / `security_blocked` | `rejected` | **不得以 0 分呈现** | 禁止声称"运行成功/执行完成" |
| `failed` | `infrastructure_error` / `internal_error` | `failed` | **不得以 0 分呈现** | 系统故障**绝不算作学生的问题** |

- 未完成/未开始的运行，其分数在对外视图中为 `null` 并附带 `state`，**不渲染为 0**。
- 安全结论三态同规则：`not_checked` 不得表述为安全（I16）。
- `rejected` **没有**对应的领域事件（拒绝只对提交会话有意义）；终态经 SSE 的 `rejected` 帧送达。`completed`/`failed` 才有领域事件。

## 8. 禁止事项

1. **禁止字段别名**：同一语义只允许一个名字（如 `content_sha256` 不得在别处以 `sha256`/`digest` 出现）。
2. **禁止双写**：同一语义只写一个字段、一张表；不接受"两处都写以便过渡"。
3. **禁止隐藏开关**：不引入"环境变量切换行为"式的静默开关；沙箱缺失时**拒绝执行**，不降级。
4. **禁止改动公共契约**：`MAIN/contracts/**` 与 `contracts.lock` 只读；发现实现与契约不一致，按 [../../../MAIN/docs/contracts/change-protocol.md](../../../MAIN/docs/contracts/change-protocol.md) 提交 CHG，由 MAIN 判定哪边为准。
5. **禁止手抄契约字段表**：需要字段定义时引用契约文件与行号，不复制。
6. **禁止把受保护内容放进本层**：隐藏用例的输入与期望输出不得进入报告的学生视图、事件、日志与导出（I27）。

## 9. 变更规程

1. 内部接口变更**先改本文**（签名 + 不变量 + 失败语义），再改实现，再改测试。
2. 变更同步 [../collaboration/interfaces.md](../collaboration/interfaces.md) 的冻结清单与 [../collaboration/workpackages.md](../collaboration/workpackages.md) 的并行前提表。
3. 若变更源于契约不一致：**不**顺着实现改契约、**不**偷偷改实现对齐，按 CHG 流程走。
4. 每新增一个内部接口，必须同时给出：字段清单、不变量编号、失败语义、契约来源（引用）四件套。

## 10. 关联

- 需求：[../requirements/scope.md](../requirements/scope.md)、[../requirements/traceability.md](../requirements/traceability.md)
- 架构：[overview.md](overview.md)、[components.md](components.md)、[models.md](models.md)、[boundaries.md](boundaries.md)
- 决策：[../adr/ADR-0001-static-analysis-tooling.md](../adr/ADR-0001-static-analysis-tooling.md)、[../adr/ADR-0002-sandbox-execution-policy.md](../adr/ADR-0002-sandbox-execution-policy.md)、[../adr/ADR-0003-content-addressed-submission-storage.md](../adr/ADR-0003-content-addressed-submission-storage.md)
- 契约：[../../../MAIN/contracts/openapi/b2.yaml](../../../MAIN/contracts/openapi/b2.yaml)、[../../../MAIN/contracts/asyncapi/events.yaml](../../../MAIN/contracts/asyncapi/events.yaml)、`../../../MAIN/contracts/schemas/`
- 标识与绑定摘要：[../../../MAIN/docs/api/identifiers.md](../../../MAIN/docs/api/identifiers.md)
- 核验工具：[../../verify/contract_lock_check.py](../../verify/contract_lock_check.py)

## 11. 五项绑定摘要的计算归属（集成期新增）

> 本节**追加在文末**而不插在第 6 节之后：插队需要给第 7–10 节重新编号，而正文与代码 docstring 里已有"§8.3"这类按编号的引用（例如 `security/decision.py` 引用的正是第 8 节第 3 条"禁止隐藏开关"）。重编号会静默改掉这些引用的含义，因此宁可新增一节也不动既有编号。

`ADR-0005` 规定「五项绑定摘要相同 ⇒ 同分数、同报告摘要」。五项里有两项**没有单一实现方拥有全部素材**，只能在把 B3/runner/部署配置接在一起的地方算：

| 摘要 | 素材 | 计算归属 |
|---|---|---|
| `submission_digest` | 文件路径 + 各文件 SHA-256 | 接收层（I8） |
| `package_digest` | package 自身 | B3 契约字段，B2 只读取 |
| `runner_policy_digest` | package 声明的沙箱策略（全部字段） | runner（`runner.policy.policy_digest`） |
| `question_version_binding` | 题目版本标识 × package 摘要 | **组合层**（`integration.bindings`） |
| `environment_digest` | 语言 × 解释器身份 × 部署声明的环境标签 | **组合层**（`integration.bindings`） |

公式（权威实现在 `src/autograder_b2/integration/bindings.py`；规范化方式与 `policy_digest` 同构——排序键 JSON + 紧凑分隔符，保证单射）：

```text
question_version_binding = sha256(json{schema:"b2.question-version-binding.v1",
                                      question_version_id:<uuid>, package_digest:<hex64>})
environment_digest      = sha256(json{schema:"b2.environment.v1", language:<python|bash>,
                                      interpreter:<策略里该语言的绝对路径>,
                                      environment_tag:<部署声明>})
```

- **为什么题目版本要连 package 摘要一起绑**：只绑版本号时，"同一版本号 ⇒ 同分"会依赖"上游永远不会返回别的包"这一未经验证的假设；取包适配器因此还显式校验响应里的 `question_version_id` 与请求一致，不一致即按不可获取处理（宁可不评测）。
- **诚实边界（不声称超过事实）**：`environment_tag` 由部署配置声明（`B2_ENVIRONMENT_TAG`），**不是**运行时观测值——取包阶段不执行沙箱，读不到镜像/rootfs 的真实内容摘要。未配置时为 `unset`，此时"仅 rootfs 内容不同、解释器路径与语言相同"的两个环境**不会被区分**。要让环境身份可信，部署必须把镜像摘要写进该配置。
- **不变量**：两项摘要对同一输入恒定、对不同输入不同（含仅环境标签不同）；`environment_digest` 在输入不满足"POSIX 绝对路径解释器 / 已知语言 / 受限字符集标签"时**拒绝计算**，而不是回退到某个默认值。

## 12. 评语轨（`RQ-T-20` / `ADR-0009`）与 AI 评分参考（`RQ-T-30` / `ADR-0014`）：白名单是接口，不是习惯

评语轨与前五项冻结接口的关系是**单向且只读**：它读已经落库的证据，别人不读它。

```python
# autograder_b2.commentary.scope
INPUT_SCOPE: tuple[str, ...]          # 允许读取的字段清单（随 provenance 返回）
def collect_evidence(connection, evaluation_run_id) -> CommentaryEvidence: ...
    # CommentaryEvidence.case_weight_total：各用例权重之和 = 参考分的满分（从 SQL 行求和）
def evidence_paths(payload) -> tuple[str, ...]: ...   # 载荷里实际出现的字段路径（可复算）

# autograder_b2.commentary.parsing
def parse_generation(raw: str) -> Generation: ...
    # Generation(text, advisory: AdvisoryProposal | None)；形状不符 ⇒ CommentaryResponseError

# autograder_b2.commentary.service
class CommentaryService:
    configured: bool                   # False ⇒ 没有 provider 对象，也就没有出网调用点
    def load(self, evaluation_run_id) -> CommentaryRecord | None: ...
    def generate(self, evaluation_run_id, *, run_state: str) -> CommentaryOutcome: ...
def resolve_advisory(proposal, *, max_score) -> AdvisoryScore | None: ...
    # 越界 ⇒ AdvisoryScoreRejected（不截断）；满分只从这里进数据
```

四条边界（每条都有测试，见 [../testing/cases.md](../testing/cases.md)）：

1. **字段级白名单**：取证只用显式列清单构造的 `SELECT`（`RUN_COLUMNS`/`CASE_COLUMNS`/
   `STATIC_COLUMNS`/`SECURITY_COLUMNS`/`SCORE_COLUMNS`），不 `SELECT *`。`case_evidence` 的两个摘录列与
   三个 `message` 列**根本不在查询里**——它们在证据行里，但生成路径读不到。
   `tests/test_commentary_track.py` 断言 `evidence_paths(...) == INPUT_SCOPE`。
   **参考分用的是同一份白名单**（`ADR-0014` 决策 4：不因为它是分数而放宽）。
2. **不可达**：`FORBIDDEN_EVIDENCE_MODULES` 列出所有能拿到源码/期望输出的模块
   （CAS、信封装载、package、工作区物化、`run_case`）；测试断言评语包的 import 闭包与它无交集。
3. **不进分数轨**：评语与参考分只写 `b2.evaluation_commentaries`；清空该表后重放同一提交，
   分数与逐项证据必须逐字相同（有测试）。`advisory` 形状的列也只存在于这张表（结构性断言）。
4. **默认关闭**：没有 `B2_COMMENTARY_*` 时 `GET` → `status: unavailable`（200），
   `POST` → `409` 并指名缺什么，且**没有出网调用**。

**参考分的三条接口级纪律**（`RQ-T-30`）：① `max_score` 由 `case_weight_total` 填入，
模型给同名键即 `failed`；② `score` 越界即 `failed`（库级 CHECK 让越界值写不进来），**不截断**；
③ 响应协议是 JSON 对象（`prompt_version = b2-commentary/2`），形状不符即 `failed`，
不退化成"把整段响应当评语"。

| 端点 | 语义 | 未配置 provider 时 |
|---|---|---|
| `GET /api/v1/evaluation-runs/{id}/commentary` | 读评语与可空参考分 | `200` + `status: unavailable` |
| `POST /api/v1/evaluation-runs/{id}/commentary` | 生成/重新生成评语**与参考分**（教师/助教/管理员） | `409`，detail 指名缺哪些配置项 |

**两个读不到的证据面（如实记录，不假装读过）**：契约的白名单还包括"三态安全结论"与"题目的公开
元数据（语言/适配器类型/比较策略）"。前者已由迁移 0011 落库（`b2.evaluation_runs.security_status`，
安全判定阶段原样写入，含 `not_checked`）；后者 B2 **从不落库**，因此**不在** `input_scope` 里——
白名单是**许可**而不是义务，少读是严格更安全的。要补上它需要一个题目元数据快照（新列 + 端口暴露
adapter kind），属需决策的改动，已上报。

## 13. 沙箱自证（`ADR-0010` 决策 2）：`not_checked` 不得写成"安全"

```python
# autograder_b2.canary
def run_canary(policy=None, *, launcher=None, work_root=None) -> CanaryReport: ...
def main(argv=None) -> int: ...       # python -m autograder_b2.canary
```

- **六项金丝雀作业**：读宿主文件、联网（TCP+DNS）、提权（euid/`setuid(0)`）、写只读根、
  超内存（申请 2× 上限）、超进程数（创建 3× 上限）。每项**必须失败**。
- **三态**（与安全结论同一套语义）：`found`（逃逸成功＝发现风险）/ `not_found`（被挡住）/
  `not_checked`（未执行或结果无法归因）。**Windows 上整表都是 `not_checked`**——本机没有 nsjail，
  沙箱不可用时不创建任何进程。退出码 `0`/`2`/`3` 区分"全挡住/发现风险/有未检查"，
  因此"没跑"不可能被 CI 当成通过。
- **复用生产执行路径**：金丝雀构造 `ExecutionPlan` 后调用 `runner.run_case._execute_case`
  （同一份 nsjail 配置渲染、同一份 `build_argv`、同一套进程树清理与有界输出采集），
  不另写一套"看起来很像"的启动逻辑。
- **它证明不了什么**：证明范围是"当前内核 + 当前 nsjail 配置下这些特定逃逸尝试失败"，
  不是形式化保证（内核漏洞、配置漂移、容器运行时差异都不在内）。

## 14. 参考解答验证轨（`CHG-0019` + `CHG-0023` / `ADR-0015`）：执行复用、记录外置、不落库

它是 `ADR-0015` 的"B2 执行、B3 记录、教师一键触发"，**不新增任何基础设施**：取参考解答与记录存证
共用 B2 早已用来取包的 B3 服务通道（同一个 `B3Endpoint` 对象），执行走与评测学生提交**同一条**
`run_case` 路径。

```python
# autograder_b2.reference.b3
class B3VerificationClient:
    def fetch_reference_answer(self, question_version_id) -> ReferenceAnswerView: ...
    def record_attestation(self, *, question_version_id, content_digest,
                           cases_total, cases_passed, verified_at) -> str | None: ...
# ReferenceAnswerView.reference_answer 是受保护正文（repr=False）；失败语义：
#   404 → 版本不存在；409 → 版本没有参考解答；其余 → 依赖不可用（对外 503）

# autograder_b2.reference.verification
class ReferenceVerificationService:
    gate: DynamicExecutionGate          # 与 EvaluationOrchestrator 共用同一个闸门对象
    b3_endpoint: B3Endpoint             # 与取包共用同一个通道对象
    def verify(self, question_version_id) -> ReferenceVerificationResult: ...
# ReferenceVerificationResult.as_contract() 的键集合 == 契约 ReferenceVerification 的九个 required
```

四条边界（每条都有测试，见 [../testing/cases.md](../testing/cases.md) 的 T-REF-*）：

1. **执行只有一条路径**：逐用例经注入的 `CaseRunner` 端口（生产装配 `RunnerCaseRunner` →
   `runner.run_case`），本包**不 import** 任何能起进程的模块，因此 `runner/policy.py` 的
   seccomp / cgroup / 禁网策略无法被绕过。另起一条"执行参考解"的路径等于在发布门禁上开一个
   不受沙箱约束的执行面。
2. **不落库**：服务层连引擎都拿不到；一次全通过的验证跑完后 `b2` 全部业务表零行、零事件，
   工作区用完即删。存证是 B3 的记录（`attestation_id`），B2 不留第二份事实源。
3. **只有真通过才留痕**：`cases_passed == cases_total` 才调 B3 记录存证；未通过时一次调用都不发。
   记录失败 ⇒ `attestation_id: null` 且逐用例结论照旧返回；上游声称 `201` 却读不出 id ⇒ `503`
   （`null` 的契约含义是"没有记录"，不是"记了但读不回来"）。
4. **不返回受保护内容**：响应只有计数、摘要与逐用例 `passed`/`failure_category`；参考解答正文、
   隐藏用例输入与期望输出都没有槽位（响应键集合由契约锁定，并由测试逐字断言）。

**两条已知边界**（详见 [../api/contract.md](../api/contract.md) §1f 的缺口 ⑦/⑧）：课程维度的授权
判定做不了（题目→课程是 B3 的事实）；"验证草稿 → 存证 → 发布"在真实 B3 上仍走不通（草稿取不到
evaluation package，而存证只接受草稿）。
