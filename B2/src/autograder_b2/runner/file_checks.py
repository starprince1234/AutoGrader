"""运行后的工作区文件检查（`file_checks`，设计文档 §3.4）。

**不读学生 stdout**：直接对工作区最终字节与权限位做检查。三条边界：

1. `lstat` 判"普通文件"：符号链接即失败（不允许用链接指向可读对象冒充产出）；
2. 路径必须解析后仍在工作区内（父目录同样校验，挡"把目录换成指向外面的符号链接"）；
3. 权限位来自 `lstat` 的 `st_mode & 0o7777`，与进程 umask 无关，文本形如 `"0744"`。

失败类别与设计文档一致：缺文件 `missing_output`，内容/权限不符 `output_mismatch`。
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from autograder_b2.domain.clock import sha256_hex
from autograder_b2.domain.evidence import FileCheckResult
from autograder_b2.runner.package import PackageCase, PackageFileCheck

#: 单项 file_check 的摘要计算预算：超出只报权限、摘要为 `None`（"没测"而不是"测出来是空"）。
MAX_FILE_CHECK_BYTES = 8 * 1024 * 1024


def format_mode(mode_bits: int) -> str:
    """权限位 → 四位八进制文本（`0o744` → `"0744"`），与 package 声明同形比较。"""
    return format(int(mode_bits) & 0o7777, "04o")


def evaluate_file_checks(case: PackageCase, workspace: Path) -> tuple[FileCheckResult, ...]:
    """检查用例声明的全部文件检查项（顺序 = 声明顺序，结果确定性）。"""
    return tuple(_evaluate_one(check, workspace) for check in case.file_checks)


def _evaluate_one(check: PackageFileCheck, workspace: Path) -> FileCheckResult:
    root = Path(workspace).resolve()
    raw = root / check.path
    # 父目录解析后必须仍在工作区内：学生可以把目录换成符号链接来骗过"最终字节"读取。
    try:
        parent = raw.parent.resolve()
    except OSError:
        return _result(check, actual_sha256=None, actual_mode=None, matched=False)
    if parent != root and not parent.is_relative_to(root):
        return _result(check, actual_sha256=None, actual_mode=None, matched=False)
    try:
        info = os.lstat(raw)
    except OSError:
        return _result(check, actual_sha256=None, actual_mode=None, matched=False)
    if not stat.S_ISREG(info.st_mode):
        # 符号链接/目录/设备文件都不算"普通文件产出"。
        return _result(check, actual_sha256=None, actual_mode=None, matched=False)
    actual_mode = format_mode(info.st_mode)
    actual_sha256: str | None = None
    if check.content_sha256 is not None and info.st_size <= MAX_FILE_CHECK_BYTES:
        try:
            actual_sha256 = sha256_hex(raw.read_bytes())
        except OSError:
            actual_sha256 = None
    matched = (check.content_sha256 is None or actual_sha256 == check.content_sha256) and (
        check.mode is None or actual_mode == check.mode
    )
    return FileCheckResult(
        path=check.path,
        expected_sha256=check.content_sha256,
        actual_sha256=actual_sha256,
        expected_mode=check.mode,
        actual_mode=actual_mode,
        matched=matched,
    )


def _result(
    check: PackageFileCheck, *, actual_sha256: str | None, actual_mode: str | None, matched: bool
) -> FileCheckResult:
    return FileCheckResult(
        path=check.path,
        expected_sha256=check.content_sha256,
        actual_sha256=actual_sha256,
        expected_mode=check.mode,
        actual_mode=actual_mode,
        matched=matched,
    )


@dataclass(frozen=True, slots=True)
class FileCheckFailure:
    """file_checks 的归类结果：`category is None` 表示全部通过。"""

    category: str | None
    missing: tuple[str, ...]
    mismatched: tuple[str, ...]
    message: str


def classify_file_checks(results: tuple[FileCheckResult, ...]) -> FileCheckFailure:
    """把检查结果归类为 `missing_output` / `output_mismatch`（不通过时）或通过。

    "缺文件"与"不符"的区分只来自**实际观察**：既没有摘要也没有权限位 ⇒ 文件不存在/不是普通文件；
    拿到了权限位但摘要不符 ⇒ 内容不符。消息只写工作区相对路径与检查项，**不写**期望内容。
    """
    if not results or all(item.matched for item in results):
        return FileCheckFailure(None, (), (), "")
    missing = tuple(
        item.path
        for item in results
        if not item.matched and item.actual_sha256 is None and item.actual_mode is None
    )
    mismatched = tuple(
        item.path
        for item in results
        if not item.matched and not (item.actual_sha256 is None and item.actual_mode is None)
    )
    if missing and mismatched:
        return FileCheckFailure(
            "output_mismatch",
            missing,
            mismatched,
            "file checks failed: missing "
            + ", ".join(missing)
            + "; mismatched "
            + ", ".join(mismatched),
        )
    if missing:
        return FileCheckFailure(
            "missing_output", missing, (), "file checks failed: missing " + ", ".join(missing)
        )
    return FileCheckFailure(
        "output_mismatch",
        (),
        mismatched,
        "file checks failed: content or permission mismatch " + ", ".join(mismatched),
    )


__all__ = [
    "MAX_FILE_CHECK_BYTES",
    "FileCheckFailure",
    "classify_file_checks",
    "evaluate_file_checks",
    "format_mode",
]
