"""Linux 真实批量执行验收：`run_cases` 的 single_run / ssh_session 在真 nsjail 内。

与 `test_nsjail_runner_acceptance.py` 的分工：那个文件验收**逐用例** `run_case`；本文件验收
Unix 作业扩展引入的**一次执行、多条证据**路径：

- `single_run`：一次沙箱执行 → 有界 stdout 按整行分隔符切分 → 每段只比较自己的期望（绝不位移）；
  结构不合法（缺/多分隔符、空段）⇒ 全部 segment 用例 `output_mismatch`；
  运行级失败（超时）⇒ 全部用例同类别；声明 `file_checks` 的用例在运行后由 B2 直接检查文件；
- `workspace_prep` profile：fixture 属于服务账号、被囚 uid 是 subordinate uid，普通 profile 下
  chmod/写入会 EPERM；准备 profile 先 chmod、降权后以被囚 uid 重建文件 inode（不在 userns 内 chown），
  因此 vi/chmod 生效；
- `ssh_session`：真实 sshd + 真实 ssh 客户端（严格 argv）+ 真实远端会话；三条 ssh_check 由
  sshd 自有日志与客户端退出判定；错误口令/超时/非法首行是学生侧失败，环境起不来是基础设施故障；
- 清理：批量运行（含超时）后不留 nsjail/sshd/学生进程，不留 `<workspace>.ssh` 临时目录。

判定口径：结论全部来自**真实执行**（退出码/信号/stdout/`ps` 观测/工作区文件状态），
拿不到证据一律 fail 并写明原因；**不以配置存在冒充限制生效**。

运行（宿主侧一条命令，见 `verify/linux-nsjail/README.md`）：

    docker run --rm --privileged --cgroupns=host \
      -e PYTHONPATH=/work/B2/src \
      -v "D:/VScodeProjects/AutoGrader:/work" -w /work/B2 \
      autograder-b2-nsjail:local python3 -m pytest tests/linux/test_nsjail_batch_acceptance.py -v -s

SSH 用例需要镜像内安装 openssh-server（提供 `/usr/sbin/sshd`、`ssh-keygen`）与 openssh-client
（提供 `/usr/bin/ssh`）；缺失时逐项 **skip 并写明原因**，不伪报通过。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_LINUX = sys.platform.startswith("linux")
pytestmark = pytest.mark.skipif(
    not _LINUX,
    reason="需要真实 Linux + nsjail（本机 Windows 无法执行；请用 verify/linux-nsjail 的容器方式）",
)

DIGEST = "a" * 64
QUESTION_ID = "11111111-1111-4111-8111-111111111111"
CASE_ID_1 = "22222222-2222-4222-8222-222222222222"
CASE_ID_2 = "33333333-3333-4333-8333-333333333333"
CASE_ID_3 = "44444444-4444-4444-8444-444444444444"
DELIMITER = "=====B2-ACCEPTANCE-SEGMENT====="
SSH_PASSWORD = "practice-acceptance-secret"

SSHD_CANDIDATES = ("/usr/sbin/sshd", "/usr/bin/sshd")
SSH_CLIENT_CANDIDATES = ("/usr/bin/ssh", "/bin/ssh")
SSH_KEYGEN_CANDIDATES = ("/usr/bin/ssh-keygen", "/bin/ssh-keygen")


def _which(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


HAS_SSHD = _which(SSHD_CANDIDATES) is not None
HAS_SSH_CLIENT = _which(SSH_CLIENT_CANDIDATES) is not None
HAS_SSH_KEYGEN = _which(SSH_KEYGEN_CANDIDATES) is not None
requires_ssh = pytest.mark.skipif(
    not (HAS_SSHD and HAS_SSH_CLIENT and HAS_SSH_KEYGEN),
    reason="镜像内缺少 openssh-server/client（本项如实 skip）",
)


# --- 包夹具 ---------------------------------------------------------------------------------


def _base_payload() -> dict:
    return {
        "question_version_id": QUESTION_ID,
        "package_digest": DIGEST,
        "language": "bash",
        "adapter": {
            "kind": "stdin",
            "args": [],
            "stdin": None,
            "expect_exit_code": None,
            "comparison": {"stdout": "exact", "stderr": "ignored", "numeric_tolerance": None},
        },
        "tests": [],
        "security_policy": {
            "network": False,
            "read_only_root": True,
            "allow_root": False,
            "max_memory_bytes": 96 * 1024 * 1024,
            "max_cpu_seconds": 10,
            "max_wall_seconds": 20,
            "max_output_bytes": 65536,
            "max_processes": 32,
            "max_files_created": 64,
        },
        "max_files": 8,
        "max_total_bytes": 200000,
        "created_at": "2026-01-01T00:00:00Z",
    }


def _case(case_id: str, ordinal: int, **extra: object) -> dict:
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
    case.update(extra)
    return case


def _segment_package(expected: tuple[str, ...], *, delimiter: str = DELIMITER) -> dict:
    payload = _base_payload()
    payload["tests"] = [
        _case(
            (CASE_ID_1, CASE_ID_2, CASE_ID_3)[index],
            index,
            segment=index,
            expected_stdout=text,
        )
        for index, text in enumerate(expected)
    ]
    payload["execution"] = {"mode": "single_run", "segment_delimiter": delimiter}
    return payload


def _file_checks_package(checks: list[dict], *, fixtures: list[tuple[str, bytes]]) -> dict:
    payload = _base_payload()
    payload["adapter"]["kind"] = "file"
    payload["adapter"]["files"] = [
        {
            "path": path,
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "mount": "input",
        }
        for path, content in fixtures
    ]
    payload["tests"] = [_case(CASE_ID_1, 0, file_checks=checks)]
    payload["execution"] = {"mode": "single_run"}
    return payload


class _FixtureProvider:
    """本验收用的最小 `FixtureProvider`：按 package 声明返回固定字节（真实通道见集成测试）。"""

    def __init__(self, items: list[tuple[str, bytes]]) -> None:
        self._items = items

    def fetch_inputs(self, *, question_version_id: str):
        del question_version_id

        from autograder_b2.runner.fixtures import FixtureContent

        return tuple(
            FixtureContent(
                path=path,
                content_sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                content=content,
            )
            for path, content in self._items
        )


def _run_batch(payload: dict, workspace: Path, provider=None):
    from autograder_b2.domain.envelope import EnvelopeFile
    from autograder_b2.domain.evidence import BatchRunInput
    from autograder_b2.runner.package import parse_evaluation_package
    from autograder_b2.runner.run_cases import run_cases

    package = parse_evaluation_package(payload)
    workspace.mkdir(parents=True, exist_ok=True)
    # 与生产 CasWorkspaceProvider 一致：被囚 uid 是 subordinate uid，工作区必须可写（0777）。
    workspace.chmod(0o777)
    entry = "main.sh"
    if not (workspace / entry).exists():
        (workspace / entry).write_text("#!/bin/bash\n", encoding="utf-8")
    batch = BatchRunInput(
        submission_id=str(uuid.uuid4()),
        evaluation_run_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        workspace=workspace,
        files=(
            EnvelopeFile(
                path=entry,
                content_sha256=DIGEST,
                size_bytes=1,
                storage_ref=entry,
            ),
        ),
        cases=package.tests,
        adapter=package.adapter,
        policy=package.security_policy,
    )
    evidences = run_cases(package, batch, fixture_provider=provider)
    return package, evidences


def _run_case(payload: dict, workspace: Path, *, entry_name: str = "main.sh", program: str = ""):
    from autograder_b2.domain.envelope import EnvelopeFile
    from autograder_b2.domain.evidence import SubmissionRunInput
    from autograder_b2.runner.package import parse_evaluation_package
    from autograder_b2.runner.run_case import run_case

    package = parse_evaluation_package(payload)
    workspace.mkdir(parents=True, exist_ok=True)
    workspace.chmod(0o777)
    (workspace / entry_name).write_text(program, encoding="utf-8")
    submission = SubmissionRunInput(
        submission_id=str(uuid.uuid4()),
        evaluation_run_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        workspace=workspace,
        files=(
            EnvelopeFile(
                path=entry_name,
                content_sha256=DIGEST,
                size_bytes=len(program.encode("utf-8")),
                storage_ref=entry_name,
            ),
        ),
        case=package.tests[0],
        adapter=package.adapter,
        policy=package.security_policy,
    )
    return package, run_case(package, submission)


def _live_processes(tmp_path: Path) -> list[str]:
    listing = subprocess.run(
        ["ps", "-eo", "pid,comm,stat,args"], capture_output=True, text=True, check=False
    ).stdout
    leftovers: list[str] = []
    for line in listing.splitlines()[1:]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        _pid, comm, state, args = parts
        if state.startswith("Z"):
            continue
        if comm in {"nsjail", "sshd", "ssh"} or str(tmp_path) in args:
            leftovers.append(line.strip())
    return leftovers


# --- single_run：分段 ------------------------------------------------------------------------


def test_single_run_segments_are_one_execution_and_all_pass(tmp_path: Path) -> None:
    """① 一次执行：程序把执行次数写进 marker，证据仍按段产出；三段全对。"""
    workspace = tmp_path / "segments-ok"
    package = _segment_package(("one\n", "two\n", "three\n"))
    program = (
        'count_file="$PWD/execution-count"\n'
        'if [ -f "$count_file" ]; then echo "REPEATED" >> "$count_file"; else echo once > "$count_file"; fi\n'
        'printf "one\\n%s\\ntwo\\n%s\\nthree\\n" "' + DELIMITER + '" "' + DELIMITER + '"\n'
    )
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.sh").write_text(program, encoding="utf-8")
    evidences = _run_batch(package, workspace)[1]

    print(f"\n[S1] executed once? {[(item.passed, item.segment) for item in evidences]}")
    assert [(item.passed, item.segment) for item in evidences] == [
        (True, 0),
        (True, 1),
        (True, 2),
    ]
    assert (workspace / "execution-count").read_text(encoding="utf-8").strip() == "once", (
        "single_run 必须只启动一次沙箱；marker 出现 REPEATED 说明被逐用例重复执行"
    )


def test_single_run_one_bad_segment_does_not_shift() -> None:
    """② 单段错：只有那一段失败，其余段按位对应（绝不把第 2 段判给第 3 段）。"""
    workspace = Path(__import__("tempfile").mkdtemp(prefix="b2-batch-seg-"))
    try:
        package = _segment_package(("one\n", "two\n", "three\n"))
        program = f'printf "one\\n{DELIMITER}\\nWRONG\\n{DELIMITER}\\nthree\\n"\n'
        (workspace / "main.sh").write_text(program, encoding="utf-8")
        evidences = _run_batch(package, workspace)[1]
        assert [item.passed for item in evidences] == [True, False, True]
        assert evidences[1].failure_category == "output_mismatch"
        assert evidences[2].actual_output_excerpt == "three\n"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.mark.parametrize(
    "program",
    [
        'printf "one\\ntwo\\nthree\\n"\n',  # 缺分隔符
        (  # 多分隔符：四个段 > 声明的三段
            'printf "one\\n' + DELIMITER + '\\ntwo\\n' + DELIMITER
            + '\\nthree\\n' + DELIMITER + '\\nfour\\n"\n'
        ),
        # 空段：连续两个分隔行
        'printf "one\\n' + DELIMITER + '\\n' + DELIMITER + '\\ntwo\\n"\n',
    ],
)
def test_single_run_structure_problems_fail_all_segments(program: str) -> None:
    """③ 结构不合法：缺/多分隔符、空段 ⇒ 全部 segment 用例 output_mismatch，消息写明分段结构不合法。"""
    import tempfile

    workspace = Path(tempfile.mkdtemp(prefix="b2-batch-structure-"))
    try:
        package = _segment_package(("one\n", "two\n", "three\n"))
        (workspace / "main.sh").write_text(program, encoding="utf-8")
        evidences = _run_batch(package, workspace)[1]
        assert all(item.failure_category == "output_mismatch" for item in evidences)
        assert all("分段结构不合法" in (item.message or "") for item in evidences)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_single_run_timeout_is_a_run_level_failure() -> None:
    """④ 超时：整次运行失败 ⇒ 全部用例同一类别（timeout/wall），均不得分。"""
    import tempfile

    workspace = Path(tempfile.mkdtemp(prefix="b2-batch-timeout-"))
    try:
        package = _segment_package(("one\n", "two\n", "three\n"))
        (workspace / "main.sh").write_text("sleep 600\n", encoding="utf-8")
        evidences = _run_batch(package, workspace)[1]
        print(f"\n[timeout] {[(i.failure_category, i.resource_limit_hit) for i in evidences]}")
        assert [item.failure_category for item in evidences] == ["timeout"] * 3
        assert all(item.resource_limit_hit == "wall" for item in evidences)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# --- workspace_prep / file_checks -----------------------------------------------------------


FIXTURE_CONTENT = b"hello fixture\n"


def test_workspace_prep_allows_vi_and_chmod_for_subordinate_uid() -> None:
    """⑤ 真实 vi 批处理 + chmod 在 workspace_prep 下生效；file_checks 通过（第 6 题形态）。

    同时留下对照证据：默认 profile（无 file_checks）下同一操作被 subuid 权限挡住（chmod EPERM），
    证明准备 profile 不是"多余装饰"。
    """
    import hashlib
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="b2-batch-prep-"))
    try:
        expected = b"edited fixture\n"
        checks = [
            {
                "path": "week6_1.txt",
                "content_sha256": hashlib.sha256(expected).hexdigest(),
                "mode": None,
            },
            {"path": "week6_2.dat", "content_sha256": None, "mode": "0744"},
        ]
        payload = _file_checks_package(
            checks, fixtures=[("week6_1.txt", FIXTURE_CONTENT), ("week6_2.dat", b"perm\n")]
        )
        workspace = root / "prep"
        workspace.mkdir()
        program = (
            "if command -v vi >/dev/null 2>&1; then\n"
            "  vi -es -c '1s/.*/edited fixture/' -c 'wq' week6_1.txt || exit 3\n"
            "else\n"
            "  printf 'edited fixture\\n' > week6_1.txt || exit 3\n"
            "fi\n"
            "chmod 0744 week6_2.dat || { echo CHMOD_DENIED; exit 4; }\n"
            "echo STUDENT_DONE\n"
        )
        (workspace / "main.sh").write_text(program, encoding="utf-8")
        _, evidences = _run_batch(
            payload,
            workspace,
            provider=_FixtureProvider(
                [("week6_1.txt", FIXTURE_CONTENT), ("week6_2.dat", b"perm\n")]
            ),
        )
        evidence = evidences[0]
        print(
            f"\n[prep] passed={evidence.passed} checks={evidence.file_checks} "
            f"output={evidence.actual_output_excerpt!r} exit={evidence.exit_code}"
        )
        assert "STUDENT_DONE" in (evidence.actual_output_excerpt or ""), (
            f"学生脚本必须在 prep profile 下正常执行：{evidence.message!r} / "
            f"{evidence.actual_output_excerpt!r}"
        )
        assert evidence.passed is True, (
            f"file_checks 必须通过：{evidence.message!r} checks={evidence.file_checks}"
        )
        assert all(check.matched for check in evidence.file_checks)
        assert os.lstat(workspace / "week6_2.dat").st_mode & 0o777 == 0o744, (
            "chmod 0744 必须真实落到工作区文件（不是读 stdout 冒充）"
        )
        assert (workspace / "week6_1.txt").read_bytes() == expected

        # 对照：默认 profile（无 file_checks）下，subordinate uid 无法 chmod 服务账号拥有的文件。
        control_payload = _base_payload()
        control_payload["tests"] = [
            _case(CASE_ID_1, 0, expected_stdout="CHMOD_DENIED\n")
        ]
        control_workspace = root / "control"
        control_workspace.mkdir()
        (control_workspace / "owned.txt").write_text("x", encoding="utf-8")
        _, control = _run_case(
            control_payload,
            control_workspace,
            program='chmod 0744 "$PWD/owned.txt" 2>/dev/null && echo CHMOD_OK || echo CHMOD_DENIED\n',
        )
        print(f"\n[prep-control] passed={control.passed} output={control.actual_output_excerpt!r}")
        assert "CHMOD_DENIED" in (control.actual_output_excerpt or ""), (
            "默认 profile 的被囚 uid 不应该能 chmod 服务账号拥有的文件"
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fixture_digest_mismatch_is_infrastructure_error() -> None:
    """⑥ fixture 摘要不符 ⇒ 技术故障（infrastructure_error），绝不记作学生答错。"""
    import hashlib
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="b2-batch-fixture-"))
    try:
        payload = _file_checks_package(
            [
                {
                    "path": "week6_1.txt",
                    "content_sha256": hashlib.sha256(FIXTURE_CONTENT).hexdigest(),
                    "mode": None,
                }
            ],
            fixtures=[("week6_1.txt", FIXTURE_CONTENT)],
        )
        workspace = root / "case"
        workspace.mkdir()
        (workspace / "main.sh").write_text("true\n", encoding="utf-8")

        class _WrongBytes:
            def fetch_inputs(self, *, question_version_id: str):
                del question_version_id
                from autograder_b2.runner.fixtures import FixtureContent

                wrong = b"not the fixture"
                return (
                    FixtureContent(
                        path="week6_1.txt",
                        content_sha256=hashlib.sha256(wrong).hexdigest(),
                        size_bytes=len(wrong),
                        content=wrong,
                    ),
                )

        package, evidences = _run_batch(payload, workspace, provider=_WrongBytes())
        del package
        assert len(evidences) == 1
        assert evidences[0].failure_category == "infrastructure_error"
        assert evidences[0].passed is False
        print(f"\n[fixture] {evidences[0].failure_category} / {evidences[0].message!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- 普通题回归 -----------------------------------------------------------------------------


def test_default_profile_keeps_non_root_and_no_network() -> None:
    """⑦ 回归：普通题仍是非 root、禁网、只读根（workspace_prep/ssh 不改变默认 profile）。"""
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="b2-batch-regression-"))
    try:
        payload = _base_payload()
        payload["tests"] = [_case(CASE_ID_1, 0, expected_stdout="NONROOT BLOCKED\n")]
        program = (
            'if [ "$(id -u)" = "0" ]; then echo ROOT; else echo -n "NONROOT "; fi\n'
            "python3 - <<'PY'\n"
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
            "    print('CONNECTED')\n"
            "except OSError:\n"
            "    print('BLOCKED')\n"
            "PY\n"
        )
        _, evidence = _run_case(payload, root / "case", program=program)
        print(f"\n[regression] passed={evidence.passed} output={evidence.actual_output_excerpt!r}")
        assert evidence.passed is True, evidence.message
        assert "NONROOT" in (evidence.actual_output_excerpt or "")
        assert "BLOCKED" in (evidence.actual_output_excerpt or "")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- ssh_session ----------------------------------------------------------------------------


def _ssh_payload(*, password: str = SSH_PASSWORD, session_timeout: float = 10) -> dict:
    payload = _base_payload()
    # sshd 启动 + 登录 + 会话全部发生在同一次执行里：墙钟上限留足（nsjail time_limit 再兜底）。
    payload["security_policy"]["max_wall_seconds"] = 60
    payload["security_policy"]["max_cpu_seconds"] = 30
    payload["tests"] = [
        _case(CASE_ID_1, 0, ssh_check="service_ready"),
        _case(CASE_ID_2, 1, ssh_check="auth_succeeded"),
        _case(CASE_ID_3, 2, ssh_check="session_closed"),
    ]
    payload["execution"] = {
        "mode": "ssh_session",
        "ssh": {
            "host": "127.0.0.1",
            "port": 22,
            "username": "user01",
            "password": password,
            "login_timeout_seconds": 10,
            "session_timeout_seconds": session_timeout,
        },
    }
    return payload


def _run_ssh(payload: dict, *, script: str):
    import tempfile

    workspace = Path(tempfile.mkdtemp(prefix="b2-batch-ssh-"))
    try:
        (workspace / "main.sh").write_text(script, encoding="utf-8")
        package, evidences = _run_batch(payload, workspace)
        print(
            f"\n[ssh] executed={[(i.passed, i.failure_category) for i in evidences]} "
            f"output={[i.actual_output_excerpt for i in evidences]}"
        )
        return package, evidences, [str(item.message) for item in evidences]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@requires_ssh
def test_ssh_real_login_closes_all_three_checks() -> None:
    """⑧ 真实登录：`ssh user01@127.0.0.1` / `exit` ⇒ service_ready、auth_succeeded、session_closed 全通过。"""
    _, evidences, messages = _run_ssh(
        _ssh_payload(), script=f"ssh user01@127.0.0.1\nexit\n"
    )
    del messages
    assert [item.passed for item in evidences] == [True, True, True], [
        (item.passed, item.message) for item in evidences
    ]


@requires_ssh
def test_ssh_wrong_password_fails_student_side_only() -> None:
    """⑨ 错误口令：service_ready 仍通过（环境正常），auth/session 失败且是学生侧类别。"""
    _, evidences, _ = _run_ssh(
        _ssh_payload(password="wrong-password"), script="ssh user01@127.0.0.1\nexit\n"
    )
    assert evidences[0].passed is True, f"sshd 已启动，service_ready 必须通过：{evidences[0].message}"
    assert evidences[1].passed is False and evidences[1].failure_category == "output_mismatch"
    assert evidences[2].passed is False and evidences[2].failure_category == "output_mismatch"


@requires_ssh
def test_ssh_invalid_first_line_is_rejected_without_running_a_shell() -> None:
    """⑩ 非法首行：三条证据全部学生侧失败，消息明确；不执行远端命令。"""
    marker = "SHOULD_NOT_RUN_MARKER"
    _, evidences, _ = _run_ssh(
        _ssh_payload(),
        script=f"echo {marker}\nexit\n",
    )
    assert all(item.failure_category == "output_mismatch" for item in evidences)
    assert all("not a valid ssh login" in (item.message or "") for item in evidences)
    assert all(marker not in (item.actual_output_excerpt or "") for item in evidences)


@requires_ssh
def test_ssh_session_that_never_exits_fails_session_closed() -> None:
    """⑪ 未退出：会话超时 ⇒ session_closed 失败（学生侧），service_ready/auth 仍如实判定。"""
    _, evidences, _ = _run_ssh(
        _ssh_payload(session_timeout=1), script="ssh user01@127.0.0.1\n"
    )
    assert evidences[0].passed is True
    assert evidences[1].passed is True, f"登录本身应成功：{evidences[1].message}"
    assert evidences[2].passed is False and evidences[2].failure_category == "output_mismatch"


@requires_ssh
def test_ssh_loopback_only_and_cleanup() -> None:
    """⑫ 禁外网 + 清理：远端访问外网失败但会话正常结束；运行后无残留进程/临时目录。"""
    import tempfile

    workspace = Path(tempfile.mkdtemp(prefix="b2-batch-ssh-net-"))
    try:
        script = (
            "ssh user01@127.0.0.1\n"
            "python3 - <<'PY'\n"
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
            "    print('CONNECTED')\n"
            "except OSError:\n"
            "    print('REMOTE_BLOCKED')\n"
            "PY\n"
            "exit\n"
        )
        (workspace / "main.sh").write_text(script, encoding="utf-8")
        package, evidences = _run_batch(_ssh_payload(), workspace)
        del package
        output = "".join(item.actual_output_excerpt or "" for item in evidences)
        print(f"\n[ssh-net] passed={[i.passed for i in evidences]} output={output!r}")
        assert "CONNECTED" not in output
        assert "REMOTE_BLOCKED" in output, (
            "远端会话只允许 loopback；连接外网必须失败（真实执行证据）"
        )
        assert all(item.passed for item in evidences), [item.message for item in evidences]
        leftovers = _live_processes(workspace)
        assert leftovers == [], f"SSH 批量运行后不得残留进程：{leftovers}"
        assert not (workspace.parent / f"{workspace.name}.ssh").exists(), (
            "每次运行的 SSH 布局目录必须清理"
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
