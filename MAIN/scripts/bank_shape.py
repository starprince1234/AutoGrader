"""统一的老题库导入脚本形状校验：1 个公开示例 + 10 个隐藏评测用例。

为什么要有这个模块：`per_case` 题的发布门禁（`B3/domain/versions.py`）要求恰好 1 公开 + 10 隐藏，
而三个老导入脚本各自按 `problem["cases"]` 原样提交。它们在 CI 里不跑，但一旦有人对着一份旧形状的
题库 JSON 执行，就会在 `publish` 阶段拿到 422 —— 错误信息只说数量不对，不说哪一条少了什么。

这里把校验提前到"建题之前"，并给出可操作的报错：缺公开示例、隐藏用例不足/过多、隐藏用例没写
期望输出，分别指名。**不自动补用例**：编造期望输出比拒绝发布危险得多。
"""

from __future__ import annotations

PUBLIC_CASE_COUNT = 1
HIDDEN_CASE_COUNT = 10


class BankShapeError(SystemExit):
    """题库 JSON 的用例形状不满足发布门禁（故意用 SystemExit：脚本就此停下并打印原因）。"""


def require_dual_track_shape(title: str, cases: list[dict] | None) -> tuple[dict, list[dict]]:
    """校验并返回 ``(公开用例, 隐藏用例列表)``；不合格直接抛 ``BankShapeError``。"""
    rows = cases or []
    public = [row for row in rows if row.get("visible_to_student")]
    hidden = [row for row in rows if not row.get("visible_to_student")]

    problems: list[str] = []
    if len(public) != PUBLIC_CASE_COUNT:
        problems.append(f"公开示例用例应为 {PUBLIC_CASE_COUNT} 个，实际 {len(public)} 个")
    if len(hidden) != HIDDEN_CASE_COUNT:
        problems.append(f"隐藏评测用例应为 {HIDDEN_CASE_COUNT} 个，实际 {len(hidden)} 个")
    missing_expected = [
        str(row.get("name") or row.get("ordinal"))
        for row in hidden
        if row.get("expected_stdout") is None and row.get("expected_stderr") is None
    ]
    if missing_expected:
        problems.append("隐藏用例缺少期望输出：" + ", ".join(missing_expected))

    if problems:
        raise BankShapeError(
            f"{title}: 用例形状不满足发布门禁（1 公开 + 10 隐藏），未提交任何请求。\n  - "
            + "\n  - ".join(problems)
            + "\n  参考 MAIN/scripts/seed_dual_track_python.py 的题目写法补齐用例后再导入。"
        )
    return public[0], hidden


__all__ = ["BankShapeError", "HIDDEN_CASE_COUNT", "PUBLIC_CASE_COUNT", "require_dual_track_shape"]
