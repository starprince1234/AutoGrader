"""写入两条演示用的待处理隔离记录（只用于演示/联调，**不是**产品功能）。

一条是"无法确定属于谁"的邮件（带课程/课序，老师可人工匹配用户），另一条是"没有附件"
（无法作为提交、只对管理员可见，只能暂存或标记作废）。

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
            "detail": "发件邮箱对应名单中的两个学生，匹配器无法确定这封邮件属于谁。",
            "subject": "演示：无法确定是谁的作业",
            "resolved": False,
            "held": False,
            "resolution": None,
            "scoped": True,
        },
        {
            "reason_code": "no_attachments",
            "detail": "邮件正文只有一句话，没有可提交的附件；管理员可核对后标记作废。",
            "subject": "演示：没有附件的邮件",
            "resolved": False,
            "held": False,
            "resolution": None,
            # 无法证明属于哪个班：只让管理员看到，符合行级 scope 的口径。
            "scoped": False,
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
                "scope_course_id": course_id if row["scoped"] else None,
                "scope_section_id": section_id if row["scoped"] else None,
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
