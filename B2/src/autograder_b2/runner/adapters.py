"""三类 adapter（`stdin` / `file` / `callable`）的执行形态与固定 harness。

本模块只把 package 声明翻译成**执行计划**与工作区内的**服务端生成物**，不启动任何进程
（启动在 `runner.run_case`）：

- `stdin`：命令行参数 + 标准输入 → 采集 stdout/stderr/退出码；
- `file`：输入 fixture 在工作区内按摘要校验（内容由 package 之外的通道交付），
  产出文件按 `mount: expected_output` 声明的摘要比较；
- `callable`：B2 **自有固定 harness**（`__b2_harness__.py`）按 package 声明的点分模块名 import
  后调用目标属性；**绝不**从绝对路径或网络加载（模块名在解析期已拒绝路径分隔符与 `..`）。

B2 拥有的执行约定（契约未声明、由 B2 定死，改动需同步文档）：入口脚本名
`main.py` / `main.sh`；callable harness 文件名与解析失败退出码见下方常量。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from autograder_b2.domain.clock import sha256_hex
from autograder_b2.domain.evidence import FileChange
from autograder_b2.runner.package import PackageAdapter, PackageCase, PackageFixture
from autograder_b2.runner.policy import RunnerPolicy, sandbox_script_path

#: 入口脚本名（契约不声明入口文件名；B2 定死为工作区根下的 `main.py` / `main.sh`）。
ENTRY_SCRIPT_BY_LANGUAGE: dict[str, str] = {"python": "main.py", "bash": "main.sh"}

#: callable harness 的固定文件名（保留路径：工作区里已存在同名文件即拒绝执行）。
HARNESS_FILENAME = "__b2_harness__.py"

#: harness 解析失败（import 失败 / 属性不可调用）的退出码与 stderr 标记 → `import_error`。
HARNESS_RESOLUTION_EXIT = 3
HARNESS_RESOLUTION_MARKER = "B2_CALLABLE_ERROR"

#: 证据摘录上限（契约 `maxLength: 2000`）。
EXCERPT_LIMIT = 2000

#: 工作区快照的**摘要计算预算**（字节，按路径排序累加）。超过预算的文件只记大小、摘要为 `None`。
#:
#: 为什么需要它：文件清单里的摘要要读内容，而学生代码可以在工作区里写出很多字节（受 `rlimit_fsize`
#: 与磁盘约束，但仍是几十 MB 量级）。预算让每次用例的证据收集成本有确定上界，且**按排序累加 ⇒ 确定性**：
#: 同一份输入永远算出同一组摘要（哈希集合的平台差异会让报告不可重放）。
EVIDENCE_HASH_BUDGET_BYTES = 32 * 1024 * 1024

#: 契约 `file_change_list.maxItems`：单类清单上限（超出即截断，并由 `explanation` 如实说明）。
FILE_CHANGE_LIMIT = 200

#: **不进证据**的工作区路径：解释器字节码缓存。
#:
#: 理由有两条，都不是审美：①`.pyc` 头里带源文件 mtime，同一份提交跑两次会得到**不同字节**，
#: 一旦进报告就会让 `result_digest` 失去"同绑定 ⇒ 同摘要"的意义（ADR-0005 第 5 条）；②它不是提交
#: 产出的文件，而是解释器的缓存。**注意**：`max_files_created` 的计数仍然照旧统计它们（那是资源口径，
#: 不是证据口径），两者刻意不共用同一个过滤。
IGNORED_CHANGE_PREFIXES: tuple[str, ...] = ("__pycache__/",)
IGNORED_CHANGE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo")


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """单个用例的执行计划（纯数据；由 `build_execution_plan` 从 package 推导）。"""

    kind: str
    language: str
    interpreter: str
    script: str
    entry_name: str
    arguments: tuple[str, ...]
    stdin_text: str | None
    expected_stdout: str | None
    expected_stderr: str | None
    expected_exit_code: int | None
    stdout_strategy: str
    stderr_strategy: str
    numeric_tolerance: float | None
    input_fixtures: tuple[PackageFixture, ...]
    expected_outputs: tuple[PackageFixture, ...]
    harness_name: str | None


@dataclass(frozen=True, slots=True)
class FixtureOutcome:
    """产出文件比较结果（只带摘要与摘录，不带期望内容原文）。"""

    path: str
    expected_sha256: str
    actual_sha256: str | None
    actual_excerpt: str | None


@dataclass(frozen=True, slots=True)
class WorkspaceFile:
    """快照里的一个文件：大小 + 内容摘要（超出哈希预算时摘要为 `None`）。"""

    size_bytes: int
    content_sha256: str | None


@dataclass(frozen=True, slots=True)
class WorkspaceChanges:
    """运行前后工作区差异（契约 `created`/`modified`/`deleted` 三张清单）。"""

    created: tuple[FileChange, ...]
    modified: tuple[FileChange, ...]
    deleted: tuple[FileChange, ...]
    truncated: bool


def render_callable_harness(entry_module: str, entry_attribute: str, arguments_json: str | None) -> str:
    """渲染固定 harness 源码（同 package 声明 ⇒ 逐字节相同；提交侧无法替换）。"""
    template = _HARNESS_TEMPLATE
    for placeholder, value in (
        ("@MODULE@", repr(entry_module)),
        ("@ATTRIBUTE@", repr(entry_attribute)),
        ("@ARGUMENTS_JSON@", repr(arguments_json)),
        ("@EXIT@", str(HARNESS_RESOLUTION_EXIT)),
        ("@MARKER@", HARNESS_RESOLUTION_MARKER),
    ):
        template = template.replace(placeholder, value)
    return template


def build_execution_plan(*, adapter: PackageAdapter, case: PackageCase, policy: RunnerPolicy,
                         language: str) -> ExecutionPlan:
    """把 package 声明翻译成执行计划（有效值优先级：用例声明 → adapter 声明 → 无）。

    解释器与入口路径**只来自策略**，`arguments` 来自 package 且逐元素原样传递（不进入 shell）。
    """
    if adapter.kind == "callable":
        if adapter.callable is None:
            raise ValueError("adapter.kind=callable requires adapter.callable")
        entry_name = HARNESS_FILENAME
        script = sandbox_script_path(policy, entry_name)
        harness_name: str | None = HARNESS_FILENAME
    else:
        entry_name = entry_name_for(language)
        script = sandbox_script_path(policy, entry_name)
        harness_name = None
    return ExecutionPlan(
        kind=adapter.kind,
        language=language,
        interpreter=policy.interpreter_for(language),
        script=script,
        entry_name=entry_name,
        arguments=tuple(case.args) if case.args else tuple(adapter.args),
        stdin_text=case.stdin if case.stdin is not None else adapter.stdin,
        expected_stdout=case.expected_stdout,
        expected_stderr=case.expected_stderr,
        expected_exit_code=(
            case.expected_exit_code
            if case.expected_exit_code is not None
            else adapter.expect_exit_code
        ),
        stdout_strategy=adapter.comparison.stdout,
        stderr_strategy=adapter.comparison.stderr,
        numeric_tolerance=adapter.comparison.numeric_tolerance,
        input_fixtures=tuple(item for item in adapter.files if item.mount == "input"),
        expected_outputs=tuple(item for item in adapter.files if item.mount == "expected_output"),
        harness_name=harness_name,
    )


def workspace_path(workspace: Path, relative: str) -> Path:
    """工作区内的相对路径 → 绝对路径，并确认**解析后仍在工作区内**（挡住符号链接穿越）。"""
    root = Path(workspace).resolve()
    candidate = (root / relative).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise ValueError(f"path escapes the case workspace: {relative!r}")
    return candidate


def prepare_case_workspace(
    plan: ExecutionPlan,
    workspace: Path,
    *,
    entry_module: str | None = None,
    entry_attribute: str | None = None,
    arguments_json: str | None = None,
) -> tuple[str, ...]:
    """把服务端生成物写入工作区并校验输入 fixture；返回问题清单（空 = 可以执行）。

    - callable：写入固定 harness。工作区里**已存在**同名文件即拒绝（提交侧不得提供 harness）。
    - 输入 fixture：必须存在且摘要与 package 声明一致（内容由 package 之外的通道交付）。
    """
    problems: list[str] = []
    if plan.harness_name is not None:
        if entry_module is None or entry_attribute is None:
            raise ValueError("callable plan requires entry_module/entry_attribute")
        harness_path = workspace_path(workspace, plan.harness_name)
        if harness_path.exists():
            raise ValueError(f"reserved harness path already exists: {plan.harness_name}")
        harness_path.write_text(
            render_callable_harness(entry_module, entry_attribute, arguments_json),
            encoding="utf-8",
        )
    for fixture in plan.input_fixtures:
        target = workspace_path(workspace, fixture.path)
        if not target.is_file():
            problems.append(f"input fixture missing: {fixture.path}")
            continue
        actual = sha256_hex(target.read_bytes())
        if actual != fixture.content_sha256:
            problems.append(f"input fixture digest mismatch: {fixture.path}")
    return tuple(problems)


def entry_script_exists(plan: ExecutionPlan, workspace: Path) -> bool:
    """入口脚本是否已在工作区内（缺失属提交侧问题，须在启动沙箱前判定）。"""
    try:
        return workspace_path(workspace, plan.entry_name).is_file()
    except ValueError:
        return False


def entry_name_for(language: str) -> str:
    """入口脚本名（B2 执行约定；未知语言即内部缺陷）。"""
    name = ENTRY_SCRIPT_BY_LANGUAGE.get(language)
    if name is None:
        raise ValueError(f"unsupported language: {language!r}")
    return name


def snapshot_workspace(
    workspace: Path, *, hash_budget_bytes: int = EVIDENCE_HASH_BUDGET_BYTES
) -> dict[str, WorkspaceFile]:
    """工作区快照（相对 POSIX 路径 → 大小 + 内容摘要）。

    遍历顺序是排序后的路径，摘要预算按该顺序累加——**同一棵工作区永远得到同一组摘要**。
    预算耗尽后的文件只记大小、摘要为 `None`（"没测"而不是"测出来是空"）。
    """
    root = Path(workspace)
    if not root.is_dir():
        return {}
    snapshot: dict[str, WorkspaceFile] = {}
    budget = hash_budget_bytes
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(root).as_posix()
        try:
            size = item.stat().st_size
        except OSError:  # pragma: no cover - 并发删除时忽略
            continue
        digest: str | None = None
        if budget >= size:
            try:
                digest = sha256_hex(item.read_bytes())
            except OSError:  # pragma: no cover - 并发删除时忽略
                digest = None
            else:
                budget -= size
        snapshot[relative] = WorkspaceFile(size_bytes=size, content_sha256=digest)
    return snapshot


def created_files(
    before: Mapping[str, WorkspaceFile], after: Mapping[str, WorkspaceFile]
) -> tuple[str, ...]:
    """运行期间**新建**的文件（相对路径，已排序 ⇒ 确定性）。

    这是**资源口径**（`max_files_created` 用它），因此不过滤解释器缓存——证据口径的过滤见
    `workspace_changes`。
    """
    return tuple(sorted(set(after) - set(before)))


def recorded_change_path(path: str) -> bool:
    """该路径是否进证据清单（解释器字节码缓存不进，理由见 `IGNORED_CHANGE_PREFIXES`）。"""
    if path.endswith(IGNORED_CHANGE_SUFFIXES):
        return False
    return not path.startswith(IGNORED_CHANGE_PREFIXES)


def _change_of(path: str, snapshot: Mapping[str, WorkspaceFile]) -> FileChange:
    entry = snapshot[path]
    return FileChange(path=path, content_sha256=entry.content_sha256, size_bytes=entry.size_bytes)


def workspace_changes(
    before: Mapping[str, WorkspaceFile],
    after: Mapping[str, WorkspaceFile],
    *,
    limit: int = FILE_CHANGE_LIMIT,
) -> WorkspaceChanges:
    """运行前后的工作区差异（`RQ-B-0-30` 验收 2 的"被修改文件清单与内容摘要"）。

    - `created`：运行后才存在的文件；
    - `deleted`：运行前存在、运行后消失的文件（大小取自**运行前**快照，摘要同样取自运行前——
      内容此时已经不在盘上，事后再算是不可能的）；
    - `modified`：两侧都存在且"大小不同"或"两侧摘要都已知且不同"的文件。

    三张清单各自按路径排序、各自截断到 `limit` 条（契约上限）；任何一张被截断时 `truncated=True`，
    由 `explanation` 如实说明——**不静默截断**。

    **摘要缺失时的判定口径**：超出哈希预算的文件只有大小。若两侧大小相同而至少一侧摘要未知，
    则该文件**不计入** `modified`（宁可少报也不凭空断言"被改了"）；大小不同则一定计入。
    """
    created_paths = sorted(path for path in set(after) - set(before) if recorded_change_path(path))
    deleted_paths = sorted(path for path in set(before) - set(after) if recorded_change_path(path))
    modified_paths: list[str] = []
    for path in sorted(set(before) & set(after)):
        if not recorded_change_path(path):
            continue
        old, new = before[path], after[path]
        if old.size_bytes != new.size_bytes:
            modified_paths.append(path)
            continue
        if (
            old.content_sha256 is not None
            and new.content_sha256 is not None
            and old.content_sha256 != new.content_sha256
        ):
            modified_paths.append(path)
    truncated = any(len(paths) > limit for paths in (created_paths, deleted_paths, modified_paths))
    return WorkspaceChanges(
        created=tuple(_change_of(path, after) for path in created_paths[:limit]),
        modified=tuple(_change_of(path, after) for path in modified_paths[:limit]),
        deleted=tuple(_change_of(path, before) for path in deleted_paths[:limit]),
        truncated=truncated,
    )


def collect_fixture_outcomes(plan: ExecutionPlan, workspace: Path) -> tuple[FixtureOutcome, ...]:
    """读取 `expected_output` 产出文件的实际摘要与摘录（缺失即 `actual_sha256=None`）。"""
    outcomes: list[FixtureOutcome] = []
    for fixture in plan.expected_outputs:
        try:
            target = workspace_path(workspace, fixture.path)
        except ValueError:
            outcomes.append(FixtureOutcome(fixture.path, fixture.content_sha256, None, None))
            continue
        if not target.is_file():
            outcomes.append(FixtureOutcome(fixture.path, fixture.content_sha256, None, None))
            continue
        content = target.read_bytes()
        outcomes.append(
            FixtureOutcome(
                path=fixture.path,
                expected_sha256=fixture.content_sha256,
                actual_sha256=sha256_hex(content),
                actual_excerpt=excerpt_of_text(content.decode("utf-8", errors="replace")),
            )
        )
    return tuple(outcomes)


def excerpt_of_text(text: str, limit: int = EXCERPT_LIMIT) -> str:
    """按上限截断摘录（I23；完整输出只留摘要）。"""
    return text if len(text) <= limit else text[:limit]


def join_problems(problems: Iterable[str]) -> str:
    """把问题清单拼成一条脱敏消息（只含工作区相对路径，不含宿主绝对路径）。"""
    return "; ".join(problems)


_HARNESS_TEMPLATE = '''"""B2 callable harness（服务端固定生成；提交侧不得提供或替换同名文件）。

在**工作区**内按 package 声明的点分模块名 import 后调用目标属性：不使用绝对路径、不访问网络。
解析失败（import 失败 / 属性不可调用）以固定退出码 @EXIT@ 与固定 stderr 标记结束，
由 runner 归类为 `import_error`；调用期异常按学生代码崩溃处理。
"""

from __future__ import annotations

import importlib
import json
import sys

MODULE_NAME = @MODULE@
ATTRIBUTE_NAME = @ATTRIBUTE@
ARGUMENTS_JSON = @ARGUMENTS_JSON@
RESOLUTION_ERROR_EXIT = @EXIT@
RESOLUTION_MARKER = "@MARKER@"


def _resolve_arguments() -> list:
    """参数优先级：package 的 arguments_json；未声明时用命令行参数。"""
    if ARGUMENTS_JSON is not None:
        payload = json.loads(ARGUMENTS_JSON)
        return list(payload) if isinstance(payload, list) else [payload]
    return list(sys.argv[1:])


def _fail(message: str) -> None:
    sys.stdout.flush()
    sys.stderr.write(RESOLUTION_MARKER + ": " + message + "\\n")
    sys.stderr.flush()
    raise SystemExit(RESOLUTION_ERROR_EXIT)


def main() -> int:
    try:
        module = importlib.import_module(MODULE_NAME)
    except BaseException as error:  # import 副作用（含 sys.exit）一律按解析失败归类
        _fail("import " + MODULE_NAME + " failed: " + type(error).__name__)
    target = getattr(module, ATTRIBUTE_NAME, None)
    if not callable(target):
        _fail("attribute " + MODULE_NAME + "." + ATTRIBUTE_NAME + " is not callable")
    try:
        arguments = _resolve_arguments()
    except ValueError as error:
        _fail("arguments_json is not valid JSON: " + type(error).__name__)
    result = target(*arguments)
    if result is None:
        return 0
    if isinstance(result, str):
        sys.stdout.write(result if result.endswith("\\n") else result + "\\n")
        return 0
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


__all__ = [
    "ENTRY_SCRIPT_BY_LANGUAGE",
    "EVIDENCE_HASH_BUDGET_BYTES",
    "EXCERPT_LIMIT",
    "FILE_CHANGE_LIMIT",
    "HARNESS_FILENAME",
    "HARNESS_RESOLUTION_EXIT",
    "HARNESS_RESOLUTION_MARKER",
    "IGNORED_CHANGE_PREFIXES",
    "IGNORED_CHANGE_SUFFIXES",
    "ExecutionPlan",
    "FixtureOutcome",
    "WorkspaceChanges",
    "WorkspaceFile",
    "build_execution_plan",
    "collect_fixture_outcomes",
    "created_files",
    "entry_name_for",
    "entry_script_exists",
    "excerpt_of_text",
    "join_problems",
    "prepare_case_workspace",
    "recorded_change_path",
    "render_callable_harness",
    "snapshot_workspace",
    "workspace_changes",
    "workspace_path",
]
