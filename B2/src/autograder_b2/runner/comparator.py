"""比较策略注册表：`exact` / `ignore_trailing_whitespace` / `numeric_tolerance` / `ignored`。

策略**只来自 package**（I21）：本模块不读提交内容、不看环境变量、没有默认策略开关；
调用方（`runner.run_case`）必须把 `package.adapter.comparison` 的取值传进来。

取值集合与 `MAIN/contracts/schemas/evaluation-package.schema.json` 的 `comparison` 枚举一致；
`runner.package` 在解析期已校验，因此这里的 `KeyError` 只可能来自内部拼错（属缺陷，不属学生输入）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from autograder_b2.runner.package import STDERR_STRATEGIES, STDOUT_STRATEGIES


@dataclass(frozen=True, slots=True)
class ComparisonOutcome:
    """一次比较的结果：`matched` + 脱敏 `detail`（不含源码、不含宿主路径）+ 首个分歧行号。

    `mismatch_line` 是**行号**（1 起），不是内容：确定性解释要说清"第几行不同"，但期望输出本身
    仍受保护（隐藏用例的期望值不得进入学生可见面）。比较通过时为 `None`。
    """

    matched: bool
    strategy: str
    detail: str
    mismatch_line: int | None = None


def normalize_trailing_whitespace(text: str) -> str:
    """忽略行尾空白：去掉末尾空行，并对每行 `rstrip`（`exact` 之外唯一的规范化）。"""
    lines = text.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(line.rstrip() for line in lines)


def first_differing_line(actual: str, expected: str, *, normalize: bool = False) -> int | None:
    """首个不同行的行号（1 起）；两段文本逐行完全相同则 `None`。

    "行数不同"也算分歧：实际少一行时，首个分歧行就是**期望多出来的那一行**的下标——
    这样"第 N 行不同"在任何情形下都有确定含义。
    """
    actual_lines = actual.split("\n")
    expected_lines = expected.split("\n")
    if normalize:
        actual_lines = [line.rstrip() for line in actual_lines]
        expected_lines = [line.rstrip() for line in expected_lines]
    for index in range(max(len(actual_lines), len(expected_lines))):
        if index >= len(actual_lines) or index >= len(expected_lines):
            return index + 1
        if actual_lines[index] != expected_lines[index]:
            return index + 1
    return None


def _compare_exact(actual: str, expected: str, tolerance: float | None) -> ComparisonOutcome:
    matched = actual == expected
    detail = "exact match" if matched else f"exact mismatch (actual {len(actual)} vs expected {len(expected)} 字符)"
    line = None if matched else first_differing_line(actual, expected)
    return ComparisonOutcome(matched, "exact", detail, line)


def _compare_ignore_trailing_whitespace(
    actual: str, expected: str, tolerance: float | None
) -> ComparisonOutcome:
    normalized_actual = normalize_trailing_whitespace(actual)
    normalized_expected = normalize_trailing_whitespace(expected)
    matched = normalized_actual == normalized_expected
    detail = (
        "match after ignoring trailing whitespace"
        if matched
        else "mismatch after ignoring trailing whitespace"
        f" (actual {len(normalized_actual)} vs expected {len(normalized_expected)} 字符)"
    )
    line = None if matched else first_differing_line(normalized_actual, normalized_expected)
    return ComparisonOutcome(matched, "ignore_trailing_whitespace", detail, line)


def _compare_numeric_tolerance(
    actual: str, expected: str, tolerance: float | None
) -> ComparisonOutcome:
    """逐行逐 token 比较：两端都可解析为数字时用绝对容差，否则按字面量全等。

    行数不同即不匹配（多一行/少一行不是"数值容差"能吸收的差异）。
    """
    if tolerance is None:
        raise ValueError("numeric_tolerance strategy requires a tolerance value")
    actual_lines = normalize_trailing_whitespace(actual).split("\n")
    expected_lines = normalize_trailing_whitespace(expected).split("\n")
    if len(actual_lines) != len(expected_lines):
        return ComparisonOutcome(
            False,
            "numeric_tolerance",
            f"line count differs (actual {len(actual_lines)} vs expected {len(expected_lines)})",
            min(len(actual_lines), len(expected_lines)) + 1,
        )
    for line_index, (actual_line, expected_line) in enumerate(zip(actual_lines, expected_lines)):
        actual_tokens = actual_line.split()
        expected_tokens = expected_line.split()
        if len(actual_tokens) != len(expected_tokens):
            return ComparisonOutcome(
                False,
                "numeric_tolerance",
                f"line {line_index}: token count differs"
                f" (actual {len(actual_tokens)} vs expected {len(expected_tokens)})",
                line_index + 1,
            )
        for token_index, (actual_token, expected_token) in enumerate(
            zip(actual_tokens, expected_tokens)
        ):
            actual_number = _as_float(actual_token)
            expected_number = _as_float(expected_token)
            if actual_number is not None and expected_number is not None:
                if abs(actual_number - expected_number) > tolerance:
                    return ComparisonOutcome(
                        False,
                        "numeric_tolerance",
                        f"line {line_index} token {token_index}:"
                        f" |{actual_number} - {expected_number}| > {tolerance}",
                        line_index + 1,
                    )
            elif actual_token != expected_token:
                return ComparisonOutcome(
                    False,
                    "numeric_tolerance",
                    f"line {line_index} token {token_index}: literal mismatch",
                    line_index + 1,
                )
    return ComparisonOutcome(True, "numeric_tolerance", f"match within tolerance {tolerance}")


def _compare_ignored(actual: str, expected: str, tolerance: float | None) -> ComparisonOutcome:
    return ComparisonOutcome(True, "ignored", "strategy ignored: comparison always matches")


def _as_float(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


COMPARATORS: dict[str, Callable[[str, str, float | None], ComparisonOutcome]] = {
    "exact": _compare_exact,
    "ignore_trailing_whitespace": _compare_ignore_trailing_whitespace,
    "numeric_tolerance": _compare_numeric_tolerance,
    "ignored": _compare_ignored,
}


def compare_output(
    actual: str,
    expected: str | None,
    *,
    strategy: str,
    numeric_tolerance: float | None = None,
) -> ComparisonOutcome:
    """按 `strategy` 比较；`expected is None` 表示 package 未声明期望输出（不参与判定）。"""
    if strategy not in COMPARATORS:
        raise ValueError(f"unknown comparison strategy: {strategy!r}")
    if expected is None:
        return ComparisonOutcome(True, strategy, "no expected output declared: comparison not applicable")
    return COMPARATORS[strategy](actual, expected, numeric_tolerance)


def compare_stdout(
    actual: str, expected: str | None, strategy: str, numeric_tolerance: float | None = None
) -> ComparisonOutcome:
    """stdout 比较（策略取值必须是 package 声明的 stdout 策略之一）。"""
    if strategy not in STDOUT_STRATEGIES:
        raise ValueError(f"strategy {strategy!r} is not a stdout strategy")
    return compare_output(actual, expected, strategy=strategy, numeric_tolerance=numeric_tolerance)


def compare_stderr(actual: str, expected: str | None, strategy: str) -> ComparisonOutcome:
    """stderr 比较（策略取值必须是 package 声明的 stderr 策略之一）。"""
    if strategy not in STDERR_STRATEGIES:
        raise ValueError(f"strategy {strategy!r} is not a stderr strategy")
    return compare_output(actual, expected, strategy=strategy)


def compare_file_digest(actual_digest: str, expected_digest: str) -> ComparisonOutcome:
    """文件产物比较：按内容摘要（`kind=file` 的 `expected_output` fixture 只带摘要）。"""
    matched = actual_digest == expected_digest
    detail = "file digest match" if matched else "file digest mismatch"
    return ComparisonOutcome(matched, "file_digest", detail)


__all__ = [
    "COMPARATORS",
    "ComparisonOutcome",
    "compare_file_digest",
    "compare_output",
    "compare_stderr",
    "compare_stdout",
    "first_differing_line",
    "normalize_trailing_whitespace",
]
