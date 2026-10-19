"""runner 核心单元测试（`RQ-B-2-50`：沙箱策略与可用性判定 + 逐用例执行与比较）。

**范围声明（诚实边界）**：本文件在 Windows 主机上运行，**没有**在真实 Linux + nsjail + cgroup v2
上执行过任何沙箱用例。"执行"一律通过注入的 `FakeLauncher`（替代操作系统进程边界）完成，
因此被验证的是 run_case 的**流水线逻辑**——前置判定、固定参数数组、输出截断、失败归类、
进程树清理调用次数、证据与摘要确定性——**不是隔离强度**。真实沙箱证据由后续 Linux 验收工作项产出。
唯一一处真实子进程是 callable harness 的自检（用本机解释器直接运行 harness），它与沙箱无关。

测试分组与工作项要求逐条对应：

1. `TestPolicyDigest`            策略摘要确定性（同策略同摘要、逐字段改则变）
2. `TestNsjailConfig`            nsjail 配置逐字节确定性 + 禁网/只读根/非 root/限制/timelimit/seccomp
3. `TestBuildArgv`               固定参数数组、无 shell 拼接、实参审计
4. `TestPackageValidation`       不合规包逐条拒绝
5. `TestNoDegradation`           沙箱不可用时抛异常且**零子进程**
6. `TestComparators`             四种比较策略
7. `TestStrategyComesFromPackage`比较策略只来自 package
8. `TestProcessTreeCleanup`      超时清理整棵进程树（假杀手，恰好一次）+ 失败不跳过其他用例
9. `TestEvidence`                证据字段完整、摘要确定性、输出截断
10. `TestAdapters`               三类 adapter、固定 harness、fixture 校验
"""

from __future__ import annotations

import ast
import copy
import importlib
import inspect
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from autograder_b2 import contracts
from autograder_b2.domain import (
    CaseEvidence,
    EnvelopeFile,
    InternalEvaluationError,
    PackageUnavailableError,
    SandboxUnavailableError,
    SubmissionRunInput,
)
from autograder_b2.runner import run_case as run_case_function
from autograder_b2.runner.adapters import (
    EXCERPT_LIMIT,
    HARNESS_FILENAME,
    HARNESS_RESOLUTION_EXIT,
    HARNESS_RESOLUTION_MARKER,
    build_execution_plan,
    render_callable_harness,
    workspace_path,
)
from autograder_b2.runner.comparator import (
    COMPARATORS,
    compare_file_digest,
    compare_output,
    compare_stderr,
    compare_stdout,
    normalize_trailing_whitespace,
)
from autograder_b2.runner.package import (
    EvaluationPackage,
    PackageRejected,
    parse_evaluation_package,
)
from autograder_b2.runner.policy import (
    LEDGER_MAX_CPUS_PER_JOB,
    LEDGER_MAX_MEMORY_BYTES,
    MAX_CPU_SECONDS,
    MAX_FILES_CREATED,
    MAX_MEMORY_BYTES,
    MAX_OPEN_FILES,
    MAX_OUTPUT_BYTES,
    MAX_PROCESSES,
    MAX_WALL_SECONDS,
    MIN_MEMORY_BYTES,
    MIN_OPEN_FILES,
    MIN_OUTPUT_BYTES,
    MIN_PROCESSES,
    MIN_STACK_MIB,
    NSJAIL_TIME_LIMIT_BACKSTOP_SECONDS,
    POLICY_BOUNDS,
    SECCOMP_ALLOW,
    SECCOMP_DENY,
    SECCOMP_X86_64_DEFINES,
    RunnerPolicy,
    SandboxAvailability,
    _seccomp_chunks,
    assert_policy_executable,
    assert_sandbox_available,
    build_argv,
    create_case_workspace,
    masked_mount_paths,
    normalize_machine,
    nsjail_config_digest,
    policy_bound_violations,
    policy_constraint_violations,
    policy_digest,
    probe_sandbox,
    render_nsjail_config,
    render_nsjail_policy_body,
    resolve_nsjail_path,
    sandbox_env,
    seccomp_policy_lines,
    uid_maps,
)
from autograder_b2.runner.run_case import (
    LIMIT_MARKERS,
    CaseOutcome,
    KillReport,
    PosixNsjailLauncher,
    PosixProcessGroupKiller,
    RunFacts,
    _scan_sandbox_log,
    build_evidence,
    classify_case_outcome,
    evidence_digest_of,
    launcher_for,
    make_finding,
    read_capped,
    redact,
    resolve_adapter,
    resolve_case,
    resolve_policy,
    run_case,
    sha256_hex,
)

CASE_ID = "11111111-1111-4111-8111-111111111111"
CASE_ID_2 = "33333333-3333-4333-8333-333333333333"
QUESTION_ID = "22222222-2222-4222-8222-222222222222"
PACKAGE_DIGEST = "a" * 64
FIXTURE_DIGEST = "b" * 64
SUBMISSION_DIGEST = "c" * 64
NSJAIL = "/usr/bin/nsjail"
CONFIG = "/tmp/b2.nsjail.cfg"

#: `autograder_b2.runner.run_case` **模块**（包级名字 `run_case` 是冻结签名的函数，故按模块表取）。
run_case_module = importlib.import_module("autograder_b2.runner.run_case")
package_module = importlib.import_module("autograder_b2.runner.package")

HOST_AVAILABILITY = probe_sandbox(RunnerPolicy())
requires_unavailable_host = pytest.mark.skipif(
    HOST_AVAILABILITY.available,
    reason="本机探测到沙箱可用（Linux），'不可用即拒绝'的真实环境断言由注入探测覆盖",
)


# --- 夹具构造 -------------------------------------------------------------------------------


def merge(base: dict, overrides: dict) -> dict:
    result = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def base_case(**overrides: object) -> dict:
    """契约形状的单个用例（默认合规：stdin 为 `42`、期望 stdout 为 `42\\n`）。"""
    case = {
        "case_id": CASE_ID,
        "name": "case one",
        "ordinal": 0,
        "weight": 1,
        "visible_to_student": True,
        "stdin": "42\n",
        "args": [],
        "expected_stdout": "42\n",
        "expected_stderr": None,
        "expected_exit_code": None,
    }
    return merge(case, overrides)


def package_payload(**overrides: object) -> dict:
    """契约形状的 package JSON（默认合规：stdin adapter、一个用例、常量策略）。"""
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
        "tests": [base_case()],
        "security_policy": {
            "network": False,
            "read_only_root": True,
            "allow_root": False,
            "max_memory_bytes": LEDGER_MAX_MEMORY_BYTES,
            "max_cpu_seconds": 30,
            "max_wall_seconds": 60,
            "max_output_bytes": 65536,
            "max_processes": 16,
            "max_files_created": 8,
        },
        "max_files": 5,
        "max_total_bytes": 100000,
        "created_at": "2026-01-01T00:00:00Z",
    }
    payload = copy.deepcopy(base)
    return merge(payload, overrides)


def load_package(**overrides: object) -> EvaluationPackage:
    return parse_evaluation_package(package_payload(**overrides))


def ledger_policy() -> RunnerPolicy:
    """与包夹具数值一致的策略（用于断言配置里的具体限制值）。"""
    return RunnerPolicy(
        max_memory_bytes=LEDGER_MAX_MEMORY_BYTES,
        max_cpu_seconds=30,
        max_wall_seconds=60,
        max_output_bytes=65536,
        max_processes=16,
        max_files_created=8,
    )


def make_files(*paths: str) -> tuple[EnvelopeFile, ...]:
    return tuple(
        EnvelopeFile(path=path, content_sha256=SUBMISSION_DIGEST, size_bytes=3, storage_ref=path)
        for path in paths
    )


def make_submission(
    package: EvaluationPackage,
    workspace: Path,
    *,
    files: tuple[EnvelopeFile, ...] | None = None,
) -> SubmissionRunInput:
    return SubmissionRunInput(
        submission_id="44444444-4444-4444-8444-444444444444",
        evaluation_run_id="55555555-5555-4555-8555-555555555555",
        correlation_id="66666666-6666-4666-8666-666666666666",
        workspace=Path(workspace),
        files=make_files("main.py") if files is None else files,
        case=package.tests[0],
        adapter=package.adapter,
        policy=package.security_policy,
    )


def tampered_submission(
    package: EvaluationPackage, workspace: Path, **changes: object
) -> SubmissionRunInput:
    return replace(make_submission(package, workspace), **changes)  # type: ignore[arg-type]


class RecordingKiller:
    """假进程组杀手：只记录调用（操作系统信号边界的测试替身，不是交付物的替身）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int | None]] = []

    def kill_tree(self, pid: int, process_group: int | None) -> KillReport:
        self.calls.append((pid, process_group))
        return KillReport(pid, process_group, True, f"fake SIGKILL to group {process_group}")


class FakeProcess:
    """假进程：`timeout_first_wait=True` 时第一次 `wait` 返回 None（模拟墙钟超时）。"""

    def __init__(
        self,
        *,
        exit_code: int | None,
        killer: RecordingKiller,
        timeout_first_wait: bool = False,
        always_alive: bool = False,
    ) -> None:
        self.pid = 4242
        self.process_group = 4242
        self._exit_code = exit_code
        self._killer = killer
        self._timeout_first_wait = timeout_first_wait
        self._always_alive = always_alive
        self.wait_calls = 0
        self.kill_calls = 0

    def wait(self, timeout_seconds: float) -> int | None:
        del timeout_seconds
        self.wait_calls += 1
        if self._always_alive:
            return None
        if self._timeout_first_wait and self.wait_calls == 1:
            return None
        return self._exit_code

    def kill_tree(self) -> KillReport:
        self.kill_calls += 1
        return self._killer.kill_tree(self.pid, self.process_group)


def executed_argv(argv: list[str]) -> list[str]:
    """`--` 之后的 argv = **真正被执行的命令**（interpreter、script、用例参数）。

    断言"被执行的是什么"时用这个，而不是硬编码下标：`--` 之前的 nsjail 选项
    （`--config` / `--log`）增删不应让这类断言失效（本文件曾因此被 `--log` 的引入打到 5 处）。
    """
    assert "--" in argv, f"argv 缺少 nsjail 的 `--` 分隔符: {argv!r}"
    return argv[argv.index("--") + 1 :]


class FakeLauncher:
    """假启动器：把"沙箱内发生的事情"变成可控输入（输出、stderr、新建文件、退出码、超时）。

    沙箱自有日志**不通过参数传入**，而是像真 nsjail 那样从 argv 的 `--log` 取值后写到那个路径
    （生产的 `build_argv` 必填该参数）——假启动器因此不能"自己发明"一条日志通道。
    """

    def __init__(
        self,
        *,
        exit_code: int | None = 0,
        stdout: bytes = b"42\n",
        stderr: bytes = b"",
        sandbox_log: bytes | None = None,
        timeout: bool = False,
        always_alive: bool = False,
        created: tuple[tuple[str, bytes], ...] = (),
        killer: RecordingKiller | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.sandbox_log = sandbox_log
        self.timeout = timeout
        self.always_alive = always_alive
        self.created = created
        self.killer = killer if killer is not None else RecordingKiller()
        self.starts = 0
        self.argv: list[str] = []
        self.env: dict[str, str] = {}
        self.stdin_text: str | None = None
        self.cwd: Path | None = None
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
        self.starts += 1
        self.argv = list(argv)
        self.env = dict(env)
        self.stdin_text = stdin_text
        self.cwd = Path(cwd)
        Path(stdout_path).write_bytes(self.stdout)
        Path(stderr_path).write_bytes(self.stderr)
        if self.sandbox_log is not None:
            # 真 nsjail 只认 argv 里的 `--log`；这里照做，顺带钉住"生产必定声明日志路径"。
            assert "--log" in argv, f"production argv must declare the sandbox log: {argv!r}"
            Path(argv[argv.index("--log") + 1]).write_bytes(self.sandbox_log)
        for relative, content in self.created:
            target = Path(cwd) / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.process = FakeProcess(
            exit_code=self.exit_code,
            killer=self.killer,
            timeout_first_wait=self.timeout,
            always_alive=self.always_alive,
        )
        return self.process


def available_probe(policy: RunnerPolicy) -> SandboxAvailability:
    """注入式"沙箱可用"探测（仅用于正路径测试；判定逻辑本身在 TestNoDegradation 覆盖）。"""
    del policy
    return SandboxAvailability(available=True, checks=(), reason=None)


def prepare_workspace(tmp_path: Path, *, language: str = "python", entry: bool = True) -> Path:
    """建工作区并放入入口脚本（`workspace.parent` 是运行目录：配置与输出都放那里）。"""
    workspace = tmp_path / "case-0"
    workspace.mkdir(parents=True, exist_ok=True)
    if entry:
        name = "main.py" if language == "python" else "main.sh"
        (workspace / name).write_text("print(42)\n", encoding="utf-8")
    return workspace


def run(
    package: EvaluationPackage,
    workspace: Path,
    *,
    launcher: FakeLauncher | None = None,
    files: tuple[EnvelopeFile, ...] | None = None,
    probe: object = available_probe,
) -> tuple[CaseEvidence, FakeLauncher]:
    active = launcher if launcher is not None else FakeLauncher()
    request = make_submission(package, workspace, files=files)
    evidence = run_case(package, request, probe=probe, launcher=active)  # type: ignore[arg-type]
    return evidence, active


# =============================================================================================
# 0. 冻结签名
# =============================================================================================


def test_run_case_signature_is_the_frozen_two_argument_form() -> None:
    parameters = list(inspect.signature(run_case_function).parameters.values())
    assert [item.name for item in parameters[:2]] == ["package", "submission"]
    assert all(item.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for item in parameters[:2])
    assert [item.name for item in parameters[2:]] == ["probe", "launcher"]
    assert all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in parameters[2:])


# =============================================================================================
# 1. 策略摘要确定性
# =============================================================================================


class TestPolicyDigest:
    def test_same_policy_has_same_digest(self) -> None:
        assert policy_digest(RunnerPolicy()) == policy_digest(RunnerPolicy())
        assert RunnerPolicy().digest == policy_digest(RunnerPolicy())

    def test_digest_is_sha256_hex(self) -> None:
        digest = policy_digest(RunnerPolicy())
        assert len(digest) == 64
        assert digest == digest.lower()
        assert all(character in "0123456789abcdef" for character in digest)

    def test_every_field_changes_the_digest(self) -> None:
        """逐字段扰动：**任何**一个字段改动都必须改变摘要（防止字段被漏进摘要）。"""
        base = RunnerPolicy()
        base_digest = policy_digest(base)
        perturbations: dict[str, object] = {
            "network": True,
            "read_only_root": False,
            "allow_root": True,
            "max_memory_bytes": base.max_memory_bytes + 1,
            "max_cpu_seconds": base.max_cpu_seconds + 1,
            "max_wall_seconds": base.max_wall_seconds + 1,
            "max_output_bytes": base.max_output_bytes + 1,
            "max_processes": base.max_processes + 1,
            "max_files_created": (base.max_files_created or 0) + 1,
            "python_interpreter": "/opt/python3",
            "bash_interpreter": "/bin/dash",
            "nsjail_path": "/opt/nsjail",
            "sandbox_uid": "1000",
            "sandbox_gid": "1000",
            "seccomp_arch": "aarch64",
            "workspace_mount": "/sandbox",
            "workspace_template": "other-",
            "cgroup_v2_mount": "/sys/fs/cgroup/unified",
            "use_seccomp": False,
            "use_cgroup": False,
            "max_cpus": 2,
            "max_open_files": 128,
            "stack_mib": 16,
            "masked_paths": ("/srv/secret",),
        }
        assert set(perturbations) == set(RunnerPolicy.__dataclass_fields__)
        for name, value in perturbations.items():
            assert policy_digest(RunnerPolicy(**{name: value})) != base_digest, name

    def test_digest_is_insensitive_to_construction_order(self) -> None:
        """摘要由排序键 JSON 决定：同一策略两种构造路径 ⇒ 同摘要、且策略相等。"""
        left = RunnerPolicy(max_memory_bytes=64 * 1024 * 1024, max_processes=4)
        right = RunnerPolicy(max_processes=4, max_memory_bytes=64 * 1024 * 1024)
        assert left == right
        assert policy_digest(left) == policy_digest(right)


# =============================================================================================
# 2. nsjail 配置：逐字节确定性 + 强隔离项
# =============================================================================================


class TestNsjailConfig:
    def test_policy_body_is_byte_identical(self) -> None:
        first = render_nsjail_policy_body(RunnerPolicy())
        second = render_nsjail_policy_body(RunnerPolicy())
        assert first == second
        assert first.encode("utf-8") == second.encode("utf-8")

    def test_full_config_is_byte_identical_for_same_policy_and_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "case-0"
        workspace.mkdir()
        policy = RunnerPolicy()
        assert render_nsjail_config(policy, workspace) == render_nsjail_config(policy, workspace)
        assert nsjail_config_digest(policy, workspace) == nsjail_config_digest(policy, workspace)

    def test_config_differs_only_in_workspace_lines_when_workspace_changes(
        self, tmp_path: Path
    ) -> None:
        left = tmp_path / "one" / "case-0"
        right = tmp_path / "two" / "case-0"
        left.mkdir(parents=True)
        right.mkdir(parents=True)
        left_lines = render_nsjail_config(RunnerPolicy(), left).split("\n")
        right_lines = render_nsjail_config(RunnerPolicy(), right).split("\n")
        assert len(left_lines) == len(right_lines)
        differing = [(a, b) for a, b in zip(left_lines, right_lines) if a != b]
        assert differing, "工作区不同必须体现在配置里"
        for left_line, right_line in differing:
            left_mentions = left.as_posix() in left_line or left.parent.as_posix() in left_line
            right_mentions = right.as_posix() in right_line or right.parent.as_posix() in right_line
            assert left_mentions and right_mentions, (left_line, right_line)
        assert render_nsjail_policy_body(RunnerPolicy()) in "\n".join(left_lines)

    @pytest.mark.parametrize(
        "expected",
        [
            "clone_newnet: true",
            "iface_no_lo: true",
            "clone_newuser: true",
            "clone_newns: true",
            "clone_newpid: true",
            "clone_newipc: true",
            "clone_newuts: true",
            "clone_newcgroup: true",
            "keep_caps: false",
            "disable_no_new_privs: false",
            'inside_id: "65534"',
            "time_limit: 65",
            "max_cpus: 1",
            "rlimit_as: 96",
            "rlimit_as_type: VALUE",
            "rlimit_core: 0",
            "rlimit_core_type: VALUE",
            "rlimit_cpu: 30",
            "rlimit_cpu_type: VALUE",
            "rlimit_fsize: 1",
            "rlimit_fsize_type: VALUE",
            "rlimit_nofile: 64",
            "rlimit_nofile_type: VALUE",
            "rlimit_nproc: 16",
            "rlimit_nproc_type: VALUE",
            "rlimit_stack: 8",
            "rlimit_stack_type: VALUE",
            "cgroup_mem_max: 100663296",
            "cgroup_pids_max: 16",
            "cgroup_cpu_ms_per_sec: 500",
            'cgroupv2_mount: "/sys/fs/cgroup"',
            "use_cgroupv2: true",
            "mount_proc: true",
            "keep_env: false",
            'envar: "TMPDIR=/workspace"',
            'cwd: "/workspace"',
            "mode: ONCE",
        ],
    )
    def test_config_declares_each_required_guarantee(self, expected: str) -> None:
        assert expected in render_nsjail_config(ledger_policy(), Path("/tmp/b2-workspace"))

    def test_two_uidmaps_keep_the_jailed_process_out_of_root(self) -> None:
        """非 root 由**第一条** uidmap/gidmap 决定；第二条只为让 nsjail 能完成挂载。

        期望值更改说明：本用例原名 `test_config_never_maps_inside_uid_zero`，断言"配置里没有
        `inside_id: "0"`"——**该期望是错的**：Linux 验收逐变体实测（`verify/linux-nsjail/README.md` §5.2）
        表明只写一条 uidmap 必然失败：只写非 root 那条时 nsjail 在新 userns 里没有 root，
        `Couldn't mount '/workspace'`；只写 `0 -> 0` 则被囚进程 uid=0，非 root 失效。
        正确形态是**非 root 在前 + 调用者 uid 在后**，因此这里改断言"第一条非 root、第二条是 0"，
        而不是把 `inside_id: "0"` 从实现里删掉。
        """
        config = render_nsjail_config(ledger_policy(), Path("/tmp/b2-workspace"), caller_uid=0,
                                      caller_gid=0)
        assert "keep_caps: false" in config
        first_uid = uid_maps(ledger_policy(), caller_uid=0, caller_gid=0)[0]
        assert first_uid == ("uidmap", "65534", "65534")  # 被囚进程：内外都不是 root
        assert uid_maps(ledger_policy(), caller_uid=0, caller_gid=0)[1] == ("uidmap", "0", "0")
        assert config.count("uidmap {") == 2
        assert config.count("gidmap {") == 2
        assert 'inside_id: "0"\n  outside_id: "0"' in config
        assert 'inside_id: "0"\n  outside_id: "65534"' not in config  # 绝不把 0 映射成非 root 外层

    @pytest.mark.parametrize(
        ("caller", "expected_first_outside"),
        [(0, "65534"), (1000, "1000"), (99999, "99999")],
    )
    def test_uid_maps_are_caller_aware_and_never_fake_root(
        self, caller: int, expected_first_outside: str
    ) -> None:
        """调用者是 root 时映射到 nobody；非 root 时映射到自己的 uid（全局都非 root）。

        反例（Linux 验收实测的"假非 root"）：`outside_id: ""` 在 root 下等于 `65534 -> 0`，
        ns 内 euid 非 0 但全局仍是 root —— 因此判据必须是从 ns 外读 `/proc/<pid>/status`。
        """
        maps = uid_maps(ledger_policy(), caller_uid=caller, caller_gid=caller)
        assert maps[0] == ("uidmap", "65534", expected_first_outside)
        assert maps[1] == ("uidmap", "0", str(caller))
        assert maps[2] == ("gidmap", "65534", expected_first_outside)
        assert maps[3] == ("gidmap", "0", str(caller))

    def test_read_only_root_and_writable_workspace(self) -> None:
        config = render_nsjail_config(ledger_policy(), Path("/tmp/b2-workspace"))
        assert 'src: "/"\n  dst: "/"\n  is_bind: true\n  rw: false' in config
        assert 'src: "/tmp/b2-workspace"\n  dst: "/workspace"\n  is_bind: true\n  rw: true' in config

    def test_rlimit_types_are_always_explicit(self) -> None:
        """每个 rlimit 都必须带 `*_type: VALUE`（proto 默认对 nproc/stack 是 SOFT，只写数值不生效）。"""
        config = render_nsjail_config(ledger_policy(), Path("/tmp/b2-workspace"))
        for field in ("as", "core", "cpu", "fsize", "nofile", "nproc", "stack"):
            assert f"rlimit_{field}:" in config
            assert f"rlimit_{field}_type: VALUE" in config

    def test_kafel_policy_uses_file_level_default_action(self) -> None:
        """kafel 语法（Linux 验收实测修正）：`DEFAULT KILL` 不能在 POLICY 块内，块间要有逗号。"""
        config = render_nsjail_config(ledger_policy(), Path("/tmp/b2-workspace"))
        assert 'seccomp_string: "POLICY b2_default_deny {"' in config
        assert 'seccomp_string: "  },"' in config  # ALLOW 块之后必须有逗号
        assert 'seccomp_string: "  KILL {"' in config
        assert 'seccomp_string: "USE b2_default_deny DEFAULT KILL"' in config
        assert 'seccomp_string: "  DEFAULT KILL"' not in config
        assert 'seccomp_string: "USE b2_default_deny DEFAULT"' not in config

    def test_seccomp_lists_have_no_trailing_comma(self) -> None:
        """kafel 要求最后一条系统调用之后不得有逗号（实测 `unexpected '}', expecting …`）。"""
        for names in (SECCOMP_ALLOW, SECCOMP_DENY):
            lines = _seccomp_chunks(names)
            assert not lines[-1].rstrip().endswith(",")
            assert all(line.rstrip().endswith(",") for line in lines[:-1])

    def test_kafel_unknown_names_are_defined_with_verified_numbers(self) -> None:
        """kafel 表里没有的名字必须用 `#define NAME <x86_64 编号>` 补齐，编号来自内核头。"""
        numbers = dict(SECCOMP_X86_64_DEFINES)
        assert numbers["clone3"] == 435
        assert numbers["openat2"] == 437
        assert numbers["close_range"] == 436
        assert numbers["faccessat2"] == 439
        assert numbers["uname"] == 63
        assert numbers["umount2"] == 166
        config = render_nsjail_config(ledger_policy(), Path("/tmp/b2-workspace"))
        assert 'seccomp_string: "#define clone3 435"' in config
        # i386 专属名与 x86_64 上不存在的系统调用**不得**定义：
        # `socketcall` 在 x86_64 上是 102 == getuid，定义它会把 getuid 当 socketcall 杀掉。
        assert not any(name in {"socketcall", "futimens"} for name, _ in SECCOMP_X86_64_DEFINES)
        assert "socketcall" not in SECCOMP_DENY
        assert "futimens" not in SECCOMP_ALLOW

    def test_seccomp_covers_measured_cpython_syscalls(self) -> None:
        """允许清单必须覆盖 Linux 验收容器内 `strace` 实测的 CPython 3.12 系统调用集合。"""
        measured = {
            "access", "arch_prctl", "brk", "chmod", "clone3", "close", "close_range", "dup2",
            "epoll_create1", "execve", "exit", "exit_group", "fcntl", "fsync", "futex",
            "getdents64", "getegid", "geteuid", "getgid", "getpeername", "getrandom",
            "getsockname", "gettid", "getuid", "ioctl", "lseek", "madvise", "mkdir", "mmap",
            "mprotect", "munmap", "newfstatat", "openat", "pipe2", "poll", "pread64", "prlimit64",
            "read", "readlink", "rename", "rseq", "rt_sigaction", "rt_sigprocmask", "sendfile",
            "set_robust_list", "set_tid_address", "sysinfo", "uname", "utimensat", "vfork", "wait4",
            "write",
        }
        assert measured <= set(SECCOMP_ALLOW)
        for syscall in ("socket", "connect", "sendto", "ptrace", "mount", "setuid", "unshare"):
            assert syscall in SECCOMP_DENY
            assert syscall not in SECCOMP_ALLOW

    def test_seccomp_arch_mismatch_is_refused(self) -> None:
        """`#define` 编号是架构相关的：架构不符必须拒绝（否则过滤器会放错/杀错系统调用）。"""
        policy = RunnerPolicy(seccomp_arch="aarch64")
        with pytest.raises(SandboxUnavailableError):
            seccomp_policy_lines(policy)
        availability = probe_sandbox(policy, platform_name="linux", machine="x86_64")
        assert availability.available is False
        assert any(check.name == "cpu_arch" and not check.ok for check in availability.checks)

    def test_machine_aliases_are_normalized(self) -> None:
        assert normalize_machine("AMD64") == "x86_64"
        assert normalize_machine("x86_64") == "x86_64"
        assert normalize_machine("arm64") == "aarch64"
        assert normalize_machine("riscv64") == "riscv64"

    def test_workspace_parent_is_masked(self, tmp_path: Path) -> None:
        """只读根会把宿主根带进沙箱，因此工作区父目录（含其他学生工作区）必须遮蔽。"""
        workspace = tmp_path / "run-1" / "case-0"
        workspace.mkdir(parents=True)
        config = render_nsjail_config(RunnerPolicy(), workspace)
        assert f'dst: "{(tmp_path / "run-1").as_posix()}"' in config
        assert 'fstype: "tmpfs"' in config
        assert "mandatory: false" in config
        assert masked_mount_paths(RunnerPolicy(), workspace) == ((tmp_path / "run-1").as_posix(),)

    def test_masking_skips_filesystem_root(self) -> None:
        assert masked_mount_paths(RunnerPolicy(), Path("/case-0")) == ()

    def test_masked_paths_from_policy_are_rendered(self, tmp_path: Path) -> None:
        workspace = tmp_path / "case-0"
        workspace.mkdir()
        config = render_nsjail_config(RunnerPolicy(masked_paths=("/srv/autograder",)), workspace)
        assert 'dst: "/srv/autograder"' in config

    def test_cpu_bandwidth_derives_from_cpu_and_wall(self) -> None:
        assert "cgroup_cpu_ms_per_sec: 500" in render_nsjail_policy_body(
            RunnerPolicy(max_cpu_seconds=5, max_wall_seconds=10)
        )
        assert "cgroup_cpu_ms_per_sec: 500" in render_nsjail_policy_body(
            RunnerPolicy(max_cpu_seconds=30, max_wall_seconds=60)
        )

    def test_fractional_seconds_are_rounded_up(self) -> None:
        """`time_limit` = ceil(墙钟) + nsjail 兜底余量；`rlimit_cpu` = ceil(CPU)。"""
        body = render_nsjail_policy_body(RunnerPolicy(max_cpu_seconds=1.5, max_wall_seconds=2.5))
        assert f"time_limit: {3 + NSJAIL_TIME_LIMIT_BACKSTOP_SECONDS}" in body
        assert "rlimit_cpu: 2" in body

    def test_environment_is_pinned_and_sorted(self) -> None:
        names = [name for name, _ in sandbox_env(RunnerPolicy())]
        assert names == sorted(names)
        assert "LC_ALL" in names
        assert "PYTHONHASHSEED" in names

    @pytest.mark.parametrize(
        "policy",
        [
            RunnerPolicy(use_seccomp=False),
            RunnerPolicy(use_cgroup=False),
            RunnerPolicy(network=True),
            RunnerPolicy(read_only_root=False),
            RunnerPolicy(allow_root=True),
        ],
    )
    def test_weakening_policy_is_refused(self, policy: RunnerPolicy) -> None:
        with pytest.raises(SandboxUnavailableError):
            assert_policy_executable(policy)
        with pytest.raises(SandboxUnavailableError):
            render_nsjail_policy_body(policy)
        with pytest.raises(SandboxUnavailableError):
            probe_sandbox(policy)

    def test_config_contains_policy_digest_for_audit(self) -> None:
        policy = RunnerPolicy()
        assert f"# runner_policy_digest: {policy_digest(policy)}" in render_nsjail_policy_body(policy)


# =============================================================================================
# 2b. 数值账本边界：越界即拒绝执行，且渲染不出超账本配置（与布尔常量同等 fail-closed）
# =============================================================================================

#: 每条 = (越界字段与取值, 期望出现在违规清单里的字段名)
LEDGER_VIOLATIONS: tuple[tuple[dict, str], ...] = (
    ({"max_memory_bytes": MAX_MEMORY_BYTES + 1}, "max_memory_bytes"),
    ({"max_memory_bytes": MIN_MEMORY_BYTES - 1}, "max_memory_bytes"),
    ({"max_cpu_seconds": MAX_CPU_SECONDS + 1}, "max_cpu_seconds"),
    ({"max_cpu_seconds": 0}, "max_cpu_seconds"),
    ({"max_wall_seconds": MAX_WALL_SECONDS + 1}, "max_wall_seconds"),
    ({"max_wall_seconds": 0}, "max_wall_seconds"),
    ({"max_output_bytes": MAX_OUTPUT_BYTES + 1}, "max_output_bytes"),
    ({"max_output_bytes": MIN_OUTPUT_BYTES - 1}, "max_output_bytes"),
    ({"max_processes": MAX_PROCESSES + 1}, "max_processes"),
    ({"max_processes": MIN_PROCESSES - 1}, "max_processes"),
    ({"max_files_created": MAX_FILES_CREATED + 1}, "max_files_created"),
    ({"max_files_created": -1}, "max_files_created"),
    ({"max_open_files": MAX_OPEN_FILES + 1}, "max_open_files"),
    ({"max_open_files": MIN_OPEN_FILES - 1}, "max_open_files"),
    ({"stack_mib": LEDGER_MAX_MEMORY_BYTES // (1024 * 1024) + 1}, "stack_mib"),
    ({"stack_mib": MIN_STACK_MIB - 1}, "stack_mib"),
    ({"max_cpus": LEDGER_MAX_CPUS_PER_JOB + 1}, "max_cpus"),
    ({"max_cpus": LEDGER_MAX_CPUS_PER_JOB - 1}, "max_cpus"),
)

LEDGER_FIELDS = {
    "max_memory_bytes",
    "max_cpu_seconds",
    "max_wall_seconds",
    "max_output_bytes",
    "max_processes",
    "max_files_created",
    "max_open_files",
    "stack_mib",
    "max_cpus",
}


class TestLedgerBounds:
    @pytest.mark.parametrize(("overrides", "field"), LEDGER_VIOLATIONS)
    def test_out_of_ledger_policy_is_refused_everywhere(
        self, overrides: dict, field: str, tmp_path: Path
    ) -> None:
        """构造 / 校验 / 渲染三处各自的表现：

        ① 构造是纯数据（字段保留非法值，供诊断与摘要用）；② 越界清单命中该字段；
        ③ 校验、渲染、探测、启动一律 fail-closed，且**不产出任何配置字节**。
        """
        policy = RunnerPolicy(**overrides)
        assert getattr(policy, field) == overrides[field]

        violations = policy_bound_violations(policy)
        assert field in {item.field for item in violations}
        violation = next(item for item in violations if item.field == field)
        assert violation.reason in {"above_maximum", "below_minimum", "inconsistent"}
        assert violation.authority, "越界明细必须带权威出处"

        with pytest.raises(SandboxUnavailableError) as excinfo:
            assert_policy_executable(policy)
        assert "policy refused" in str(excinfo.value)
        assert field in str(excinfo.value)

        produced: list[str] = []
        for render in (
            lambda: render_nsjail_policy_body(policy),
            lambda: render_nsjail_config(policy, tmp_path / "case-0"),
        ):
            with pytest.raises(SandboxUnavailableError):
                produced.append(render())
        assert produced == [], "越界策略绝不产出配置字节"
        assert list(tmp_path.iterdir()) == [], "越界策略不落任何文件"

        with pytest.raises(SandboxUnavailableError):
            probe_sandbox(policy)
        with pytest.raises(SandboxUnavailableError):
            build_argv(
                policy,
                config_path=Path(CONFIG),
                sandbox_log_path=Path(CONFIG).with_suffix(".nsjail.log"),
                language="python",
                script="/workspace/main.py",
            )

    @pytest.mark.parametrize(
        ("overrides", "snippet"),
        [
            ({"max_memory_bytes": MAX_MEMORY_BYTES}, "cgroup_mem_max: 100663296"),
            ({"max_cpu_seconds": MAX_CPU_SECONDS}, "rlimit_cpu: 30"),
            (
                {"max_wall_seconds": MAX_WALL_SECONDS},
                # nsjail 的 `time_limit` 是**兜底**：等于策略墙钟时它会先开枪，父进程只看到 SIGKILL
                # 退出码，超时会被误判成 output_mismatch（Linux 验收实测）。故渲染值是"墙钟 + 余量"。
                f"time_limit: {int(MAX_WALL_SECONDS) + NSJAIL_TIME_LIMIT_BACKSTOP_SECONDS}",
            ),
            ({"max_output_bytes": MAX_OUTPUT_BYTES}, "rlimit_fsize: 1"),
            ({"max_processes": MAX_PROCESSES}, "cgroup_pids_max: 64"),
            ({"max_open_files": MAX_OPEN_FILES}, "rlimit_nofile: 1024"),
            ({"stack_mib": LEDGER_MAX_MEMORY_BYTES // (1024 * 1024)}, "rlimit_stack: 96"),
            ({"max_cpus": LEDGER_MAX_CPUS_PER_JOB}, "max_cpus: 1"),
            ({"max_files_created": MAX_FILES_CREATED, "max_memory_bytes": MIN_MEMORY_BYTES},
             "cgroup_mem_max: 16777216"),
        ],
    )
    def test_boundary_values_are_accepted(self, overrides: dict, snippet: str, tmp_path: Path) -> None:
        """边界值本身必须被接受（防 off-by-one：最大值不得被当成越界），并如实进配置。"""
        policy = RunnerPolicy(**overrides)
        assert policy_bound_violations(policy) == ()
        assert_policy_executable(policy)
        assert snippet in render_nsjail_config(policy, tmp_path / "case-0")

    def test_default_policy_is_within_the_ledger(self) -> None:
        assert policy_bound_violations(RunnerPolicy()) == ()
        assert policy_constraint_violations(RunnerPolicy()) == ()
        assert_policy_executable(RunnerPolicy())

    def test_bound_table_covers_every_numeric_limit(self) -> None:
        assert {bound.field for bound in POLICY_BOUNDS} == LEDGER_FIELDS
        assert all(bound.authority for bound in POLICY_BOUNDS)

    def test_package_and_runtime_share_one_bound_table(self) -> None:
        """同一越界值：package 解析明细与执行期守卫明细**逐字相同**（证明只有一张边界表）。"""
        too_big = MAX_MEMORY_BYTES + 1
        runtime = policy_bound_violations(RunnerPolicy(max_memory_bytes=too_big))
        assert runtime
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(package_payload(security_policy={"max_memory_bytes": too_big}))
        assert excinfo.value.reason == "policy_out_of_range"
        assert excinfo.value.detail == runtime[0].detail

    def test_package_parser_holds_no_second_copy_of_the_numbers(self) -> None:
        source = Path(package_module.__file__).read_text(encoding="utf-8")
        assert "policy_bound_violations" in source
        for literal in ("100663296", "16777216", "1048576", "10000"):
            assert literal not in source, f"package.py 不得持有第二份边界数字：{literal}"

    def test_b2_owned_bounds_declare_their_authority(self) -> None:
        authorities = {bound.field: bound.authority for bound in POLICY_BOUNDS}
        assert "evaluation-package.schema.json" in authorities["max_output_bytes"]
        assert "resource-budget.md" in authorities["max_memory_bytes"]
        assert "B2 自有执行常量" in authorities["max_open_files"]
        for field in ("max_open_files", "stack_mib", "max_cpus"):
            assert authorities[field], field

    def test_null_max_files_created_stays_within_the_ledger(self) -> None:
        policy = RunnerPolicy(max_files_created=None)
        assert policy_bound_violations(policy) == ()
        assert_policy_executable(policy)

    def test_non_numeric_limit_is_refused(self) -> None:
        policy = RunnerPolicy(max_memory_bytes="96MiB")  # type: ignore[arg-type]
        assert [item.reason for item in policy_bound_violations(policy)] == ["not_a_number"]
        with pytest.raises(SandboxUnavailableError):
            assert_policy_executable(policy)

    def test_run_case_refuses_out_of_ledger_policy_before_launching(self, tmp_path: Path) -> None:
        """端到端：即使越界策略绕过了 package 解析（直接构造），也在**启动前**被拒。"""
        package = load_package()
        bad_policy = RunnerPolicy(max_memory_bytes=MAX_MEMORY_BYTES + 1)
        poisoned = replace(package, security_policy=bad_policy)
        submission = tampered_submission(
            package, prepare_workspace(tmp_path), policy=bad_policy
        )
        launcher = FakeLauncher()
        with pytest.raises(SandboxUnavailableError):
            run_case(poisoned, submission, probe=available_probe, launcher=launcher)
        assert launcher.starts == 0
        assert not (tmp_path / "case-0.nsjail.cfg").exists()


# =============================================================================================
# 3. 启动 argv 为固定数组（无 shell 拼接）
# =============================================================================================


class TestBuildArgv:
    """argv 形态：固定数组、无 shell 拼接，且**每次启动都声明沙箱自有日志**（`--log`）。"""

    #: 与 `CONFIG` 同目录（工作区之外）——生产的不变量：日志不得落进可写工作区。
    LOG = "/tmp/b2.nsjail.log"

    def test_argv_is_a_plain_list_of_strings(self) -> None:
        policy = RunnerPolicy()
        argv = build_argv(
            policy,
            config_path=Path(CONFIG),
            sandbox_log_path=Path(self.LOG),
            language="python",
            script="/workspace/main.py",
        )
        assert isinstance(argv, list)
        assert all(isinstance(item, str) for item in argv)
        assert argv == [
            resolve_nsjail_path(policy) or NSJAIL,
            "--config",
            str(Path(CONFIG)),
            "--log",
            str(Path(self.LOG)),
            "--",
            "/usr/bin/python3",
            "/workspace/main.py",
        ]
        assert argv[1] == "--config"
        assert argv[3] == "--log"
        assert argv[5] == "--"
        # 沙箱侧归因的边界还依赖两条**结构性约定**，这里显式钉住以防今后被静默破坏：
        # ① 不得把宿主自有日志/任意 fd 显式传进沙箱（`--log_fd` 会改写日志去向、`--pass_fd` 会把
        #    可写句柄带进被囚进程 ⇒ 学生又能伪造归因）；
        # ② 日志与配置同目录（都在工作区之外，见 `test_sandbox_log_must_live_in_the_config_directory`）。
        assert "--log_fd" not in argv
        assert "--pass_fd" not in argv
        assert Path(argv[argv.index("--log") + 1]).parent == Path(argv[argv.index("--config") + 1]).parent

    def test_exec_form_matches_the_documented_array(self) -> None:
        argv = build_argv(
            RunnerPolicy(),
            config_path=Path(CONFIG),
            sandbox_log_path=Path(self.LOG),
            language="bash",
            script="/workspace/main.sh",
            arguments=["a"],
        )
        assert len(argv) == 9
        assert argv[0].endswith("nsjail")
        assert argv[6] == "/bin/bash"
        assert argv[7] == "/workspace/main.sh"
        assert argv[8] == "a"

    def test_sandbox_log_must_live_in_the_config_directory(self) -> None:
        """`build_argv` 能强制的是"日志与配置同目录"——这条校验就是边界的落点。

        `build_argv` 拿不到宿主工作区路径，因此"日志在工作区之外"不是它直接校验的谓词，而是**调用方
        不变量**：`_execute_case` 把配置与日志都放在 `workspace.parent`（该目录在沙箱内被遮蔽为只读空
        tmpfs），并由 `test_sandbox_log_is_a_per_run_artifact` 在调用侧钉住。红队第 2 轮指出旧用例名
        把两层混成一层（F3），故拆开：这里只验校验本体，调用侧不变量在那边验。
        """
        with pytest.raises(ValueError, match="same host directory"):
            build_argv(
                RunnerPolicy(),
                config_path=Path(CONFIG),
                sandbox_log_path=Path("/tmp/other/case-0.nsjail.log"),
                language="python",
                script="/workspace/main.py",
            )

    def test_shell_metacharacters_stay_literal(self) -> None:
        hostile = ["; rm -rf /", "$(id)", "`id`", "&&cat /etc/shadow", "a b\tc", "'quoted'", "*"]
        argv = build_argv(
            RunnerPolicy(),
            config_path=Path(CONFIG),
            sandbox_log_path=Path(self.LOG),
            language="python",
            script="/workspace/main.py",
            arguments=hostile,
        )
        assert argv[8:] == hostile
        assert len(argv) == 8 + len(hostile)

    def test_interpreter_only_comes_from_policy(self) -> None:
        policy = RunnerPolicy(python_interpreter="/opt/venv/bin/python3")
        argv = build_argv(
            policy,
            config_path=Path(CONFIG),
            sandbox_log_path=Path(self.LOG),
            language="python",
            script="/workspace/main.py",
        )
        assert argv[6] == "/opt/venv/bin/python3"
        assert (
            build_argv(
                policy,
                config_path=Path(CONFIG),
                sandbox_log_path=Path(self.LOG),
                language="bash",
                script="/workspace/main.sh",
            )[6]
            == "/bin/bash"
        )
        with pytest.raises(ValueError):
            policy.interpreter_for("perl")

    @pytest.mark.parametrize(
        "script", ["/etc/passwd", "main.py", "/workspace/../etc/passwd", "/workspace/a\\b"]
    )
    def test_script_must_live_inside_the_sandbox_workspace(self, script: str) -> None:
        with pytest.raises(ValueError):
            build_argv(
                RunnerPolicy(),
                config_path=Path(CONFIG),
                sandbox_log_path=Path(self.LOG),
                language="python",
                script=script,
            )

    def test_nul_and_non_string_arguments_are_refused(self) -> None:
        with pytest.raises(ValueError):
            build_argv(
                RunnerPolicy(),
                config_path=Path(CONFIG),
                sandbox_log_path=Path(self.LOG),
                language="python",
                script="/workspace/main.py",
                arguments=["ok", "bad\x00arg"],
            )
        with pytest.raises(TypeError):
            build_argv(
                RunnerPolicy(),
                config_path=Path(CONFIG),
                sandbox_log_path=Path(self.LOG),
                language="python",
                script="/workspace/main.py",
                arguments=[1],  # type: ignore[list-item]
            )

    def test_runner_sources_never_enable_a_shell(self) -> None:
        """源码审计（T-RUN-006）：AST 级检查——不得出现 shell=True / os.system / os.popen。"""
        runner_dir = Path(run_case_module.__file__).parent
        sources = {path.name: path.read_text(encoding="utf-8") for path in runner_dir.glob("*.py")}
        assert sources
        forbidden_attributes = {"system", "popen", "getoutput", "getstatusoutput"}
        for name, source in sources.items():
            tree = ast.parse(source, filename=name)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "shell":
                        continue
                    constant = isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                    assert not constant, f"{name}: shell=True 被禁止"
                function = node.func
                if isinstance(function, ast.Attribute) and function.attr in forbidden_attributes:
                    owner = function.value
                    assert not (
                        isinstance(owner, ast.Name) and owner.id in {"os", "subprocess"}
                    ), f"{name}: {function.attr}() 被禁止"
        assert "shell=False" in sources["run_case.py"]

    def test_launcher_passes_list_argv_and_no_shell(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """monkeypatch `subprocess.Popen` 记录实参：必须是 list[str]，且 shell 非真值。"""
        recorded: list[tuple[list[str], dict]] = []

        class RecordedPopen:
            def __init__(self, argv: list[str], **kwargs: object) -> None:
                recorded.append((list(argv), dict(kwargs)))
                self.pid = 999

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                return 0

        monkeypatch.setattr(run_case_module.subprocess, "Popen", RecordedPopen)
        workspace = tmp_path / "case-0"
        workspace.mkdir()
        launcher = PosixNsjailLauncher(os_name="posix")
        argv = build_argv(
            RunnerPolicy(),
            config_path=Path(CONFIG),
            sandbox_log_path=Path(TestBuildArgv.LOG),
            language="python",
            script="/workspace/main.py",
            arguments=["a; rm -rf /"],
        )
        launcher.start(
            argv,
            cwd=workspace,
            env={"LC_ALL": "C.UTF-8"},
            stdin_text="42\n",
            stdin_path=tmp_path / "in.txt",
            stdout_path=tmp_path / "out.txt",
            stderr_path=tmp_path / "err.txt",
        )
        assert len(recorded) == 1
        recorded_argv, kwargs = recorded[0]
        assert isinstance(recorded_argv, list)
        assert recorded_argv == argv
        assert kwargs["shell"] is False
        assert kwargs["start_new_session"] is True
        assert kwargs["cwd"] == str(workspace)
        assert (tmp_path / "in.txt").read_text(encoding="utf-8") == "42\n"

    def test_launcher_refuses_non_posix_host_without_starting_anything(self, tmp_path: Path) -> None:
        calls: list[str] = []

        def forbidden_popen(*args: object, **kwargs: object) -> object:
            calls.append("popen")
            raise AssertionError("非 POSIX 主机上不得创建任何进程")

        launcher = PosixNsjailLauncher(popen_factory=forbidden_popen, os_name="nt")
        with pytest.raises(SandboxUnavailableError):
            launcher.start(
                [NSJAIL, "--config", CONFIG, "--", "/usr/bin/python3", "/workspace/main.py"],
                cwd=tmp_path,
                env={},
                stdin_text=None,
                stdin_path=tmp_path / "in.txt",
                stdout_path=tmp_path / "out.txt",
                stderr_path=tmp_path / "err.txt",
            )
        assert calls == []

    def test_launcher_rejects_a_string_argv(self, tmp_path: Path) -> None:
        launcher = PosixNsjailLauncher(os_name="posix")
        with pytest.raises(TypeError):
            launcher.start(
                f"{NSJAIL} --config {CONFIG} -- /usr/bin/python3 /workspace/main.py",
                cwd=tmp_path,
                env={},
                stdin_text=None,
                stdin_path=tmp_path / "in.txt",
                stdout_path=tmp_path / "out.txt",
                stderr_path=tmp_path / "err.txt",
            )

    def test_launcher_for_returns_nsjail_launcher_only(self) -> None:
        assert isinstance(launcher_for(RunnerPolicy()), PosixNsjailLauncher)


# =============================================================================================
# 4. package 校验：逐条拒绝
# =============================================================================================


class TestPackageValidation:
    def test_valid_fixture_package_matches_the_contract_schema(self) -> None:
        """夹具本身必须通过 MAIN 契约 schema —— 否则后面所有拒绝断言都失去意义。"""
        payload = package_payload()
        assert contracts.contract_schema_errors(contracts.EVALUATION_PACKAGE_SCHEMA, payload) == []
        assert parse_evaluation_package(payload).tests[0].case_id == CASE_ID

    def test_parser_reads_contract_shape(self) -> None:
        package = load_package()
        assert package.language == "python"
        assert package.adapter.kind == "stdin"
        assert package.adapter.comparison.stdout == "exact"
        assert package.security_policy.max_memory_bytes == LEDGER_MAX_MEMORY_BYTES
        assert package.security_policy.network is False
        assert package.max_files == 5
        assert package.case_by_id(CASE_ID) is not None
        assert package.case_by_id(CASE_ID_2) is None

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"security_policy": {"network": True}}, "policy_const_violation"),
            ({"security_policy": {"read_only_root": False}}, "policy_const_violation"),
            ({"security_policy": {"allow_root": True}}, "policy_const_violation"),
            ({"security_policy": {"max_memory_bytes": 100663297}}, "policy_out_of_range"),
            ({"security_policy": {"max_memory_bytes": 16777215}}, "policy_out_of_range"),
            ({"security_policy": {"max_memory_bytes": "huge"}}, "schema_invalid"),
            ({"security_policy": {"max_cpu_seconds": 30.5}}, "policy_out_of_range"),
            ({"security_policy": {"max_cpu_seconds": 0}}, "policy_out_of_range"),
            ({"security_policy": {"max_wall_seconds": 60.5}}, "policy_out_of_range"),
            ({"security_policy": {"max_output_bytes": 1048577}}, "policy_out_of_range"),
            ({"security_policy": {"max_processes": 65}}, "policy_out_of_range"),
            ({"security_policy": {"max_processes": 0}}, "policy_out_of_range"),
            ({"security_policy": {"max_files_created": 10001}}, "policy_out_of_range"),
            ({"security_policy": {"sandbox_backend": "bare"}}, "policy_unknown_field"),
            ({"security_policy": {"max_syscalls": 10}}, "policy_unknown_field"),
            ({"adapter": {"comparison": {"stdout": "fuzzy"}}}, "invalid_comparison"),
            ({"adapter": {"comparison": {"stdout": "numeric_tolerance"}}}, "invalid_comparison"),
            (
                {"adapter": {"comparison": {"stdout": "exact", "stderr": "numeric_tolerance"}}},
                "invalid_comparison",
            ),
            ({"adapter": {"kind": "harness"}}, "invalid_adapter"),
            ({"adapter": {"kind": "callable"}}, "invalid_callable_entry"),
            ({"language": "ruby"}, "invalid_language"),
            ({"max_files": 0}, "out_of_range"),
            ({"max_total_bytes": 0}, "out_of_range"),
            ({"tests": []}, "invalid_test_case"),
            ({"created_at": "not-a-date"}, "schema_invalid"),
            ({"extra_field": 1}, "unknown_field"),
            ({"question_version_id": "not-a-uuid"}, "schema_invalid"),
            ({"package_digest": "short"}, "schema_invalid"),
        ],
    )
    def test_invalid_packages_are_rejected(self, overrides: dict, reason: str) -> None:
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(package_payload(**overrides))
        assert excinfo.value.reason == reason
        # 编排层按冻结契约只捕获 PackageUnavailableError 即可
        assert isinstance(excinfo.value, PackageUnavailableError)
        assert excinfo.value.detail

    def test_null_max_files_created_means_unbounded(self) -> None:
        package = load_package(security_policy={"max_files_created": None})
        assert package.security_policy.max_files_created is None

    def test_missing_required_field_is_rejected(self) -> None:
        payload = package_payload()
        del payload["security_policy"]
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == "schema_invalid"
        assert "security_policy" in excinfo.value.detail

    @pytest.mark.parametrize(
        ("path", "reason"),
        [
            ("/etc/passwd", "path_not_allowed"),
            ("../main.py", "path_not_allowed"),
            ("a/../../b.py", "path_not_allowed"),
            ("..", "path_not_allowed"),
            ("a\\b.py", "path_not_allowed"),
            ("", "schema_invalid"),
        ],
    )
    def test_fixture_path_must_stay_inside_the_workspace(self, path: str, reason: str) -> None:
        payload = package_payload(
            adapter={"files": [{"path": path, "content_sha256": FIXTURE_DIGEST, "mount": "input"}]}
        )
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == reason

    @pytest.mark.parametrize(
        ("entry", "reason"),
        [
            ({"module": "os.path", "attribute": "join"}, None),
            ({"module": "/tmp/evil", "attribute": "run"}, "invalid_callable_entry"),
            ({"module": "pkg..mod", "attribute": "run"}, "invalid_callable_entry"),
            ({"module": "pkg/evil", "attribute": "run"}, "invalid_callable_entry"),
            ({"module": "solution", "attribute": "run()"}, "invalid_callable_entry"),
            (
                {"module": "solution", "attribute": "run", "arguments_json": "{"},
                "invalid_callable_entry",
            ),
            ({"module": "solution", "attribute": "run", "arguments_json": "[1, 2]"}, None),
        ],
    )
    def test_callable_entry_is_validated(self, entry: dict, reason: str | None) -> None:
        payload = package_payload(adapter={"kind": "callable", "callable": entry})
        if reason is None:
            package = parse_evaluation_package(payload)
            assert package.adapter.callable is not None
            assert package.adapter.callable.module == entry["module"]
            return
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == reason

    def test_duplicate_case_ids_are_rejected(self) -> None:
        payload = package_payload(tests=[base_case(), base_case(ordinal=1)])
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == "duplicate_case"

    def test_duplicate_ordinals_are_rejected(self) -> None:
        payload = package_payload(tests=[base_case(), base_case(case_id=CASE_ID_2)])
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == "duplicate_case"

    def test_cases_are_ordered_by_ordinal(self) -> None:
        payload = package_payload(
            tests=[base_case(), base_case(case_id=CASE_ID_2, name="case two", ordinal=7, weight=2)]
        )
        package = parse_evaluation_package(payload)
        assert [case.ordinal for case in package.tests] == [0, 7]
        assert package.tests[1].weight == 2

    def test_relaxed_policy_is_also_schema_invalid(self) -> None:
        """两层防线：契约 schema 的 `const` 拦住放宽，B2 解析器再给出**明确**拒绝原因。"""
        payload = package_payload(security_policy={"network": True})
        assert contracts.contract_schema_errors(contracts.EVALUATION_PACKAGE_SCHEMA, payload)
        with pytest.raises(PackageRejected) as excinfo:
            parse_evaluation_package(payload)
        assert excinfo.value.reason == "policy_const_violation"


# =============================================================================================
# 5. 不降级：沙箱不可用 ⇒ 抛异常且零子进程
# =============================================================================================


class TestNoDegradation:
    def test_declared_nsjail_path_wins_when_executable(self) -> None:
        """解析顺序：策略声明的路径优先（部署事实优先于兜底搜索）。"""
        assert resolve_nsjail_path(RunnerPolicy(), is_executable=lambda path: path == NSJAIL) == NSJAIL

    def test_nsjail_is_found_at_the_standard_source_install_location(self) -> None:
        """回归（Linux 真实 runner 验收发现的真缺陷）：声明路径不在时，必须找到标准安装位置。

        nsjail 从源码 `make install` 落在 `/usr/local/bin/nsjail`（本项目验收镜像即如此），
        而策略默认声明 `/usr/bin/nsjail` 且该字段**不来自 package 策略**——没有兜底搜索时，
        正常供应的 Linux 主机上产品会永远判定沙箱不可用（fail-closed，但功能不可用）。
        """
        source_install = "/usr/local/bin/nsjail"
        assert (
            resolve_nsjail_path(RunnerPolicy(), is_executable=lambda path: path == source_install)
            == source_install
        )
        # 探测与解析必须一致：探针说可用 ⇒ 启动拿到的是同一个绝对路径
        availability = probe_sandbox(
            RunnerPolicy(),
            platform_name="linux",
            machine="x86_64",
            path_exists=lambda path: path
            in {
                source_install,
                "/sys/fs/cgroup/cgroup.controllers",
                "/proc/self/ns/user",
                "/proc/sys/kernel/unprivileged_userns_clone",
            },
            read_text=lambda path: (
                "cpuset cpu io memory pids" if path.endswith("cgroup.controllers") else "1"
            ),
            is_executable=lambda path: path == source_install,
        )
        assert availability.available is True, availability.reason

    def test_missing_nsjail_everywhere_reports_the_searched_locations(self) -> None:
        """都找不到时 fail-closed，且原因里列出**搜索过的位置**（可诊断，不猜）。"""
        assert resolve_nsjail_path(RunnerPolicy(), is_executable=lambda path: False) is None
        availability = probe_sandbox(
            RunnerPolicy(),
            platform_name="linux",
            machine="x86_64",
            path_exists=lambda path: True,
            read_text=lambda path: (
                "cpuset cpu io memory pids" if path.endswith("cgroup.controllers") else "1"
            ),
            is_executable=lambda path: False,
        )
        assert availability.available is False
        nsjail_check = next(check for check in availability.checks if check.name == "nsjail")
        assert "/usr/local/bin/nsjail" in nsjail_check.detail, nsjail_check.detail
        assert "候选位置" in nsjail_check.detail

    def test_probe_checks_platform_arch_nsjail_cgroup_and_userns(self) -> None:
        availability = probe_sandbox(RunnerPolicy())
        assert [check.name for check in availability.checks] == [
            "platform",
            "cpu_arch",
            "nsjail",
            "cgroup_v2",
            "user_namespace",
        ]

    def test_linux_with_everything_present_is_available(self) -> None:
        availability = probe_sandbox(
            RunnerPolicy(),
            platform_name="linux",
            path_exists=lambda path: path
            in {
                NSJAIL,
                "/sys/fs/cgroup/cgroup.controllers",
                "/proc/self/ns/user",
                "/proc/sys/kernel/unprivileged_userns_clone",
            },
            read_text=lambda path: (
                "cpuset cpu io memory pids" if path.endswith("cgroup.controllers") else "1"
            ),
            is_executable=lambda path: path == NSJAIL,
        )
        assert availability.available is True
        assert availability.reason is None
        assert availability.failed() == ()

    @pytest.mark.parametrize("broken", ["platform", "nsjail", "cgroup", "userns_clone", "apparmor"])
    def test_any_missing_precondition_blocks_execution(self, broken: str) -> None:
        controllers = "/sys/fs/cgroup/cgroup.controllers"
        paths = {
            NSJAIL,
            controllers,
            "/proc/self/ns/user",
            "/proc/sys/kernel/unprivileged_userns_clone",
            "/proc/sys/kernel/apparmor_restrict_unprivileged_userns",
        }
        contents = {
            controllers: "cpuset cpu memory pids",
            "/proc/sys/kernel/unprivileged_userns_clone": "1",
            "/proc/sys/kernel/apparmor_restrict_unprivileged_userns": "0",
        }
        if broken == "nsjail":
            paths.discard(NSJAIL)
        if broken == "cgroup":
            paths.discard(controllers)
        if broken == "userns_clone":
            contents["/proc/sys/kernel/unprivileged_userns_clone"] = "0"
        if broken == "apparmor":
            contents["/proc/sys/kernel/apparmor_restrict_unprivileged_userns"] = "1"
        platform_name = "darwin" if broken == "platform" else "linux"

        def exists(path: str) -> bool:
            return path in paths

        def read(path: str) -> str:
            return contents[path]

        def executable(path: str) -> bool:
            return path in paths and path == NSJAIL

        availability = probe_sandbox(
            RunnerPolicy(),
            platform_name=platform_name,
            path_exists=exists,
            read_text=read,
            is_executable=executable,
        )
        assert availability.available is False
        assert availability.reason
        assert availability.failed()
        with pytest.raises(SandboxUnavailableError):
            assert_sandbox_available(
                RunnerPolicy(),
                platform_name=platform_name,
                path_exists=exists,
                read_text=read,
                is_executable=executable,
            )

    @requires_unavailable_host
    def test_this_host_reports_sandbox_unavailable(self) -> None:
        assert HOST_AVAILABILITY.available is False
        assert HOST_AVAILABILITY.failed()

    def test_run_case_refuses_and_starts_no_process_when_nsjail_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """nsjail 缺失 ⇒ 抛 `SandboxUnavailableError`，并且**没有任何子进程被创建**。"""
        calls = {"popen": 0, "run": 0}

        def forbidden_popen(*args: object, **kwargs: object) -> object:
            calls["popen"] += 1
            raise AssertionError("沙箱不可用时不得创建任何进程")

        def forbidden_run(*args: object, **kwargs: object) -> object:
            calls["run"] += 1
            raise AssertionError("沙箱不可用时不得创建任何进程")

        monkeypatch.setattr(run_case_module.subprocess, "Popen", forbidden_popen)
        monkeypatch.setattr(run_case_module.subprocess, "run", forbidden_run)

        def missing_nsjail_probe(policy: RunnerPolicy) -> SandboxAvailability:
            del policy
            return SandboxAvailability(
                available=False,
                checks=(),
                reason="nsjail: /usr/bin/nsjail exists+executable=False",
            )

        package = load_package()
        workspace = prepare_workspace(tmp_path)
        launcher = FakeLauncher()
        with pytest.raises(SandboxUnavailableError) as excinfo:
            run_case(
                package,
                make_submission(package, workspace),
                probe=missing_nsjail_probe,
                launcher=launcher,
            )
        assert "nsjail" in str(excinfo.value)
        assert calls == {"popen": 0, "run": 0}
        assert launcher.starts == 0

    @requires_unavailable_host
    def test_run_case_with_real_probe_starts_no_process_on_this_host(self, tmp_path: Path) -> None:
        package = load_package()
        workspace = prepare_workspace(tmp_path)
        launcher = FakeLauncher()
        with pytest.raises(SandboxUnavailableError):
            run_case(package, make_submission(package, workspace), launcher=launcher)
        assert launcher.starts == 0

    def test_probe_and_launch_happen_in_this_order(self, tmp_path: Path) -> None:
        """顺序断言：探测失败时连启动器都不允许被触达（不降级的可测判据）。"""
        order: list[str] = []

        def recording_probe(policy: RunnerPolicy) -> SandboxAvailability:
            order.append("probe")
            raise SandboxUnavailableError(f"sandbox unavailable: {policy.nsjail_path}")

        class RecordingLauncher:
            def start(self, *args: object, **kwargs: object) -> object:
                order.append("start")
                raise AssertionError("探测失败后不得启动")

        package = load_package()
        with pytest.raises(SandboxUnavailableError):
            run_case(
                package,
                make_submission(package, prepare_workspace(tmp_path)),
                probe=recording_probe,
                launcher=RecordingLauncher(),  # type: ignore[arg-type]
            )
        assert order == ["probe"]


# =============================================================================================
# 6. 比较策略
# =============================================================================================


class TestComparators:
    def test_registry_covers_contract_strategies(self) -> None:
        assert set(COMPARATORS) == {
            "exact",
            "ignore_trailing_whitespace",
            "numeric_tolerance",
            "ignored",
        }

    @pytest.mark.parametrize(
        ("strategy", "actual", "expected", "matched"),
        [
            ("exact", "42\n", "42\n", True),
            ("exact", "42", "42\n", False),
            ("exact", "42 \n", "42\n", False),
            ("ignore_trailing_whitespace", "42   \n", "42\n", True),
            ("ignore_trailing_whitespace", "42  \n\n\n", "42\n", True),
            ("ignore_trailing_whitespace", "42\nx", "42\nx\n", True),
            ("ignore_trailing_whitespace", "42\nx", "42\ny", False),
            ("ignored", "anything", "else", True),
        ],
    )
    def test_text_strategies(self, strategy: str, actual: str, expected: str, matched: bool) -> None:
        outcome = compare_stdout(actual, expected, strategy)
        assert outcome.matched is matched
        assert outcome.strategy == strategy

    @pytest.mark.parametrize(
        ("actual", "expected", "tolerance", "matched"),
        [
            ("3.14159\n", "3.14\n", 0.01, True),
            ("3.14159\n", "3.14\n", 0.0001, False),
            ("1 2 3\n", "1.0 2.0 3.0\n", 0.0, True),
            ("1 2\n", "1 2 3\n", 5.0, False),
            ("1\n2\n", "1\n", 1.0, False),
            ("hello 1\n", "hello 1.0\n", 0.1, True),
            ("hello\n", "world\n", 1.0, False),
        ],
    )
    def test_numeric_tolerance(
        self, actual: str, expected: str, tolerance: float, matched: bool
    ) -> None:
        assert compare_stdout(actual, expected, "numeric_tolerance", tolerance).matched is matched

    def test_numeric_tolerance_requires_a_tolerance(self) -> None:
        with pytest.raises(ValueError):
            compare_output("1", "1", strategy="numeric_tolerance")

    def test_no_expected_output_is_not_a_comparison(self) -> None:
        outcome = compare_stdout("whatever", None, "exact")
        assert outcome.matched is True
        assert "not applicable" in outcome.detail

    def test_stderr_does_not_accept_numeric_tolerance(self) -> None:
        with pytest.raises(ValueError):
            compare_stderr("1", "1", "numeric_tolerance")
        assert compare_stderr("", None, "exact").matched is True

    def test_unknown_strategy_is_an_internal_defect(self) -> None:
        with pytest.raises(ValueError):
            compare_output("a", "a", strategy="fuzzy")

    def test_file_digest_comparison(self) -> None:
        assert compare_file_digest(FIXTURE_DIGEST, FIXTURE_DIGEST).matched is True
        assert compare_file_digest(FIXTURE_DIGEST, "d" * 64).matched is False

    def test_normalization_helper_is_idempotent(self) -> None:
        once = normalize_trailing_whitespace("a  \nb\t\n\n")
        assert normalize_trailing_whitespace(once) == once == "a\nb"


# =============================================================================================
# 7. 比较策略只来自 package（不随提交变）
# =============================================================================================


class TestStrategyComesFromPackage:
    def test_result_follows_the_package_strategy(self, tmp_path: Path) -> None:
        exact = load_package(adapter={"comparison": {"stdout": "exact", "stderr": "ignored"}})
        relaxed = load_package(
            adapter={"comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"}}
        )
        # 同一份"实际输出"（带行尾空白）：exact 不符，ignore_trailing_whitespace 相符
        evidence_exact, _ = run(
            exact, prepare_workspace(tmp_path / "a"), launcher=FakeLauncher(stdout=b"42   \n")
        )
        assert evidence_exact.passed is False
        assert evidence_exact.failure_category == "output_mismatch"

        evidence_relaxed, _ = run(
            relaxed, prepare_workspace(tmp_path / "b"), launcher=FakeLauncher(stdout=b"42   \n")
        )
        assert evidence_relaxed.passed is True
        assert evidence_relaxed.failure_category is None

    def test_submission_side_adapter_cannot_override_the_package(self, tmp_path: Path) -> None:
        """提交侧（调用方）自带的 adapter 与包不一致 ⇒ 拒绝执行，而不是按提交侧策略比较。"""
        exact = load_package(adapter={"comparison": {"stdout": "exact", "stderr": "ignored"}})
        relaxed = load_package(
            adapter={"comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"}}
        )
        workspace = prepare_workspace(tmp_path)
        tampered = replace(
            make_submission(relaxed, workspace),
            case=relaxed.tests[0],
            adapter=relaxed.adapter,
            policy=relaxed.security_policy,
        )
        with pytest.raises(InternalEvaluationError):
            resolve_adapter(exact, tampered)
        with pytest.raises(InternalEvaluationError):
            run_case(exact, tampered, probe=available_probe, launcher=FakeLauncher())

    def test_tampered_case_cannot_change_expected_output(self, tmp_path: Path) -> None:
        package = load_package()
        tampered = tampered_submission(
            package,
            prepare_workspace(tmp_path),
            case=replace(package.tests[0], expected_stdout="99\n"),
        )
        with pytest.raises(InternalEvaluationError):
            resolve_case(package, tampered)
        with pytest.raises(InternalEvaluationError):
            run_case(package, tampered, probe=available_probe, launcher=FakeLauncher())

    def test_unknown_case_is_refused(self, tmp_path: Path) -> None:
        package = load_package()
        ghost = replace(package.tests[0], case_id=CASE_ID_2, ordinal=9)
        tampered = tampered_submission(package, prepare_workspace(tmp_path), case=ghost)
        with pytest.raises(InternalEvaluationError):
            resolve_case(package, tampered)
        with pytest.raises(InternalEvaluationError):
            run_case(package, tampered, probe=available_probe, launcher=FakeLauncher())

    def test_submission_side_policy_cannot_relax_the_package(self, tmp_path: Path) -> None:
        package = load_package()
        tampered = tampered_submission(
            package,
            prepare_workspace(tmp_path),
            policy=RunnerPolicy(max_memory_bytes=16 * 1024 * 1024),
        )
        with pytest.raises(InternalEvaluationError):
            resolve_policy(package, tampered)
        with pytest.raises(InternalEvaluationError):
            run_case(package, tampered, probe=available_probe, launcher=FakeLauncher())


# =============================================================================================
# 8. 进程树清理与"失败不跳过"
# =============================================================================================


class TestProcessTreeCleanup:
    def test_timeout_kills_the_tree_exactly_once(self, tmp_path: Path) -> None:
        killer = RecordingKiller()
        launcher = FakeLauncher(stdout=b"partial\n", timeout=True, exit_code=137, killer=killer)
        evidence, _ = run(load_package(), prepare_workspace(tmp_path), launcher=launcher)

        assert launcher.process is not None
        assert launcher.process.kill_calls == 1
        assert killer.calls == [(launcher.process.pid, launcher.process.process_group)]
        assert evidence.failure_category == "timeout"
        assert evidence.resource_limit_hit == "wall"
        assert evidence.passed is False
        assert "process tree cleanup" in (evidence.message or "")

    def test_tree_still_alive_after_kill_is_a_sandbox_violation(self, tmp_path: Path) -> None:
        killer = RecordingKiller()
        launcher = FakeLauncher(timeout=True, always_alive=True, killer=killer, exit_code=None)
        evidence, _ = run(load_package(), prepare_workspace(tmp_path), launcher=launcher)
        assert launcher.process is not None
        assert launcher.process.kill_calls == 1
        assert evidence.failure_category == "timeout"
        assert [finding.category for finding in evidence.sandbox_violations] == ["sandbox_violation"]
        assert evidence.sandbox_violations[0].severity == "high"
        assert evidence.sandbox_violations[0].detected_in == "runtime"

    def test_successful_case_never_calls_the_killer(self, tmp_path: Path) -> None:
        killer = RecordingKiller()
        launcher = FakeLauncher(killer=killer)
        evidence, _ = run(load_package(), prepare_workspace(tmp_path), launcher=launcher)
        assert evidence.passed is True
        assert killer.calls == []
        assert launcher.process is not None
        assert launcher.process.kill_calls == 0

    def test_exit_code_mismatch_is_a_crash_without_killing(self, tmp_path: Path) -> None:
        killer = RecordingKiller()
        launcher = FakeLauncher(exit_code=1, killer=killer)
        package = load_package(tests=[base_case(expected_exit_code=0)])
        evidence, _ = run(package, prepare_workspace(tmp_path), launcher=launcher)
        assert evidence.failure_category == "crash"
        assert evidence.exit_code == 1
        assert killer.calls == []

    def test_expected_nonzero_exit_code_is_accepted(self, tmp_path: Path) -> None:
        package = load_package(
            adapter={"comparison": {"stdout": "ignored", "stderr": "ignored"}},
            tests=[base_case(expected_stdout=None, expected_exit_code=3)],
        )
        evidence, _ = run(
            package, prepare_workspace(tmp_path), launcher=FakeLauncher(exit_code=3, stdout=b"")
        )
        assert evidence.passed is True

    def test_posix_killer_sends_kill_to_the_process_group(self) -> None:
        seen: list[tuple[int, int]] = []
        killer = PosixProcessGroupKiller(killpg=lambda group, sig: seen.append((group, sig)))
        report = killer.kill_tree(4242, 4242)
        assert seen == [(4242, 9)]
        assert report.signalled is True
        assert report.process_group == 4242

    def test_posix_killer_falls_back_to_pid_and_tolerates_missing_group(self) -> None:
        seen: list[tuple[int, int]] = []
        killer = PosixProcessGroupKiller(killpg=lambda group, sig: seen.append((group, sig)))
        assert killer.kill_tree(77, None).process_group == 77
        assert seen == [(77, 9)]

        def already_gone(group: int, sig: int) -> None:
            raise ProcessLookupError

        report = PosixProcessGroupKiller(killpg=already_gone).kill_tree(78, 78)
        assert report.signalled is False
        assert report.detail == "process group already gone"

        def not_allowed(group: int, sig: int) -> None:
            raise PermissionError

        assert PosixProcessGroupKiller(killpg=not_allowed).kill_tree(79, 79).signalled is False

    def test_one_failing_case_does_not_skip_the_others(self, tmp_path: Path) -> None:
        """逐用例循环：超时/失败的用例不使其余用例被跳过或伪造通过（I28）。"""
        package = load_package(
            tests=[base_case(name="ok"), base_case(case_id=CASE_ID_2, name="boom", ordinal=1)]
        )
        workspace = prepare_workspace(tmp_path)
        schedule: list[tuple[str, FakeLauncher]] = [
            (CASE_ID, FakeLauncher(stdout=b"42\n")),
            (CASE_ID_2, FakeLauncher(stdout=b"nope\n")),
        ]
        results: list[CaseEvidence] = []
        for case_id, launcher in schedule:
            case = package.case_by_id(case_id)
            assert case is not None
            request = replace(make_submission(package, workspace), case=case)
            results.append(run_case(package, request, probe=available_probe, launcher=launcher))

        assert [item.ordinal for item in results] == [0, 1]
        assert [item.passed for item in results] == [True, False]
        assert results[1].failure_category == "output_mismatch"
        assert results[1].case_id == CASE_ID_2

    def test_run_directory_artifacts_are_removed(self, tmp_path: Path) -> None:
        evidence, _ = run(
            load_package(), prepare_workspace(tmp_path), launcher=FakeLauncher(stdout=b"42\n")
        )
        assert evidence.passed is True
        assert sorted(path.name for path in tmp_path.glob("case-0.*")) == ["case-0.nsjail.cfg"]


# =============================================================================================
# 9. 证据完整性与确定性
# =============================================================================================


class TestEvidence:
    def test_evidence_has_every_frozen_field(self, tmp_path: Path) -> None:
        evidence, _ = run(load_package(), prepare_workspace(tmp_path), launcher=FakeLauncher())
        assert set(CaseEvidence.__dataclass_fields__) == {
            "case_id",
            "ordinal",
            "weight",
            "passed",
            "failure_category",
            "input_digest",
            "stdout_digest",
            "stderr_digest",
            "actual_output_excerpt",
            "expected_output_excerpt",
            "exit_code",
            "duration_ms",
            "resource_limit_hit",
            "sandbox_violations",
            "message",
            # CHG-0012 的五个字段（见 `domain.evidence.CaseEvidence` 与 interfaces.md 第 5.1 节）。
            "mismatch_line",
            "missing_outputs",
            "mismatched_outputs",
            "resource",
            "artifacts",
            "evidence_digest",
        }
        assert evidence.case_id == CASE_ID
        assert evidence.ordinal == 0
        assert evidence.weight == 1
        assert evidence.passed is True
        assert evidence.failure_category is None
        assert len(evidence.input_digest) == 64
        assert len(evidence.stdout_digest) == 64
        assert len(evidence.stderr_digest) == 64
        assert len(evidence.evidence_digest) == 64
        assert evidence.actual_output_excerpt == "42\n"
        assert evidence.expected_output_excerpt == "42\n"
        assert evidence.exit_code == 0
        assert isinstance(evidence.duration_ms, int)
        assert evidence.resource_limit_hit is None
        assert evidence.sandbox_violations == ()
        assert evidence.message and len(evidence.message) <= 1000

    def test_failure_category_stays_inside_the_contract_enum(self, tmp_path: Path) -> None:
        allowed = {
            "timeout",
            "crash",
            "output_mismatch",
            "resource_limit",
            "missing_output",
            "import_error",
            "security_blocked",
            "infrastructure_error",
        }
        launchers = [
            FakeLauncher(stdout=b"nope\n"),
            FakeLauncher(exit_code=1, stdout=b""),
            FakeLauncher(timeout=True, always_alive=True),
            FakeLauncher(stderr=f"{HARNESS_RESOLUTION_MARKER}: import boom failed\n".encode()),
        ]
        for index, launcher in enumerate(launchers):
            evidence, _ = run(
                load_package(), prepare_workspace(tmp_path / f"w{index}"), launcher=launcher
            )
            assert evidence.failure_category in allowed

    def test_evidence_digest_is_deterministic(self, tmp_path: Path) -> None:
        package = load_package()
        first, _ = run(package, prepare_workspace(tmp_path / "a"), launcher=FakeLauncher())
        second, _ = run(package, prepare_workspace(tmp_path / "b"), launcher=FakeLauncher())
        assert first.evidence_digest == second.evidence_digest
        assert first.evidence_digest == evidence_digest_of(first)

    def test_evidence_digest_ignores_wall_clock_duration(self, tmp_path: Path) -> None:
        evidence, _ = run(load_package(), prepare_workspace(tmp_path), launcher=FakeLauncher())
        slower = replace(evidence, duration_ms=evidence.duration_ms + 123456)
        assert slower.duration_ms != evidence.duration_ms
        assert evidence_digest_of(slower) == evidence.evidence_digest

    @pytest.mark.parametrize("field", ["passed", "failure_category", "exit_code", "message"])
    def test_evidence_digest_covers_verdict_fields(self, tmp_path: Path, field: str) -> None:
        evidence, _ = run(load_package(), prepare_workspace(tmp_path), launcher=FakeLauncher())
        changed = {
            "passed": not evidence.passed,
            "failure_category": "crash",
            "exit_code": 42,
            "message": "different",
        }[field]
        assert evidence_digest_of(replace(evidence, **{field: changed})) != evidence.evidence_digest

    def test_input_digest_follows_the_bound_inputs(self, tmp_path: Path) -> None:
        package = load_package()
        workspace = prepare_workspace(tmp_path)
        first, _ = run(package, workspace, launcher=FakeLauncher())
        second, _ = run(package, workspace, launcher=FakeLauncher())
        assert first.input_digest == second.input_digest

        other_package = load_package(tests=[base_case(expected_stdout="43\n")])
        third, _ = run(other_package, workspace, launcher=FakeLauncher())
        assert third.input_digest != first.input_digest

        fourth, _ = run(
            package, workspace, launcher=FakeLauncher(), files=make_files("main.py", "helper.py")
        )
        assert fourth.input_digest != first.input_digest

    def test_output_is_truncated_at_max_output_bytes(self, tmp_path: Path) -> None:
        limit = 1024
        package = load_package(security_policy={"max_output_bytes": limit})
        launcher = FakeLauncher(stdout=b"x" * (limit + 500))
        evidence, _ = run(package, prepare_workspace(tmp_path), launcher=launcher)
        assert evidence.failure_category == "resource_limit"
        assert evidence.resource_limit_hit == "output"
        assert evidence.passed is False
        assert len(evidence.actual_output_excerpt or "") <= EXCERPT_LIMIT
        assert evidence.stdout_digest == sha256_hex(b"x" * limit)

    def test_excerpt_limit_is_enforced_for_long_output(self, tmp_path: Path) -> None:
        package = load_package(
            security_policy={"max_output_bytes": EXCERPT_LIMIT * 2},
            tests=[base_case(expected_stdout=None)],
            adapter={"comparison": {"stdout": "ignored", "stderr": "ignored"}},
        )
        launcher = FakeLauncher(stdout=b"y" * (EXCERPT_LIMIT + 100))
        evidence, _ = run(package, prepare_workspace(tmp_path), launcher=launcher)
        assert len(evidence.actual_output_excerpt or "") == EXCERPT_LIMIT

    def test_read_capped_reports_truncation(self, tmp_path: Path) -> None:
        path = tmp_path / "out.bin"
        path.write_bytes(b"abcdef")
        assert read_capped(path, 3) == (b"abc", True)
        assert read_capped(path, 6) == (b"abcdef", False)
        assert read_capped(tmp_path / "missing.bin", 10) == (b"", False)

    def test_created_files_limit_is_enforced(self, tmp_path: Path) -> None:
        package = load_package(security_policy={"max_files_created": 2})
        launcher = FakeLauncher(created=(("a.txt", b"a"), ("b.txt", b"b"), ("c.txt", b"c")))
        evidence, _ = run(package, prepare_workspace(tmp_path), launcher=launcher)
        assert evidence.failure_category == "resource_limit"
        assert evidence.resource_limit_hit == "files"

    def test_created_files_limit_not_hit_below_the_cap(self, tmp_path: Path) -> None:
        package = load_package(security_policy={"max_files_created": 5})
        evidence, _ = run(
            package, prepare_workspace(tmp_path), launcher=FakeLauncher(created=(("a.txt", b"a"),))
        )
        assert evidence.passed is True

    def test_evidence_never_contains_host_absolute_paths(self, tmp_path: Path) -> None:
        workspace = prepare_workspace(tmp_path)
        launcher = FakeLauncher(stdout=f"cwd={workspace.as_posix()}\n".encode(), exit_code=1)
        evidence, _ = run(load_package(), workspace, launcher=launcher)
        blob = json.dumps(
            {
                "excerpt": evidence.actual_output_excerpt,
                "message": evidence.message,
                "expected": evidence.expected_output_excerpt,
                "violations": [item.message for item in evidence.sandbox_violations],
            },
            ensure_ascii=False,
        )
        assert workspace.as_posix() not in blob
        assert str(workspace) not in blob
        assert "<workspace>" in blob

    def test_seccomp_kill_is_security_blocked_with_a_finding(self, tmp_path: Path) -> None:
        launcher = FakeLauncher(
            exit_code=128 + 31,
            stdout=b"",
            stderr=b"[E][2026-01-01T00:00:00+0000][7] nsjail: child killed: bad system call\n",
        )
        package = load_package(
            adapter={"comparison": {"stdout": "ignored", "stderr": "ignored"}},
            tests=[base_case(expected_stdout=None)],
        )
        evidence, _ = run(package, prepare_workspace(tmp_path), launcher=launcher)
        assert evidence.failure_category == "security_blocked"
        assert evidence.passed is False
        assert [item.category for item in evidence.sandbox_violations] == ["sandbox_violation"]
        assert evidence.sandbox_violations[0].rule_id == "runner.seccomp"

    def test_student_printed_text_cannot_forge_a_violation(self, tmp_path: Path) -> None:
        """只有 nsjail 自有诊断行参与违规判定，学生自己打印的文本不得触发 security_blocked。"""
        launcher = FakeLauncher(
            stdout=b"Network is unreachable\nRead-only file system\n",
            stderr=b"Operation not permitted\nseccomp\n",
        )
        package = load_package(
            tests=[base_case(expected_stdout=None)],
            adapter={"comparison": {"stdout": "ignored", "stderr": "ignored"}},
        )
        evidence, _ = run(package, prepare_workspace(tmp_path), launcher=launcher)
        assert evidence.failure_category is None
        assert evidence.passed is True

    def test_student_streams_are_recorded_verbatim_and_carry_no_sandbox_diagnostics(
        self, tmp_path: Path
    ) -> None:
        """学生流按字节记账，沙箱自有诊断不进学生流（分离后的可观测后果）。

        此前 nsjail 诊断与学生 stderr 同写一份文件，于是"剥掉日志前缀"成了必需步骤；分离之后
        学生流的摘要必须**逐字节**等于学生自己打印的内容——一个像 nsjail 日志前缀的行也不再被改写。
        """
        raw = b"[E][2026-01-01T00:00:00+0000][111] nsjail: fork failed\n"
        evidence, _ = run(
            load_package(),
            prepare_workspace(tmp_path / "a"),
            launcher=FakeLauncher(exit_code=1, stderr=raw),
        )
        assert evidence.stderr_digest == sha256_hex(raw)

    def test_sandbox_diagnostics_are_read_from_the_log_file_not_the_student_stream(
        self, tmp_path: Path
    ) -> None:
        """同一个标记串：写在**日志**里算命中，写在**学生 stderr** 里不算。"""
        marker = b"[E][2026-01-01T00:00:00+0000][111] fork failed: rlimit_nproc reached\n"
        package = load_package()
        from_log, _ = run(
            package,
            prepare_workspace(tmp_path / "logged"),
            launcher=FakeLauncher(exit_code=1, stdout=b"42\n", stderr=b"", sandbox_log=marker),
        )
        assert from_log.failure_category == "resource_limit"
        assert from_log.resource_limit_hit == "processes"
        from_student, _ = run(
            package,
            prepare_workspace(tmp_path / "student"),
            launcher=FakeLauncher(exit_code=1, stdout=b"42\n", stderr=marker),
        )
        assert from_student.failure_category == "crash"
        assert from_student.resource_limit_hit is None

    def test_sandbox_log_is_a_per_run_artifact(self, tmp_path: Path) -> None:
        """调用侧不变量 + 产物卫生：日志与配置/标准流同放**工作区之外**，且读后即删（含宿主绝对路径，
        不进证据、不留盘）。这里同时钉住 F3 里说的那一层：日志路径由调用方放在 `workspace.parent`。"""
        workspace = prepare_workspace(tmp_path / "case")
        _, launcher = run(load_package(), workspace, launcher=FakeLauncher(sandbox_log=b"[I] something\n"))
        log_argument = launcher.argv[launcher.argv.index("--log") + 1]
        assert Path(log_argument) == workspace.parent / f"{workspace.name}.nsjail.log"
        assert Path(log_argument).parent != workspace, "日志不得落在可写工作区内"
        assert not Path(log_argument).exists()
        assert not (workspace.parent / f"{workspace.name}.stderr").exists()
        assert not (workspace.parent / f"{workspace.name}.stdout").exists()

    def test_redact_helper(self, tmp_path: Path) -> None:
        redacted = redact(f"x {tmp_path / 'a'} y", tmp_path)
        assert "<workspace>" in redacted
        assert str(tmp_path) not in redacted
        assert len(redact("z" * 2000, tmp_path)) == 1000

    def test_build_evidence_without_output(self, tmp_path: Path) -> None:
        package = load_package()
        plan = build_execution_plan(
            adapter=package.adapter,
            case=package.tests[0],
            policy=package.security_policy,
            language=package.language,
        )
        evidence = build_evidence(
            case=package.tests[0],
            outcome=CaseOutcome(False, "crash", None, (), "boom"),
            plan=plan,
            workspace=tmp_path,
            digest_input="e" * 64,
            stdout_text="",
            stderr_text="",
            exit_code=None,
            duration_ms=0,
        )
        assert evidence.actual_output_excerpt is None
        assert evidence.expected_output_excerpt == "42\n"
        assert evidence.message == "boom"
        assert len(evidence.evidence_digest) == 64


# =============================================================================================
# 10. 三类 adapter 与固定 harness
# =============================================================================================


class TestAdapters:
    def test_stdin_adapter_passes_stdin_and_arguments(self, tmp_path: Path) -> None:
        package = load_package(
            adapter={"args": ["--mode", "fast"], "stdin": "from-adapter\n"},
            tests=[base_case(stdin="from-case\n", args=["--case"], expected_stdout="42\n")],
        )
        evidence, launcher = run(package, prepare_workspace(tmp_path), launcher=FakeLauncher())
        assert launcher.stdin_text == "from-case\n"  # 用例声明优先于 adapter 声明
        assert executed_argv(launcher.argv) == ["/usr/bin/python3", "/workspace/main.py", "--case"]
        assert evidence.passed is True

    def test_adapter_stdin_used_when_case_does_not_declare_one(self, tmp_path: Path) -> None:
        package = load_package(
            adapter={"stdin": "from-adapter\n"},
            tests=[base_case(stdin=None, expected_stdout="42\n")],
        )
        _, launcher = run(package, prepare_workspace(tmp_path), launcher=FakeLauncher())
        assert launcher.stdin_text == "from-adapter\n"

    def test_bash_language_uses_bash_interpreter_and_entry(self, tmp_path: Path) -> None:
        package = load_package(language="bash")
        evidence, launcher = run(
            package, prepare_workspace(tmp_path, language="bash"), launcher=FakeLauncher()
        )
        assert executed_argv(launcher.argv) == ["/bin/bash", "/workspace/main.sh"]
        assert evidence.passed is True

    def test_missing_entry_script_fails_before_launching(self, tmp_path: Path) -> None:
        package = load_package()
        evidence, launcher = run(
            package, prepare_workspace(tmp_path, entry=False), launcher=FakeLauncher()
        )
        assert evidence.failure_category == "crash"
        assert "entry script is not present" in (evidence.message or "")
        assert launcher.starts == 0

    def test_config_file_is_written_deterministically_before_launch(self, tmp_path: Path) -> None:
        package = load_package()
        workspace = prepare_workspace(tmp_path)
        _, launcher = run(package, workspace, launcher=FakeLauncher())
        config_path = tmp_path / "case-0.nsjail.cfg"
        assert config_path.is_file()
        assert config_path.read_text(encoding="utf-8") == render_nsjail_config(
            package.security_policy, workspace
        )
        assert "--config" in launcher.argv
        assert launcher.argv[launcher.argv.index("--config") + 1] == str(config_path)
        assert executed_argv(launcher.argv)[0] == "/usr/bin/python3"
        assert launcher.cwd == workspace

    def test_callable_harness_is_written_and_fixed(self, tmp_path: Path) -> None:
        package = load_package(
            adapter={
                "kind": "callable",
                "callable": {"module": "solution", "attribute": "add", "arguments_json": "[1, 2]"},
                "comparison": {"stdout": "exact", "stderr": "ignored"},
            },
            tests=[base_case(stdin=None, expected_stdout="3\n")],
        )
        workspace = prepare_workspace(tmp_path)
        _, launcher = run(package, workspace, launcher=FakeLauncher())
        harness = workspace / HARNESS_FILENAME
        assert harness.is_file()
        entry = package.adapter.callable
        assert entry is not None
        assert harness.read_text(encoding="utf-8") == render_callable_harness(
            entry.module, entry.attribute, entry.arguments_json
        )
        assert executed_argv(launcher.argv) == ["/usr/bin/python3", f"/workspace/{HARNESS_FILENAME}"]

    def test_harness_content_is_deterministic_and_bounded(self) -> None:
        first = render_callable_harness("solution", "add", "[1, 2]")
        assert first == render_callable_harness("solution", "add", "[1, 2]")
        assert "importlib.import_module" in first
        assert "'solution'" in first
        assert "'add'" in first
        assert HARNESS_RESOLUTION_MARKER in first
        assert str(HARNESS_RESOLUTION_EXIT) in first
        # 绝不从绝对路径加载：模板里没有按路径加载的 API
        assert "spec_from_file_location" not in first
        assert "sys.path.insert" not in first

    def test_submitted_file_cannot_impersonate_the_harness(self, tmp_path: Path) -> None:
        package = load_package(
            adapter={"kind": "callable", "callable": {"module": "solution", "attribute": "add"}}
        )
        workspace = prepare_workspace(tmp_path)
        (workspace / HARNESS_FILENAME).write_text("print('forged')\n", encoding="utf-8")
        with pytest.raises(InternalEvaluationError):
            run_case(
                package,
                make_submission(package, workspace),
                probe=available_probe,
                launcher=FakeLauncher(),
            )

    def test_import_error_is_classified_from_the_harness_marker(self, tmp_path: Path) -> None:
        package = load_package(
            adapter={
                "kind": "callable",
                "callable": {"module": "solution", "attribute": "add"},
                "comparison": {"stdout": "ignored", "stderr": "ignored"},
            },
            tests=[base_case(stdin=None, expected_stdout=None)],
        )
        marker = f"{HARNESS_RESOLUTION_MARKER}: import solution failed: ModuleNotFoundError\n"
        launcher = FakeLauncher(exit_code=HARNESS_RESOLUTION_EXIT, stdout=b"", stderr=marker.encode())
        evidence, _ = run(package, prepare_workspace(tmp_path), launcher=launcher)
        assert evidence.failure_category == "import_error"
        assert evidence.passed is False

    def test_harness_runs_a_real_callable_with_the_local_interpreter(self, tmp_path: Path) -> None:
        """harness 逻辑自检：用本机解释器直接运行 harness（**与沙箱无关**，只证明 harness 可用）。"""
        workspace = tmp_path / "harness-check"
        workspace.mkdir()
        (workspace / "solution.py").write_text(
            "def add(left, right):\n    return left + right\n", encoding="utf-8"
        )
        harness = workspace / HARNESS_FILENAME
        harness.write_text(render_callable_harness("solution", "add", "[1, 2]"), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(harness)],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout == "3\n"

        missing = workspace / "missing.py"
        missing.write_text(render_callable_harness("nope", "add", None), encoding="utf-8")
        failure = subprocess.run(
            [sys.executable, str(missing)],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            check=False,
        )
        assert failure.returncode == HARNESS_RESOLUTION_EXIT
        assert HARNESS_RESOLUTION_MARKER in failure.stderr

    def test_file_adapter_compares_the_produced_output_digest(self, tmp_path: Path) -> None:
        produced = b"report\n"
        package = load_package(
            adapter={
                "kind": "file",
                "files": [
                    {
                        "path": "input.txt",
                        "content_sha256": sha256_hex(b"input\n"),
                        "mount": "input",
                    },
                    {
                        "path": "report.txt",
                        "content_sha256": sha256_hex(produced),
                        "mount": "expected_output",
                    },
                ],
                "comparison": {"stdout": "ignored", "stderr": "ignored"},
            },
            tests=[base_case(stdin=None, expected_stdout=None)],
        )
        workspace = prepare_workspace(tmp_path)
        (workspace / "input.txt").write_bytes(b"input\n")
        (workspace / "report.txt").write_bytes(produced)
        evidence, _ = run(package, workspace, launcher=FakeLauncher(stdout=b""))
        assert evidence.passed is True
        assert evidence.actual_output_excerpt == "report\n"

    def test_file_adapter_missing_output(self, tmp_path: Path) -> None:
        package = load_package(
            adapter={
                "kind": "file",
                "files": [
                    {
                        "path": "report.txt",
                        "content_sha256": FIXTURE_DIGEST,
                        "mount": "expected_output",
                    }
                ],
                "comparison": {"stdout": "ignored", "stderr": "ignored"},
            },
            tests=[base_case(stdin=None, expected_stdout=None)],
        )
        evidence, _ = run(package, prepare_workspace(tmp_path), launcher=FakeLauncher(stdout=b""))
        assert evidence.failure_category == "missing_output"
        assert "report.txt" in (evidence.message or "")

    def test_file_adapter_digest_mismatch(self, tmp_path: Path) -> None:
        package = load_package(
            adapter={
                "kind": "file",
                "files": [
                    {
                        "path": "report.txt",
                        "content_sha256": FIXTURE_DIGEST,
                        "mount": "expected_output",
                    }
                ],
                "comparison": {"stdout": "ignored", "stderr": "ignored"},
            },
            tests=[base_case(stdin=None, expected_stdout=None)],
        )
        workspace = prepare_workspace(tmp_path)
        (workspace / "report.txt").write_bytes(b"wrong\n")
        evidence, _ = run(package, workspace, launcher=FakeLauncher(stdout=b""))
        assert evidence.failure_category == "output_mismatch"

    @pytest.mark.parametrize("broken", ["missing", "mismatch"])
    def test_input_fixture_problems_are_infrastructure_errors(
        self, tmp_path: Path, broken: str
    ) -> None:
        package = load_package(
            adapter={
                "kind": "file",
                "files": [{"path": "input.txt", "content_sha256": FIXTURE_DIGEST, "mount": "input"}],
            }
        )
        workspace = prepare_workspace(tmp_path)
        if broken == "mismatch":
            (workspace / "input.txt").write_bytes(b"not the fixture\n")
        evidence, launcher = run(package, workspace, launcher=FakeLauncher())
        assert evidence.failure_category == "infrastructure_error"
        assert evidence.passed is False
        assert launcher.starts == 0

    def test_workspace_path_refuses_escape(self, tmp_path: Path) -> None:
        workspace = tmp_path / "case-0"
        workspace.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        with pytest.raises(ValueError):
            workspace_path(workspace, "../outside.txt")
        link = workspace / "link.txt"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            pytest.skip("本机不允许创建符号链接，跳过符号链接穿越断言")
        with pytest.raises(ValueError):
            workspace_path(workspace, "link.txt")

    def test_create_case_workspace_uses_the_policy_template(self, tmp_path: Path) -> None:
        policy = RunnerPolicy(workspace_template="b2-case-")
        first = create_case_workspace(policy, tmp_path / "runs")
        second = create_case_workspace(policy, tmp_path / "runs")
        assert first.is_dir() and second.is_dir()
        assert first != second  # I22：每个用例独立工作区
        assert first.name.startswith("b2-case-")


# =============================================================================================
# 归类函数直接单测（不经 run_case）
# =============================================================================================


class TestClassification:
    def facts(self, **overrides: object) -> RunFacts:
        base: dict[str, object] = {
            "exit_code": 0,
            "timed_out": False,
            "stdout_text": "42\n",
            "stderr_text": "",
            "sandbox_log_text": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "stdout_comparison": compare_output("42\n", "42\n", strategy="exact"),
            "stderr_comparison": None,
            "fixture_outcomes": (),
            "limit_exceeded_files": False,
            "tree_still_alive": False,
            "kill_detail": None,
        }
        return RunFacts(**{**base, **overrides})  # type: ignore[arg-type]

    def test_passing_facts(self) -> None:
        outcome = classify_case_outcome(self.facts(), expected_exit_code=None)
        assert outcome.passed is True
        assert outcome.failure_category is None
        assert outcome.message.startswith("passed:")

    def test_failure_categories_are_prioritized(self) -> None:
        facts = self.facts(
            timed_out=True,
            stdout_truncated=True,
            limit_exceeded_files=True,
            stdout_comparison=compare_output("a", "b", strategy="exact"),
        )
        outcome = classify_case_outcome(facts, expected_exit_code=None)
        assert outcome.failure_category == "timeout"
        assert outcome.resource_limit_hit == "wall"

    def test_output_limit_beats_output_mismatch(self) -> None:
        facts = self.facts(
            stdout_truncated=True, stdout_comparison=compare_output("a", "b", strategy="exact")
        )
        outcome = classify_case_outcome(facts, expected_exit_code=None)
        assert outcome.failure_category == "resource_limit"
        assert outcome.resource_limit_hit == "output"

    def test_student_text_cannot_forge_a_limit_attribution(self) -> None:
        """红队证伪 → 修复后的不变量：学生文本**不是**归因来源，沙箱日志才是。

        修复前：`_scan_markers` 只做 `"nsjail" in line` 过滤，而学生 stdout/stderr 与 nsjail 自有日志
        同写一份文件 ⇒ 学生打印一行 `nsjail: out of memory` 即命中（红队用真实编排层落库到
        `case_evidence`）。修复后这两条都成真：学生文本无论如何都**不**命中，日志里的同一串才命中。
        """
        forged = "nsjail: out of memory\n"
        assert _scan_sandbox_log(forged, LIMIT_MARKERS) == "memory"  # 纯函数本身仍会匹配……
        facts = self.facts(
            exit_code=1,
            stderr_text=forged,  # ……但学生流根本不进这个函数
            stdout_comparison=compare_output("", "42\n", strategy="exact"),
        )
        outcome = classify_case_outcome(facts, expected_exit_code=None)
        assert outcome.passed is False
        assert outcome.failure_category == "output_mismatch"
        assert outcome.resource_limit_hit is None
        # 同一串出现在沙箱日志里（宿主自有、学生写不进）时才构成沙箱侧证据
        from_log = classify_case_outcome(
            self.facts(exit_code=1, sandbox_log_text=forged), expected_exit_code=None
        )
        assert from_log.failure_category == "resource_limit"
        assert from_log.resource_limit_hit == "memory"

    def test_plain_student_memoryerror_is_not_a_limit_hit(self) -> None:
        """学生程序自己打印的 `MemoryError` 不构成限制标记（学生文本不是归因来源）。"""
        outcome = classify_case_outcome(
            self.facts(
                exit_code=1,
                stderr_text="Traceback (most recent call last):\nMemoryError\n",
                stdout_comparison=compare_output("", "42\n", strategy="exact"),
            ),
            expected_exit_code=None,
        )
        assert outcome.failure_category == "output_mismatch"
        assert outcome.resource_limit_hit is None

    def test_negative_exit_code_is_a_resource_limit_with_a_finding(self) -> None:
        outcome = classify_case_outcome(self.facts(exit_code=-9), expected_exit_code=None)
        assert outcome.failure_category == "resource_limit"
        assert outcome.violations[0].category == "resource_limit"

    def test_sigkill_convention_128_plus_9(self) -> None:
        outcome = classify_case_outcome(self.facts(exit_code=137), expected_exit_code=None)
        assert outcome.failure_category == "resource_limit"

    def test_finding_ids_are_deterministic(self) -> None:
        first = make_finding(category="resource_limit", severity="medium", message="m", rule_id="r")
        second = make_finding(category="resource_limit", severity="medium", message="m", rule_id="r")
        assert first == second
        assert first.finding_id.startswith("SF-")
        assert first.detected_in == "runtime"


def test_sandbox_env_is_never_inherited(tmp_path: Path) -> None:
    """启动环境是固定集合（与宿主环境无关），避免宿主变量与路径进入沙箱。"""
    env = dict(sandbox_env(RunnerPolicy()))
    assert set(env) == {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
        "PYTHONIOENCODING",
        "TMPDIR",
    }
    assert "DATABASE_URL" not in env
    launcher = FakeLauncher()
    run(load_package(), prepare_workspace(tmp_path), launcher=launcher)
    assert launcher.env == env
