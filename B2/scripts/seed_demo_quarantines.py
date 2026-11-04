"""写入几条演示用的隔离记录（只用于演示/联调，**不是**产品功能）。

为什么需要显式确认：这些行会出现在老师的待处理列表里，写入生产库会被当成真实邮件。
脚本因此要求 `--confirm-demo-write`，并在输出里打印数据库主机与库名（不打印密码），
让执行者先看清目标。

用法（在 B2 目录）：
    python scripts/seed_demo_quarantines.py --database-url "$DATABASE_URL" \
        --course-id <uuid> --section-id <uuid> --confirm-demo-write

不传课程/课序时写入的是"未关联"行，只有管理员看得到。脚本只插 `intake_quarantines`，
不建提交、不发事件、不评测；`匹配用户`仍要求原邮件在邮箱里（隔离记录不存附件）。
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy import create_engine

from autograder_b2.db.schema import intake_quarantines

DEMO_PREFIX = "demo-"
#: 演示行的操作人（只用于满足"暂存/作废必须留痕"的约束，不指向真实账号）。
DEMO_ACTOR = uuid.UUID("00000000-0000-4000-8000-0000000000d0")


def _uuid(value: str | None) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise SystemExit(f"invalid UUID: {value}") from error


def _target(url: str) -> str:
    parts = urlsplit(url.replace("postgresql+psycopg://", "postgresql://", 1))
    return f"{parts.hostname or '?'}:{parts.port or '?'}/{parts.path.lstrip('/') or '?'}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Insert demo quarantine rows (explicit confirmation).")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--course-id", default=None)
    parser.add_argument("--section-id", default=None)
    parser.add_argument("--confirm-demo-write", action="store_true")
    args = parser.parse_args(argv)
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    if not args.confirm_demo_write:
        raise SystemExit(
            "refusing to write demo rows without --confirm-demo-write (they look like real mail)"
        )
    course_id = _uuid(args.course_id)
    section_id = _uuid(args.section_id)
    if (course_id is None) != (section_id is None):
        raise SystemExit("course-id and section-id must be provided together")

    engine = create_engine(args.database_url)
    now = datetime.now(UTC)
    rows = [
        {
            "reason_code": "not_uniquely_matched",
            "detail": "同一学号在名单中出现两次，匹配器无法确定是哪一位学生。",
            "subject": "演示：第一次作业提交",
            "resolved": False,
            "held": False,
            "resolution": None,
        },
        {
            "reason_code": "ambiguous_match",
            "detail": "两个班的同名文件指向不同学生，需要老师人工确认。",
            "subject": "演示：作业二（补交）",
            "resolved": False,
            "held": False,
            "resolution": None,
        },
        {
            "reason_code": "executable_attachment",
            "detail": "附件 run.sh 是可执行文件，按安全策略拒绝；可标记作废或人工核对后匹配用户。",
            "subject": "演示：误发的脚本",
            "resolved": False,
            "held": False,
            "resolution": None,
        },
        {
            "reason_code": "not_uniquely_matched",
            "detail": "老师已看过，先暂存，稍后再处理。",
            "subject": "演示：暂存记录",
            "resolved": False,
            "held": True,
            "resolution": None,
        },
        {
            "reason_code": "ambiguous_match",
            "detail": "垃圾邮件，已标记作废。",
            "subject": "演示：作废记录",
            "resolved": True,
            "held": False,
            "resolution": "dismissed",
        },
    ]
    inserted: list[str] = []
    with engine.begin() as connection:
        for index, row in enumerate(rows):
            message_id = f"<{DEMO_PREFIX}{index}-{uuid.uuid4()}@example.edu>"
            identifier = uuid.uuid4()
            values: dict[str, object] = {
                "intake_quarantine_id": identifier,
                "intake": "email",
                "reason_code": row["reason_code"],
                "detail": row["detail"],
                "mailbox": "demo@example.edu",
                "uidvalidity": 1,
                "uid": 9000 + index,
                "message_id": message_id,
                "created_at": now - timedelta(minutes=index),
                "resolved": row["resolved"],
                "resolved_student_id": None,
                "resolution_note": None,
                "scope_course_id": course_id,
                "scope_section_id": section_id,
                "subject": row["subject"],
                "resolution": row["resolution"],
                "resolved_at": now if row["resolved"] else None,
                "resolved_by": DEMO_ACTOR if row["resolved"] else None,
                "resolved_submission_id": None,
                "held": row["held"],
                "held_at": now if row["held"] else None,
                "held_by": DEMO_ACTOR if row["held"] else None,
            }
            connection.execute(sa.insert(intake_quarantines).values(**values))
            inserted.append(str(identifier))
    print(f"wrote {len(inserted)} demo rows to {_target(args.database_url)}")
    print("course/section:", course_id, section_id)
    for identifier in inserted:
        print(" ", identifier)
    return 0


if __name__ == "__main__":
    sys.exit(main())
