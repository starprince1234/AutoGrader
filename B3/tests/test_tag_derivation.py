"""Default tags derived from a question's structural characteristics (文档 2.0 第 4 条).

只断言"结构事实 → 标签"这一层：语言与运行方式是写入时就在手边的确定信息，标签形态交给
``domain/tags.py`` 的校验器（它同时负责拒绝不合形态的值）。
"""

from __future__ import annotations

from autograder_b3.domain.tag_derivation import derive_tags


def test_derive_tags_uses_language_and_adapter_kind():
    assert derive_tags("python", "stdin") == ("python", "stdin")
    assert derive_tags("bash", "file") == ("bash", "file")
    assert derive_tags("python", "callable") == ("python", "callable")


def test_derive_tags_does_not_invent_tags_for_unknown_values():
    # 词表以外的值不加标签：宁可没有标签，也不把一个不认识的词写进题库。
    assert derive_tags("ruby", "network") == ()
