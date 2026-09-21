"""申诉证据包（`CHG-0014` 的 `getAppealPack`）：自包含、可移动、**两处强制脱敏**。

契约权威：`MAIN/contracts/openapi/b2.yaml` 的 `getAppealPack` 与 `AppealPack`。

为什么它必须是专门文档、而不是"报告端点加个下载头"：证据包会**离开这台机器**，一个没有登录本部署的
复核者要能**仅凭这份文件**判断判分是否成立（`CHG-0014` §1.2）。因此包里嵌的是报告本身、五项绑定摘要、
runner 策略摘要与可读的重现说明，并由 `result_digest` 钉住。

## 两处强制脱敏，都是**主动做**的而不是"相信没有"

1. **宿主文件系统绝对路径被移除**。判据分两层，两层都替换成 `"[host path removed]"`：
   - **部署配置里的宿主根**（工件根、工作区根）——出现即替换（大小写敏感，逐字匹配）；
   - **绝对路径形态的子串**：Windows 盘符路径（`D:\\…`）或 POSIX 根路径（`/a/b/…`，至少两段），
     **但沙箱内部挂载点下的路径除外**（`/workspace/main.py` 是沙箱内路径，不是宿主路径）。
   契约要求"至少一项" `redactions` 条目来**列出**被移除的东西与原因：静默省略与"本来没东西可省"
   无法区分，而读者被要求信任这份文件。
2. **导出永不扩大学生视图**。`view=student` 时报告用 `evaluation.report.for_student`（与报告端点
   **同一个**裁剪函数），因此隐藏用例的期望/实际输出摘录与 `expected_sha256` 都不会进包；学生索要
   `view=teacher` 一律 403，**不静默降级**（与报告端点同一纪律）。

包里**没有**源码：报告本身不含源码（`case_evidence` 只有输出摘录与摘要），生成证据包也不读任何
源码/CAS 对象——`build_appeal_pack` 的输入只有一个已持久化的报告映射、一个摘要串与两个宿主根。

## 组装后的自检

组装完成前调用 `evaluation.report.report_schema_errors` 复验包内报告仍过 MAIN 的报告 schema
（脱敏不能把报告弄成不合契约的形状），并逐项检查必填字段、`redactions` 非空、`result_digest` 形态。
自检失败即抛异常（**不**返回一份"看起来像证据包"的东西）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.domain.clock import is_digest
from autograder_b2.evaluation.report import (
    ReportContractError,
    for_student,
    for_teacher,
    report_schema_errors,
)

#: 契约 `getAppealPack.view` 的取值域（默认 `teacher`，与报告端点的默认 `student` 不同）。
APPEAL_VIEWS = ("student", "teacher")

#: 被移除内容的占位串（出现在包内任何字符串里，表示"这里原本有宿主路径"）。
HOST_PATH_PLACEHOLDER = "[host path removed]"
#: 契约 `AppealPack.redactions[].maxLength` 与 `replay.maxLength`。
REDACTION_MAX_LENGTH = 200
REPLAY_MAX_LENGTH = 2000

#: 沙箱**内部**的挂载点：它下面的路径不是宿主路径，因此不受绝对路径规则影响。
#:
#: 与 `runner.policy.DEFAULT_WORKSPACE_MOUNT` 同值——这里**不 import** runner（编排层不依赖执行层实现，
#: 否则端口就不再是唯一的组合缝）；一致性由
#: `tests/test_b2_routes.py::test_appeal_pack_keeps_sandbox_internal_paths_and_the_policy_constants_honest`
#: 对着 runner 的策略对象断言。
SANDBOX_INTERNAL_ROOTS: tuple[str, ...] = ("/workspace",)

#: Windows 盘符绝对路径（本项目开发机形态）。
_WINDOWS_ABSOLUTE = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]*")
#: POSIX 绝对路径：至少要两段（`/srv/autograder` 是宿主路径，`/tmp` 这种单段不匹配）。
_POSIX_ABSOLUTE = re.compile(r"/(?:[A-Za-z0-9_.+@%-]+/)+[A-Za-z0-9_.+@%-]*")
#: 会被从句尾剥离的标点（替换路径时不能把句子的逗号/括号一起吃掉）。
_TRAILING_PUNCTUATION = ".,;:)"


@dataclass(frozen=True, slots=True)
class AppealPackInputs:
    """组装一个证据包所需的全部输入（**不含**源码、不含存储句柄、不含任何可读内容的访问器）。"""

    evaluation_run_id: str
    #: 已持久化报告的映射（`evaluation_results.report`）；本模块只读、不修改。
    report: Mapping[str, object]
    host_roots: tuple[str, ...]
    view: str = "teacher"
    generated_at: datetime | None = None


def redact_host_paths(value: Any, host_roots: Sequence[str]) -> Any:
    """递归脱敏：把宿主根与绝对路径形态的子串替换成 `HOST_PATH_PLACEHOLDER`。

    返回**新对象**（不修改入参）：已持久化的报告是共享对象，就地改写会污染后续读取。
    """
    roots = tuple(root for root in host_roots if root)
    if isinstance(value, str):
        return _scrub_text(value, roots)
    if isinstance(value, Mapping):
        return {key: redact_host_paths(item, roots) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_host_paths(item, roots) for item in value]
    return value


def _scrub_text(text: str, host_roots: Sequence[str]) -> str:
    scrubbed = text
    for root in host_roots:
        scrubbed = scrubbed.replace(root, HOST_PATH_PLACEHOLDER)
    scrubbed = _WINDOWS_ABSOLUTE.sub(_replace_windows_path, scrubbed)
    return _POSIX_ABSOLUTE.sub(_replace_posix_path, scrubbed)


def _replace_windows_path(match: re.Match[str]) -> str:
    found = match.group(0)
    # 句尾标点属于句子而不属于路径：切下来再接回去。
    cut = len(found.rstrip(_TRAILING_PUNCTUATION))
    return HOST_PATH_PLACEHOLDER + found[cut:]


def _replace_posix_path(match: re.Match[str]) -> str:
    found = match.group(0)
    if any(
        found == root or found.startswith(f"{root}/") for root in SANDBOX_INTERNAL_ROOTS
    ):
        return found
    return HOST_PATH_PLACEHOLDER


def build_appeal_pack(inputs: AppealPackInputs) -> dict[str, Any]:
    """组装并**自检**契约 `AppealPack` 的响应体。

    异常：`ReportContractError`（包内报告不自洽/不合契约）、`ValueError`（视图取值不在契约枚举内、
    `result_digest` 形态非法）——这些都是**缺陷**，不是客户端错误，因此原样上抛由宿主记 500。
    """
    if inputs.view not in APPEAL_VIEWS:
        raise ValueError(f"unknown appeal view: {inputs.view!r}")
    report = inputs.report
    result_digest = report.get("result_digest")
    if not is_digest(result_digest):
        raise ReportContractError(
            "the persisted report carries no valid result_digest; a pack without its pin is not"
            " evidence"
        )
    view_report = for_teacher(report) if inputs.view == "teacher" else for_student(report)
    scrubbed = redact_host_paths(view_report, inputs.host_roots)
    errors = report_schema_errors(scrubbed)
    if errors:
        raise ReportContractError(
            "the packed report does not satisfy the report contract after redaction: "
            + "; ".join(errors)
        )
    binding = _binding_of(report)
    replay = replay_instruction(binding=binding, result_digest=str(result_digest))
    redactions = _redactions(inputs.view)
    _require_bounded("replay", replay, REPLAY_MAX_LENGTH)
    for entry in redactions:
        _require_bounded("redactions[]", entry, REDACTION_MAX_LENGTH)
    pack: dict[str, Any] = {
        "evaluation_run_id": inputs.evaluation_run_id,
        "result_digest": str(result_digest),
        "report": scrubbed,
        "binding": binding,
        "runner_policy_summary": runner_policy_summary(report),
        "replay": replay,
        "redactions": redactions,
    }
    if inputs.generated_at is not None:
        pack["generated_at"] = rfc3339_millis(inputs.generated_at)
    required = {
        "evaluation_run_id",
        "result_digest",
        "report",
        "binding",
        "replay",
        "redactions",
    }
    missing = sorted(required - set(pack))
    if missing:
        raise ReportContractError(f"appeal pack is missing required fields: {missing}")
    if not pack["redactions"]:
        raise ReportContractError("appeal pack must list at least one redaction")
    return pack


def _binding_of(report: Mapping[str, object]) -> dict[str, Any]:
    """五项绑定摘要（契约 `AppealPack.binding`，`additionalProperties: false`）。

    从报告里**逐字段取**而不是整段拷贝：报告里的 `binding` 还带 `interpreter_version`，而契约的
    `AppealPack.binding` 没有这个键——`additionalProperties: false` 会因此拒收整份证据包。
    """
    raw = report.get("binding")
    if not isinstance(raw, Mapping):
        raise ReportContractError("the persisted report carries no binding section")
    keys = (
        "submission_digest",
        "package_digest",
        "runner_policy_digest",
        "environment_digest",
    )
    binding: dict[str, Any] = {}
    for key in keys:
        value = raw.get(key)
        if not is_digest(value):
            raise ReportContractError(f"the persisted report's binding.{key} is not a digest")
        binding[key] = str(value)
    question = raw.get("question_version_binding")
    if question is not None and not is_digest(question):
        raise ReportContractError(
            "the persisted report's binding.question_version_binding is not a digest"
        )
    binding["question_version_binding"] = None if question is None else str(question)
    return binding


def runner_policy_summary(report: Mapping[str, object]) -> dict[str, Any]:
    """"当时生效了哪些隔离设置"的摘要——**不含任何宿主路径**（契约的措辞即"by construction"）。

    如实边界：具体**数值**限额（内存/CPU/墙钟/进程数…）随 package 声明而来，B2 只把它的
    `runner_policy_digest` 落库，**不落一份可打印的策略副本**。因此这里报告的是
    "B2 拒绝放宽的那几个常量"（它们的取值与 `runner.policy.RunnerPolicy` 的默认值同源，
    并由测试对着策略对象断言）+ 那个摘要；数值限额只能由摘要指认，本模块**不猜测**它们的取值。
    """
    raw = report.get("binding")
    digest = raw.get("runner_policy_digest") if isinstance(raw, Mapping) else None
    return {
        "runner_policy_digest": str(digest) if is_digest(digest) else None,
        # 这几项是 `runner.policy.policy_constraint_violations` 拒绝放宽的常量：
        # 任何试图翻转它们的策略都会让本次运行**拒绝执行**，因此"跑过沙箱"蕴含它们是这些取值。
        "enforced_constants": {
            "network": False,
            "read_only_root": True,
            "allow_root": False,
            "seccomp": True,
            "cgroup_v2": True,
        },
        "numeric_limits": (
            "identified by runner_policy_digest; B2 does not persist a printable per-run policy"
        ),
        "host_paths": "excluded",
    }


def replay_instruction(*, binding: Mapping[str, Any], result_digest: str) -> str:
    """可读的重现说明（契约 `replay`，≤2000 字符）：**自包含**，不依赖本项目的 runbook。

    说明里出现的是摘要与字段名，不是路径，也不是"去某台机器上跑某个脚本"。
    """
    question = binding.get("question_version_binding") or "(not recorded)"
    return (
        "How to reproduce and check this result from the pack alone: "
        "1) take the submission whose submission_digest is "
        f"{binding['submission_digest']} and the immutable question version whose package_digest is "
        f"{binding['package_digest']} (question_version_binding {question}); "
        "2) evaluate it under a runner whose isolation policy hashes to runner_policy_digest "
        f"{binding['runner_policy_digest']} (see runner_policy_summary for the constants that were in"
        " force; the numeric limits are pinned by that digest) inside an environment whose "
        f"environment_digest is {binding['environment_digest']}; "
        "3) recompute result_digest as SHA-256 over the canonical JSON of the report with "
        "generated_at, result_digest and evaluation_run_id removed and with duration_ms and "
        "resource removed from every case_evidence entry, then compare it with "
        f"{result_digest}; "
        "4) identical values for those five digests must reproduce an identical score and identical "
        "per-case evidence (ADR-0005). Any difference means the pack was altered or the run is not "
        "reproducible from these bindings."
    )


def _redactions(view: str) -> list[str]:
    """被移除/未包含的内容与原因（契约要求**列出**而不是暗示，至少一项）。

    每条都保持在契约的 `maxLength`（200）以内——**不截断**：被截断的说明正是"读者无法判断省略了
    什么"的成因。长度由 `build_appeal_pack` 的自检强制。
    """
    entries = [
        (
            "host filesystem absolute paths and configured host roots: replaced by"
            f" '{HOST_PATH_PLACEHOLDER}' because the pack leaves this machine and absolute paths"
            " describe internal layout"
        ),
        (
            "submission source code, reference answers and hidden case inputs: never read and never"
            " present, because the pack is assembled from the stored report alone"
        ),
    ]
    if view == "student":
        entries.append(
            "expected/actual output excerpts and expected file digests of cases not visible to the"
            " student: withheld as the report endpoint withholds them, because exporting must not"
            " widen visibility"
        )
    return entries


def _require_bounded(name: str, text: str, limit: int) -> None:
    """契约字段的上限检查（超限是**缺陷**，不是客户端错误）：宁可响亮失败，也不静默截断。"""
    if not text:
        raise ReportContractError(f"{name} must not be empty")
    if len(text) > limit:
        raise ReportContractError(f"{name} exceeds the contract limit of {limit} characters")


__all__ = [
    "APPEAL_VIEWS",
    "HOST_PATH_PLACEHOLDER",
    "REPLAY_MAX_LENGTH",
    "SANDBOX_INTERNAL_ROOTS",
    "AppealPackInputs",
    "build_appeal_pack",
    "redact_host_paths",
    "replay_instruction",
    "runner_policy_summary",
]
