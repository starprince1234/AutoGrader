"""`run_cases(package, batch_input) -> tuple[CaseEvidence, ...]`：一次沙箱执行产生全部用例证据。

设计文档 `MAIN/docs/architecture/unix-homework-evaluation.md` §3.2/§3.3/§6：`single_run` 与
`ssh_session` 都只启动**一次**沙箱，但每条用例仍各自产出 `CaseEvidence`（进度帧按证据逐条产生，
不得解释成执行次数）。本模块是这两个模式的唯一入口：

- `single_run`：一次执行 → 有界 stdout 按独占整行的 `segment_delimiter` 切分（每段只与自己的
  `expected_stdout` 比较，绝不位移）→ 声明 `file_checks` 的用例在运行后直接检查工作区文件；
- `ssh_session`：一次真实 SSH 练习会话（B2 固定驱动 + 真实 sshd + 真实 ssh 客户端），三条
  `ssh_check` 由 sshd 自有日志与客户端退出判定；环境起不来 ⇒ `SandboxUnavailableError`
  （编排层落 `infrastructure_error`），学生脚本不合法/登录失败/未干净退出 ⇒ 学生侧证据。

**不降级**：沙箱探测不可用即抛 `SandboxUnavailableError`，绝不执行未隔离进程；启动只用固定 argv。
**不位移**：分段结构不合法时全部 segment 用例 `output_mismatch`，不会把某一段判给别的小题。
**不受保护值外泄**：SSH 口令只经驱动 stdin；证据/消息里不出现口令、期望输出原文或宿主绝对路径。

`per_case` 仍走 `run_case`（每个用例一份独立工作区，批量输入表达不了）；`email_delivery` 不经过
runner（由编排层的投递核验端口判定）。
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from autograder_b2.domain import (
    CaseEvidence,
    InternalEvaluationError,
    PackageUnavailableError,
    SandboxUnavailableError,
)
from autograder_b2.domain.evidence import BatchRunInput, FileCheckResult
from autograder_b2.runner.adapters import (
    ExecutionPlan,
    build_batch_execution_plan,
    entry_script_exists,
    join_problems,
    prepare_case_workspace,
    snapshot_workspace,
    write_workspace_prepare_program,
)
from autograder_b2.runner.cgroup import CgroupV2Peaks, case_resource_from_peaks, limit_hit_for
from autograder_b2.runner.comparator import compare_stdout
from autograder_b2.runner.file_checks import classify_file_checks, evaluate_file_checks
from autograder_b2.runner.fixtures import FixtureProvider, materialize_fixtures
from autograder_b2.runner.package import EvaluationPackage, PackageCase
from autograder_b2.runner.policy import (
    SANDBOX_PROFILE_SSH,
    SANDBOX_PROFILE_WORKSPACE_PREP,
    SSH_CLIENT_CANDIDATES,
    SSH_DAEMON_CANDIDATES,
    SSH_DRIVER_SCRIPT_NAME,
    SSH_KEYGEN_CANDIDATES,
    WORKSPACE_PREP_GID,
    WORKSPACE_PREP_UID,
    RunnerPolicy,
    create_ssh_sandbox_paths,
    probe_sandbox,
    render_nsjail_ssh_config,
    resolve_ssh_binary,
    sandbox_script_path,
)
from autograder_b2.runner.run_case import (
    SIGSYS_NUMBER,
    CaseOutcome,
    ProcessLauncher,
    RunFacts,
    SandboxCommand,
    _killed_by,
    build_evidence,
    classify_case_outcome,
    elapsed_ms,
    execute_sandbox_plan,
    input_digest_for,
    launcher_for,
    make_finding,
)
from autograder_b2.runner.ssh_session import (
    SSH_DRIVER_EXIT_BINARY_MISSING,
    SSH_DRIVER_EXIT_DRIVER_ERROR,
    SSH_DRIVER_EXIT_INVALID_SCRIPT,
    SSH_DRIVER_EXIT_LOGIN_TIMEOUT,
    SSH_DRIVER_EXIT_SESSION_TIMEOUT,
    SSH_DRIVER_EXIT_SSHD_FAILED,
    build_ssh_driver_config,
    dump_ssh_driver_config,
    new_ssh_nonce,
    parse_ssh_evidence,
    parse_student_ssh_command,
    prepare_ssh_nss_files,
    render_ssh_driver_program,
    ssh_check_failure_message,
    ssh_check_passed,
)

#: 分段结构不合法时写进证据消息的固定短语（设计文档 §3.2 要求"明确写分段结构不合法"）。
SEGMENT_STRUCTURE_MESSAGE = "分段结构不合法"

#: 学生侧 SSH 证据的 message 前缀（明确"这是学生侧失败"，不是环境故障）。
SSH_STUDENT_FAILURE_PREFIX = "ssh check failed"

#: SSH 驱动自行判定的学生侧退出码：登录/会话超时（不是环境故障）。
_SSH_STUDENT_EXIT_CODES = frozenset(
    {0, SSH_DRIVER_EXIT_INVALID_SCRIPT, SSH_DRIVER_EXIT_LOGIN_TIMEOUT, SSH_DRIVER_EXIT_SESSION_TIMEOUT}
)
#: SSH 驱动自判的环境故障退出码：一律升级为 `SandboxUnavailableError`（infrastructure_error）。
_SSH_INFRA_EXIT_CODES = frozenset(
    {SSH_DRIVER_EXIT_SSHD_FAILED, SSH_DRIVER_EXIT_BINARY_MISSING, SSH_DRIVER_EXIT_DRIVER_ERROR}
)


# --- 公开入口 -------------------------------------------------------------------------------


def run_cases(
    package: EvaluationPackage,
    batch: BatchRunInput,
    *,
    probe: Callable[..., Any] | None = None,
    launcher: ProcessLauncher | None = None,
    fixture_provider: FixtureProvider | None = None,
) -> tuple[CaseEvidence, ...]:
    """执行一次批量（`single_run` / `ssh_session`）并返回**按 ordinal 排序**的逐用例证据。

    只有三类情形抛异常：沙箱/练习环境不可用（`SandboxUnavailableError`）、package 不可用
    （`PackageUnavailableError`）、内部缺陷（`InternalEvaluationError`）。学生侧失败一律返回证据。

    `probe` / `launcher` 是仅测试可见的接缝；`fixture_provider` 是生产接缝（B3 fixture 交付）。
    """
    started = time.monotonic()
    try:
        mode = package.execution.mode
        if mode == "per_case":
            raise InternalEvaluationError(
                "run_cases does not execute per_case packages: each case has its own workspace,"
                " so use run_case per case"
            )
        if mode == "email_delivery":
            raise InternalEvaluationError(
                "email_delivery is verified through the delivery-verifier port, not by run_cases"
            )
        cases = resolve_batch_cases(package, batch)
        if mode == "single_run":
            return _run_single_run(
                package,
                batch,
                cases,
                probe=probe,
                launcher=launcher,
                fixture_provider=fixture_provider,
                started=started,
            )
        if mode == "ssh_session":
            return _run_ssh_session(
                package,
                batch,
                cases,
                probe=probe,
                launcher=launcher,
                fixture_provider=fixture_provider,
                started=started,
            )
        raise InternalEvaluationError(f"unsupported execution.mode: {mode!r}")
    except (SandboxUnavailableError, PackageUnavailableError, InternalEvaluationError):
        raise
    except Exception as error:  # 内部异常不得被吞、不得伪装成用例失败或通过
        raise InternalEvaluationError(
            f"run_cases failed internally: {type(error).__name__}: {error}"
        ) from error


def resolve_batch_cases(
    package: EvaluationPackage, batch: BatchRunInput
) -> tuple[PackageCase, ...]:
    """批量必须**恰好覆盖** package 的全部用例（顺序按 ordinal）。

    与 `run_case` 的 `resolve_*` 同源：adapter/policy/用例全部必须严格等于 package 解析结果，
    调用方无法自带一份"更宽松"的策略或改过的用例。
    """
    if batch.adapter != package.adapter:
        raise InternalEvaluationError(
            "batch adapter differs from the package adapter: the comparison policy comes from"
            " the package only (I21)"
        )
    if batch.policy != package.security_policy:
        raise InternalEvaluationError(
            "batch policy differs from the package security_policy: refusing to execute"
        )
    ordered = tuple(sorted(batch.cases, key=lambda item: item.ordinal))
    if not ordered:
        raise InternalEvaluationError("batch contains no cases")
    expected_ids = [case.case_id for case in package.tests]
    actual_ids = [case.case_id for case in ordered]
    if actual_ids != expected_ids:
        raise InternalEvaluationError(
            "batch cases must be exactly the package cases (ordered by ordinal)"
        )
    for case in ordered:
        declared = package.case_by_id(case.case_id)
        if declared is None or declared != case:
            raise InternalEvaluationError(
                f"case {case.case_id} differs between the package and the batch input"
                " (the package is authoritative)"
            )
    return ordered


# --- single_run -----------------------------------------------------------------------------


def split_stdout_segments(stdout_text: str, delimiter: str) -> tuple[str, ...]:
    """按"整行等于 delimiter"切分 stdout，**保留每段的原始行与行尾换行**。

    段数 = 分隔行数 + 1（分隔行本身不属于任何一段）。结构是否合法（段数、空段）由调用方判定：
    本函数只做机械切分，绝不猜测"这个分隔符大概属于哪一段"。
    """
    segments: list[str] = []
    current: list[str] = []
    for line in stdout_text.splitlines(keepends=True):
        if _line_body(line) == delimiter:
            segments.append("".join(current))
            current = []
        else:
            current.append(line)
    segments.append("".join(current))
    return tuple(segments)


def _line_body(line: str) -> str:
    """去掉一个行尾换行符（`\\r\\n` 视为一个终止符；不吞掉正文里的其他字符）。"""
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith(("\n", "\r")):
        return line[:-1]
    return line


def _case_evidence_plan(plan: ExecutionPlan, case: PackageCase) -> ExecutionPlan:
    """证据构造用的逐用例计划：期望值来自用例，其余执行参数来自批量级计划。"""
    return replace(
        plan,
        expected_stdout=case.expected_stdout,
        expected_stderr=case.expected_stderr,
    )


def _run_single_run(
    package: EvaluationPackage,
    batch: BatchRunInput,
    cases: tuple[PackageCase, ...],
    *,
    probe: Callable[..., Any] | None,
    launcher: ProcessLauncher | None,
    fixture_provider: FixtureProvider | None,
    started: float,
) -> tuple[CaseEvidence, ...]:
    adapter = batch.adapter
    declared_policy = batch.policy
    # 只要有一个用例声明 file_checks，本次共享执行就需要"准备 profile"（一次执行一份工作区）。
    needs_prep = any(case.file_checks for case in cases)
    policy = (
        replace(declared_policy, sandbox_profile=SANDBOX_PROFILE_WORKSPACE_PREP)
        if needs_prep
        else declared_policy
    )
    submitted_entry = batch.files[0].path if len(batch.files) == 1 else None
    plan = build_batch_execution_plan(
        adapter=adapter, policy=policy, language=package.language, submitted_entry=submitted_entry
    )
    context = {
        "segment_delimiter": package.execution.segment_delimiter,
        "workspace_prep": needs_prep,
    }
    digests = {
        case.case_id: input_digest_for(
            package,
            files=batch.files,
            case=case,
            adapter=adapter,
            plan=_case_evidence_plan(plan, case),
            policy=policy,
            execution_mode="single_run",
            execution_context=context,
        )
        for case in cases
    }
    workspace = Path(batch.workspace)
    probe_function = probe_sandbox if probe is None else probe
    availability = probe_function(policy)
    if not availability.available:
        raise SandboxUnavailableError(f"sandbox unavailable: {availability.reason}")
    active_launcher = launcher_for(policy) if launcher is None else launcher

    problems = list(materialize_fixtures(package, workspace, fixture_provider))
    if not problems:
        problems.extend(prepare_case_workspace(plan, workspace))
    if problems:
        return tuple(
            _prelaunch_evidence(
                package,
                case,
                plan=plan,
                workspace=workspace,
                digests=digests,
                duration_ms=elapsed_ms(started),
                outcome=CaseOutcome(
                    False, "infrastructure_error", None, (), join_problems(problems)
                ),
            )
            for case in cases
        )
    if not entry_script_exists(plan, workspace):
        return tuple(
            _prelaunch_evidence(
                package,
                case,
                plan=plan,
                workspace=workspace,
                digests=digests,
                duration_ms=elapsed_ms(started),
                outcome=CaseOutcome(
                    False,
                    "crash",
                    None,
                    (),
                    f"entry script is not present in the workspace: {plan.entry_name}",
                ),
            )
            for case in cases
        )
    command: SandboxCommand | None = None
    if needs_prep:
        command = _workspace_prep_command(policy, plan, workspace)
    before = snapshot_workspace(workspace)
    facts = execute_sandbox_plan(
        plan=plan,
        policy=policy,
        workspace=workspace,
        launcher=active_launcher,
        before=before,
        command=command,
    )
    run_outcome = classify_case_outcome(
        facts,
        expected_exit_code=plan.expected_exit_code,
        wall_limit_ms=int(policy.max_wall_seconds * 1000),
    )
    if run_outcome.failure_category is not None:
        return tuple(
            _facts_evidence(
                case,
                outcome=CaseOutcome(
                    False,
                    run_outcome.failure_category,
                    run_outcome.resource_limit_hit,
                    run_outcome.violations,
                    run_outcome.message,
                ),
                plan=_case_evidence_plan(plan, case),
                workspace=workspace,
                digests=digests,
                facts=facts,
                stdout_text=facts.stdout_text,
                digest_input=facts_digest(digests, case),
                duration_ms=elapsed_ms(started),
                segment=case.segment,
                file_checks=evaluate_file_checks(case, workspace),
            )
            for case in cases
        )
    delimiter = package.execution.segment_delimiter
    segment_count = sum(1 for case in cases if case.segment is not None)
    return tuple(
        _single_run_case_evidence(
            case,
            plan=plan,
            workspace=workspace,
            digests=digests,
            facts=facts,
            started=started,
            delimiter=delimiter,
            segment_count=segment_count,
        )
        for case in cases
    )


def _single_run_case_evidence(
    case: PackageCase,
    *,
    plan: ExecutionPlan,
    workspace: Path,
    digests: dict[str, str],
    facts: RunFacts,
    started: float,
    delimiter: str | None,
    segment_count: int,
) -> CaseEvidence:
    case_plan = _case_evidence_plan(plan, case)
    if case.segment is not None:
        segments = split_stdout_segments(facts.stdout_text, delimiter or "")
        structure_ok = (
            delimiter is not None
            and len(segments) == segment_count
            and all(segment != "" for segment in segments)
        )
        if not structure_ok:
            return _facts_evidence(
                case,
                outcome=CaseOutcome(
                    False,
                    "output_mismatch",
                    None,
                    (),
                    f"{SEGMENT_STRUCTURE_MESSAGE}: stdout 的分隔行数量与声明的 segment 数不符",
                ),
                plan=case_plan,
                workspace=workspace,
                digests=digests,
                facts=facts,
                stdout_text=facts.stdout_text,
                digest_input=facts_digest(digests, case),
                duration_ms=elapsed_ms(started),
                segment=case.segment,
                file_checks=(),
            )
        segment_text = segments[case.segment]
        comparison = compare_stdout(
            segment_text,
            case.expected_stdout,
            plan.stdout_strategy,
            plan.numeric_tolerance,
        )
        outcome = CaseOutcome(
            comparison.matched,
            None if comparison.matched else "output_mismatch",
            None,
            (),
            ("passed: segment " + comparison.detail)
            if comparison.matched
            else comparison.detail,
        )
        return _facts_evidence(
            case,
            outcome=outcome,
            plan=case_plan,
            workspace=workspace,
            digests=digests,
            facts=facts,
            stdout_text=segment_text,
            digest_input=facts_digest(digests, case),
            duration_ms=elapsed_ms(started),
            segment=case.segment,
            file_checks=(),
            mismatch_line=comparison.mismatch_line,
        )
    # 声明 file_checks 的用例：不参与分段，运行后直接检查工作区文件（不读 stdout 判定内容）。
    file_checks = evaluate_file_checks(case, workspace)
    failure = classify_file_checks(file_checks)
    if failure.category is None:
        outcome = CaseOutcome(True, None, None, (), "passed: file checks matched")
    else:
        outcome = CaseOutcome(
            False,
            failure.category,  # type: ignore[arg-type]
            None,
            (),
            failure.message,
            missing_outputs=failure.missing,
            mismatched_outputs=failure.mismatched,
        )
    return _facts_evidence(
        case,
        outcome=outcome,
        plan=case_plan,
        workspace=workspace,
        digests=digests,
        facts=facts,
        stdout_text=facts.stdout_text,
        digest_input=facts_digest(digests, case),
        duration_ms=elapsed_ms(started),
        segment=None,
        file_checks=file_checks,
    )


def _workspace_prep_command(
    policy: RunnerPolicy, plan: ExecutionPlan, workspace: Path
) -> SandboxCommand:
    """`workspace_prep` 的命令：B2 固定准备程序 + 学生解释器/脚本/参数（无学生输入驱动）。"""
    script = write_workspace_prepare_program(workspace)
    return SandboxCommand(
        language="python",
        script=script,
        arguments=(
            str(WORKSPACE_PREP_UID),
            str(WORKSPACE_PREP_GID),
            plan.interpreter,
            plan.script,
            *plan.arguments,
        ),
    )


def facts_digest(digests: dict[str, str], case: PackageCase) -> str:
    return digests[case.case_id]


def _prelaunch_evidence(
    package: EvaluationPackage,
    case: PackageCase,
    *,
    plan: ExecutionPlan,
    workspace: Path,
    digests: dict[str, str],
    duration_ms: int,
    outcome: CaseOutcome,
) -> CaseEvidence:
    """运行前就失败（fixture/入口脚本问题）时的证据：不产生 artifacts/resource（没有执行）。"""
    del package
    return build_evidence(
        case=case,
        outcome=outcome,
        plan=_case_evidence_plan(plan, case),
        workspace=workspace,
        digest_input=facts_digest(digests, case),
        stdout_text="",
        stderr_text="",
        exit_code=None,
        duration_ms=duration_ms,
    )


def _facts_evidence(
    case: PackageCase,
    *,
    outcome: CaseOutcome,
    plan: ExecutionPlan,
    workspace: Path,
    digests: dict[str, str],
    facts: RunFacts,
    stdout_text: str,
    digest_input: str,
    duration_ms: int,
    segment: int | None,
    file_checks: tuple[FileCheckResult, ...],
    mismatch_line: int | None = None,
) -> CaseEvidence:
    return build_evidence(
        case=case,
        outcome=outcome,
        plan=plan,
        workspace=workspace,
        digest_input=digest_input,
        stdout_text=stdout_text,
        stderr_text=facts.stderr_text,
        exit_code=facts.exit_code,
        duration_ms=duration_ms,
        fixture_outcomes=facts.fixture_outcomes,
        mismatch_line=mismatch_line,
        changes=facts.changes,
        resource=case_resource_from_peaks(
            facts.peaks if facts.peaks is not None else CgroupV2Peaks(),
            wall_seconds=None if facts.duration_ms is None else facts.duration_ms / 1000,
            limit_hit=limit_hit_for(outcome.resource_limit_hit),
        ),
        segment=segment,
        file_checks=file_checks,
    )


# --- ssh_session ----------------------------------------------------------------------------


def _run_ssh_session(
    package: EvaluationPackage,
    batch: BatchRunInput,
    cases: tuple[PackageCase, ...],
    *,
    probe: Callable[..., Any] | None,
    launcher: ProcessLauncher | None,
    fixture_provider: FixtureProvider | None,
    started: float,
) -> tuple[CaseEvidence, ...]:
    ssh = package.execution.ssh
    if ssh is None:  # pragma: no cover - 解析器已保证；防御内部构造
        raise InternalEvaluationError("ssh_session package is missing execution.ssh")
    adapter = batch.adapter
    policy = replace(batch.policy, sandbox_profile=SANDBOX_PROFILE_SSH)
    workspace = Path(batch.workspace)
    entry_name = batch.files[0].path if len(batch.files) == 1 else "main.sh"
    plan = build_batch_execution_plan(
        adapter=adapter, policy=policy, language=package.language, submitted_entry=entry_name
    )
    # 摘要只绑定非受保护执行参数：口令绝不参与任何摘要。
    context = {
        "ssh_host": ssh.host,
        "ssh_port": ssh.port,
        "ssh_username": ssh.username,
        "ssh_login_timeout_seconds": ssh.login_timeout_seconds,
        "ssh_session_timeout_seconds": ssh.session_timeout_seconds,
    }
    digests = {
        case.case_id: input_digest_for(
            package,
            files=batch.files,
            case=case,
            adapter=adapter,
            plan=_case_evidence_plan(plan, case),
            policy=policy,
            execution_mode="ssh_session",
            execution_context=context,
        )
        for case in cases
    }
    # 环境可用性判定在**任何进程被创建之前**完成，且优先于"学生脚本不合法"：不可用绝不折算成
    # 学生答错（与 per_case 的 `run_case` 顺序一致）。
    sshd_path = resolve_ssh_binary(SSH_DAEMON_CANDIDATES)
    client_path = resolve_ssh_binary(SSH_CLIENT_CANDIDATES)
    keygen_path = resolve_ssh_binary(SSH_KEYGEN_CANDIDATES)
    if sshd_path is None or client_path is None or keygen_path is None:
        raise SandboxUnavailableError(
            "ssh practice environment unavailable: ssh/sshd/ssh-keygen is missing on this host"
        )
    probe_function = probe_sandbox if probe is None else probe
    availability = probe_function(policy)
    if not availability.available:
        raise SandboxUnavailableError(f"sandbox unavailable: {availability.reason}")
    active_launcher = launcher_for(policy) if launcher is None else launcher
    fixture_problems = list(materialize_fixtures(package, workspace, fixture_provider))
    if not fixture_problems:
        fixture_problems.extend(prepare_case_workspace(plan, workspace))
    if fixture_problems:
        return tuple(
            _prelaunch_evidence(
                package,
                case,
                plan=plan,
                workspace=workspace,
                digests=digests,
                duration_ms=elapsed_ms(started),
                outcome=CaseOutcome(
                    False,
                    "infrastructure_error",
                    None,
                    (),
                    join_problems(fixture_problems),
                ),
            )
            for case in cases
        )
    student_script = _read_student_script(workspace, entry_name)
    if student_script is None:
        return tuple(
            _prelaunch_evidence(
                package,
                case,
                plan=plan,
                workspace=workspace,
                digests=digests,
                duration_ms=elapsed_ms(started),
                outcome=CaseOutcome(
                    False,
                    "crash",
                    None,
                    (),
                    f"entry script is not present in the workspace: {entry_name}",
                ),
            )
            for case in cases
        )
    first_line = student_script.splitlines()[0] if student_script.splitlines() else ""
    login_command, invalid_reason = parse_student_ssh_command(first_line)
    if login_command is None:
        # 学生首行不是合法 ssh 登录：三条证据全部学生侧失败，且**不启动任何 shell/沙箱**。
        message = (
            "the first line is not a valid ssh login command: "
            + (invalid_reason or "rejected")
        )
        return tuple(
            _prelaunch_evidence(
                package,
                case,
                plan=plan,
                workspace=workspace,
                digests=digests,
                duration_ms=elapsed_ms(started),
                outcome=CaseOutcome(False, "output_mismatch", None, (), message),
            )
            for case in cases
        )
    run_dir = workspace.parent / f"{workspace.name}.ssh"
    try:
        paths = create_ssh_sandbox_paths(run_dir)
        prepare_ssh_nss_files(paths, ssh.username, ssh.password)
        _write_ssh_driver(workspace)
        nonce = new_ssh_nonce()
        config_text = render_nsjail_ssh_config(policy, workspace, paths)
        driver_config = build_ssh_driver_config(
            nonce=nonce,
            entry_name=entry_name,
            ssh=ssh,
            sshd_path=sshd_path,
            ssh_client_path=client_path,
            ssh_keygen_path=keygen_path,
            max_output_bytes=policy.max_output_bytes,
        )
        command = SandboxCommand(
            language="python",
            script=sandbox_script_path(policy, SSH_DRIVER_SCRIPT_NAME),
            arguments=(),
        )
        before = snapshot_workspace(workspace)
        facts = execute_sandbox_plan(
            plan=plan,
            policy=policy,
            workspace=workspace,
            launcher=active_launcher,
            before=before,
            command=command,
            config_text=config_text,
            stdin_text=dump_ssh_driver_config(driver_config),
        )
        evidence = parse_ssh_evidence(facts.stderr_text, nonce)
        run_failure = _ssh_run_level_failure(facts)
        if run_failure is not None:
            return tuple(
                _ssh_evidence(
                    case,
                    outcome=run_failure,
                    plan=plan,
                    workspace=workspace,
                    digests=digests,
                    facts=facts,
                    exit_code=facts.exit_code,
                    duration_ms=elapsed_ms(started),
                )
                for case in cases
            )
        if facts.exit_code in _SSH_INFRA_EXIT_CODES:
            raise SandboxUnavailableError(
                "ssh practice environment failed to start: "
                + (evidence.driver_error or "driver reported an infrastructure failure")
            )
        if facts.exit_code not in _SSH_STUDENT_EXIT_CODES:
            raise SandboxUnavailableError(
                "ssh driver terminated abnormally; the practice environment is not usable"
            )
        if not evidence.service_ready:
            raise SandboxUnavailableError(
                "ssh practice environment did not report a listening sshd on 127.0.0.1"
            )
        if evidence.invalid_first_line:
            message = "the first line is not a valid ssh login command: rejected by the driver"
            return tuple(
                _ssh_evidence(
                    case,
                    outcome=CaseOutcome(False, "output_mismatch", None, (), message),
                    plan=plan,
                    workspace=workspace,
                    digests=digests,
                    facts=facts,
                    exit_code=facts.exit_code,
                    duration_ms=elapsed_ms(started),
                )
                for case in cases
            )
        ssh_client_success = evidence.ssh_exit_code == 0 and not evidence.ssh_killed
        built: list[CaseEvidence] = []
        for case in cases:
            check = case.ssh_check or ""
            passed = ssh_check_passed(
                check, evidence, ssh_client_success=ssh_client_success
            )
            if passed:
                outcome = CaseOutcome(True, None, None, (), f"passed: ssh check {check}")
            else:
                outcome = CaseOutcome(
                    False,
                    "output_mismatch",
                    None,
                    (),
                    SSH_STUDENT_FAILURE_PREFIX
                    + " "
                    + check
                    + ": "
                    + ssh_check_failure_message(check),
                )
            built.append(
                _ssh_evidence(
                    case,
                    outcome=outcome,
                    plan=plan,
                    workspace=workspace,
                    digests=digests,
                    facts=facts,
                    exit_code=(
                        evidence.ssh_exit_code
                        if evidence.ssh_exit_code is not None
                        else facts.exit_code
                    ),
                    duration_ms=elapsed_ms(started),
                )
            )
        return tuple(built)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def _read_student_script(workspace: Path, entry_name: str) -> str | None:
    try:
        target = (Path(workspace) / entry_name).resolve()
    except OSError:
        return None
    if not target.is_relative_to(Path(workspace).resolve()) or not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _write_ssh_driver(workspace: Path) -> None:
    target = Path(workspace) / SSH_DRIVER_SCRIPT_NAME
    if target.exists() or target.is_symlink():
        raise InternalEvaluationError(
            f"reserved ssh driver path already exists: {SSH_DRIVER_SCRIPT_NAME}"
        )
    target.write_text(render_ssh_driver_program(), encoding="utf-8")


def _ssh_run_level_failure(facts: RunFacts) -> CaseOutcome | None:
    """SSH 批量执行的**运行级**失败（整次运行同类别，均不得分）。

    只有宿主侧可观测的整次运行问题在这里判定：墙钟超时、输出截断、seccomp 杀进程。
    驱动自行判定的登录/会话超时是**学生侧**失败（退出码 23/24），不在这里。
    """
    if facts.timed_out:
        return CaseOutcome(False, "timeout", "wall", (), "ssh session exceeded the wall-clock limit")
    if facts.stdout_truncated or facts.stderr_truncated:
        return CaseOutcome(
            False,
            "resource_limit",
            "output",
            (),
            "ssh session output exceeded max_output_bytes",
        )
    if _killed_by(facts.exit_code, SIGSYS_NUMBER):
        finding = make_finding(
            category="sandbox_violation",
            severity="high",
            message="sandbox terminated the ssh driver: denied system call (seccomp default-deny)",
            rule_id="runner.seccomp",
        )
        return CaseOutcome(
            False,
            "security_blocked",
            None,
            (finding,),
            "sandbox terminated the ssh driver: denied system call (seccomp default-deny)",
        )
    return None


def _ssh_evidence(
    case: PackageCase,
    *,
    outcome: CaseOutcome,
    plan: ExecutionPlan,
    workspace: Path,
    digests: dict[str, str],
    facts: RunFacts,
    exit_code: int | None,
    duration_ms: int,
) -> CaseEvidence:
    """SSH 用例证据：`stdout_digest`/摘录 = 学生可见的会话输出；驱动证据不进证据字段。

    驱动 stderr 是**宿主自有证据通道**（带运行级 nonce）：它含运行时事实（如 sshd 日志摘要），
    既不学生可见、也不是跨运行的确定值，因此只用于判定，不写进任何可重放的证据字段。
    """
    return build_evidence(
        case=case,
        outcome=outcome,
        plan=_case_evidence_plan(plan, case),
        workspace=workspace,
        digest_input=digests[case.case_id],
        stdout_text=facts.stdout_text,
        stderr_text="",
        exit_code=exit_code,
        duration_ms=duration_ms,
        changes=facts.changes,
        segment=None,
        file_checks=(),
    )


__all__ = [
    "SEGMENT_STRUCTURE_MESSAGE",
    "SSH_STUDENT_FAILURE_PREFIX",
    "facts_digest",
    "resolve_batch_cases",
    "run_cases",
    "split_stdout_segments",
]
