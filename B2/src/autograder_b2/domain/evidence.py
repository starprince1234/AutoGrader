"""`run_case(package, submission) -> CaseEvidence`：逐用例执行的冻结签名与其证据形态。

签名、不变量（I20–I28）与失败语义见
[docs/architecture/interfaces.md](../../docs/architecture/interfaces.md) 第 5 节。本模块落地：
`SubmissionRunInput` / `CaseEvidence` / `CaseResource` / `CaseArtifacts` / `CaseRunner` 协议 / 三种失败异常。

关键不可协商项：

- 一次调用执行**一个**用例；逐用例循环由编排层负责；
- 学生侧失败（超时/崩溃/输出不符/资源超限）**返回**证据而非抛异常，且不跳过其余用例；
- **沙箱不可用必须抛 `SandboxUnavailableError`——绝不执行任何未隔离进程**。

`CaseResource` 的每个字段都可空，这条设计不是"允许偷懒"而是**诚实边界**（CHG-0012 / ADR-0010 决策 3）：
沙箱可能**能强制**限额却**无法回读**峰值，而"强制了但没测量"与"测出来很小"是两件不同的事——
用 0 表示"没测量"会被读成"没占用"。因此读不到就是 `None`，永远不写 0。

包/策略/用例的实现在 `WP-B2-C`（runner 包）内定义；此处只以字符串注解引用，不在本模块实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from autograder_b2.domain.envelope import EnvelopeFile
from autograder_b2.domain.security import SecurityFinding

if TYPE_CHECKING:  # pragma: no cover - 仅类型期可见，运行期不导入（实现属 WP-B2-C）
    from autograder_b2.runner.package import EvaluationPackage, PackageAdapter, PackageCase
    from autograder_b2.runner.policy import RunnerPolicy

FailureCategory = Literal[
    "timeout",
    "crash",
    "output_mismatch",
    "resource_limit",
    "missing_output",
    "import_error",
    "security_blocked",
    "infrastructure_error",
]


class SandboxUnavailableError(RuntimeError):
    """沙箱前置条件不满足（nsjail / cgroup v2 / 非特权用户命名空间缺失）。

    抛出本异常是"拒绝执行"的唯一合法表达；**禁止**捕获它之后改用未隔离进程执行。
    """


class PackageUnavailableError(RuntimeError):
    """evaluation package 不可获取或不可解析（B3 侧依赖失败，技术原因）。

    两个附加事实都**只在真的收到响应时**有值（网络不可达、超时、响应形状不符时都是 `None`）：

    - `status_code`：上游返回的 HTTP 状态码。`404`（版本不存在）与 `409`（版本存在但没有包——
      草稿即如此）对调用方是**两件不同的事**，而 `CHG-0019` 的参考解答验证端点必须把它们分成
      `404` 与 `422`；只靠消息文本区分会迫使调用方去解析字符串。
    - `upstream_detail`：**Problem Details 里的 `detail`**（`common.yaml` 固定的形状），已按长度
      上限截取。它存在是因为 `409` 的**原因推不出来**（草稿尚未发布 / 摘要与发布时记录的不符 /
      受保护内容被改过），而这几种情形要教师做的事完全不同。它与异常**消息**是分开的：
      消息照旧只报状态码（不把上游正文塞进日志），只有需要给调用者一个可行动原因的那条路径
      （参考解答验证的 `422`）才转述它。

    **不是在放宽"不泄露内部信息"**：`detail` 来自本项目自己的 B3 模块、是写给人看的一句话，
    而主机名/端口/URL 依旧不出现在任何被带出的字段里。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        upstream_detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_detail = upstream_detail


class InternalEvaluationError(RuntimeError):
    """评测内部异常；不得被吞、不得伪装成用例失败或通过。"""


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
    """一次用例执行的资源读数（契约 `case_resource`；每个字段独立可空）。

    `wall_seconds` 由 B2 自己测量（沙箱运行时长），因此**总**有值；其余三项来自 cgroup v2，
    读不到即 `None`。`limit_hit` 只承载**契约枚举内**的限制项（`memory`/`cpu`/`pids`/`wall`/`output`）：
    B2 的"产出文件数超限"是**执行后计数**判定，不是"某个被强制执行的限额结束了本用例"，
    契约枚举里也没有 `files`，因此它在这里是 `None`（该事实由 `resource_limit_hit` 与
    `explanation` 如实表达）。
    """

    memory_peak_bytes: int | None = None
    cpu_user_seconds: float | None = None
    cpu_system_seconds: float | None = None
    pids_peak: int | None = None
    wall_seconds: float | None = None
    limit_hit: str | None = None


@dataclass(frozen=True, slots=True)
class FileChange:
    """工作区内一个文件的变化（契约 `file_change_list` 的条目）。"""

    path: str                            # 工作区相对路径（POSIX 分隔符）
    content_sha256: str | None
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class CaseArtifacts:
    """一次用例的文件级结果（契约 `case_artifacts`）。

    `expected_sha256` 是**受保护**值：摘要不是内容，但它是一个可检验的预言机（猜到文件内容即可验证），
    因此只在可见用例上出现（`evaluation.report.for_student` 负责裁剪）。

    `created/modified/deleted` 的清单本身**受契约上限约束**（每类 200 条）；`truncated` 是 B2 内部字段，
    用于让 `explanation` 能如实说明"清单被截断"，且**不进契约**（报告里没有这个键）。
    """

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
    #: 首个不一致行号（1 起；stdout 比较得出的行号）。`None` 表示比较未得出行号
    #: （比较通过、策略为 `ignored`、或差异不在行内——例如产出文件摘要不符）。
    mismatch_line: int | None = field(default=None, kw_only=True)
    #: 声明了却未产出的文件（工作区相对路径，已排序）。
    missing_outputs: tuple[str, ...] = field(default=(), kw_only=True)
    #: 已产出但摘要与声明不符的文件（工作区相对路径，已排序）。
    mismatched_outputs: tuple[str, ...] = field(default=(), kw_only=True)
    #: 资源读数；`None` 表示本用例**没有真的在沙箱里跑过**（前置失败），而不是"读数不可得"。
    resource: CaseResource | None = field(default=None, kw_only=True)
    #: 文件级结果；`None` 的含义同上。
    artifacts: CaseArtifacts | None = field(default=None, kw_only=True)
    #: 最后声明：`kw_only` 字段（上面 5 个）不参与"默认值必须在无默认值字段之后"的校验，
    #: 因此既保留了 `evidence_digest` 必填，又让证据对象保持声明顺序与文档逐字一致。
    evidence_digest: str


class CaseRunner(Protocol):
    """冻结签名的协议形态：调用一次即执行一个用例。

    编排层只依赖本协议；生产装配只构造 nsjail 实现（无环境变量开关切换执行后端）。
    """

    def run_case(self, package: EvaluationPackage, submission: SubmissionRunInput) -> CaseEvidence: ...
