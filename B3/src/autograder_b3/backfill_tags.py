"""Backfill default tags for questions that have none.

文档 2.0 第 4 条要求"题库里的题目根据他们的特点打上标签"。新建/更新时已经会自动补标签
（``domain/tag_derivation.py``），但**存量题目**不会自己长出标签，所以这里提供一次性回填。

两条安全约束：

1. **默认只读**：不加 ``--apply`` 时只统计"会补哪些题、补成什么"，不写任何数据。
2. **只补空标签**：已经有标签的题目一律跳过——作者写的标签永远原样保留。

用法（在 B3 目录下）::

    uv run python -m autograder_b3.backfill_tags             # dry-run
    uv run python -m autograder_b3.backfill_tags --apply     # 真正写入
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import select

from .config import Settings
from .domain.tag_derivation import derive_tags
from .infra.db import Database
from .infra.models import Question, QuestionVersion


def _latest_manifest(session: Any, question_id: Any) -> dict[str, Any] | None:
    version = session.execute(
        select(QuestionVersion)
        .where(QuestionVersion.question_id == question_id)
        .order_by(QuestionVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    return None if version is None else dict(version.adapter_manifest or {})


def backfill(database: Database, *, apply: bool) -> dict[str, Any]:
    """Return ``{"scanned": n, "tagged": n, "samples": [...]}``; write only when *apply*."""

    scanned = 0
    tagged = 0
    samples: list[dict[str, Any]] = []
    with database.protected_session() as session:
        questions = session.execute(
            select(Question).where(Question.deleted_at.is_(None)).order_by(Question.created_at)
        ).scalars()
        for question in questions:
            scanned += 1
            if question.tags:
                continue
            manifest = _latest_manifest(session, question.question_id)
            if manifest is None:
                continue
            tags = derive_tags(question.language, str(manifest.get("kind", "")))
            if not tags:
                continue
            tagged += 1
            if len(samples) < 10:
                samples.append(
                    {
                        "question_id": str(question.question_id),
                        "title": question.title,
                        "tags": list(tags),
                    }
                )
            if apply:
                question.tags = list(tags)
        if apply:
            session.commit()
    return {"scanned": scanned, "tagged": tagged, "samples": samples, "applied": apply}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autograder_b3.backfill_tags")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the derived tags; without it the command only reports what it would do",
    )
    arguments = parser.parse_args(argv)
    database = Database(Settings.from_env())
    try:
        summary = backfill(database, apply=arguments.apply)
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        database.dispose()
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
