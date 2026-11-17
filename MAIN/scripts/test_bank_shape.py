"""`bank_shape.require_dual_track_shape` 的单元测试。

它是三个老导入脚本的公共门禁：形状不合就带标题报错退出，且**不发出任何请求**。
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, r"D:\github本地仓库\AutoGrader\MAIN\scripts")

from bank_shape import BankShapeError, require_dual_track_shape  # noqa: E402


def _cases(public: int = 1, hidden: int = 10, expected: bool = True) -> list[dict]:
    rows = [
        {
            "name": f"public-{index}",
            "ordinal": index,
            "visible_to_student": True,
            "expected_stdout": "ok\n",
        }
        for index in range(public)
    ]
    rows += [
        {
            "name": f"hidden-{index}",
            "ordinal": public + index,
            "visible_to_student": False,
            "expected_stdout": "ok\n" if expected else None,
        }
        for index in range(hidden)
    ]
    return rows


def test_one_public_and_ten_hidden_is_accepted():
    public, hidden = require_dual_track_shape("题一", _cases())
    assert public["visible_to_student"] is True
    assert len(hidden) == 10


def test_missing_public_case_is_reported_by_name():
    with pytest.raises(BankShapeError) as error:
        require_dual_track_shape("题一", _cases(public=0))
    assert "题一" in str(error.value)
    assert "公开示例用例应为 1 个，实际 0 个" in str(error.value)


def test_wrong_hidden_count_is_reported():
    with pytest.raises(BankShapeError) as error:
        require_dual_track_shape("题二", _cases(hidden=2))
    assert "隐藏评测用例应为 10 个，实际 2 个" in str(error.value)


def test_hidden_case_without_expected_output_is_named():
    with pytest.raises(BankShapeError) as error:
        require_dual_track_shape("题三", _cases(expected=False))
    assert "hidden-0" in str(error.value)
    assert "缺少期望输出" in str(error.value)


def test_missing_cases_entirely_is_rejected():
    with pytest.raises(BankShapeError):
        require_dual_track_shape("题四", None)
