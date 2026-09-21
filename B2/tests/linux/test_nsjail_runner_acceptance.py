"""Linux 真实 runner 验收：把**真实** `run_case` + `PosixNsjailLauncher` 接进真 nsjail 跑评测。

与 `test_nsjail_acceptance.py` 的分工（两者互补，**不重复**）：

- `test_nsjail_acceptance.py`：自带一份最小 nsjail 策略、**不导入 `src/**`**，逐项实测"隔离与限制是否生效"
  （禁网/只读根/非 root/内存/CPU/pids/输出/墙钟/进程树），并只读校验 runner 生成的配置被真 nsjail 接受。
- **本文件**：导入**真实的** `src/autograder_b2/**`，用生产签名 `run_case(package, submission)` 在真 nsjail 里
  执行学生代码，断言"合法提交能被评测成通过的证据、超限样本被**归类**、超时后进程树无残留"，
  并在有数据库时跑**真实编排层**得到终态与分数、验证同绑定摘要重放同分同摘要。

因此本文件补上的正是此前登记的缺口：**`run_case` 的真实 Linux 执行路径无验收证据**。

判定口径：所有结论都来自**真实进程执行**（退出码/信号/stdout/`ps` 观测），拿不到证据一律 fail 并写明原因；
**不以配置存在冒充限制生效**，**不以断言冒充实测**。

运行（宿主侧一条命令，见 `verify/linux-nsjail/README.md`）：

    docker run --rm --privileged --cgroupns=host \
      --add-host=host.docker.internal:host-gateway \
      -e PYTHONPATH=/work/B2/src \
      -e B2_TEST_DATABASE_URL=postgresql+psycopg://autograder_platform:change-me-local-only@host.docker.internal:55433/<db> \
      -v "D:/VScodeProjects/AutoGrader:/work" -w /work/B2 \
      autograder-b2-nsjail:local python3 -m pytest tests/linux/test_nsjail_runner_acceptance.py -v -s

数据库那一条可选：未给 `B2_TEST_DATABASE_URL` 时，编排层用例 **skip 并写明原因**（不伪报通过）。
"""

from __future__ import annotations

import copy
import os
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
POLICY_DIGEST_INPUT = "b" * 64
QUESTION_ID = "11111111-1111-4111-8111-111111111111"
CASE_ID = "22222222-2222-4222-8222-222222222222"
DATABASE_URL = os.environ.get("B2_TEST_DATABASE_URL")

#: 学生程序（**真实执行**，不是断言出来的）：读一行、原样输出。
LEGAL_PROGRAM = "import sys\nprint(sys.stdin.readline().strip())\n"
#: 禁网样本：在沙箱内发起网络连接（必须失败）。
NETWORK_PROGRAM = (
    "import socket, sys\n"
    "try:\n"
    "    socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
    "    print('CONNECTED')\n"
    "except OSError as error:\n"
    "    print('BLOCKED', type(error).__name__)\n"
)
#: 超时样本：无限循环（墙钟必须杀掉它，且**整个进程树**都要清理）。
TIMEOUT_PROGRAM = "import sys\nsys.stdin.readline()\nwhile True:\n    pass\n"
#: fork bomb 样本：不断 fork（pids 上限必须挡住）。
FORKBOMB_PROGRAM = (
    "import os, sys, time\n"
    "sys.stdin.readline()\n"
    "children = []\n"
    "try:\n"
    "    for _ in range(200):\n"
    "        children.append(os.fork())\n"
    "except OSError as error:\n"
    "    print('FORK_BLOCKED', type(error).__name__)\n"
    "    sys.stdout.flush()\n"
    "for pid in children:\n"
    "    try:\n"
    "        os.waitpid(pid, 0)\n"
    "    except OSError:\n"
    "        pass\n"
)
#: 内存样本：申请远超上限的内存。
MEMORY_PROGRAM = (
    "import sys\n"
    "sys.stdin.readline()\n"
    "block = bytearray(900 * 1024 * 1024)\n"
    "print('ALLOCATED', len(block))\n"
)
#: 输出样本：打印远超输出上限的内容。
OUTPUT_PROGRAM = (
    "import sys\n"
    "sys.stdin.readline()\n"
    "for _ in range(200000):\n"
    "    print('OUTPUT_FLOOD_' + 'x' * 60)\n"
)


def _package_payload(*, program_stdin: str = "42\n", expected_stdout: str = "42\n", **overrides):
    """契约形状的 package JSON（与 `tests/test_runner_core.py` 同一形态；由**真实解析器**解析）。"""
    payload = {
        "question_version_id": QUESTION_ID,
        "package_digest": DIGEST,
        "language": "python",
        "adapter": {
            "kind": "stdin",
            "args": [],
            "stdin": None,
            "expect_exit_code": None,
            "comparison": {"stdout": "exact", "stderr": "ignored", "numeric_tolerance": None},
        },
        "tests": [
            {
                "case_id": CASE_ID,
                "name": "case one",
                "ordinal": 0,
                "weight": 1,
                "visible_to_student": True,
                "stdin": program_stdin,
                "args": [],
                "expected_stdout": expected_stdout,
                "expected_stderr": None,
                "expected_exit_code": None,
            }
        ],
        "security_policy": {
            "network": False,
            "read_only_root": True,
            "allow_root": False,
            "max_memory_bytes": 64 * 1024 * 1024,
            "max_cpu_seconds": 5,
            "max_wall_seconds": 5,
            "max_output_bytes": 65536,
            "max_processes": 16,
            "max_files_created": 8,
        },
        "max_files": 5,
        "max_total_bytes": 100000,
        "created_at": "2026-01-01T00:00:00Z",
    }
    merged = copy.deepcopy(payload)
    merged.update(overrides)
    return merged


def _run_program(program: str, *, workspace: Path, package_overrides: dict | None = None):
    """用**生产签名**跑一次真实评测：`run_case(package, submission)`（真 nsjail、真策略、真工作区）。"""
    from autograder_b2.domain.envelope import EnvelopeFile
    from autograder_b2.domain.evidence import SubmissionRunInput
    from autograder_b2.runner.package import parse_evaluation_package
    from autograder_b2.runner.run_case import run_case

    workspace.mkdir(parents=True, exist_ok=True)
    entry = workspace / "main.py"
    entry.write_text(program, encoding="utf-8")
    package = parse_evaluation_package(_package_payload(**(package_overrides or {})))
    submission = SubmissionRunInput(
        submission_id=str(uuid.uuid4()),
        evaluation_run_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        workspace=workspace,
        files=(
            EnvelopeFile(
                path="main.py",
                content_sha256=DIGEST,
                size_bytes=len(program.encode("utf-8")),
                storage_ref="main.py",
            ),
        ),
        case=package.tests[0],
        adapter=package.adapter,
        policy=package.security_policy,
    )
    return package, run_case(package, submission)


def test_i2_legal_submission_passes_inside_the_real_sandbox(tmp_path: Path) -> None:
    """① 合法提交：真实 nsjail 内执行学生程序 → 用例通过、退出码 0、真实 stdout 被比较。"""
    package, evidence = _run_program(LEGAL_PROGRAM, workspace=tmp_path / "legal")

    print(
        f"\n[I2] case={evidence.case_id} passed={evidence.passed} "
        f"failure_category={evidence.failure_category!r} exit_code={evidence.exit_code} "
        f"duration_ms={evidence.duration_ms} actual={evidence.actual_output_excerpt!r}"
    )
    assert evidence.case_id == CASE_ID
    assert evidence.passed is True, f"合法提交必须通过，实得 {evidence.failure_category!r} / {evidence.message!r}"
    assert evidence.failure_category is None
    assert evidence.exit_code == 0, "真实进程退出码必须是 0"
    assert evidence.duration_ms is not None and evidence.duration_ms >= 0
    assert evidence.actual_output_excerpt is not None
    assert "42" in evidence.actual_output_excerpt, "证据必须来自真实执行的 stdout"
    assert evidence.resource_limit_hit is None
    assert package.security_policy.max_wall_seconds == 5  # 策略确实来自 package


def test_i3_limit_and_hostile_samples_are_classified(tmp_path: Path) -> None:
    """② 禁网/超时/fork bomb/内存/输出超限样本：被拦截并**归类**到可辨的限制项或失败类别。"""
    observations: dict[str, tuple[object, object, object]] = {}

    # 禁网：连接必须失败，程序自己打印 BLOCKED（真实执行证据）
    _package, network = _run_program(NETWORK_PROGRAM, workspace=tmp_path / "network")
    observations["network"] = (network.passed, network.failure_category, network.actual_output_excerpt)

    # 超时：墙钟命中 → timeout + wall
    _package, timed = _run_program(TIMEOUT_PROGRAM, workspace=tmp_path / "timeout")
    observations["timeout"] = (timed.passed, timed.failure_category, timed.resource_limit_hit)

    # fork bomb：pids 上限挡住 → 资源限制（processes）或被信号杀掉
    _package, bomb = _run_program(FORKBOMB_PROGRAM, workspace=tmp_path / "forkbomb")
    observations["forkbomb"] = (bomb.passed, bomb.failure_category, bomb.resource_limit_hit)

    # 内存：rlimit_as 命中 → resource_limit + memory
    _package, memory = _run_program(MEMORY_PROGRAM, workspace=tmp_path / "memory")
    observations["memory"] = (memory.passed, memory.failure_category, memory.resource_limit_hit)

    # 输出：读取侧截断 → resource_limit + output
    _package, output = _run_program(OUTPUT_PROGRAM, workspace=tmp_path / "output")
    observations["output"] = (output.passed, output.failure_category, output.resource_limit_hit)

    for name, (passed, category, detail) in observations.items():
        print(f"[I3] {name}: passed={passed} failure_category={category!r} limit={detail!r}")

    assert all(item[0] is False for item in observations.values()), "五个样本都不得被判通过"
    # 禁网：真实执行里被**沙箱**拦下并归类。两种合法形态都要接受：
    #   (a) seccomp 默认拒绝 `socket` 系统调用 ⇒ security_blocked + 记录 `runner.seccomp` 违规（本容器实测）；
    #   (b) 若允许 socket 但网络命名空间隔离 ⇒ 程序自己观测到 OSError 并打印 BLOCKED。
    network_blocked = network.failure_category == "security_blocked" and any(
        finding.rule_id == "runner.seccomp" for finding in network.sandbox_violations
    )
    socket_denied_at_runtime = network.actual_output_excerpt is not None and "BLOCKED" in (
        network.actual_output_excerpt
    )
    assert network_blocked or socket_denied_at_runtime, (
        f"禁网样本必须被沙箱拦下：category={network.failure_category!r} "
        f"violations={network.sandbox_violations!r} output={network.actual_output_excerpt!r}"
    )
    print(
        f"[I3] network 拦截形态={'seccomp 默认拒绝' if network_blocked else '网络命名空间隔离'}；"
        f"exit_code={network.exit_code} violations={[f.rule_id for f in network.sandbox_violations]}"
    )
    assert timed.failure_category == "timeout" and timed.resource_limit_hit == "wall"
    assert output.failure_category == "resource_limit" and output.resource_limit_hit == "output"
    # 内存：`rlimit_as` 用尽时内核不写日志，nsjail 也没有诊断行，因此**归类**只能落在学生侧类别
    # （本容器实测 output_mismatch / exit=1）——归因面刻意不采信学生程序自己打印的 MemoryError
    # （否则被测方可把失败改写成"系统限制"）。这里断言的是**强制证据**：分配确实失败、进程随即
    # 非零退出、且输出与期望不符；限制本身的 jail 级证明见 test_nsjail_acceptance.py 的 05a 项。
    memory_evidence = (
        memory.passed is False
        and memory.exit_code not in (0, None)
        and memory.failure_category is not None
    )
    assert memory_evidence, (
        f"内存超限样本必须失败并留下证据：passed={memory.passed} exit={memory.exit_code} "
        f"category={memory.failure_category!r}"
    )
    print(
        f"[I3] memory 归类={memory.failure_category!r}/{memory.resource_limit_hit!r} "
        f"exit={memory.exit_code}（进程侧签名不计入归因，理由见用例 docstring）"
    )
    # fork bomb：本样本在程序内捕获了 fork 失败并打印，因此归类可能是"输出不符"（用例期望 42）
    # ——但**强制证据**必须在真实输出里：要么命中 processes 限制标记，要么程序自己观测到 EAGAIN。
    fork_evidence = (
        bomb.resource_limit_hit == "processes"
        or (bomb.actual_output_excerpt is not None and "FORK_BLOCKED" in bomb.actual_output_excerpt)
        or bomb.failure_category in {"resource_limit", "crash"}
    )
    assert fork_evidence, (
        f"fork bomb 必须被拦截并留下证据：category={bomb.failure_category!r} "
        f"limit={bomb.resource_limit_hit!r} output={bomb.actual_output_excerpt!r}"
    )
    print(
        f"[I3] forkbomb 拦截证据：category={bomb.failure_category!r} limit={bomb.resource_limit_hit!r} "
        f"output={(bomb.actual_output_excerpt or '')[:60]!r}"
    )


def test_i4_timeout_leaves_no_process_tree_residue(tmp_path: Path) -> None:
    """② 超时后进程检查无残留：真 nsjail 被杀后，容器内不得留下**存活**的 nsjail/学生进程。

    检查口径（按 `comm` 精确判定，不用子串匹配——本测试的**命令行自己就含 "nsjail"**，
    子串匹配会把自己当成残留）：
    - 不得存在 `comm=nsjail` 的**存活**进程；
    - 不得存在命令行里带本工作区路径的**存活**进程；
    - `<defunct>`（僵尸）单独看：僵尸是"已终止但未被父进程回收"，是否出现取决于容器里谁是 PID 1
      （无 init 时是 pytest，僵尸会留着；用 `--init`/tini 时会被回收）。因此本断言只针对存活进程，
      并在容器命令里用 `--init`，僵尸问题如实记录在验收命令与该断言说明里。
    """
    _package, evidence = _run_program(TIMEOUT_PROGRAM, workspace=tmp_path / "residue")
    assert evidence.failure_category == "timeout"

    listing = subprocess.run(
        ["ps", "-eo", "pid,ppid,comm,stat,args"], capture_output=True, text=True, check=False
    ).stdout
    live_nsjail: list[str] = []
    live_workspace: list[str] = []
    zombies: list[str] = []
    for line in listing.splitlines()[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        _pid, _ppid, comm, stat, args = parts
        if stat.startswith("Z"):
            zombies.append(line.strip())
            continue
        if comm == "nsjail":
            live_nsjail.append(line.strip())
        if str(tmp_path) in args:
            live_workspace.append(line.strip())
    print(
        f"\n[I4] timeout 后检查：存活 nsjail={live_nsjail} 存活且引用本工作区={live_workspace} "
        f"僵尸={zombies}"
    )
    assert live_nsjail == [], f"不得残留存活的 nsjail 进程：{live_nsjail}"
    assert live_workspace == [], f"不得残留引用本工作区的存活进程：{live_workspace}"
    # 沙箱自身也不得报告"进程树在宽限期后仍存活"
    assert not any(
        finding.category == "sandbox_violation" and "process tree" in (finding.message or "")
        for finding in evidence.sandbox_violations
    ), evidence.sandbox_violations


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="需要真实 PostgreSQL：设置 B2_TEST_DATABASE_URL（编排层用例不伪报通过）",
)
def test_i5_real_pipeline_reaches_completed_with_a_score(tmp_path: Path) -> None:
    """① 合法提交在**真实沙箱 + 真实编排层**下得到终态与分数（同绑定摘要重放同分同摘要）。

    装配说明（**哪些是真的**，如实标注）：
    - **真**：`EvaluationOrchestrator` + `RunnerCaseRunner`（→ 真实 `run_case` + 真实 nsjail）、
      `StaticSecurityDecider`（真实静态安全判定）、真实 PostgreSQL（真实 `b2` schema）。
    - **替身（仅本验收用）**：package 来源直接返回真实解析出的 `EvaluationPackage` 句柄（B3 HTTP 取包
      由 `tests/test_integration_adapters.py` 用真实本地 HTTP 服务验收）；工作区提供者把文件写进临时目录
      （真实 CAS 物化路径由 `tests/test_integration_adapters.py` T-INT-008/009 验收）。
      这两处替身都**不涉及沙箱是否真的隔离**——那正是本文件要证的东西。
    """
    from contextlib import contextmanager
    from datetime import UTC, datetime

    from sqlalchemy import create_engine

    from autograder_b2.db.schema import evaluation_runs, submissions
    from autograder_b2.domain.envelope import ApiProvenance, EnvelopeFile, SubmissionEnvelope
    from autograder_b2.evaluation.orchestrator import DynamicExecutionGate, EvaluationOrchestrator
    from autograder_b2.evaluation.ports import CaseDescriptor, ComparisonPolicy, PackageView
    from autograder_b2.integration.adapters import RunnerCaseRunner, StaticSecurityDecider
    from autograder_b2.runner.package import parse_evaluation_package
    from autograder_b2.runner.policy import policy_digest

    moment = datetime(2026, 9, 16, 9, 0, 0, tzinfo=UTC)
    program = LEGAL_PROGRAM
    digest = __import__("hashlib").sha256(program.encode("utf-8")).hexdigest()

    class _FixedPackageSource:
        """按题目版本返回真实解析出的包（句柄即真实对象）。"""

        def __init__(self, package, view) -> None:
            self.package = package
            self.view = view

        def fetch(self, *, evaluation_run_id: str, question_version_id: str) -> PackageView:
            return self.view

    class _LocalWorkspaces:
        """把提交文件写进每个用例独立的工作区（替身；真实 CAS 物化另有集成验收）。"""

        def __init__(self, root: Path, contents: dict[str, bytes]) -> None:
            self._root = root
            self._contents = contents

        @property
        def root(self) -> Path:
            return self._root

        def prepare(self, *, submission_id, evaluation_run_id, files, case_id=None):
            target = self._root / f"ws-{case_id or 'run'}"
            target.mkdir(parents=True, exist_ok=True)
            for name, payload in self._contents.items():
                (target / name).write_bytes(payload)
            return target

        @contextmanager
        def hold(self):  # pragma: no cover - 编排不调用，占位以匹配协议
            yield

        def release(self, workspace: Path) -> None:
            import shutil

            shutil.rmtree(workspace, ignore_errors=True)

    engine = create_engine(DATABASE_URL)
    outcomes = []
    try:
        # **同一次提交重放两次**（这才是"同绑定摘要 ⇒ 同摘要"的正确实验形态）：
        # 报告摘要的投影含提交标识等字段，两次不同的提交本就应当得到不同摘要；
        # 只有"同一提交 + 同包/同策略/同环境/同题目版本绑定"才要求摘要相同。
        package = parse_evaluation_package(_package_payload())
        view = PackageView(
            package_digest=package.package_digest,
            language=package.language,
            cases=tuple(
                CaseDescriptor(
                    case_id=case.case_id,
                    ordinal=case.ordinal,
                    weight=case.weight,
                    visible_to_student=case.visible_to_student,
                    handle=case,
                )
                for case in package.tests
            ),
            runner_policy_digest=policy_digest(package.security_policy),
            environment_digest="c" * 64,
            comparison=ComparisonPolicy(
                stdout=package.adapter.comparison.stdout,
                stderr=package.adapter.comparison.stderr,
                numeric_tolerance=package.adapter.comparison.numeric_tolerance,
            ),
            question_version_binding="d" * 64,
            interpreter_version="CPython 3.12",
            handle=package,
            adapter_handle=package.adapter,
            policy_handle=package.security_policy,
        )
        contents = {"main.py": program.encode("utf-8")}
        root = tmp_path / "pipeline"
        root.mkdir(parents=True, exist_ok=True)
        workspaces = _LocalWorkspaces(root / "workspaces", contents)

        student_id = uuid.uuid4()
        course_id = uuid.uuid4()
        section_id = uuid.uuid4()
        question_version_id = uuid.UUID(QUESTION_ID)
        submission_id = uuid.uuid4()
        correlation_id = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                submissions.insert().values(
                    submission_id=submission_id,
                    student_id=student_id,
                    course_id=course_id,
                    section_id=section_id,
                    question_version_id=question_version_id,
                    intake="api",
                    idempotency_key=f"linux-{submission_id}",
                    idempotency_scope=str(correlation_id),
                    submission_digest=digest,
                    state="validated",
                    created_at=moment,
                    updated_at=moment,
                    correlation_id=correlation_id,
                    request_id=str(correlation_id),
                )
            )
        envelope = SubmissionEnvelope(
            submission_id=str(submission_id),
            student_id=str(student_id),
            course_id=str(course_id),
            section_id=str(section_id),
            question_version_id=str(question_version_id),
            idempotency_key=f"linux-{submission_id}",
            intake="api",
            provenance=ApiProvenance(request_id=str(correlation_id)),
            correlation_id=str(correlation_id),
            files=(
                EnvelopeFile(
                    path="main.py",
                    content_sha256=digest,
                    size_bytes=len(program.encode("utf-8")),
                    storage_ref="main.py",
                ),
            ),
            submission_digest=digest,
            received_at=moment,
        )
        for attempt in range(2):
            orchestrator = EvaluationOrchestrator(
                engine=engine,
                package_source=_FixedPackageSource(package, view),
                workspace_provider=workspaces,
                security=StaticSecurityDecider(),
                runner=RunnerCaseRunner(),
                gate=DynamicExecutionGate(),
                now=lambda: moment,
            )
            outcome = orchestrator.run(envelope)
            outcomes.append(outcome)
            with engine.connect() as connection:
                row = connection.execute(
                    evaluation_runs.select().where(
                        evaluation_runs.c.evaluation_run_id == uuid.UUID(outcome.evaluation_run_id)
                    )
                ).one()
            print(
                f"\n[I5#{attempt}] run={outcome.evaluation_run_id} state={outcome.state} "
                f"score={outcome.score} digest={outcome.result_digest} db_state={row.state} "
                f"message={outcome.message!r}"
            )
    finally:
        engine.dispose()

    first, second = outcomes
    assert first.evaluation_run_id != second.evaluation_run_id, "两次必须是两个不同的运行实例"
    assert first.state == "completed", first.message
    assert first.score is not None and float(first.score) > 0, "合法提交必须拿到正分"
    assert first.result_digest, "必须产出结果摘要"
    assert second.state == "completed"
    assert (second.score, second.result_digest) == (first.score, first.result_digest), (
        "同一提交同绑定摘要重放必须得到相同分数与相同 result_digest"
    )


def test_i8_student_cannot_forge_a_sandbox_limit_attribution(tmp_path: Path) -> None:
    """红队证伪回归：学生**打印沙箱字样**不得改变归因，且**写不进**沙箱自有日志（真 nsjail 内验）。

    背景：修复前 `_scan_markers` 的过滤只是 `"nsjail" in line`，而学生的 stdout/stderr 与 nsjail 自有日志
    写进同一份文件；红队曾用一行 `nsjail: out of memory` 让 `case_evidence.failure_category` 落成
    `resource_limit/memory`。现在沙箱侧归因只读 `nsjail --log` 那份宿主自有日志（`RunFacts.sandbox_log_text`），
    本用例在容器内真跑同时钉住三件事：

    1. 学生打印 `nsjail: out of memory` / `nsjail: operation not permitted` 后，归类仍是学生侧的
       `output_mismatch`，`resource_limit_hit` 为空，且没有 `runner.resource_limit` 违规；
    2. 学生**尝试往日志文件里追加**同样的字样会失败（该路径在工作区之外，沙箱内被遮蔽为只读空 tmpfs）——
       即伪造路径不是"没被触发"，而是**根本不可达**；
    3. 该日志是宿主自有的每次运行产物，运行结束后不留盘。

    **本用例断言的强弱要说清（红队第 2 轮指出）**：`FORGED_LOG_WRITE=DENIED` 只证明"写入以 `OSError`
    结束"，**不能**区分 `EACCES`/`EROFS`（撞沙箱只读边界）与 `ENOENT`（该路径在沙箱内不可见）。
    边界结论不靠这一条单独支撑，而由三条**独立**证据共同支撑（红队第 2 轮实测）：
    ①最坏假设下（学生已知宿主日志绝对路径）`append -> errno=30 Read-only file system`——撞的是只读层，
    不是"父目录不存在"；②对照组：把工作区权限放开后写工作区得到 `WRITE_OK`（证明 `DENIED` 来自沙箱边界
    而不是"这个环境下什么都不能写"）；③fd 维度：沙箱内只见 fd 0/1/2，写满 24 个 fd 后日志快照仍 0 字节且
    不含学生标记（没有继承来的日志 fd，生产 argv 里也没有 `--pass_fd`/`--log_fd`）。
    """
    log_path = tmp_path / "forgery.nsjail.log"  # run_dir = workspace.parent，stem = 工作区目录名
    forgery = (
        "import sys\n"
        "sys.stdin.readline()\n"
        "print('nsjail: out of memory', file=sys.stderr)\n"
        "print('nsjail: operation not permitted', file=sys.stderr)\n"
        "try:\n"
        f"    with open({str(log_path)!r}, 'a', encoding='utf-8') as handle:\n"
        "        handle.write('nsjail: out of memory\\n')\n"
        "    print('FORGED_LOG_WRITE=OK')\n"
        "except OSError as error:\n"
        "    print(f'FORGED_LOG_WRITE=DENIED errno={error.errno}')\n"
        "sys.exit(1)\n"
    )
    _package, evidence = _run_program(forgery, workspace=tmp_path / "forgery")
    print(
        f"\n[I8] 伪造样张：passed={evidence.passed} category={evidence.failure_category!r} "
        f"limit={evidence.resource_limit_hit!r} exit={evidence.exit_code} "
        f"output={evidence.actual_output_excerpt!r}"
    )
    assert evidence.passed is False
    assert evidence.failure_category == "output_mismatch", (
        f"学生打印的沙箱字样不得改变归因，实得 {evidence.failure_category!r}"
    )
    assert evidence.resource_limit_hit is None
    assert not any(f.rule_id == "runner.resource_limit" for f in evidence.sandbox_violations)
    assert "FORGED_LOG_WRITE=DENIED" in (evidence.actual_output_excerpt or ""), (
        "沙箱内必须写不进沙箱自有日志（工作区之外、只读空 tmpfs）"
    )
    assert not any(f.rule_id == "runner.sandbox_log" for f in evidence.sandbox_violations)
    assert not log_path.exists(), "沙箱日志是每次运行的宿主自有产物，读后即删"


def test_i7_runner_refuses_to_run_without_a_sandbox(tmp_path: Path) -> None:
    """反面对照：策略不可用时**拒绝执行**，绝不裸进程跑学生代码（本容器内逐点实测）。"""
    from autograder_b2.domain.envelope import EnvelopeFile
    from autograder_b2.domain.evidence import SandboxUnavailableError, SubmissionRunInput
    from autograder_b2.runner.package import parse_evaluation_package
    from autograder_b2.runner.policy import probe_sandbox
    from autograder_b2.runner.run_case import run_case

    package = parse_evaluation_package(_package_payload())
    availability = probe_sandbox(package.security_policy)
    print(f"\n[I7] 真实探测：available={availability.available} reason={availability.reason!r}")
    assert probe_sandbox(package.security_policy).available == availability.available, "探测必须是纯函数"

    workspace = tmp_path / "refuse"
    workspace.mkdir(parents=True, exist_ok=True)
    program = LEGAL_PROGRAM
    (workspace / "main.py").write_text(program, encoding="utf-8")
    submission = SubmissionRunInput(
        submission_id=str(uuid.uuid4()),
        evaluation_run_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        workspace=workspace,
        files=(
            EnvelopeFile(
                path="main.py",
                content_sha256="e" * 64,
                size_bytes=len(program.encode("utf-8")),
                storage_ref="main.py",
            ),
        ),
        case=package.tests[0],
        adapter=package.adapter,
        policy=package.security_policy,
    )
    if availability.available:
        pytest.skip(
            "本容器内沙箱可用 ⇒ 走真实执行路径（拒绝路径的正面证据见 tests/test_integration_adapters.py "
            "T-INT-007 与 Windows 上的真实 run_case 调用）"
        )
    with pytest.raises(SandboxUnavailableError):
        run_case(package, submission)
    assert list(workspace.iterdir()) != []  # 工作区内容仍在，但从未被执行
    print("[I7] 沙箱不可用 ⇒ 抛 SandboxUnavailableError（未执行学生代码）✓")
