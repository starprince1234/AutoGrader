"""runner 批量扩展单元测试（Unix 作业评测：`execution`/分段/file_checks/fixture/SSH 配置）。

**范围声明（诚实边界）**：本文件在 Windows 主机上运行，**不启动真实 nsjail/sshd/ssh**。
被验证的是 run_cases 的**流水线逻辑**（一次执行、分段判定、文件检查、证据摘要、SSH 命令白名单、
配置渲染的确定性与强隔离项）与 package 解析期的严格拒绝；真实沙箱/SSH 证据由
`tests/linux/test_nsjail_batch_acceptance.py` 在 Linux 上产出。SSH 的 `/etc/shadow` 哈希需要
Linux 的 `crypt` 模块，因此该项在本机 skip。

测试分组：

1. `TestExecutionPackageValidation`   `execution` 与用例级执行字段的模式互斥/必需（逐条拒绝）
2. `TestSegmentation`                 整行分隔符切分、段内容保留、空段/段数结构判定
3. `TestFileChecksEvaluation`         lstat 普通文件、权限位、摘要、符号链接与父目录逃逸
4. `TestFixtures`                     FixtureProvider 端口 + 物化（缺失/摘要不符/同名冲突/依赖异常）
5. `TestSingleRunBatch`               一次执行多条证据、运行级失败同类别、prep profile 触发、单次启动
6. `TestEvidenceDigest`               segment/file_checks 参与证据摘要（缺省不进载荷）
7. `TestSshCommandWhitelist`          严格 argv 白名单（拒绝 -o/ProxyCommand/重定向/第二条命令）
8. `TestSshConfigRendering`           SSH profile 配置：lo、uidmap 顺序、root=练习根、client chroot、seccomp
9. `TestSshEvidence`                  nonce 门控、证据解析、pass/fail 判定
10. `TestWorkspacePrep`               准备程序内容/配置 profile/普通题配置不变
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import replace
from pathlib import Path

import pytest

from autograder_b2.domain import SandboxUnavailableError
from autograder_b2.domain.evidence import BatchRunInput, FileCheckResult
from autograder_b2.runner import ssh_session as ssh_session_module  # noqa: F401  (导入路径自检)
from autograder_b2.runner.adapters import (
    PREPARE_PROGRAM_FILENAME,
    build_execution_plan,
    render_workspace_prepare_program,
)
from autograder_b2.runner.file_checks import (
    classify_file_checks,
    evaluate_file_checks,
    format_mode,
)
from autograder_b2.runner.fixtures import FixtureContent, materialize_fixtures
from autograder_b2.runner.package import (
    EvaluationPackage,
    PackageRejected,
    parse_evaluation_package,
)
from autograder_b2.runner.policy import (
    SANDBOX_PROFILE_DEFAULT,
    SANDBOX_PROFILE_SSH,
    SANDBOX_PROFILE_WORKSPACE_PREP,
    WORKSPACE_PREP_GID,
    WORKSPACE_PREP_SECCOMP_ALLOW_EXTRA,
    WORKSPACE_PREP_UID,
    RunnerPolicy,
    SandboxAvailability,
    create_ssh_sandbox_paths,
    nsjail_config_digest,
    policy_digest,
    render_nsjail_config,
    render_nsjail_policy_body,
    render_nsjail_ssh_config,
    ssh_seccomp_policy_lines,
    workspace_prep_config_digest,
    workspace_prep_seccomp_policy_lines,
)
from autograder_b2.runner.run_case import (
    evidence_digest_of,
    input_digest_for,
)
from autograder_b2.runner.run_cases import (
    SEGMENT_STRUCTURE_MESSAGE,
    resolve_batch_cases,
    run_cases,
    split_stdout_segments,
)
from autograder_b2.runner.ssh_session import (
    SSH_EVIDENCE_PREFIX,
    SshRunEvidence,
    build_ssh_driver_config,
    parse_ssh_evidence,
    parse_student_ssh_command,
    render_ssh_client_argv,
    render_ssh_driver_program,
    render_ssh_nss_files,
    ssh_check_passed,
)

CASE_ID_1 = "11111111-1111-4111-8111-111111111111"
CASE_ID_2 = "22222222-2222-4222-8222-222222222222"
CASE_ID_3 = "33333333-3333-4333-8333-333333333333"
QUESTION_ID = "44444444-4444-4444-8444-444444444444"
PACKAGE_DIGEST = "a" * 64
FIXTURE_DIGEST = "b" * 64
SUBMISSION_DIGEST = "c" * 64
DELIMITER = "=====B2-SEGMENT====="
SSH_PASSWORD = "practice-secret-42"


# --- 夹具构造 -------------------------------------------------------------------------------


def merge(base: dict, overrides: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def case_payload(case_id: str, ordinal: int, **overrides: object) -> dict:
    case = {
        "case_id": case_id,
        "name": f"case {ordinal}",
        "ordinal": ordinal,
        "weight": 1,
        "visible_to_student": True,
        "stdin": None,
        "args": [],
        "expected_stdout": None,
        "expected_stderr": None,
        "expected_exit_code": None,
    }
    return merge(case, overrides)


def package_payload(**overrides: object) -> dict:
    base = {
        "question_version_id": QUESTION_ID,
        "package_digest": PACKAGE_DIGEST,
        "language": "python",
        "adapter": {
            "kind": "stdin",
            "args": [],
            "stdin": None,
            "expect_exit_code": None,
            "comparison": {"stdout": "exact", "stderr": "ignored", "numeric_tolerance": None},
        },
        "tests": [case_payload(CASE_ID_1, 0, expected_stdout="one\n")],
        "security_policy": {
            "network": False,
            "read_only_root": True,
            "allow_root": False,
            "max_memory_bytes": 96 * 1024 * 1024,
            "max_cpu_seconds": 10,
            "max_wall_seconds": 20,
            "max_output_bytes": 65536,
            "max_processes": 16,
            "max_files_created": 32,
        },
        "max_files": 5,
        "max_total_bytes": 100000,
        "created_at": "2026-01-01T00:00:00Z",
    }
    return merge(base, overrides)


def single_run_payload(*, segments: tuple[str, ...] = ("one\n", "two\n"), with_file_checks: bool = False) -> dict:
    """single_run 包：每段一个用例（或一个 file_checks 用例）。"""
    cases = []
    if with_file_checks:
        cases.append(
            case_payload(
                CASE_ID_1,
                0,
                file_checks=[{"path": "week6_1.txt", "content_sha256": FIXTURE_DIGEST, "mode": None}],
            )
        )
        execution: dict = {"mode": "single_run"}
    else:
        for index, expected in enumerate(segments):
            cases.append(
                case_payload(
                    (CASE_ID_1, CASE_ID_2, CASE_ID_3)[index],
                    index,
                    segment=index,
                    expected_stdout=expected,
                )
            )
        execution = {"mode": "single_run", "segment_delimiter": DELIMITER}
    return package_payload(language="bash", tests=cases, execution=execution)


def ssh_payload(*, ssh_overrides: dict | None = None) -> dict:
    ssh = {
        "host": "127.0.0.1",
        "port": 22,
        "username": "user01",
        "password": SSH_PASSWORD,
        "login_timeout_seconds": 10,
        "session_timeout_seconds": 10,
    }
    if ssh_overrides:
        ssh.update(ssh_overrides)
    cases = [
        case_payload(CASE_ID_1, 0, ssh_check="service_ready"),
        case_payload(CASE_ID_2, 1, ssh_check="auth_succeeded"),
        case_payload(CASE_ID_3, 2, ssh_check="session_closed"),
    ]
    return package_payload(
        language="bash",
        tests=cases,
        execution={"mode": "ssh_session", "ssh": ssh},
    )


def load(payload: dict) -> EvaluationPackage:
    return parse_evaluation_package(payload)


def make_batch(package: EvaluationPackage, workspace: Path) -> BatchRunInput:
    from autograder_b2.domain import EnvelopeFile

    entry = "main.sh" if package.language == "bash" else "main.py"
    return BatchRunInput(
        submission_id="55555555-5555-4555-8555-555555555555",
        evaluation_run_id="66666666-6666-4666-8666-666666666666",
        correlation_id="77777777-7777-4777-8777-777777777777",
        workspace=workspace,
        files=(
            EnvelopeFile(
                path=entry,
                content_sha256=SUBMISSION_DIGEST,
                size_bytes=10,
                storage_ref=entry,
            ),
        ),
        cases=package.tests,
        adapter=package.adapter,
        policy=package.security_policy,
    )


class FakeKiller:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int | None]] = []

    def kill_tree(self, pid: int, process_group: int | None):
        from autograder_b2.runner.run_case import KillReport

        self.calls.append((pid, process_group))
        return KillReport(pid, process_group, True, "fake SIGKILL")


class FakeProcess:
    def __init__(self, *, exit_code: int | None, killer: FakeKiller, timeout: bool, alive: bool) -> None:
        self.pid = 4242
        self.process_group = 4242
        self._exit_code = exit_code
        self._killer = killer
        self._timeout = timeout
        self._alive = alive
        self.waits = 0
        self.kill_calls = 0

    def wait(self, timeout_seconds: float) -> int | None:
        del timeout_seconds
        self.waits += 1
        if self._alive:
            return None
        if self._timeout and self.waits == 1:
            return None
        return self._exit_code

    def kill_tree(self):
        self.kill_calls += 1
        return self._killer.kill_tree(self.pid, self.process_group)


class FakeLauncher:
    """把"沙箱内发生的事情"变成可控输入；配置文本从 argv 的 `--config` 读回（像真 nsjail）。"""

    def __init__(
        self,
        *,
        exit_code: int | None = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        sandbox_log: bytes | None = None,
        timeout: bool = False,
        alive: bool = False,
        created: tuple[tuple[str, bytes, int | None], ...] = (),
        created_dirs: tuple[str, ...] = (),
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.sandbox_log = sandbox_log
        self.timeout = timeout
        self.alive = alive
        self.created = created
        self.created_dirs = created_dirs
        self.killer = FakeKiller()
        self.starts = 0
        self.argv: list[str] = []
        self.config_text = ""
        self.stdin_text: str | None = None
        self.process: FakeProcess | None = None

    def start(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin_text: str | None,
        stdin_path: Path,
        stdout_path: Path,
        stderr_path: Path,
    ) -> FakeProcess:
        del env
        self.starts += 1
        self.argv = list(argv)
        self.stdin_text = stdin_text
        config_index = argv.index("--config")
        self.config_text = Path(argv[config_index + 1]).read_text(encoding="utf-8")
        Path(stdout_path).write_bytes(self.stdout)
        Path(stderr_path).write_bytes(self.stderr)
        if self.sandbox_log is not None:
            Path(argv[argv.index("--log") + 1]).write_bytes(self.sandbox_log)
        for relative in self.created_dirs:
            (Path(cwd) / relative).mkdir(parents=True, exist_ok=True)
        for relative, content, mode in self.created:
            target = Path(cwd) / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            if mode is not None:
                target.chmod(mode)
        self.process = FakeProcess(
            exit_code=self.exit_code, killer=self.killer, timeout=self.timeout, alive=self.alive
        )
        return self.process


def available_probe(policy: RunnerPolicy) -> SandboxAvailability:
    del policy
    return SandboxAvailability(available=True, checks=(), reason=None)


def prepare_workspace(tmp_path: Path, entry: str = "main.sh") -> Path:
    workspace = tmp_path / "batch-case"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / entry).write_text("#!/bin/bash\n", encoding="utf-8")
    return workspace


# =============================================================================================
# 1. package 解析：execution 模式与字段
# =============================================================================================


class TestExecutionPackageValidation:
    def test_per_case_default_is_unchanged(self) -> None:
        package = load(package_payload())
        assert package.execution.mode == "per_case"
        assert package.execution.segment_delimiter is None
        assert package.execution.email is None
        assert package.execution.ssh is None
        assert package.tests[0].segment is None
        assert package.tests[0].file_checks == ()

    def test_single_run_segments_are_parsed(self) -> None:
        package = load(single_run_payload())
        assert package.execution.mode == "single_run"
        assert package.execution.segment_delimiter == DELIMITER
        assert [case.segment for case in package.tests] == [0, 1]

    def test_single_run_file_checks_are_parsed(self) -> None:
        payload = single_run_payload(with_file_checks=True)
        payload["adapter"]["kind"] = "file"
        payload["adapter"]["files"] = [
            {"path": "week6_1.txt", "content_sha256": FIXTURE_DIGEST, "mount": "input"}
        ]
        package = load(payload)
        check = package.tests[0].file_checks[0]
        assert check.path == "week6_1.txt"
        assert check.content_sha256 == FIXTURE_DIGEST
        assert check.mode is None
        assert package.adapter.kind == "file"

    def test_single_run_file_adapter_is_accepted_for_file_checks(self) -> None:
        """第 6 题形态：file adapter + single_run + input fixture + file_checks 必须被接受。"""
        payload = package_payload(
            language="bash",
            adapter={
                "kind": "file",
                "files": [
                    {"path": "week6_1.txt", "content_sha256": FIXTURE_DIGEST, "mount": "input"},
                    {"path": "week6_2.dat", "content_sha256": FIXTURE_DIGEST, "mount": "input"},
                ],
            },
            tests=[
                case_payload(
                    CASE_ID_1,
                    0,
                    file_checks=[{"path": "week6_1.txt", "content_sha256": FIXTURE_DIGEST, "mode": None}],
                ),
                case_payload(
                    CASE_ID_2,
                    1,
                    file_checks=[{"path": "week6_2.dat", "content_sha256": None, "mode": "0744"}],
                ),
            ],
            execution={"mode": "single_run"},
        )
        package = load(payload)
        assert package.execution.mode == "single_run"
        assert package.adapter.kind == "file"

    def test_single_run_segments_require_stdin_adapter(self) -> None:
        payload = package_payload(
            language="bash",
            adapter={"kind": "file"},
            tests=[case_payload(CASE_ID_1, 0, segment=0)],
            execution={"mode": "single_run", "segment_delimiter": "x" * 8},
        )
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == "invalid_execution"

    def test_ssh_session_is_parsed_and_password_is_not_in_repr(self) -> None:
        package = load(ssh_payload())
        ssh = package.execution.ssh
        assert ssh is not None
        assert ssh.username == "user01"
        assert ssh.password == SSH_PASSWORD
        assert SSH_PASSWORD not in repr(package)
        assert SSH_PASSWORD not in repr(package.execution)
        assert [case.ssh_check for case in package.tests] == [
            "service_ready",
            "auth_succeeded",
            "session_closed",
        ]

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            # execution 自身
            ({"execution": {"mode": "nope"}}, "invalid_execution"),
            ({"execution": {"segment_delimiter": "x" * 8}}, "invalid_execution"),
            ({"execution": {"mode": "per_case", "segment_delimiter": "x" * 8}}, "invalid_execution"),
            (
                {
                    "execution": {
                        "mode": "per_case",
                        "email": {
                            "attempt": 1,
                            "subject_template": "{student_number}_{attempt}",
                        },
                    }
                },
                "invalid_execution",
            ),
            # per_case 禁止用例级 segment/ssh_check/email_check
            (
                {"execution": {"mode": "per_case"}, "tests": [case_payload(CASE_ID_1, 0, segment=0)]},
                "invalid_execution",
            ),
            # single_run：adapter 必须 stdin；每用例恰一个 segment 或 file_checks；segment 完整集合
            (
                {
                    "adapter": {"kind": "file"},
                    "execution": {"mode": "single_run", "segment_delimiter": "x" * 8},
                    "tests": [case_payload(CASE_ID_1, 0, segment=0)],
                },
                "invalid_execution",
            ),
            (
                {
                    "execution": {"mode": "single_run", "segment_delimiter": "x" * 8},
                    "tests": [case_payload(CASE_ID_1, 0, segment=0), case_payload(CASE_ID_2, 1)],
                },
                "invalid_execution",
            ),
            (
                {
                    "execution": {"mode": "single_run"},
                    "tests": [case_payload(CASE_ID_1, 0, segment=0)],
                },
                "invalid_execution",
            ),
            (
                {
                    "execution": {"mode": "single_run", "segment_delimiter": "x" * 8},
                    "tests": [case_payload(CASE_ID_1, 0, segment=1)],
                },
                "invalid_segment",
            ),
            (
                {
                    "execution": {"mode": "single_run", "segment_delimiter": "x" * 8},
                    "tests": [case_payload(CASE_ID_1, 0, segment=0), case_payload(CASE_ID_2, 1, segment=0)],
                },
                "invalid_segment",
            ),
            (
                {
                    "execution": {"mode": "single_run", "segment_delimiter": "x" * 8},
                    "tests": [
                        case_payload(CASE_ID_1, 0, segment=0),
                        case_payload(CASE_ID_2, 1, segment=2),
                    ],
                },
                "invalid_segment",
            ),
            # 分隔符形态：长度/行尾空白/非可打印
            (
                {
                    "execution": {"mode": "single_run", "segment_delimiter": "short"},
                    "tests": [case_payload(CASE_ID_1, 0, segment=0)],
                },
                "invalid_execution",
            ),
            (
                {
                    "execution": {"mode": "single_run", "segment_delimiter": "x" * 7 + " "},
                    "tests": [case_payload(CASE_ID_1, 0, segment=0)],
                },
                "invalid_execution",
            ),
            (
                {
                    "execution": {"mode": "single_run", "segment_delimiter": "x" * 7 + "\t"},
                    "tests": [case_payload(CASE_ID_1, 0, segment=0)],
                },
                "invalid_execution",
            ),
            (
                {
                    "execution": {"mode": "single_run", "segment_delimiter": "x" * 7 + "\x00"},
                    "tests": [case_payload(CASE_ID_1, 0, segment=0)],
                },
                "invalid_execution",
            ),
        ],
    )
    def test_single_run_and_per_case_rejections(self, overrides: dict, reason: str) -> None:
        payload = package_payload(**overrides)
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == reason

    def test_ssh_session_rejections(self) -> None:
        # 缺 ssh 配置
        payload = ssh_payload()
        payload["execution"].pop("ssh")
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == "invalid_execution"
        # ssh_session 不接受 segment_delimiter/email
        email_config = {
            "attempt": 1,
            "subject_template": "{student_number}_{attempt}",
        }
        for extra in ({"segment_delimiter": "x" * 8}, {"email": email_config}):
            payload = ssh_payload()
            payload["execution"].update(extra)
            with pytest.raises(PackageRejected) as excinfo:
                parse_evaluation_package(payload)
            assert excinfo.value.reason == "invalid_execution"
        # adapter 必须是 stdin、语言必须是 bash。改 kind 必须保留 adapter 的其余字段：
        # 用浅层 `payload.update({"adapter": ...})` 会连 comparison 一起丢掉，于是被更早的
        # adapter 校验拒绝（这正是本用例最初记录下来的错误原因，不是 ssh 规则本身）。
        payload = ssh_payload()
        payload["adapter"]["kind"] = "file"
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == "invalid_execution"
        payload = ssh_payload()
        payload["language"] = "python"
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == "invalid_execution"
        # 每个用例必须恰好一个 ssh_check
        payload = ssh_payload()
        payload["tests"][0].pop("ssh_check")
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == "invalid_ssh_check"

    @pytest.mark.parametrize(
        ("ssh_overrides", "reason"),
        [
            ({"host": "0.0.0.0"}, "invalid_ssh_check"),
            ({"port": 0}, "invalid_ssh_check"),
            ({"port": 65536}, "invalid_ssh_check"),
            ({"username": "bad user"}, "invalid_ssh_check"),
            ({"username": "a/b"}, "invalid_ssh_check"),
            ({"password": ""}, "invalid_ssh_check"),
            ({"login_timeout_seconds": 0}, "invalid_ssh_check"),
            ({"session_timeout_seconds": 120}, "invalid_ssh_check"),
        ],
    )
    def test_ssh_config_fields_are_strict(self, ssh_overrides: dict, reason: str) -> None:
        payload = ssh_payload()
        payload["execution"]["ssh"].update(ssh_overrides)
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == reason

    def test_ssh_checks_must_be_unique(self) -> None:
        payload = ssh_payload()
        payload["tests"][1]["ssh_check"] = "service_ready"
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == "invalid_ssh_check"

    def test_ssh_session_does_not_require_all_three_checks(self) -> None:
        """契约只要求"每种至多一个"，不要求三种齐全（题库可以只声明其中一条）。"""
        payload = ssh_payload()
        payload["tests"] = [case_payload(CASE_ID_1, 0, ssh_check="auth_succeeded")]
        package = load(payload)
        assert [case.ssh_check for case in package.tests] == ["auth_succeeded"]

    def test_file_checks_are_validated(self) -> None:
        payload = package_payload(execution={"mode": "per_case"})
        payload["tests"][0]["file_checks"] = [
            {"path": "a.txt", "content_sha256": FIXTURE_DIGEST, "mode": "0744"}
        ]
        package = load(payload)
        assert package.tests[0].file_checks[0].mode == "0744"
        for bad, reason in (
            ([{"path": "../a.txt", "content_sha256": FIXTURE_DIGEST, "mode": None}], "path_not_allowed"),
            ([{"path": "a.txt", "content_sha256": None, "mode": None}], "invalid_file_check"),
            ([{"path": "a.txt", "content_sha256": FIXTURE_DIGEST, "mode": "777"}], "invalid_file_check"),
            ([{"path": "a.txt", "content_sha256": FIXTURE_DIGEST, "mode": "0999"}], "invalid_file_check"),
            (
                [
                    {"path": "a.txt", "content_sha256": FIXTURE_DIGEST, "mode": None},
                    {"path": "a.txt", "content_sha256": FIXTURE_DIGEST, "mode": None},
                ],
                "invalid_file_check",
            ),
        ):
            payload = package_payload()
            payload["tests"][0]["file_checks"] = bad
            with pytest.raises(PackageRejected) as excinfo:
                parse_evaluation_package(payload)
            assert excinfo.value.reason == reason


# =============================================================================================
# 2. 分段
# =============================================================================================


class TestSegmentation:
    def test_whole_line_delimiter_splits_and_keeps_line_endings(self) -> None:
        stdout = f"alpha\r\n{DELIMITER}\nbeta\r\n"
        assert split_stdout_segments(stdout, DELIMITER) == ("alpha\r\n", "beta\r\n")

    def test_delimiter_must_be_the_whole_line(self) -> None:
        stdout = f"prefix{DELIMITER}\n{DELIMITER}suffix\n"
        assert split_stdout_segments(stdout, DELIMITER) == (f"prefix{DELIMITER}\n{DELIMITER}suffix\n",)

    def test_missing_trailing_newline_is_preserved(self) -> None:
        stdout = f"one\n{DELIMITER}\ntwo"
        assert split_stdout_segments(stdout, DELIMITER) == ("one\n", "two")

    def test_empty_segments_are_observable(self) -> None:
        stdout = f"{DELIMITER}\n{DELIMITER}\n"
        assert split_stdout_segments(stdout, DELIMITER) == ("", "", "")


# =============================================================================================
# 3. file_checks 检查
# =============================================================================================


class TestFileChecksEvaluation:
    def _case(self, payload_overrides: list[dict]):
        payload = package_payload()
        payload["tests"][0]["file_checks"] = payload_overrides
        return load(payload)

    def test_content_matches(self, tmp_path: Path) -> None:
        target = tmp_path / "week6_1.txt"
        target.write_bytes(b"hello\n")
        from autograder_b2.domain.clock import sha256_hex

        package = self._case(
            [{"path": "week6_1.txt", "content_sha256": sha256_hex(b"hello\n"), "mode": None}]
        )
        results = evaluate_file_checks(package.tests[0], tmp_path)
        assert len(results) == 1
        result = results[0]
        assert result.path == "week6_1.txt"
        assert result.expected_sha256 == sha256_hex(b"hello\n")
        assert result.actual_sha256 == sha256_hex(b"hello\n")
        assert result.expected_mode is None
        assert result.matched is True
        assert classify_file_checks(results).category is None

    @pytest.mark.skipif(os.name != "posix", reason="Windows 不提供 POSIX 权限位语义")
    def test_mode_matches_on_posix(self, tmp_path: Path) -> None:
        target = tmp_path / "week6_2.dat"
        target.write_bytes(b"x")
        target.chmod(0o744)
        package = self._case([{"path": "week6_2.dat", "content_sha256": None, "mode": "0744"}])
        result = evaluate_file_checks(package.tests[0], tmp_path)[0]
        assert result.actual_mode == "0744"
        assert result.matched is True

    def test_missing_file_is_missing_output(self, tmp_path: Path) -> None:
        package = self._case([{"path": "nope.txt", "content_sha256": FIXTURE_DIGEST, "mode": None}])
        results = evaluate_file_checks(package.tests[0], tmp_path)
        failure = classify_file_checks(results)
        assert failure.category == "missing_output"
        assert failure.missing == ("nope.txt",)

    def test_mode_mismatch_is_output_mismatch(self, tmp_path: Path) -> None:
        target = tmp_path / "week6_2.dat"
        target.write_bytes(b"x")
        target.chmod(0o644)
        package = self._case([{"path": "week6_2.dat", "content_sha256": None, "mode": "0744"}])
        failure = classify_file_checks(evaluate_file_checks(package.tests[0], tmp_path))
        assert failure.category == "output_mismatch"
        assert failure.mismatched == ("week6_2.dat",)

    def test_symlink_is_not_a_regular_file(self, tmp_path: Path) -> None:
        real = tmp_path / "real.txt"
        real.write_bytes(b"data")
        link = tmp_path / "link.txt"
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            pytest.skip("本机不允许创建符号链接")
        package = self._case([{"path": "link.txt", "content_sha256": None, "mode": "0644"}])
        result = evaluate_file_checks(package.tests[0], tmp_path)[0]
        assert result.matched is False
        assert result.actual_mode is None

    def test_symlinked_parent_escaping_the_workspace_is_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"secret")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        try:
            os.symlink(outside, workspace / "sub")
        except (OSError, NotImplementedError):
            pytest.skip("本机不允许创建符号链接")
        package = self._case([{"path": "sub/secret.txt", "content_sha256": None, "mode": "0644"}])
        result = evaluate_file_checks(package.tests[0], workspace)[0]
        assert result.matched is False and result.actual_mode is None

    def test_mode_text_is_four_octal_digits(self) -> None:
        assert format_mode(0o744) == "0744"
        assert format_mode(0o4755) == "4755"
        assert format_mode(0o644) == "0644"


# =============================================================================================
# 4. fixture 物化
# =============================================================================================


class FakeFixtureProvider:
    def __init__(self, items: tuple[FixtureContent, ...] = (), *, error: Exception | None = None) -> None:
        self.items = items
        self.error = error
        self.calls: list[str] = []

    def fetch_inputs(self, *, question_version_id: str) -> tuple[FixtureContent, ...]:
        self.calls.append(question_version_id)
        if self.error is not None:
            raise self.error
        return self.items


def fixture_package(content: bytes = b"fixture\n", path: str = "input.txt") -> EvaluationPackage:
    from autograder_b2.domain.clock import sha256_hex

    payload = package_payload(
        adapter={
            "files": [
                {
                    "path": path,
                    "content_sha256": sha256_hex(content),
                    "mount": "input",
                }
            ]
        }
    )
    return load(payload)


class TestFixtures:
    def test_materializes_and_verifies_bytes(self, tmp_path: Path) -> None:
        from autograder_b2.domain.clock import sha256_hex

        content = b"fixture bytes\n"
        package = fixture_package(content)
        provider = FakeFixtureProvider(
            (FixtureContent("input.txt", sha256_hex(content), len(content), content),)
        )
        problems = materialize_fixtures(package, tmp_path, provider)
        assert problems == ()
        assert (tmp_path / "input.txt").read_bytes() == content
        assert provider.calls == [package.question_version_id]

    def test_missing_provider_defers_to_the_workspace_validator(self, tmp_path: Path) -> None:
        """无物化通道 ≠ 静默成功：预置 fixture 由 `prepare_case_workspace` 校验（旧行为）。"""
        package = fixture_package()
        assert materialize_fixtures(package, tmp_path, None) == ()

    def test_missing_item_is_reported(self, tmp_path: Path) -> None:
        package = fixture_package()
        provider = FakeFixtureProvider(())
        problems = materialize_fixtures(package, tmp_path, provider)
        assert problems and "missing" in problems[0]

    def test_digest_mismatch_is_reported(self, tmp_path: Path) -> None:
        from autograder_b2.domain.clock import sha256_hex

        content = b"fixture bytes\n"
        package = fixture_package(content)
        provider = FakeFixtureProvider(
            (FixtureContent("input.txt", sha256_hex(b"other"), len(content), content),)
        )
        problems = materialize_fixtures(package, tmp_path, provider)
        assert problems and "digest mismatch" in problems[0]

    def test_existing_submission_file_is_never_overwritten(self, tmp_path: Path) -> None:
        from autograder_b2.domain.clock import sha256_hex

        content = b"fixture bytes\n"
        package = fixture_package(content)
        (tmp_path / "input.txt").write_bytes(b"student file")
        provider = FakeFixtureProvider(
            (FixtureContent("input.txt", sha256_hex(content), len(content), content),)
        )
        problems = materialize_fixtures(package, tmp_path, provider)
        assert problems and "already exists" in problems[0]
        assert (tmp_path / "input.txt").read_bytes() == b"student file"

    def test_provider_failure_is_reported_not_raised(self, tmp_path: Path) -> None:
        package = fixture_package()
        provider = FakeFixtureProvider(error=RuntimeError("b3 down"))
        problems = materialize_fixtures(package, tmp_path, provider)
        assert problems and "provider unavailable" in problems[0]

    def test_sandbox_unavailable_still_raises(self, tmp_path: Path) -> None:
        package = fixture_package()
        provider = FakeFixtureProvider(error=SandboxUnavailableError("no sandbox"))
        with pytest.raises(SandboxUnavailableError):
            materialize_fixtures(package, tmp_path, provider)


# =============================================================================================
# 5. single_run 批量执行（FakeLauncher）
# =============================================================================================


def run_batch(
    package: EvaluationPackage,
    workspace: Path,
    *,
    launcher: FakeLauncher,
    provider: FakeFixtureProvider | None = None,
) -> tuple[tuple, FakeLauncher]:
    batch = make_batch(package, workspace)
    evidences = run_cases(
        package, batch, probe=available_probe, launcher=launcher, fixture_provider=provider
    )
    return evidences, launcher


class TestSingleRunBatch:
    def _workspace(self, tmp_path: Path) -> Path:
        workspace = prepare_workspace(tmp_path)
        return workspace

    def test_all_segments_pass_with_one_invocation(self, tmp_path: Path) -> None:
        package = load(single_run_payload())
        stdout = f"one\n{DELIMITER}\ntwo\n".encode()
        evidences, launcher = run_batch(
            package, self._workspace(tmp_path), launcher=FakeLauncher(stdout=stdout)
        )
        assert launcher.starts == 1, "批量只允许一次沙箱执行"
        assert [item.passed for item in evidences] == [True, True]
        assert [item.segment for item in evidences] == [0, 1]
        assert evidences[0].actual_output_excerpt == "one\n"
        assert evidences[1].actual_output_excerpt == "two\n"

    def test_one_bad_segment_does_not_shift_others(self, tmp_path: Path) -> None:
        package = load(single_run_payload())
        stdout = f"one\n{DELIMITER}\nWRONG\n".encode()
        evidences, _ = run_batch(
            package, self._workspace(tmp_path), launcher=FakeLauncher(stdout=stdout)
        )
        assert [item.passed for item in evidences] == [True, False]
        assert evidences[1].failure_category == "output_mismatch"

    @pytest.mark.parametrize(
        "stdout",
        [
            b"one\ntwo\n",  # 缺分隔符
            f"one\n{DELIMITER}\ntwo\n{DELIMITER}\nthree\n".encode(),  # 多分隔符
            f"{DELIMITER}\none\n{DELIMITER}\ntwo\n".encode(),  # 空段
            f"one\n{DELIMITER}\n{DELIMITER}\ntwo\n".encode(),  # 空段
        ],
    )
    def test_structure_problems_fail_every_segment_case_without_shifting(
        self, tmp_path: Path, stdout: bytes
    ) -> None:
        package = load(single_run_payload())
        evidences, launcher = run_batch(
            package, self._workspace(tmp_path), launcher=FakeLauncher(stdout=stdout)
        )
        assert launcher.starts == 1
        assert [item.passed for item in evidences] == [False, False]
        assert all(item.failure_category == "output_mismatch" for item in evidences)
        assert all(SEGMENT_STRUCTURE_MESSAGE in (item.message or "") for item in evidences)

    def test_timeout_is_run_level_for_every_case(self, tmp_path: Path) -> None:
        package = load(single_run_payload())
        evidences, _ = run_batch(
            package,
            self._workspace(tmp_path),
            launcher=FakeLauncher(
                stdout=f"one\n{DELIMITER}\ntwo\n".encode(), timeout=True, exit_code=137
            ),
        )
        assert [item.failure_category for item in evidences] == ["timeout", "timeout"]
        assert all(item.resource_limit_hit == "wall" for item in evidences)

    def test_file_checks_case_uses_workspace_prep_profile(self, tmp_path: Path) -> None:
        from autograder_b2.domain.clock import sha256_hex

        content = b"edited content\n"
        payload = single_run_payload(with_file_checks=True)
        payload["tests"][0]["file_checks"] = [
            {"path": "week6_1.txt", "content_sha256": sha256_hex(content), "mode": None}
        ]
        package = load(payload)
        launcher = FakeLauncher(stdout=b"", created=(("week6_1.txt", content, None),))
        evidences, launcher = run_batch(package, self._workspace(tmp_path), launcher=launcher)
        assert evidences[0].passed is True, evidences[0].message
        assert evidences[0].file_checks[0].matched is True
        assert evidences[0].segment is None
        # prep profile：配置声明 keep_caps + 首条 uidmap 0 -> caller；argv 启动准备程序。
        assert "keep_caps: true" in launcher.config_text
        assert "autograder-b2-workspace-prep" in launcher.config_text
        executed = launcher.argv[launcher.argv.index("--") + 1 :]
        assert executed[1].endswith(PREPARE_PROGRAM_FILENAME)
        assert executed[2] == str(WORKSPACE_PREP_UID)
        assert executed[3] == str(WORKSPACE_PREP_GID)

    @pytest.mark.skipif(os.name != "posix", reason="Windows 不提供 POSIX 权限位语义")
    def test_file_checks_mode_is_checked_after_prep_run(self, tmp_path: Path) -> None:
        payload = single_run_payload(with_file_checks=True)
        payload["tests"][0]["file_checks"] = [
            {"path": "week6_2.dat", "content_sha256": None, "mode": "0744"}
        ]
        package = load(payload)
        launcher = FakeLauncher(created=(("week6_2.dat", b"perm\n", 0o744),))
        evidences, _ = run_batch(package, self._workspace(tmp_path), launcher=launcher)
        assert evidences[0].passed is True, evidences[0].message
        assert evidences[0].file_checks[0].actual_mode == "0744"

    def test_file_checks_failure_is_classified(self, tmp_path: Path) -> None:
        package = load(single_run_payload(with_file_checks=True))
        evidences, _ = run_batch(package, self._workspace(tmp_path), launcher=FakeLauncher())
        assert evidences[0].passed is False
        assert evidences[0].failure_category == "missing_output"

    def test_segment_only_run_keeps_the_default_profile(self, tmp_path: Path) -> None:
        package = load(single_run_payload())
        _, launcher = run_batch(
            package,
            self._workspace(tmp_path),
            launcher=FakeLauncher(stdout=f"one\n{DELIMITER}\ntwo\n".encode()),
        )
        assert "keep_caps: false" in launcher.config_text
        assert "autograder-b2-case" in launcher.config_text
        assert "autograder-b2-workspace-prep" not in launcher.config_text

    def test_batch_rejects_per_case_and_email_modes(self, tmp_path: Path) -> None:
        from autograder_b2.domain import InternalEvaluationError

        per_case = load(package_payload())
        with pytest.raises(InternalEvaluationError):
            run_cases(
                per_case,
                make_batch(per_case, self._workspace(tmp_path)),
                probe=available_probe,
                launcher=FakeLauncher(),
            )

    def test_batch_rejects_tampered_handles(self, tmp_path: Path) -> None:
        from autograder_b2.domain import InternalEvaluationError

        package = load(single_run_payload())
        batch = make_batch(package, self._workspace(tmp_path))
        with pytest.raises(InternalEvaluationError):
            resolve_batch_cases(package, replace(batch, cases=package.tests[:1]))
        with pytest.raises(InternalEvaluationError):
            resolve_batch_cases(package, replace(batch, adapter=replace(package.adapter, args=("x",))))
        with pytest.raises(InternalEvaluationError):
            resolve_batch_cases(
                package, replace(batch, policy=replace(package.security_policy, max_processes=4))
            )

    def test_sandbox_unavailable_raises(self, tmp_path: Path) -> None:
        package = load(single_run_payload())

        def unavailable(policy: RunnerPolicy) -> SandboxAvailability:
            del policy
            return SandboxAvailability(
                available=False, checks=(), reason="nsjail: missing"
            )

        with pytest.raises(SandboxUnavailableError):
            run_cases(
                package,
                make_batch(package, self._workspace(tmp_path)),
                probe=unavailable,
                launcher=FakeLauncher(),
            )


# =============================================================================================
# 6. 证据摘要
# =============================================================================================


class TestEvidenceDigest:
    def test_segment_and_file_checks_participate(self, tmp_path: Path) -> None:
        package = load(single_run_payload())
        evidences, _ = run_batch(
            package,
            prepare_workspace(tmp_path),
            launcher=FakeLauncher(stdout=f"one\n{DELIMITER}\ntwo\n".encode()),
        )
        other = replace(evidences[0], segment=7)
        assert evidence_digest_of(other) != evidences[0].evidence_digest
        changed = replace(
            evidences[0],
            file_checks=(
                FileCheckResult("a.txt", None, None, "0644", "0644", True),
            ),
        )
        assert evidence_digest_of(changed) != evidences[0].evidence_digest

    def test_default_values_do_not_enter_the_legacy_payload(self, tmp_path: Path) -> None:
        """缺省 segment/file_checks 不改变旧证据摘要（引入扩展前生成的数据仍可重放）。"""
        package = load(package_payload())
        from autograder_b2.runner.adapters import build_execution_plan
        from autograder_b2.runner.run_case import CaseOutcome, build_evidence

        plan = build_execution_plan(
            adapter=package.adapter,
            case=package.tests[0],
            policy=package.security_policy,
            language=package.language,
        )
        evidence = build_evidence(
            case=package.tests[0],
            outcome=CaseOutcome(True, None, None, (), "passed"),
            plan=plan,
            workspace=tmp_path,
            digest_input="e" * 64,
            stdout_text="one\n",
            stderr_text="",
            exit_code=0,
            duration_ms=1,
        )
        assert evidence.segment is None
        assert evidence.file_checks == ()
        payload = json.loads(json.dumps({"x": evidence.evidence_digest}))
        assert payload["x"] == evidence.evidence_digest

    def test_input_digest_never_binds_the_ssh_password(self) -> None:
        """口令不参与任何摘要：除口令外完全同构的两份 package ⇒ 同一 input_digest。"""
        package = load(ssh_payload())
        other_package = load(ssh_payload(ssh_overrides={"password": "another-secret"}))
        left_case = package.tests[1]
        right_package_case = other_package.tests[1]
        context = {"ssh_username": "user01", "ssh_port": 22}
        left = input_digest_for(
            package,
            files=(),
            case=left_case,
            adapter=package.adapter,
            plan=build_execution_plan(
                adapter=package.adapter,
                case=left_case,
                policy=package.security_policy,
                language=package.language,
            ),
            policy=package.security_policy,
            execution_mode="ssh_session",
            execution_context=context,
        )
        right = input_digest_for(
            other_package,
            files=(),
            case=right_package_case,
            adapter=other_package.adapter,
            plan=build_execution_plan(
                adapter=other_package.adapter,
                case=right_package_case,
                policy=other_package.security_policy,
                language=other_package.language,
            ),
            policy=other_package.security_policy,
            execution_mode="ssh_session",
            execution_context=context,
        )
        assert left == right


# =============================================================================================
# 7. SSH 命令白名单
# =============================================================================================


class TestSshCommandWhitelist:
    @pytest.mark.parametrize(
        "line",
        [
            "ssh user01@127.0.0.1",
            "ssh 127.0.0.1",
            "ssh -p 2222 user01@127.0.0.1",
            "ssh -l user01 127.0.0.1",
            "  ssh   user01@127.0.0.1  ",
        ],
    )
    def test_accepts_strict_forms(self, line: str) -> None:
        command, error = parse_student_ssh_command(line)
        assert error is None
        assert command is not None

    @pytest.mark.parametrize(
        "line",
        [
            "bash",
            "ssh -o ProxyCommand='sh -c id' user01@127.0.0.1",
            "ssh -o LocalCommand=id user01@127.0.0.1",
            "ssh user01@127.0.0.1; rm -rf /",
            "ssh user01@127.0.0.1 && id",
            "ssh user01@127.0.0.1 > /tmp/x",
            "ssh user01@127.0.0.1 | cat",
            "ssh -p 22 user01@example.com",
            "ssh user01@127.0.0.1 extra",
            "ssh -p abc user01@127.0.0.1",
            "ssh -p 22 -p 23 user01@127.0.0.1",
            "ssh -l a -l b 127.0.0.1",
            "/usr/bin/ssh user01@127.0.0.1",
            "ssh",
        ],
    )
    def test_rejects_anything_that_could_run_local_commands(self, line: str) -> None:
        command, error = parse_student_ssh_command(line)
        assert command is None
        assert error

    def test_client_argv_is_fixed_and_never_uses_the_package_password(self) -> None:
        command, _ = parse_student_ssh_command("ssh user01@127.0.0.1")
        assert command is not None
        argv = render_ssh_client_argv("/usr/bin/ssh", command, connect_timeout_seconds=10)
        assert argv[0] == "/usr/bin/ssh"
        assert "StrictHostKeyChecking=no" in argv
        assert "UserKnownHostsFile=/dev/null" in argv
        assert argv[-1] == "127.0.0.1"
        assert all("secret" not in item for item in argv)
        assert "-o" in argv and "ProxyCommand" not in " ".join(argv)


# =============================================================================================
# 8. SSH profile 配置渲染
# =============================================================================================


def ssh_profile() -> RunnerPolicy:
    return replace(RunnerPolicy(), sandbox_profile=SANDBOX_PROFILE_SSH)


class TestSshConfigRendering:
    def test_config_is_deterministic_and_declares_the_isolated_practice_environment(
        self, tmp_path: Path
    ) -> None:
        paths = create_ssh_sandbox_paths(tmp_path / "ssh")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        first = render_nsjail_ssh_config(
            ssh_profile(), workspace, paths, caller_uid=1000, caller_gid=1000
        )
        second = render_nsjail_ssh_config(
            ssh_profile(), workspace, paths, caller_uid=1000, caller_gid=1000
        )
        assert first == second
        assert "iface_no_lo: false" in first
        assert "keep_caps: true" in first
        assert 'name: "autograder-b2-ssh"' in first
        assert 'src: "' + paths.practice_root.as_posix() + '"' in first
        assert 'dst: "/"' in first
        assert 'src: "/dev"' in first
        assert 'dst: "/dev/pts"' in first
        assert "newinstance" in first, "SSH profile 需要独立 devpts 实例（PTY 归属沙箱 userns）"
        assert 'dst: "/workspace"' in first
        assert 'dst: "/client/usr/bin"' in first
        # 首条 uidmap 让 jailed uid = 0-in-userns；gidmap 只有 65534 一条（subuid 只有 count=1）。
        # The subordinate ID itself is a host allocation, so the assertion stays structural: the
        # caller mapping is the injected uid and the 65534 mapping uses *some* delegated id.
        assert first.index('inside_id: "0"') < first.index('inside_id: "65534"')
        assert first.count("gidmap {") == 1
        assert re.search(r'inside_id: "0"\n  outside_id: "1000"', first), first
        assert re.search(r'inside_id: "65534"\n  outside_id: "\d+"', first), first
        # seccomp：本地网络只在 SSH profile 的允许清单里，并且没有 ERRNO(socket) 行。
        lines = list(ssh_seccomp_policy_lines(ssh_profile()))
        allow_start = lines.index("  ALLOW {")
        kill_start = lines.index("  KILL {")
        allow_text = "\n".join(lines[allow_start:kill_start])
        kill_text = "\n".join(lines[kill_start:])
        for name in ("socket", "connect", "bind", "listen", "accept", "chroot", "setuid"):
            assert name in allow_text
        for name in ("mount", "ptrace", "setns", "unshare", "bpf"):
            assert name in kill_text
            assert name not in allow_text
        assert all("ERRNO(1) { socket }" not in line for line in lines)

    def test_default_profile_has_no_loopback_and_denies_sockets(self) -> None:
        body = render_nsjail_policy_body(RunnerPolicy())
        assert "iface_no_lo: true" in body
        assert 'seccomp_string: "  ERRNO(1) { socket },"' in body
        assert "keep_caps: false" in body

    def test_ssh_config_requires_ssh_profile(self, tmp_path: Path) -> None:
        paths = create_ssh_sandbox_paths(tmp_path / "ssh")
        with pytest.raises(SandboxUnavailableError):
            render_nsjail_ssh_config(RunnerPolicy(), tmp_path, paths)
        # 普通渲染入口拒绝 ssh profile（避免渲染出缺挂载的"看似正常"配置）。
        with pytest.raises(SandboxUnavailableError):
            render_nsjail_config(ssh_profile(), tmp_path)
        # 正确 profile 时本函数可以渲染（workspace 只需是一个路径）。
        rendered = render_nsjail_ssh_config(ssh_profile(), tmp_path / "workspace", paths)
        assert 'name: "autograder-b2-ssh"' in rendered

    def test_sshd_seccomp_allows_local_network_but_not_privileged_escapes(self) -> None:
        lines = list(ssh_seccomp_policy_lines(ssh_profile()))
        allow_start = lines.index("  ALLOW {")
        kill_start = lines.index("  KILL {")
        allow_text = "\n".join(lines[allow_start:kill_start])
        kill_text = "\n".join(lines[kill_start:])
        for name in ("socket", "connect", "bind", "listen", "accept", "chroot", "setuid"):
            assert name in allow_text
        for name in ("mount", "ptrace", "setns", "unshare", "bpf"):
            assert name in kill_text
            assert name not in allow_text

    def test_create_ssh_sandbox_paths_layout(self, tmp_path: Path) -> None:
        paths = create_ssh_sandbox_paths(tmp_path / "ssh")
        assert (paths.practice_root / "workspace").is_dir()
        assert (paths.client_root / "dev" / "null").exists()
        assert (paths.empty_root / "sshd").is_dir()

    @pytest.mark.skipif(os.name != "posix", reason="Windows 不提供 POSIX 权限位语义")
    def test_practice_home_and_tmp_are_writable(self, tmp_path: Path) -> None:
        paths = create_ssh_sandbox_paths(tmp_path / "ssh")
        assert (paths.practice_root / "home" / "user01").stat().st_mode & 0o777 == 0o777
        assert (paths.practice_root / "tmp").stat().st_mode & 0o777 == 0o777


# =============================================================================================
# 9. SSH 证据解析与判定
# =============================================================================================


class TestSshEvidence:
    def test_evidence_lines_are_gated_by_the_run_nonce(self) -> None:
        nonce = "abc123"
        stderr = "\n".join(
            [
                f"{SSH_EVIDENCE_PREFIX} {nonce} service_ready=1",
                f"{SSH_EVIDENCE_PREFIX} {nonce} auth_succeeded=1",
                f"{SSH_EVIDENCE_PREFIX} {nonce} session_closed=1",
                f"{SSH_EVIDENCE_PREFIX} {nonce} ssh_exit_code=0",
                f"{SSH_EVIDENCE_PREFIX} {nonce} ssh_killed=0",
                f"{SSH_EVIDENCE_PREFIX} other service_ready=0",
                "Traceback (most recent call last):",
                f"{SSH_EVIDENCE_PREFIX} {nonce} driver_error=sshd_exited",
            ]
        )
        evidence = parse_ssh_evidence(stderr, nonce)
        assert evidence.service_ready is True
        assert evidence.auth_succeeded is True
        assert evidence.session_closed is True
        assert evidence.ssh_exit_code == 0
        assert evidence.ssh_killed is False
        assert evidence.driver_error == "sshd_exited"

    def test_missing_evidence_is_none_not_false(self) -> None:
        evidence = parse_ssh_evidence("garbage\n", "nonce")
        assert evidence.service_ready is None
        assert evidence.auth_succeeded is None
        assert evidence.session_closed is None
        assert evidence.evidence_complete() is False

    def test_check_semantics(self) -> None:
        ready_failed = SshRunEvidence(nonce="n", service_ready=False)
        assert ssh_check_passed("service_ready", ready_failed, ssh_client_success=False) is False
        good = SshRunEvidence(
            nonce="n",
            service_ready=True,
            auth_succeeded=True,
            session_closed=True,
            ssh_exit_code=0,
        )
        assert ssh_check_passed("service_ready", good, ssh_client_success=True)
        assert ssh_check_passed("auth_succeeded", good, ssh_client_success=True)
        assert ssh_check_passed("session_closed", good, ssh_client_success=True)
        assert (
            ssh_check_passed("session_closed", good, ssh_client_success=False) is False
        )
        killed = replace(good, ssh_killed=True)
        assert ssh_check_passed("session_closed", killed, ssh_client_success=True) is False

    def test_driver_program_is_deterministic_and_self_contained(self) -> None:
        first = render_ssh_driver_program()
        assert first == render_ssh_driver_program()
        for needle in (
            "B2SSH-EVIDENCE",
            "sshd_config",
            "ChrootDirectory /",
            "os.chroot(CLIENT_ROOT)",
            "os.setuid(JAILED_UID)",
            "/dev/null",
            "StrictHostKeyChecking=no",
            "PasswordAuthentication yes",
            "session closed for user ",
            "Accepted password for ",
        ):
            assert needle in first
        assert "autograder_b2" not in first, "驱动不得 import B2 包（沙箱内没有它）"

    def test_driver_config_carries_the_password_only_over_stdin(self) -> None:
        package = load(ssh_payload())
        ssh = package.execution.ssh
        assert ssh is not None
        config = build_ssh_driver_config(
            nonce="n",
            entry_name="main.sh",
            ssh=ssh,
            sshd_path="/usr/sbin/sshd",
            ssh_client_path="/usr/bin/ssh",
            ssh_keygen_path="/usr/bin/ssh-keygen",
            max_output_bytes=65536,
        )
        assert config["password"] == SSH_PASSWORD
        assert json.dumps(config)  # 只作为 stdin 内容；不写进 argv/环境（见 run_cases）

    def test_nss_files_resolve_namespace_root_and_the_practice_account(self) -> None:
        """uid 0 必须有 passwd 条目：`ssh-keygen` 以 userns root 运行时用 `getpwuid(0)`。

        缺条目时 ssh-keygen 直接 `fatal("No user exists for uid 0")`（退出码 255），
        sshd 起不来——这是生产环境 `host_key_generation_failed` 的根因。
        """
        passwd, group, shadow = render_ssh_nss_files("user01", SSH_PASSWORD)
        assert "root:x:0:0:" in passwd
        assert "root:x:0:" in group
        assert "root:!*:" in shadow
        # 练习账号仍是 uid 65534 的第一条：客户端未显式指定用户时解析成练习账号。
        assert passwd.index("user01:") < passwd.index("sshd:")
        assert "user01:x:65534:" in passwd
        for text in (passwd, group, shadow):
            assert SSH_PASSWORD not in text, "NSS 文件不得含口令明文"


# =============================================================================================
# 10. workspace_prep
# =============================================================================================


class TestWorkspacePrep:
    def test_prepare_program_is_fixed_and_validates_the_jailed_identity(self) -> None:
        source = render_workspace_prepare_program()
        assert source == render_workspace_prepare_program()
        assert "JAILED_UID = 65534" in source
        assert "JAILED_GID = 65534" in source
        assert "unexpected jailed identity" in source
        assert "os.path.islink" in source
        assert 'resolved.startswith(WORKSPACE + "/")' in source
        assert "os.setgroups([])" in source
        assert "os.setuid" in source and "os.execv" in source
        assert "sys.argv" in source
        # 所有权靠"被囚身份重建文件 inode"取得，而不是 chown（userns 内 chown 宿主文件 EPERM）。
        assert "os.chown" not in source
        assert "os.replace" in source and "_claim_entries()" in source

    def test_prep_profile_config_differs_only_in_the_expected_places(self, tmp_path: Path) -> None:
        workspace = tmp_path / "case"
        workspace.mkdir()
        default = render_nsjail_config(RunnerPolicy(), workspace)
        prep = render_nsjail_config(
            replace(RunnerPolicy(), sandbox_profile=SANDBOX_PROFILE_WORKSPACE_PREP), workspace
        )
        assert "keep_caps: false" in default and "keep_caps: true" in prep
        assert "autograder-b2-workspace-prep" in prep
        # 首条 uidmap 0 -> caller（jailed uid=0）；gidmap 只有 65534（不映射 gid 0）。
        assert prep.index('inside_id: "0"') < prep.index('inside_id: "65534"')
        assert prep.count("gidmap {") == 1
        assert 'inside_id: "0"\n  inside_id: "0"' not in prep
        # prep seccomp 只允许多出的降权调用；chown 不在允许清单（仍走默认拒绝）。
        assert WORKSPACE_PREP_SECCOMP_ALLOW_EXTRA == ("setgid", "setgroups", "setuid")
        prep_seccomp = "\n".join(workspace_prep_seccomp_policy_lines(
            replace(RunnerPolicy(), sandbox_profile=SANDBOX_PROFILE_WORKSPACE_PREP)
        ))
        for name in WORKSPACE_PREP_SECCOMP_ALLOW_EXTRA:
            assert name in prep_seccomp
        assert 'seccomp_string: "  ERRNO(1) { socket },"' in prep

    def test_default_body_has_no_new_profile_markers(self) -> None:
        assert SANDBOX_PROFILE_DEFAULT == "default"
        default = render_nsjail_policy_body(RunnerPolicy())
        assert "workspace-prep" not in default
        assert "autograder-b2-ssh" not in default
        assert policy_digest(RunnerPolicy()) == policy_digest(RunnerPolicy())

    def test_workspace_prep_config_digest_is_reproducible(self, tmp_path: Path) -> None:
        policy = replace(RunnerPolicy(), sandbox_profile=SANDBOX_PROFILE_WORKSPACE_PREP)
        workspace = tmp_path / "case"
        workspace.mkdir()
        first = workspace_prep_config_digest(policy, workspace)
        assert first == workspace_prep_config_digest(policy, workspace)
        assert first != nsjail_config_digest(RunnerPolicy(), workspace)
        # profile 进入策略摘要（非缺省），因此证据绑定可区分两种执行形态。
        assert policy_digest(policy) != policy_digest(RunnerPolicy())
