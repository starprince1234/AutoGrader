"""受保护 fixture 字节交付：`FixtureProvider` 端口 + 工作区物化（设计文档 §5）。

背景：package 只声明 fixture 的 `content_sha256`，字节由 B3 存储；B2 必须**真的**把
`mount=input` 的 fixture 取回、写进工作区并逐字节校验，第 6 题的初始文件才可复现。

不可协商项：

- fixture **不覆盖学生文件**，学生文件也不覆盖 fixture：目标路径已存在（提交文件/上次残留）即失败；
- 写入后**回读逐字节比较**，再对 package 声明的摘要做终验；
- 任何 fixture 交付失败都是**技术故障**（`infrastructure_error`），不是学生答错；
- `content` 字节受保护：`repr=False`，不进日志/事件/公开 DTO。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from autograder_b2.domain.clock import sha256_hex
from autograder_b2.domain.evidence import SandboxUnavailableError
from autograder_b2.runner.adapters import workspace_path
from autograder_b2.runner.package import EvaluationPackage


@dataclass(frozen=True, slots=True)
class FixtureContent:
    """B3 交付的一份 input fixture 字节（受保护内容，不出现在任何 repr/日志里）。"""

    path: str
    content_sha256: str
    size_bytes: int
    content: bytes = field(repr=False)


class FixtureProvider(Protocol):
    """按 `question_version_id` 取回 `mount=input` 的 fixture 字节（B3 实现）。

    只返回 input fixture；`expected_output` 的比较只需摘要，**不存在**字节读取通道。
    取不到（依赖不可用/响应形状不符）时抛异常——`materialize_fixtures` 会把它转成
    `infrastructure_error` 证据，而不会让学生的分数承担技术故障。
    """

    def fetch_inputs(self, *, question_version_id: str) -> tuple[FixtureContent, ...]: ...


def materialize_fixtures(
    package: EvaluationPackage,
    workspace: Path,
    provider: FixtureProvider | None,
) -> tuple[str, ...]:
    """把 package 声明的 input fixture 物化进工作区；返回问题清单（空 = 全部就绪）。

    顺序（每一步失败都返回问题、**不**抛普通异常）：

    1. 路径白名单（解析期已校验，这里再走 `workspace_path` 的"解析后仍在工作区内"）；
    2. 目标已存在（与提交文件同名）⇒ 失败，绝不静默覆盖；
    3. `provider is None` ⇒ 不做物化（工作区里预置的 fixture 由 `prepare_case_workspace` 按
       package 声明逐字节校验；缺失/不符同样落 `infrastructure_error`）；provider 取不到 ⇒ 失败；
    4. 写字节 → 回读逐字节比较 → 与 package 声明摘要比较。

    `SandboxUnavailableError` 照旧抛出（它是"拒绝执行"的唯一合法表达，不是 fixture 问题）。
    """
    fixtures = tuple(item for item in package.adapter.files if item.mount == "input")
    if not fixtures:
        return ()
    if provider is None:
        # 没有物化通道时**不做**任何事（不是"静默成功"）：工作区里预先就位的 fixture 由
        # `runner.adapters.prepare_case_workspace` 按 package 声明逐字节校验（旧行为不变）；
        # 缺失/摘要不符仍会在那里被判 `infrastructure_error`。这样接线未注入 provider 的
        # 部署不会被误判为"新包一律技术故障"，而真正的交付失败也不会被吞掉。
        return ()
    try:
        items = provider.fetch_inputs(question_version_id=package.question_version_id)
    except SandboxUnavailableError:
        raise
    except Exception as error:  # noqa: BLE001 - 依赖失败 ⇒ 技术故障证据，不吞成学生失败
        return (f"input fixture provider unavailable: {type(error).__name__}",)

    delivered: dict[str, FixtureContent] = {}
    problems: list[str] = []
    for item in items:
        if item.path in delivered:
            problems.append(f"input fixture provider returned duplicate path: {item.path}")
            continue
        delivered[item.path] = item

    for declared in fixtures:
        try:
            target = workspace_path(workspace, declared.path)
        except ValueError:
            problems.append(f"input fixture path escapes the workspace: {declared.path}")
            continue
        if target.exists() or target.is_symlink():
            problems.append(f"input fixture path already exists in the workspace: {declared.path}")
            continue
        item = delivered.get(declared.path)
        if item is None:
            problems.append(f"input fixture missing: {declared.path}")
            continue
        if item.content_sha256 != declared.content_sha256:
            problems.append(f"input fixture digest mismatch: {declared.path}")
            continue
        if len(item.content) != item.size_bytes:
            problems.append(f"input fixture size mismatch: {declared.path}")
            continue
        if sha256_hex(item.content) != declared.content_sha256:
            problems.append(f"input fixture digest mismatch: {declared.path}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(item.content)
            written = target.read_bytes()
        except OSError as error:
            problems.append(f"input fixture write failed: {declared.path} ({type(error).__name__})")
            continue
        if written != item.content:
            problems.append(f"input fixture write verification failed: {declared.path}")
            continue
        if sha256_hex(written) != declared.content_sha256:
            problems.append(f"input fixture digest mismatch: {declared.path}")
    return tuple(problems)


__all__ = [
    "FixtureContent",
    "FixtureProvider",
    "materialize_fixtures",
]
