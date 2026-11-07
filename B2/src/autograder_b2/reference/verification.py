"""参考解答验证的编排（`CHG-0019` / `ADR-0015`）：取参考解答 → 取评测包 → 沙箱逐用例 → 存证。

## 按 package 的 `execution.mode` 分派（设计基线 `unix-homework-evaluation.md` §3/§6）

| mode | 参考解怎么验证 |
|---|---|
| `per_case` | 逐用例 `runner.run_case`（既有行为，**每个用例一份新沙箱**） |
| `single_run` / `ssh_session` | **一份**批量工作区 + **一次** `runner.run_cases`（与学生提交是同一条执行路径） |
| `email_delivery` | 不启动沙箱：参考解答是一份**受保护的合成邮件回执 JSON**，用与学生评测**同一个**纯规则引擎判定 |

三条不可混淆的事实（每条都有测试钉住）：

1. **参考答案检验与学生评测走同一条判定链**：批量模式共用 `run_cases`（`runner/policy.py` 的
   seccomp/cgroup/禁网策略照旧生效）；邮件模式共用 `intake.email_rules.evaluate_email_receipt`。
   另写一条"执行参考解"的路径会绕开那份策略/规则，等于在发布门禁上开一个不受约束的判定面。
2. **邮件模式的输出是"参考解自检"，不是"真实邮件已验证"**：合成回执只证明规则引擎对声明要求的
   判定自洽；真实收件事实来自 IMAP 收件链路（评测侧 `DeliveryVerifier`）。响应契约没有模式字段，
   因此本模块**只在日志里**用一个独立事件名（`reference.verification.email_receipt_checked`）
   记录"这是合成回执规则检查"，且日志只含计数与失败类别，不含回执字段、地址、主题或附件名。
3. **不落库**：本模块不碰引擎、不写任何表、不发任何事件（存证由 B3 记录，`attestation_id`）。

## 它做什么、不做什么

`ADR-0015` 决策 1 把职责切得很窄：B2 **不回答"这道题好不好"**，只回答"按这道题自己声明的适配器与
比较策略，参考答案是否通过"。因此本模块：

- **执行**走 `runner.run_case` / `runner.run_cases`（注入的 `CaseRunner` 端口）——生产装配就是
  `RunnerCaseRunner`，与学生提交是**同一条**执行路径。
- **不落库**：不碰引擎、不写任何表、不发任何事件。存证由 B3 记录（`attestation_id`），
  而 B2 这一侧连一行业务数据都不产生——"验证是低频动作，B2 再存一份就是第二个事实源"。
- **不返回受保护内容**：响应只有计数、摘要与逐用例的 `passed`/`failure_category`；
  参考解答正文与隐藏用例的期望输出**都不进响应**（与报告端点 `view=teacher` 同级，不是更多）。

## 三个标识字段如实说明（**不伪造**）

`SubmissionRunInput` / `BatchRunInput` 要求 `submission_id`/`evaluation_run_id`/`correlation_id`。
本路径**没有**提交、也**没有**评测运行——它是"验证某个题目版本"，不是"评测某个学生"。这三个字段在
runner 里**一处都没被读**，本路径也不落库、不记日志，因此统一填本次验证的服务端生成 UUID。若 runner
将来开始记录它们，这里必须改成先扩展那个冻结签名，**不得**继续把验证标识塞进一个叫 `evaluation_run_id`
的槽位。

## 一个**刻意与评测编排不同**的取值：不在沙箱违规处提前停止

`EvaluationOrchestrator` 在某个用例 `security_blocked` 时**终止整次评测**（拒绝即终止：已经违规的
代码不该继续跑），因为那要给出一个**运行级**终态（`rejected`）。本端点没有终态、不落库，
它的契约是"逐用例给出结论"（`cases` 的每一项都有 `ordinal`/`passed`/`failure_category`），
`cases_total` 又必须描述**该版本的用例总数**（B3 存证时也按它交叉核对）。

因此这里**跑完全部用例**：`per_case` 每个用例本来就有独立的、策略相同的新沙箱作业与新的工作区，
隔离不依赖"上一个用例是否违规"；批量模式本来就是**一次**执行，一次调用返回全部用例证据，不存在
"提前停止"的中间状态。**这条已由 MAIN 复核并裁决为保留（2026-09-20），不要再"顺手统一"成提前停止**：
提前停止会让 `cases_total` 要么算错、要么含义悄悄变成"跑过的数量"，而 B3 在记录存证时按 `cases_total`
**交叉核对**它是不是这个版本的用例数——两者对不上就会让一次全通过也拿不到存证。钉住这条行为的用例是
`tests/test_reference_verification.py::test_security_blocked_case_is_reported_without_persisting`。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import get_args

from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.domain.clock import sha256_hex, utc_now
from autograder_b2.domain.envelope import EnvelopeFile
from autograder_b2.domain.evidence import (
    BatchRunInput,
    CaseEvidence,
    FailureCategory,
    InternalEvaluationError,
    PackageUnavailableError,
    SandboxUnavailableError,
    SubmissionRunInput,
)
from autograder_b2.evaluation.orchestrator import DynamicExecutionGate
from autograder_b2.evaluation.ports import (
    CaseDescriptor,
    CaseRunner,
    PackageSource,
    PackageView,
)
from autograder_b2.intake.email_rules import (
    EmailReceipt,
    EmailRequirements,
    evaluate_email_receipt,
)
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
        mode = view.execution.mode
        if mode == "email_delivery":
            cases = self._verify_email_delivery(answer, view)
        elif mode in ("single_run", "ssh_session"):
            cases = self._run_batch(verification_id, answer, view)
        elif mode == "per_case":
            cases = self._run_per_case(verification_id, answer, view)
        else:  # pragma: no cover - PackageView 已在端口层拒绝未知模式
            raise ReferenceVerificationDefect(f"the package declares an unknown execution mode: {mode!r}")
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

    def _answer_files(self, answer: ReferenceAnswerView, entry_name: str) -> tuple[EnvelopeFile, ...]:
        """参考解答的包络文件（摘要派生引用：本路径**不写 CAS**，runner 也不读 `storage_ref`）。"""
        digest = sha256_hex(answer.reference_answer.encode("utf-8"))
        return (
            EnvelopeFile(
                path=entry_name,
                content_sha256=digest,
                size_bytes=len(answer.reference_answer.encode("utf-8")),
                storage_ref=f"sha256/{digest}",
            ),
        )

    def _run_per_case(
        self, verification_id: str, answer: ReferenceAnswerView, view: PackageView
    ) -> tuple[VerificationCase, ...]:
        """`per_case`：逐用例一份新工作区、一次 `run_case`（既有行为）。"""
        entry_name = entry_name_for(answer.language)
        files = self._answer_files(answer, entry_name)
        observed: list[VerificationCase] = []
        for case in view.ordered_cases():
            # 逐个跑完，**不**在 `security_blocked` 处提前停止：见模块 docstring 的
            # "刻意与评测编排不同的取值"（该取舍已由 MAIN 复核并裁决为保留）。
            observed.append(self._run_case(verification_id, answer, view, case, entry_name, files))
        return tuple(sorted(observed, key=lambda item: item.ordinal))

    def _run_batch(
        self, verification_id: str, answer: ReferenceAnswerView, view: PackageView
    ) -> tuple[VerificationCase, ...]:
        """`single_run` / `ssh_session`：**一份**批量工作区 + **一次** `run_cases`（与学生同一入口）。

        批量入口一次调用即产出全部用例证据：这里不做逐用例循环（那会变成 N 次沙箱执行，
        与学生评测的计数语义 `invocation_count=1` 冲突）。一次调用也意味着 `security_blocked`
        出现在某条证据上时不存在"后续用例"可停止——见模块 docstring 的裁决说明。
        """
        entry_name = entry_name_for(answer.language)
        files = self._answer_files(answer, entry_name)
        workspace = self._workspaces.prepare_batch(
            verification_id=verification_id,
            filename=entry_name,
            content=answer.reference_answer,
        )
        try:
            batch = BatchRunInput(
                # 见模块 docstring：本路径没有提交/运行标识，三个字段当前都不被 runner 读取。
                submission_id=verification_id,
                evaluation_run_id=verification_id,
                correlation_id=verification_id,
                workspace=workspace,
                files=files,
                cases=tuple(case.handle for case in view.ordered_cases()),  # type: ignore[arg-type]
                adapter=view.adapter_handle,  # type: ignore[arg-type] - 不透明句柄
                policy=view.policy_handle,  # type: ignore[arg-type] - 不透明句柄
            )
            with self._gate.hold():
                evidences = self._runner.run_cases(view.handle, batch)
            self._log_batch_answer_verification(view, evidences)
        except SandboxUnavailableError as error:
            log_dependency_unavailable(
                logger, dependency="sandbox", error=error, stage="reference_verification"
            )
            raise ReferenceSandboxUnavailable(
                "the sandbox is unavailable or failed to initialize, so the reference answer was not "
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
        return self._verification_cases(view, evidences, context="batch")

    def _verification_cases(
        self,
        view: PackageView,
        evidences: Sequence[CaseEvidence],
        *,
        context: str,
    ) -> tuple[VerificationCase, ...]:
        """批量证据与 package 用例集合的一致性校验 + 转成契约行（形状不自洽即缺陷）。"""
        declared = {case.ordinal: case for case in view.ordered_cases()}
        if len(evidences) != len(declared):
            raise ReferenceVerificationDefect(
                f"the {context} runner returned {len(evidences)} evidence rows for"
                f" {len(declared)} declared cases"
            )
        collected: dict[int, VerificationCase] = {}
        for evidence in evidences:
            case = declared.get(evidence.ordinal)
            if case is None:
                raise ReferenceVerificationDefect(
                    f"the {context} runner returned evidence for an undeclared case ordinal"
                )
            if evidence.ordinal in collected:
                raise ReferenceVerificationDefect(
                    f"the {context} runner returned duplicate evidence for one case ordinal"
                )
            if str(evidence.case_id) != case.case_id:
                raise ReferenceVerificationDefect(
                    f"the {context} runner returned evidence for a different case"
                )
            collected[evidence.ordinal] = _case_row(evidence)
        return tuple(collected[ordinal] for ordinal in sorted(collected))

    def _log_batch_answer_verification(
        self, view: PackageView, evidences: Sequence[CaseEvidence]
    ) -> None:
        """批量验证的诊断日志：只记计数/类别，不记 stdout、期望输出或工作区路径。"""
        completed = sum(1 for item in evidences if item.passed)
        log_event(
            logger,
            "reference.verification.batch",
            fields={
                "question_version_id": getattr(view.handle, "question_version_id", None),
                "total": len(evidences),
                "passed": completed,
                "failed": len(evidences) - completed,
            },
        )

    def _verify_email_delivery(
        self, answer: ReferenceAnswerView, view: PackageView
    ) -> tuple[VerificationCase, ...]:
        """`email_delivery`：不启动沙箱，对**受保护的合成邮件回执 JSON** 调用共享规则引擎。

        为什么是合成回执而不是真邮件：参考解自检必须可在任意环境重复执行，而真实邮件端到端
        依赖 IMAP 收件链路（那属于评测侧 `DeliveryVerifier` 的事实来源）。本方法只证明
        "按 package 声明的投递要求，规则引擎对这份回执给出的逐项判定是什么"——
        **不产生**"真实邮件已验证"的声明，日志只含计数。
        """
        receipt = _parse_synthetic_receipt(answer.reference_answer)
        requirements = _email_requirements(view)
        try:
            outcomes = evaluate_email_receipt(receipt, requirements)
        except ValueError as error:
            # 模板/attempt 非法属于 package/装配缺陷（不是参考解没通过），按缺陷处置。
            raise ReferenceVerificationDefect(
                "the email rule engine rejected the declared requirements"
            ) from error
        by_check = {outcome.check: outcome for outcome in outcomes}
        if len(by_check) != len(outcomes):
            raise ReferenceVerificationDefect("the email rule engine returned duplicate check outcomes")
        observed: list[VerificationCase] = []
        seen_checks: set[str] = set()
        for case in view.ordered_cases():
            check = _declared_email_check(case)
            if check in seen_checks:
                raise ReferenceVerificationDefect(
                    "the email_delivery package declares the same email check twice"
                )
            seen_checks.add(check)
            outcome = by_check.get(check)
            if outcome is None:
                raise ReferenceVerificationDefect(
                    "the email rule engine returned no outcome for a declared check"
                )
            observed.append(
                VerificationCase(
                    ordinal=case.ordinal,
                    passed=outcome.passed,
                    failure_category=outcome.category,
                )
            )
        self._log_email_answer_verification(view, observed)
        return tuple(sorted(observed, key=lambda item: item.ordinal))

    def _log_email_answer_verification(
        self, view: PackageView, cases: Sequence[VerificationCase]
    ) -> None:
        """合成回执规则检查的日志：**只含计数**（不计"真实邮件已验证"，不含回执字段）。"""
        passed = sum(1 for item in cases if item.passed)
        log_event(
            logger,
            "reference.verification.email_receipt_checked",
            fields={
                "question_version_id": getattr(view.handle, "question_version_id", None),
                "total": len(cases),
                "passed": passed,
                "failed": len(cases) - passed,
            },
        )

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
            # 诊断用：只记"跑没跑起来、输出多长、比较到哪一行"，不记 stdout/期望正文本体。
            # 线上出现过"每条用例都判输出不一致"却看不出原因的情况，这里给出可判读的数字。
            logger.info(
                "reference.verification.case",
                extra={
                    "b2_event": {
                        "event": "reference.verification.case",
                        "fields": {
                            "question_version_id": getattr(view, "question_version_id", None),
                            "ordinal": case.ordinal,
                            "passed": evidence.passed,
                            "failure_category": evidence.failure_category,
                            "exit_code": evidence.exit_code,
                            "actual_len": len(evidence.actual_output_excerpt or ""),
                            "expected_len": len(evidence.expected_output_excerpt or ""),
                            "stdout_digest": (evidence.stdout_digest or "")[:8],
                            "message": (evidence.message or "")[:200],
                            "mismatch_line": evidence.mismatch_line,
                            "resource_readings": evidence.resource is not None,
                        },
                    }
                },
            )
        except SandboxUnavailableError as error:
            # 无沙箱的平台（本机 Windows 即如此，ADR-0004 禁止降级为裸进程）：503 且**零存证**。
            # 报成功是伪造，报"用例失败"是把环境问题写成题目的问题——两条都不允许。
            log_dependency_unavailable(
                logger, dependency="sandbox", error=error, stage="reference_verification"
            )
            raise ReferenceSandboxUnavailable(
                "the sandbox is unavailable or failed to initialize, so the reference answer was not "
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


#: 合成邮件回执 JSON 的 schema 标签（内部格式；参考答案正文就是这份 JSON 的 UTF-8 文本）。
SYNTHETIC_RECEIPT_SCHEMA = "b2.reference-email-receipt.v1"

#: 合成邮件回执的键集合 = `EmailReceipt` 的字段集合（一一对应；多一个少一个都算缺陷）。
_SYNTHETIC_RECEIPT_FIELDS = frozenset(
    {
        "student_number",
        "subject",
        "sender",
        "account_email",
        "answer_attachment_name",
        "received",
        "account_email_verified",
        "sender_binding_matches",
    }
)


def _parse_synthetic_receipt(text: str) -> EmailReceipt:
    """严格解析**受保护的合成邮件回执 JSON**（参考解答正文）。

    形态固定（与共享规则引擎的输入类型一一对应，多键/缺键都拒绝）：

    ```json
    {"student_number": "20230001", "subject": "20230001_1", "sender": "alice@example.edu",
     "account_email": "alice@example.edu", "answer_attachment_name": "1.txt",
     "received": true, "account_email_verified": true, "sender_binding_matches": true}
    ```

    `answer_attachment_name` 是**该题在收件映射里的答案文件名**（`N.txt`，设计 §1.4/§6.5）：
    非空 = 该题有映射答案文件（`attachment` 检查通过），null = 没有映射。它不是"学号.txt"模板，
    也不再是一份附件名数组。

    这不是"真实邮件"：它只是参考解自检的输入。解析失败即 `ReferenceVerificationDefect`（缺陷），
    **不**映射成"参考解未通过"——否则一份写坏的参考答案会被读成一次正常的验证失败。
    """
    try:
        document = json.loads(text)
    except (ValueError, UnicodeDecodeError) as error:
        raise ReferenceVerificationDefect(
            "the email_delivery reference answer is not valid JSON"
        ) from error
    if not isinstance(document, Mapping):
        raise ReferenceVerificationDefect(
            "the email_delivery reference answer is not a JSON object"
        )
    keys = frozenset(str(key) for key in document)
    if keys != _SYNTHETIC_RECEIPT_FIELDS:
        raise ReferenceVerificationDefect(
            "the email_delivery reference answer does not match the synthetic receipt shape"
        )
    optional_text = ("student_number", "subject", "sender", "account_email")
    values: dict[str, object] = {}
    for name in optional_text:
        value = document[name]
        if value is not None and not isinstance(value, str):
            raise ReferenceVerificationDefect("a synthetic receipt text field is not a string")
        values[name] = value
    for name in ("received", "account_email_verified", "sender_binding_matches"):
        value = document[name]
        if not isinstance(value, bool):
            raise ReferenceVerificationDefect("a synthetic receipt boolean field is not a boolean")
        values[name] = value
    answer_attachment_name = document["answer_attachment_name"]
    if answer_attachment_name is not None and not isinstance(answer_attachment_name, str):
        raise ReferenceVerificationDefect(
            "the synthetic receipt answer_attachment_name is not a string or null"
        )
    values["answer_attachment_name"] = answer_attachment_name
    return EmailReceipt(**values)  # type: ignore[arg-type] - 键集合与字段一一对应已校验


def _email_requirements(view: PackageView) -> EmailRequirements:
    """从 package 的 `execution.email` 取投递要求（受保护配置只在本进程内使用，不落日志/响应）。

    `required_attachment_template` 已随契约删除（设计 §2.6）：附件名由作业题目位置统一决定
    （第 N 题 = `N.txt`），`attachment` 检查只看"该题有没有映射答案文件"。
    """
    execution = view.execution.handle
    config = getattr(execution, "email", None)
    if config is None:
        raise ReferenceVerificationDefect(
            "the email_delivery package carries no email requirements"
        )
    try:
        return EmailRequirements(
            attempt=int(config.attempt),
            subject_template=str(config.subject_template),
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise ReferenceVerificationDefect(
            "the email_delivery package declares invalid email requirements"
        ) from error


def _declared_email_check(case: CaseDescriptor) -> str:
    """取用例声明的 `email_check`（缺失说明 package 解析/校验有缺陷，不能猜一个默认值）。"""
    handle = case.handle
    check = getattr(handle, "email_check", None)
    if not isinstance(check, str):
        raise ReferenceVerificationDefect(
            "an email_delivery case declares no email_check"
        )
    return check


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
