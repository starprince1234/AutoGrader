"""沙箱自证（canary）：用一组**必须失败**的作业证明隔离真的生效（`ADR-0010` 决策 2）。

## 为什么需要一个这样的入口

`ADR-0010` 的结论 (B) 是："真正缺的不是更强的机制，而是'隔离生效'这件事此前无法被验证"。
因此本模块提供一个可被运维与验收调用的自证入口：跑一组金丝雀作业——尝试读宿主文件、尝试联网、
尝试提权、尝试写只读根、尝试超内存/超进程数——逐一核对"应当失败的确实失败"，并输出**结构化逐项结论**。

## 三态语义（与安全结论同一套，不允许把"没跑"说成"安全"）

| 状态 | 含义 |
|---|---|
| `found` | **发现了风险**：该金丝雀作业成功做到了它不该做到的事（隔离没生效） |
| `not_found` | **未发现风险**：该作业被沙箱挡住（按预期失败） |
| `not_checked` | **未检查**：本项没有跑起来（例如沙箱不可用、或结果无法归因） |

**Windows 上整表都是 `not_checked`**，这正是三态存在的意义：本机没有 nsjail，
"没跑"绝不能显示成"安全"。退出码同样区分：`0` = 每一项都 `not_found`；
`2` = 有 `found`；`3` = 有 `not_checked` 而没有 `found`——CI/验收把非零当失败，因此
"没检查"不可能被误当成通过。

## 复用哪条执行路径

金丝雀**不另起一套启动逻辑**：它构造一个 `ExecutionPlan` 并调用 `runner.run_case` 内部的
`_execute_case`（同一份 nsjail 配置渲染、同一份 `build_argv`、同一套进程树清理与有界输出采集）。
换句话说，金丝雀证明的是**生产执行路径**，而不是一个"看起来很像"的复刻版。

自证是**只读探测 + 一次性作业**，不写任何业务表；它不构成"绕过评测闸门"的入口（不是 HTTP 端点，
只能由运维在宿主机上执行）。

## 它证明不了什么（必须写清楚）

金丝雀证明的是"当前内核 + 当前 nsjail 配置下，这些特定的逃逸尝试失败了"。它**不是**形式化保证：
内核漏洞、配置漂移、容器运行时差异都不在证明范围内（`ADR-0010` 遗留风险节）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from autograder_b2.runner.adapters import ExecutionPlan, snapshot_workspace
from autograder_b2.runner.policy import (
    RunnerPolicy,
    SandboxAvailability,
    policy_digest,
    probe_sandbox,
    sandbox_script_path,
)
from autograder_b2.runner.run_case import (
    PosixNsjailLauncher,
    ProcessLauncher,
    RunFacts,
    _execute_case,
)

#: 金丝雀程序**成功逃逸**时打印的标记（宿主据此判定 `found`）。
ESCAPED_MARKER = "B2_CANARY_ESCAPED"

#: 金丝雀程序**按预期被挡住**时打印的标记（宿主据此判定 `not_found`）。
BLOCKED_MARKER = "B2_CANARY_BLOCKED"

#: 判为"沙箱/内核主动终止了它"的信号编号（Linux 编号）：SIGKILL / SIGXCPU / SIGXFSZ / SIGSYS。
#: 这四种是"隔离生效"的合法形态；其它信号（例如 SIGSEGV）说明结果**无法归因**，记 `not_checked`。
CONTAINMENT_SIGNALS = (9, 24, 25, 31)

#: 退出码：`0` = 全部挡住；`2` = 发现风险；`3` = 有未检查项。
EXIT_OK = 0
EXIT_FOUND_RISK = 2
EXIT_NOT_CHECKED = 3

#: 金丝雀作业的墙钟上限（秒）：自证不该跑很久；单项最多这么久。
CANARY_WALL_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class CanaryProbe:
    """一项金丝雀作业：程序源码 + 它必须在沙箱内失败的理由。"""

    probe_id: str
    description: str
    expectation: str
    source: str


@dataclass(frozen=True, slots=True)
class CanaryItem:
    """一项金丝雀作业的结论（三态之一 + 可读细节）。"""

    probe_id: str
    description: str
    expectation: str
    status: str
    detail: str

    def as_json(self) -> dict[str, str]:
        return {
            "probe_id": self.probe_id,
            "description": self.description,
            "expectation": self.expectation,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CanaryReport:
    """一次自证的完整结论（逐项 + 汇总 + 策略摘要）。"""

    sandbox: SandboxAvailability
    items: tuple[CanaryItem, ...]

    @property
    def counts(self) -> dict[str, int]:
        summary = {"found": 0, "not_found": 0, "not_checked": 0}
        for item in self.items:
            summary[item.status] = summary.get(item.status, 0) + 1
        return summary

    @property
    def verdict(self) -> str:
        counts = self.counts
        if counts["found"]:
            return "found_risk"
        if counts["not_checked"]:
            return "not_checked"
        return "not_found"

    @property
    def exit_code(self) -> int:
        counts = self.counts
        if counts["found"]:
            return EXIT_FOUND_RISK
        if counts["not_checked"]:
            return EXIT_NOT_CHECKED
        return EXIT_OK

    def as_json(self, policy: RunnerPolicy) -> dict[str, object]:
        """结构化结论。

        `statement` 是给验收者读的一句话，它**永远**不会在存在未检查项时说"安全"。
        """
        counts = self.counts
        if counts["found"]:
            statement = (
                f"发现 {counts['found']} 项隔离失效：沙箱没有挡住应当失败的作业，"
                "在处置之前不应认为本宿主可安全评测。"
            )
        elif counts["not_checked"]:
            statement = (
                f"{counts['not_checked']} 项未检查（沙箱不可用或结果无法归因），"
                f"{counts['not_found']} 项已确认被挡住。"
                "「未检查」不等于「安全」：本宿主上的隔离**未被证明**。"
            )
        else:
            statement = (
                f"全部 {counts['not_found']} 项金丝雀作业都被挡住。"
                "这证明的是「当前内核 + 当前 nsjail 配置下这些特定逃逸尝试失败」，"
                "不是形式化保证。"
            )
        return {
            "policy_digest": policy_digest(policy),
            "sandbox": {
                "available": self.sandbox.available,
                "reason": self.sandbox.reason,
                "checks": [
                    {"name": check.name, "ok": check.ok, "detail": check.detail}
                    for check in self.sandbox.checks
                ],
            },
            "items": [item.as_json() for item in self.items],
            "summary": {**counts, "verdict": self.verdict},
            "statement": statement,
        }


def _probe_source(body: str) -> str:
    """把一段探测逻辑包成完整的金丝雀程序（自带两个标记的定义与异常归一）。"""
    return (
        '"""B2 沙箱自证金丝雀（由 autograder_b2.canary 生成；不属于任何学生样本）。"""\n'
        "import os\n"
        "import socket\n"
        "import sys\n"
        "\n"
        f'ESCAPED = "{ESCAPED_MARKER}"\n'
        f'BLOCKED = "{BLOCKED_MARKER}"\n'
        "\n"
        "\n"
        "def main() -> int:\n"
        "    try:\n"
        + "\n".join(f"        {line}" if line else "" for line in body.strip("\n").split("\n"))
        + "\n"
        "    except BaseException as error:  # 任何异常都算「被挡住」，但要如实说出是哪一类\n"
        '        print(BLOCKED + ": " + type(error).__name__)\n'
        "        return 0\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    sys.exit(main())\n"
    )


def _canary_probes(policy: RunnerPolicy) -> tuple[CanaryProbe, ...]:
    """金丝雀清单（每一项的路径/数值都取自策略，因此与真实执行配置同源）。"""
    host_file = f"{policy.workspace_mount}/../b2-canary-host-file"
    root_write = "/b2-canary-root-write"
    memory_target = policy.max_memory_bytes * 2
    pids_target = policy.max_processes * 3
    return (
        CanaryProbe(
            probe_id="host_file_read",
            description="读取工作区之外的宿主文件（含运行目录）",
            expectation="读取必须失败：只读根里这些路径被遮蔽为空的只读 tmpfs",
            source=_probe_source(
                f"""
try:
    with open({host_file!r}, "rb") as handle:
        payload = handle.read(64)
except OSError as error:
    print(BLOCKED + ": read denied (" + error.__class__.__name__ + ")")
    return 0
print(ESCAPED + ":host_file_read:" + repr(payload))
return 0
"""
            ),
        ),
        CanaryProbe(
            probe_id="network",
            description="建立 TCP 连接与 DNS 解析",
            expectation="必须失败：独立网络命名空间内没有任何网络接口（含 lo 未启用）",
            source=_probe_source(
                """
try:
    connection = socket.create_connection(("1.1.1.1", 53), timeout=3)
except OSError as error:
    print(BLOCKED + ": connect denied (" + error.__class__.__name__ + ")")
else:
    connection.close()
    print(ESCAPED + ":network:tcp")
    return 0
try:
    socket.getaddrinfo("example.invalid", 80)
except OSError as error:
    print(BLOCKED + ": dns denied (" + error.__class__.__name__ + ")")
    return 0
print(ESCAPED + ":network:dns")
return 0
"""
            ),
        ),
        CanaryProbe(
            probe_id="privilege_escalation",
            description="尝试成为 root（读 euid 并尝试 setuid(0)）",
            expectation="必须失败：被囚进程不是 root，且不具备提权所需能力",
            source=_probe_source(
                """
if os.geteuid() == 0:
    print(ESCAPED + ":privilege_escalation:euid-0")
    return 0
try:
    os.setuid(0)
except OSError as error:
    print(BLOCKED + ": setuid denied (" + error.__class__.__name__ + ")")
    return 0
print(ESCAPED + ":privilege_escalation:setuid-succeeded")
return 0
"""
            ),
        ),
        CanaryProbe(
            probe_id="read_only_root",
            description="向根文件系统写入文件（只读根）",
            expectation="必须失败：根以只读方式绑定，工作区是唯一可写路径",
            source=_probe_source(
                f"""
try:
    with open({root_write!r}, "wb") as handle:
        handle.write(b"b2-canary")
except OSError as error:
    print(BLOCKED + ": write denied (" + error.__class__.__name__ + ")")
    return 0
print(ESCAPED + ":read_only_root")
return 0
"""
            ),
        ),
        CanaryProbe(
            probe_id="memory_limit",
            description=f"申请超过上限的内存（策略上限 {policy.max_memory_bytes} 字节，尝试申请 2 倍）",
            expectation="必须失败：内存上限由 rlimit_as + cgroup v2 强制（表现为分配失败或被杀死）",
            source=_probe_source(
                f"""
chunks = []
target = {memory_target}
try:
    while sum(len(item) for item in chunks) < target:
        chunks.append(bytearray(1024 * 1024))
except MemoryError as error:
    print(BLOCKED + ": MemoryError (" + error.__class__.__name__ + ")")
    return 0
print(ESCAPED + ":memory_limit:" + str(sum(len(item) for item in chunks)))
return 0
"""
            ),
        ),
        CanaryProbe(
            probe_id="pids_limit",
            description=f"创建超过上限的进程（策略上限 {policy.max_processes}，尝试创建 3 倍）",
            expectation="必须失败：进程数上限由 rlimit_nproc + cgroup v2 强制",
            source=_probe_source(
                f"""
children = []
target = {pids_target}
try:
    while len(children) < target:
        pid = os.fork()
        if pid == 0:
            import time
            time.sleep(5)
            os._exit(0)
        children.append(pid)
except OSError as error:
    print(BLOCKED + ": fork denied (" + error.__class__.__name__ + ")")
    return 0
print(ESCAPED + ":pids_limit:" + str(len(children)))
return 0
"""
            ),
        ),
    )


def _judge(probe: CanaryProbe, facts: RunFacts) -> CanaryItem:
    """把一次执行的事实归到三态之一（**归因不确定就记未检查**，绝不四舍五入成"安全"）。"""
    stdout = facts.stdout_text
    if ESCAPED_MARKER in stdout:
        return CanaryItem(
            probe.probe_id,
            probe.description,
            probe.expectation,
            "found",
            "金丝雀作业做到了它不该做到的事：" + _marker_tail(stdout, ESCAPED_MARKER),
        )
    if BLOCKED_MARKER in stdout:
        return CanaryItem(
            probe.probe_id,
            probe.description,
            probe.expectation,
            "not_found",
            "被挡住：" + _marker_tail(stdout, BLOCKED_MARKER),
        )
    signal_number = _killed_by_signal(facts.exit_code)
    if signal_number in CONTAINMENT_SIGNALS:
        return CanaryItem(
            probe.probe_id,
            probe.description,
            probe.expectation,
            "not_found",
            f"进程被沙箱/内核以信号 {signal_number} 终止（隔离生效）",
        )
    if facts.timed_out:
        return CanaryItem(
            probe.probe_id,
            probe.description,
            probe.expectation,
            "not_found",
            "进程达到墙钟上限后被清理（未完成逃逸尝试）",
        )
    return CanaryItem(
        probe.probe_id,
        probe.description,
        probe.expectation,
        "not_checked",
        "结果无法归因：既没有逃逸标记也没有阻断标记"
        f"（exit_code={facts.exit_code}, timed_out={facts.timed_out}）",
    )


def _marker_tail(stdout: str, marker: str) -> str:
    for line in stdout.splitlines():
        if marker in line:
            return line.strip()[:200]
    return marker  # pragma: no cover - 调用方已确认标记存在


def _killed_by_signal(exit_code: int | None) -> int | None:
    """退出码 → 等价信号编号（负值 / 128+sig / 256-sig 三种约定都认）。"""
    if exit_code is None:
        return None
    for signal_number in CONTAINMENT_SIGNALS:
        if exit_code in (-signal_number, 128 + signal_number, 256 - signal_number):
            return signal_number
    return None


def run_canary(
    policy: RunnerPolicy | None = None,
    *,
    launcher: ProcessLauncher | None = None,
    work_root: Path | None = None,
) -> CanaryReport:
    """执行全部金丝雀作业并返回逐项结论。

    **沙箱不可用 ⇒ 全部 `not_checked` 且不创建任何进程**：本函数在探测失败时直接返回，
    既不写程序、也不调用启动器（`ADR-0004`/SEC-20 禁止任何形式的降级执行）。

    `launcher` 与 `work_root` 是**仅测试可见**的接缝（默认即生产实现与系统临时目录）。
    """
    active_policy = policy if policy is not None else RunnerPolicy()
    availability = probe_sandbox(active_policy)
    probes = _canary_probes(active_policy)
    if not availability.available:
        reason = availability.reason or "沙箱不可用"
        return CanaryReport(
            sandbox=availability,
            items=tuple(
                CanaryItem(
                    probe.probe_id,
                    probe.description,
                    probe.expectation,
                    "not_checked",
                    f"沙箱不可用，本项未执行：{reason}",
                )
                for probe in probes
            ),
        )
    active_launcher = launcher if launcher is not None else PosixNsjailLauncher()
    root = Path(work_root) if work_root is not None else Path(tempfile.mkdtemp(prefix="b2-canary-"))
    root.mkdir(parents=True, exist_ok=True)
    items: list[CanaryItem] = []
    try:
        for index, probe in enumerate(probes):
            items.append(_run_probe(active_policy, probe, index, root, active_launcher))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return CanaryReport(sandbox=availability, items=tuple(items))


def _run_probe(
    policy: RunnerPolicy,
    probe: CanaryProbe,
    index: int,
    root: Path,
    launcher: ProcessLauncher,
) -> CanaryItem:
    """跑一项金丝雀作业（复用生产执行路径：同一份配置渲染、同一份 argv、同一套清理）。"""
    workspace = root / f"canary-{index:02d}-{probe.probe_id}"
    workspace.mkdir(parents=True, exist_ok=True)
    entry = workspace / "main.py"
    entry.write_text(probe.source, encoding="utf-8")
    # 运行目录里放一份"宿主文件"：金丝雀会尝试读它，而该目录在沙箱内被遮蔽（ADR-0004 的遮蔽挂载）。
    (workspace.parent / "b2-canary-host-file").write_text("b2-canary-host-file", encoding="utf-8")
    wall_policy = policy
    if policy.max_wall_seconds > CANARY_WALL_SECONDS:
        wall_policy = replace(policy, max_wall_seconds=CANARY_WALL_SECONDS)
    plan = ExecutionPlan(
        kind="stdin",
        language="python",
        interpreter=wall_policy.interpreter_for("python"),
        script=sandbox_script_path(wall_policy, "main.py"),
        entry_name="main.py",
        arguments=(),
        stdin_text=None,
        expected_stdout=None,
        expected_stderr=None,
        expected_exit_code=None,
        stdout_strategy="ignored",
        stderr_strategy="ignored",
        numeric_tolerance=None,
        input_fixtures=(),
        expected_outputs=(),
        harness_name=None,
    )
    try:
        facts = _execute_case(
            plan=plan,
            policy=wall_policy,
            workspace=workspace,
            launcher=launcher,
            before=snapshot_workspace(workspace),
        )
    except Exception as error:  # noqa: BLE001 - 单项作业失败不得让整次自证中断（记"未检查"）
        return CanaryItem(
            probe.probe_id,
            probe.description,
            probe.expectation,
            "not_checked",
            f"本项未能执行：{type(error).__name__}",
        )
    return _judge(probe, facts)


def main(argv: Sequence[str] | None = None) -> int:
    """运维入口：`python -m autograder_b2.canary`（打印 JSON 结论，退出码见模块 docstring）。"""
    parser = argparse.ArgumentParser(
        prog="python -m autograder_b2.canary",
        description="B2 沙箱自证：跑一组必须失败的作业，逐项报告 发现风险/未发现风险/未检查。",
    )
    parser.parse_args(argv)
    policy = RunnerPolicy()
    report = run_canary(policy)
    print(json.dumps(report.as_json(policy), ensure_ascii=False, indent=2, sort_keys=True))
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main(sys.argv[1:]))


__all__ = [
    "BLOCKED_MARKER",
    "CANARY_WALL_SECONDS",
    "CONTAINMENT_SIGNALS",
    "ESCAPED_MARKER",
    "EXIT_FOUND_RISK",
    "EXIT_NOT_CHECKED",
    "EXIT_OK",
    "CanaryItem",
    "CanaryProbe",
    "CanaryReport",
    "main",
    "run_canary",
]
