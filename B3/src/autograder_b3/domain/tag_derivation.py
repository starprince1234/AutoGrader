"""Derive a default tag set from a question's structural characteristics.

文档 2.0 第 4 条：题库里的题目要"根据他们的特点"打上标签。这里刻意只使用**写入时已经在手边、
确定无疑的结构化事实**——语言与运行方式——不猜题干语义，也不引入受控词表：

* 语言：``python`` / ``bash``（契约 `Language` 的枚举原值）。
* 运行方式：``stdin`` / ``file`` / ``callable``（契约 `AdapterManifest.kind`）。

这些值本身满足 ``domain/tags.py`` 的标签形态（小写、连字符、以字母数字开头），因此不需要
任何"归一化"，也就不会出现"系统把我的标签改写成了别的词"这种情况。

只有在作者**没有填标签**时才调用本函数：作者写的标签永远原样保存
（见 ``domain/tags.py`` 的"不发明词表、不改写"约定）。
"""

from __future__ import annotations

from .tags import validate_tags

#: 语言 → 标签。枚举以外的值不加标签，避免把一个不认识的词写进题库。
_LANGUAGE_TAGS = {"python": "python", "bash": "bash"}

#: 运行方式 → 标签。
_ADAPTER_TAGS = {"stdin": "stdin", "file": "file", "callable": "callable"}


def derive_tags(language: str, adapter_kind: str) -> tuple[str, ...]:
    """Return the default tags for a question with these characteristics.

    The result is passed through the ordinary :func:`validate_tags` so a future edit to
    the language or adapter vocabulary cannot silently write something the reader
    would then refuse.
    """

    derived: list[str] = []
    language_tag = _LANGUAGE_TAGS.get(language)
    if language_tag is not None:
        derived.append(language_tag)
    adapter_tag = _ADAPTER_TAGS.get(adapter_kind)
    if adapter_tag is not None:
        derived.append(adapter_tag)
    return validate_tags(derived)
