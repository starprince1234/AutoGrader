"""参考解答验证的编排（`CHG-0019` / `ADR-0015`）：取参考解答 → 取评测包 → 沙箱逐用例 → 存证。

## 它做什么、不做什么

`ADR-0015` 决策 1 把职责切得很窄：B2 **不回答"这道题好不好"**，只回答"按这道题自己声明的适配器与
比较策略，参考答案是否通过"。因此本模块：

- **执行**走 `runner.run_case`（注入的 `CaseRunner` 端口）——生产装配就是 `RunnerCaseRunner`，
  与评测学生提交是**同一条**执行路径（`runner/policy.py` 的 seccomp / cgroup / 禁网策略照旧生效）。
  另写一条"执行参考解"的路径会绕开那份策略，那等于在发布门禁上开一个不受沙箱约束的执行面。
- **不落库**：本模块不碰引擎、不写任何表、不发任何事件。存证由 B3 记录（`attestation_id`），
  而 B2 这一侧连一行业务数据都不产生——"验证是低频动作，B2 再存一份就是第二个事实源"。
- **不返回受保护内容**：响应只有计数、摘要与逐用例的 `passed`/`failure_category`；
  参考解答正文与隐藏用例的期望输出**都不进响应**（与报告端点 `view=teacher` 同级，不是更多）。

## 三个标识字段如实说明（**不伪造**）

`SubmissionRunInput` 要求 `submission_id`/`evaluation_run_id`/`correlation_id`。本路径**没有**
提交、也**没有**评测运行——它是"验证某个题目版本"，不是"评测某个学生"。这三个字段在 runner 里
**一处都没被读**（`run_case` 不引用它们），本路径也不落库、不记日志，因此统一填本次验证的
服务端生成 UUID。若 runner 将来开始记录它们，这里必须改成先扩展那个冻结签名，
**不得**继续把验证标识塞进一个叫 `evaluation_run_id` 的槽位。

## 一个**刻意与评测编排不同**的取值：不在沙箱违规处提前停止

`EvaluationOrchestrator` 在某个用例 `security_blocked` 时**终止整次评测**（拒绝即终止：已经违规的
代码不该继续跑），因为那要给出一个**运行级**终态（`rejected`）。本端点没有终态、不落库，
它的契约是"逐用例给出结论"（`cases` 的每一项都有 `ordinal`/`passed`/`failure_category`），
`cases_total` 又必须描述**该版本的用例总数**（B3 存证时也按它交叉核对）。
因此这里**跑完全部用例**：每个用例本来就有独立的、策略相同的新沙箱作业与新的工作区，
隔离不依赖"上一个用例是否违规"。

**这条已由 MAIN 复核并裁决为保留（2026-09-20），不要再"顺手统一"成提前停止**：提前停止会让
`cases_total` 要么算错、要么含义悄悄变成"跑过的数量"，而 B3 在记录存证时按 `cases_total`
**交叉核对**它是不是这个版本的用例数——两者对不上就会让一次全通过也拿不到存证。
"违规即终止"的原始理由（给运行级终态、避免继续消耗算力）对一个**只读、不落库、输出就是逐用例列表**
的验证都不成立；而且同一个版本的参考解答对所有用例是**同一份代码、同一个沙箱策略**，
继续跑并不比停下更不安全。钉住这条行为的用例是
`tests/test_reference_verification.py::test_security_blocked_case_is_reported_without_persisting`。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import get_args

from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.domain.clock import sha256_hex, utc_now
from autograder_b2.domain.envelope import EnvelopeFile
from autograder_b2.domain.evidence import (
    CaseEvidence,
    FailureCategory,
    InternalEvaluationError,
    PackageUnavailableError,
    SandboxUnavailableError,
    SubmissionRunInput,
)
from autograder_b2.evaluation.orchestrator import DynamicExecutionGate
from autograder_b2.evaluation.ports import CaseDescriptor, CaseRunner, PackageSource, PackageView
from autograder_b2.integration.adapters import B3Endpoint
from autograder_b2.logging.logger import log_dependency_unavailable, log_event
from autograder_b2.reference.b3 import (
    B3VerificationClient,
    ReferenceAnswerMissing,
    ReferenceAnswerNotFound,
    ReferenceAnswerUnavailable,
    ReferenceAnswerView,
)
from autograder_b2.reference.workspace import ReferenceAnswerWorkspace
from autograder_b2.runner.adapters import entry_name_for

logger = logging.getLogger("autograder_b2.reference.verification")

#: 契约 `ReferenceVerification.cases[].failure_category` 的取值域。**不在这里第二份手写**：
#: 它就是 runner 产出的 `FailureCategory`，与 `evaluation-report` 的失败类别是同一套词表
#: （`CHG-0019` §2.6：新造一份会让同一个失败在两个地方意思不同）。
FAILURE_CATEGORIES: tuple[str, ...] = get_args(FailureCategory)


class ReferenceVerificationNotFound(LookupError):
    """该题目版本不存在——对外 `404`。"""


class ReferenceVerificationNotPossible(RuntimeError):
    """版本存在，但它的状态让验证不可能——对外 `422`。

    两种情形：版本没有参考解答（上游 `409`），版本还没有 evaluation package（上游 `409`，草稿即如此）。
    都不是"资源不存在"（`governance.md` 硬规则 1 禁止把两者混为一谈）。
    """


class ReferenceVerificationUnavailable(RuntimeError):
    """依赖不可用——对外 `503`，且**不产生存证**：沙箱不可用、B3 通道不可用、两侧声明不一致。"""


class ReferenceSandboxUnavailable(ReferenceVerificationUnavailable):
    """本平台**没有沙箱**（`ADR-0004` 禁止降级为裸进程）——对外同样是 `503`、同样零存证。

    单独一个类型（而不是靠比对错误文本）只有一个理由：它是**已被文档化的平台条件**，不是"未预期的
    依赖故障"，因此路由层要能把它与真正的故障区分开——只有后者才计入 `record_failure()`（见
    `runtime.B2Runtime.record_failure`：把一个必然发生的平台条件算成"未预期失败"，会让 readiness
    的那句诊断永远脏下去，而这件事已经由 503 的 detail 与 `runner.sandbox_unavailable` 日志如实反映）。
    """


class ReferenceVerificationDefect(RuntimeError):
    """内部一致性缺陷（**不伪装**成用例失败或依赖故障）：由宿主记 500。"""


@dataclass(frozen=True, slots=True)
class VerificationCase:
    """契约 `ReferenceVerification.cases[]` 的一项（只有序、通过与失败类别）。"""

    ordinal: int
    passed: bool
    failure_category: str | None

    def as_contract(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "passed": self.passed,
            "failure_category": self.failure_category,
        }


@dataclass(frozen=True, slots=True)
class ReferenceVerificationResult:
    """契约 `ReferenceVerification` 载荷（字段集合由契约锁定，这里一个不多一个不少）。"""

    question_version_id: str
    content_digest: str
    language: str
    adapter_kind: str
    cases: tuple[VerificationCase, ...]
    verified_at: datetime
    attestation_id: str | None

    @property
    def cases_total(self) -> int:
        return len(self.cases)

    @property
    def cases_passed(self) -> int:
        return sum(1 for case in self.cases if case.passed)

    @property
    def all_passed(self) -> bool:
        return self.cases_total > 0 and self.cases_passed == self.cases_total

    def as_contract(self) -> dict[str, object]:
        return {
            "question_version_id": self.question_version_id,
            "content_digest": self.content_digest,
            "language": self.language,
            "adapter_kind": self.adapter_kind,
            "cases_total": self.cases_total,
            "cases_passed": self.cases_passed,
            "cases": [case.as_contract() for case in self.cases],
            "verified_at": rfc3339_millis(self.verified_at),
            "attestation_id": self.attestation_id,
        }


class ReferenceVerificationService:
    """端点的实现体：一次调用 = 一次验证（无状态、不落库）。"""

    def __init__(
        self,
        *,
        reference_answers: B3VerificationClient,
        packages: PackageSource,
        runner: CaseRunner,
        workspaces: ReferenceAnswerWorkspace,
        gate: DynamicExecutionGate,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._reference_answers = reference_answers
        self._packages = packages
        self._runner = runner
        self._workspaces = workspaces
        self._gate = gate
        self._now = now

    @property
    def gate(self) -> DynamicExecutionGate:
        """与评测编排**共用**的沙箱并发闸门（单并发上限对两条路径一起生效）。"""
        return self._gate

    @property
    def b3_endpoint(self) -> B3Endpoint:
        """取参考解答与记录存证用的那一个 B3 通道（与取包同一个对象）。"""
        return self._reference_answers.endpoint

    def verify(self, question_version_id: str) -> ReferenceVerificationResult:
        """验证一个题目版本的参考解答；任何失败都不产生存证。"""
        verification_id = str(uuid.uuid4())
        answer = self._fetch_answer(question_version_id)
        view = self._fetch_package(answer)
        cases = self._run_cases(verification_id, answer, view)
        moment = self._now()
        result = ReferenceVerificationResult(
            question_version_id=answer.question_version_id,
            content_digest=answer.content_digest,
            language=answer.language,
            adapter_kind=answer.adapter_kind,
            cases=cases,
            verified_at=moment,
            attestation_id=None,
        )
        return self._attest_if_passed(result)

    # --- 三步 ---------------------------------------------------------------------------

    def _fetch_answer(self, question_version_id: str) -> ReferenceAnswerView:
        try:
            return self._reference_answers.fetch_reference_answer(question_version_id)
        except ReferenceAnswerNotFound as error:
            raise ReferenceVerificationNotFound(str(error)) from error
        except ReferenceAnswerMissing as error:
            raise ReferenceVerificationNotPossible(str(error)) from error
        except ReferenceAnswerUnavailable as error:
            log_dependency_unavailable(
                logger, dependency="b3-reference-answer", error=error, stage="reference_verification"
            )
            raise ReferenceVerificationUnavailable(str(error)) from error

    def _fetch_package(self, answer: ReferenceAnswerView) -> PackageView:
        """取该版本的 evaluation package（隐藏用例与期望输出的唯一来源）。

        `PackageSource.fetch` 的 `evaluation_run_id` 在本路径上没有对应实体（见模块 docstring
        的"三个标识字段"）：本端点不落库、不记日志，该参数当前只被用来做 UUID 形态校验，
        因此传**本次验证**的标识，而不是编一个评测运行标识。
        """
        try:
            view = self._packages.fetch(
                evaluation_run_id=str(uuid.uuid4()),
                question_version_id=answer.question_version_id,
            )
        except PackageUnavailableError as error:
            raise _from_package_error(error) from error
        if view.language != answer.language:
            # 上游两处对"这个版本用什么语言"说法不一致：此刻执行哪一份都可能跑错解释器，
            # 因此判为依赖不可用，而不是挑一个继续。
            raise ReferenceVerificationUnavailable(
                "the question version's language differs between the reference answer and the package"
            )
        return view

    def _run_cases(
        self, verification_id: str, answer: ReferenceAnswerView, view: PackageView
    ) -> tuple[VerificationCase, ...]:
        entry_name = entry_name_for(answer.language)
        digest = sha256_hex(answer.reference_answer.encode("utf-8"))
        files = (
            EnvelopeFile(
                path=entry_name,
                content_sha256=digest,
                size_bytes=len(answer.reference_answer.encode("utf-8")),
                # 摘要派生引用：本路径**不写 CAS**（不落库），因此它不是一条可读的存储位置，
                # 而 runner 也不读它——工作区已经物化好了。
                storage_ref=f"sha256/{digest}",
            ),
        )
        observed: list[VerificationCase] = []
        for case in view.ordered_cases():
            # 逐个跑完，**不**在 `security_blocked` 处提前停止：见模块 docstring 的
            # "刻意与评测编排不同的取值"（该取舍已由 MAIN 复核并裁决为保留）。
            observed.append(self._run_case(verification_id, answer, view, case, entry_name, files))
        return tuple(sorted(observed, key=lambda item: item.ordinal))

    def _run_case(
        self,
        verification_id: str,
        answer: ReferenceAnswerView,
        view: PackageView,
        case: CaseDescriptor,
        entry_name: str,
        files: tuple[EnvelopeFile, ...],
    ) -> VerificationCase:
        workspace = self._workspaces.prepare(
            verification_id=verification_id,
            case_id=case.case_id,
            filename=entry_name,
            content=answer.reference_answer,
        )
        try:
            submission = SubmissionRunInput(
                # 见模块 docstring：本路径没有提交/运行标识，三个字段当前都不被 runner 读取。
                submission_id=verification_id,
                evaluation_run_id=verification_id,
                correlation_id=verification_id,
                workspace=workspace,
                files=files,
                case=case.handle,  # type: ignore[arg-type] - 不透明句柄：本层不解释 package 结构
                adapter=view.adapter_handle,  # type: ignore[arg-type] - 同上
                policy=view.policy_handle,  # type: ignore[arg-type] - 同上
            )
            with self._gate.hold():
                evidence = self._runner.run_case(view.handle, submission)
        except SandboxUnavailableError as error:
            # 无沙箱的平台（本机 Windows 即如此，ADR-0004 禁止降级为裸进程）：503 且**零存证**。
            # 报成功是伪造，报"用例失败"是把环境问题写成题目的问题——两条都不允许。
            log_dependency_unavailable(
                logger, dependency="sandbox", error=error, stage="reference_verification"
            )
            raise ReferenceSandboxUnavailable(
                "the sandbox is unavailable on this platform, so the reference answer was not "
                "executed (no attestation was recorded)"
            ) from error
        except PackageUnavailableError as error:
            log_dependency_unavailable(
                logger,
                dependency="question_package",
                error=error,
                stage="reference_verification",
            )
            raise ReferenceVerificationUnavailable(str(error)) from error
        except InternalEvaluationError as error:
            raise ReferenceVerificationDefect(
                "the runner reported an internal defect while executing the reference answer"
            ) from error
        finally:
            self._workspaces.release(workspace)
        return _case_row(evidence)

    def _attest_if_passed(self, result: ReferenceVerificationResult) -> ReferenceVerificationResult:
        """**只在参考解答真的全部通过时**才请 B3 记录存证。

        未通过时不调用、不写任何"部分通过"的记录：契约把存证定义为"这份内容被验证过"，
        一份未通过的验证如果留下记录，事后就会被读成"通过"（`ADR-0015` 决策 4）。
        记录失败时 `attestation_id` 为 `None`，**逐用例结论照旧返回**——那是真实发生的事。
        """
        log_event(
            logger,
            "reference.verification.completed",
            fields={
                "question_version_id": result.question_version_id,
                "outcome": "accepted" if result.all_passed else "partial",
                "total": result.cases_total,
                "passed": result.cases_passed,
            },
        )
        if not result.all_passed:
            return result
        try:
            identifier = self._reference_answers.record_attestation(
                question_version_id=result.question_version_id,
                content_digest=result.content_digest,
                cases_total=result.cases_total,
                cases_passed=result.cases_passed,
                verified_at=rfc3339_millis(result.verified_at),
            )
        except ReferenceAnswerUnavailable as error:
            # 上游说"记下了"却给不出 `attestation_id`：**不**写成 `null`（那等于说"没有记录"，
            # 而我们并不知道），也不静默吞掉——按依赖故障处置（对外 `503`）。
            log_dependency_unavailable(
                logger,
                dependency="b3-attestations",
                error=error,
                stage="reference_verification",
            )
            raise ReferenceVerificationUnavailable(str(error)) from error
        if identifier is None:
            log_event(
                logger,
                "reference.attestation.not_recorded",
                level=logging.WARNING,
                fields={
                    "question_version_id": result.question_version_id,
                    "dependency": "b3-attestations",
                    "outcome": "not_recorded",
                },
            )
        return ReferenceVerificationResult(
            question_version_id=result.question_version_id,
            content_digest=result.content_digest,
            language=result.language,
            adapter_kind=result.adapter_kind,
            cases=result.cases,
            verified_at=result.verified_at,
            attestation_id=identifier,
        )


def _case_row(evidence: CaseEvidence) -> VerificationCase:
    """`CaseEvidence` → 契约的一行；**形状不自洽即报缺陷**，不悄悄归一。

    契约写明 `failure_category` "Null exactly when `passed` is true"，词表也只有 8 个值。
    这两条由 runner 保证；这里做一次自检，因为把缺陷归一成"这一项查不出来"会让一次内部故障
    看起来像一次正常的参考答案失败。
    """
    category = evidence.failure_category
    if evidence.passed and category is not None:
        raise ReferenceVerificationDefect(
            "the runner reported a passed case together with a failure category"
        )
    if category is not None and category not in FAILURE_CATEGORIES:
        raise ReferenceVerificationDefect(
            f"the runner reported an unknown failure category: {category!r}"
        )
    return VerificationCase(
        ordinal=evidence.ordinal, passed=evidence.passed, failure_category=category
    )


def _from_package_error(error: PackageUnavailableError) -> RuntimeError:
    """把取包失败按上游状态码映射成对外语义（`CHG-0019` 需要区分"不存在"与"状态不允许"）。

    **`409` 的原因必须转述 B3 的，不自己造**：`409` 说的是"版本状态与这个请求冲突"，但**为什么**
    推不出来——至少三种：`a draft version has no evaluation package; publish it first`、
    `the compiled package no longer matches the digest recorded at publish time; protected content
    changed after publication`、以及 `this version has no recorded package digest`。三者要教师做的事
    完全不同（先发布 / 别改已发布内容 / 重新发布），而一句自造的"版本还没有包"在其中两种下是错的。
    取不到 `detail` 时只陈述 `409` 本身成立的事实，不猜。
    """
    status = error.status_code
    if status == 404:
        return ReferenceVerificationNotFound("the question version does not exist")
    if status == 409:
        upstream = error.upstream_detail
        if upstream is None:
            return ReferenceVerificationNotPossible(
                f"B3 refused to serve the evaluation package (HTTP {status}) without a reason; the "
                "version cannot be verified against its cases in its current state"
            )
        return ReferenceVerificationNotPossible(
            f"B3 refused to serve the evaluation package (HTTP {status}): {upstream}"
        )
    log_dependency_unavailable(
        logger, dependency="question_package", error=error, stage="reference_verification"
    )
    return ReferenceVerificationUnavailable(str(error))


__all__ = [
    "FAILURE_CATEGORIES",
    "ReferenceVerificationDefect",
    "ReferenceVerificationNotFound",
    "ReferenceVerificationNotPossible",
    "ReferenceVerificationResult",
    "ReferenceVerificationService",
    "ReferenceVerificationUnavailable",
    "VerificationCase",
]
