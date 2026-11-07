"""编排层依赖的**窄协议**（端口）+ 编排需要知道的只读输入视图。

为什么是协议而不是直接 import 实现：`SecurityDecider`（安全判定）与 `CaseRunner`（逐用例沙箱执行）
的实现分属并行工作项，编排层在它们落地之前就必须可写、可测；`PackageSource`（B3 侧 evaluation
package 获取）与 `WorkspaceProvider`（把提交文件物化成服务端工作区）是**组合缝**——编排只声明
"我需要什么形态的输入"，生产装配由集成方注入。

执行端口的形态刻意压到最窄，且按 package 的 `execution.mode` 分派（设计基线
`MAIN/docs/architecture/unix-homework-evaluation.md` §3/§6）：

- `SecurityDecider.decide(*, evaluation_run_id, files, language) -> SecurityDecision`：给定**文件内容**与
  运行标识，给出三态安全结论（`not_checked` 不得被折叠成"安全"）；
- `CaseRunner.run_case(package, submission) -> CaseEvidence`：与冻结签名
  `run_case(package, submission) -> CaseEvidence` 同形，一次调用执行**一个**用例（`per_case` 模式）；
  沙箱不可用时必须抛 `SandboxUnavailableError`，绝不退化为裸进程执行；
- `CaseRunner.run_cases(package, batch) -> tuple[CaseEvidence, ...]`：**批量入口**，一次调用 =
  一次沙箱执行（`single_run` / `ssh_session`）；`batch` 是 `domain.evidence.BatchRunInput`（runner 拥有，
  本模块只引用，不重定义），`per_case`/`email_delivery` 不得走这条入口；
- `DeliveryVerifier.verify(package, submission) -> tuple[CaseEvidence, ...]`：`email_delivery` 专用，
  不启动沙箱；它把"这次提交是否真的收到了合格邮件"变成与 package 用例一一对应的证据。

**只使用、不修改** `autograder_b2.domain.*` 的冻结类型：本模块只描述"编排需要知道的 package 侧面"
（用例清单、比较策略、四项绑定摘要、`execution` 声明视图与若干不透明句柄），不复制 package 的完整
形态，也不重定义任何冻结类型。取值集合（比较策略、语言、执行模式）与
`MAIN/contracts/schemas/evaluation-package.schema.json` / `common.yaml` 同源，测试逐字比对，
改契约而代码没跟上时测试必须失败。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from autograder_b2.domain.clock import is_digest
from autograder_b2.domain.envelope import EnvelopeFile, SubmissionEnvelope
from autograder_b2.domain.evidence import CaseEvidence, SubmissionRunInput
from autograder_b2.domain.security import SecurityDecision

if TYPE_CHECKING:  # pragma: no cover - 只用于注解：BatchRunInput 由 runner 工作项定义
    from autograder_b2.domain.evidence import BatchRunInput

#: `adapter.comparison.stdout` 取值域（package 契约）。
STDOUT_STRATEGIES = ("exact", "ignore_trailing_whitespace", "numeric_tolerance", "ignored")
#: `adapter.comparison.stderr` 取值域。
STDERR_STRATEGIES = ("exact", "ignore_trailing_whitespace", "ignored")
#: 受评测语言（`RQ-B-0-10`）。
LANGUAGES = ("python", "bash")
#: `execution.mode` 取值域（契约 `ExecutionMode`；缺省 = `per_case`）。
EXECUTION_MODES = ("per_case", "single_run", "ssh_session", "email_delivery")


class PortContractError(ValueError):
    """端口输入不满足本模块声明的前置条件（在调用实现之前就拒绝）。"""


def _require_uuid(name: str, value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PortContractError(f"{name} 必须是 UUID 形态的字符串") from exc


def _require_digest(name: str, value: object) -> str:
    if not is_digest(value):
        raise PortContractError(f"{name} 必须是 64 位小写十六进制摘要")
    return str(value)


@dataclass(frozen=True, slots=True)
class ComparisonPolicy:
    """package 声明的比较策略（判分依据之一，学生代码无法影响）。"""

    stdout: str
    stderr: str = "exact"
    numeric_tolerance: float | None = None

    def __post_init__(self) -> None:
        if self.stdout not in STDOUT_STRATEGIES:
            raise PortContractError(f"未知的 stdout 比较策略：{self.stdout!r}")
        if self.stderr not in STDERR_STRATEGIES:
            raise PortContractError(f"未知的 stderr 比较策略：{self.stderr!r}")
        if self.numeric_tolerance is not None and (
            isinstance(self.numeric_tolerance, bool) or self.numeric_tolerance < 0
        ):
            raise PortContractError("numeric_tolerance 必须是非负数或 None")


@dataclass(frozen=True, slots=True)
class ExecutionView:
    """package `execution` 声明中**编排需要知道的侧面**（设计基线 §2/§3/§6）。

    为什么拆成"公开字段 + 一个不透明句柄"：

    - `mode` 决定编排怎么走（逐用例 / 一次批量 / 投递核验），是编排**必须**解释的唯一字段；
      `segment_delimiter` 只用于让编排层在进度/日志里描述分段语义（切分本身在 runner 内），
      也允许为空（`single_run` 只声明 `file_checks` 时不需要分隔行）；
    - SSH 口令等**受保护配置**不进这里的公开字段：它们只随 `handle`（解析后的 execution 声明）
      原样交给 runner/verifier，绝不复制到日志、事件、报告或本视图的任何可打印字段；
    - `execution_digest` 是 execution 声明的规范化 JSON 摘要，**只作 B2 内部诊断值**：execution 是
      package 内容的一部分、已由 `package_digest` 绑定，因此它**不**参与五项绑定摘要、不进报告。

    `handle` 与 `PackageView` 的其它句柄同纪律：编排不解释它的结构，原样放行。
    """

    mode: str = "per_case"
    execution_digest: str | None = None
    segment_delimiter: str | None = None
    handle: object = None

    def __post_init__(self) -> None:
        if self.mode not in EXECUTION_MODES:
            raise PortContractError(f"未知执行模式：{self.mode!r}")
        if self.execution_digest is not None:
            _require_digest("execution_digest", self.execution_digest)
        if self.segment_delimiter is not None:
            _require_segment_delimiter(self.segment_delimiter)


def _require_segment_delimiter(value: object) -> str:
    """分隔行形态（与 package 契约同一条判据）：8..120 个可打印 ASCII，首尾不是空白。

    独占一行的固定标记是**判分依据**：一行被误当作分隔行会让整个分段错位，因此这里有形态下限。
    """
    if not isinstance(value, str):
        raise PortContractError("segment_delimiter 必须是字符串")
    if not 8 <= len(value) <= 120:
        raise PortContractError("segment_delimiter 长度必须在 8..120 之间")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise PortContractError("segment_delimiter 必须是可打印 ASCII")
    if value != value.strip():
        raise PortContractError("segment_delimiter 首尾不得是空白（分隔行必须独占一整行）")
    return value


@dataclass(frozen=True, slots=True)
class CaseDescriptor:
    """一个用例的**无内容**描述：编排只需要标识、序、权重与可见性。

    `handle` 是**不透明句柄**（生产装配里就是 package 自己的用例对象）：编排把它原样放进
    `SubmissionRunInput.case` 交给 runner，既不需要、也不允许解释它的结构。
    """

    case_id: str
    ordinal: int
    weight: float
    visible_to_student: bool = False
    handle: object = None

    def __post_init__(self) -> None:
        _require_uuid("case_id", self.case_id)
        if self.ordinal < 0:
            raise PortContractError("ordinal 必须非负")
        if isinstance(self.weight, bool) or not isinstance(self.weight, (int, float)):
            raise PortContractError("weight 必须是数字")
        if self.weight <= 0:
            raise PortContractError("weight 必须为正（0 权重的用例会让总分与逐项之和对不上）")


@dataclass(frozen=True, slots=True)
class PackageView:
    """编排层眼中的 evaluation package（不含隐藏用例输入与期望输出）。

    `handle` / `adapter_handle` / `policy_handle` 都是**不透明句柄**：原样交给 `CaseRunner`
    （填进 `SubmissionRunInput` 的 `case`/`adapter`/`policy`），编排层不解释它们的结构——这是
    "B2 能在不理解题目语义的前提下执行 package"的实现形态（对比策略等判定依据由 runner 从 package
    自己取，见 I21）。

    `execution` 是 package 的 `execution` 声明视图（缺省 = `per_case`）：它决定编排走逐用例、
    一次批量还是投递核验。**行为只由它决定**，从不从标题/标签/路径推断。
    """

    package_digest: str
    language: str
    cases: tuple[CaseDescriptor, ...]
    runner_policy_digest: str
    environment_digest: str
    comparison: ComparisonPolicy = field(default_factory=lambda: ComparisonPolicy("exact"))
    question_version_binding: str | None = None
    interpreter_version: str | None = None
    execution: ExecutionView = field(default_factory=ExecutionView)
    handle: object = None
    adapter_handle: object = None
    policy_handle: object = None

    def __post_init__(self) -> None:
        _require_digest("package_digest", self.package_digest)
        _require_digest("runner_policy_digest", self.runner_policy_digest)
        _require_digest("environment_digest", self.environment_digest)
        if self.question_version_binding is not None:
            _require_digest("question_version_binding", self.question_version_binding)
        if self.language not in LANGUAGES:
            raise PortContractError(f"未知语言：{self.language!r}")
        if not self.cases:
            raise PortContractError("package 必须至少声明一个用例")
        ordinals = [case.ordinal for case in self.cases]
        if len(set(ordinals)) != len(ordinals):
            raise PortContractError("用例 ordinal 必须唯一（evidence 按 ordinal 追溯）")
        identities = [case.case_id for case in self.cases]
        if len(set(identities)) != len(identities):
            raise PortContractError("用例 case_id 必须唯一")

    @property
    def cases_total(self) -> int:
        return len(self.cases)

    def ordered_cases(self) -> tuple[CaseDescriptor, ...]:
        """按 `ordinal` 升序（判分与报告的顺序必须与 package 声明一致且确定）。"""
        return tuple(sorted(self.cases, key=lambda case: case.ordinal))


class PackageSource(Protocol):
    """获取（并解析）一次评测所用的 evaluation package。

    不可获取或不可解析 ⇒ 抛 `PackageUnavailableError`（技术原因，不是学生的错）。

    **接线约定（由 runner 实现者确认）**：`PackageView` 的三个不透明句柄必须来自
    `autograder_b2.runner.package.parse_evaluation_package(...)` 的解析结果——
    `handle` = 解析后的 package、`CaseDescriptor.handle` = `package.tests[i]`、
    `adapter_handle` = `package.adapter`、`policy_handle` = `package.security_policy`。
    runner 对 `submission.policy`/`submission.case`/`submission.adapter` 做**严格相等校验**，
    因此编排层**不得**自造（哪怕更严的）策略副本：需要收紧策略语义属于 runner 的变更，不在编排层实现。
    """

    def fetch(self, *, evaluation_run_id: str, question_version_id: str) -> PackageView: ...


class WorkspaceProvider(Protocol):
    """把提交文件物化成服务端生成的独立工作区（静态检查与 runner 的唯一 IO 入口）。

    **两种粒度**：

    - `batch=False`：`case_id=None` 是运行级工作区（静态检查与安全判定读它），非空是该用例专用的
      沙箱工作区——`per_case` 模式**每个用例一份**新工作区（冻结签名把 `workspace` 定义为
      "本用例独立工作区"，绝不复用上一个用例的目录）；
    - `batch=True`：`single_run` / `ssh_session` 的一次批量执行只准备**一份**工作区（`case_id`
      必须为 `None`）。批量工作区与用例工作区同权限口径（沙箱用户可读写、父目录私有），
      且**不得**被多个批量执行复用：每次 `prepare` 都建新目录。

    单文件提交执行其声明的文件；多文件提交使用 `main.py`（python）/`main.sh`（bash）入口，
    callable 适配器的 harness 文件名为 `__b2_harness__.py`——命名只在 runner 侧定义一次，
    本层只负责提供干净的工作区，不另立一套名字。
    """

    def prepare(
        self,
        *,
        submission_id: str,
        evaluation_run_id: str,
        files: Sequence[EnvelopeFile],
        case_id: str | None = None,
        batch: bool = False,
    ) -> Path: ...

    def release(self, workspace: Path) -> None: ...


class SecurityDecider(Protocol):
    """进入 runner **之前**的安全判定端口（三态结论，见 `domain.security`）。"""

    def decide(
        self, *, evaluation_run_id: str, files: Mapping[str, str], language: str
    ) -> SecurityDecision: ...


class DeliveryUnavailableError(RuntimeError):
    """投递核验**依赖不可用**（收件链路心跳过期/未配置、验证记录读取失败）。

    它与"学生没收到邮件"是两件事：前者是平台故障（编排层落 `failed + infrastructure_error`），
    后者才是学生侧 `missing_output`。端口用独立异常表达，是因为调用方**必须**能区分它们，
    而不是从消息文本里猜。
    """


class DeliveryVerificationDefect(RuntimeError):
    """投递核验内部缺陷（证据形状与 package 声明不一致、规则引擎装配非法）。

    缺陷不是学生失败也不是依赖故障：编排层落 `failed + internal_error`，绝不折算成"没收到邮件"。
    """


class CaseRunner(Protocol):
    """沙箱执行端口：`run_case` 是一次一个用例（`per_case`），`run_cases` 是一次批量执行。

    `run_cases` 的 `batch` 是 `autograder_b2.domain.evidence.BatchRunInput`（由 runner 拥有；
    本模块只引用它，不重定义字段）。`per_case` 与 `email_delivery` **不得**走批量入口：
    前者需要逐用例独立工作区，后者根本不执行代码。
    """

    def run_case(self, package: object, submission: SubmissionRunInput) -> CaseEvidence: ...

    def run_cases(
        self, package: object, batch: BatchRunInput
    ) -> tuple[CaseEvidence, ...]: ...


class DeliveryVerifier(Protocol):
    """`email_delivery` 的核验端口：把"本次提交是否真的收到了合格邮件"变成逐用例证据。

    它**不启动沙箱**：事实来自真实 IMAP 收件链路落下的验证记录；逐项判定与参考答案自检
    共用同一个纯规则引擎（由集成方注入）。返回的证据必须与 package 声明的用例一一对应
    （同一 `case_id`/`ordinal`，顺序按 ordinal），否则编排层按内部缺陷处理。

    失败语义见 `DeliveryUnavailableError`（依赖）与 `DeliveryVerificationDefect`（缺陷）；
    学生侧事实不符**返回**证据（`missing_output`/`output_mismatch`），不抛异常。
    """

    def verify(
        self, package: object, submission: SubmissionEnvelope
    ) -> tuple[CaseEvidence, ...]: ...
