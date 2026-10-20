"""Self-service registration: allowlist parsing, verification codes and account creation.

The flow this module implements has no approval step, which is a deliberate product decision:
a student registers and can sign in immediately. What replaces the human check is data we
already own plus one proof the claimant can produce:

* **the allowlist** (an administrator's `姓名`/`学号` CSV) answers "does this identity exist
  in this cohort";
* **the e-mail code** answers "can this person receive mail at this address".

Neither is a strong identity proof on its own -- the roster fields are semi-public, and the
mailbox proves reachability rather than identity -- so both are required. That is also why the
code is bound to the exact intent: a code issued for `(username, student_number, name)` cannot
be replayed to create a different account.

Nothing here stores a password or a code in plaintext: the password is hashed by
`security.passwords`, and the code row keeps a SHA-256 digest bound to its own `code_id`.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import re
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, and_, delete, func, insert, select, update

from .db.tables import (
    credentials,
    enrollments,
    registration_allowlist,
    registration_allowlist_batches,
    registration_codes,
    users,
)
from .db.tables import roles as roles_table
from .errors import unprocessable
from .security.passwords import hash_password

#: Lowercase, starts with a letter, no `@`: the login field is matched verbatim against this
#: column, and an `@` in a username would make "looks like an e-mail" indistinguishable from
#: "is a username" for every future reader.
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{2,31}$")
STUDENT_NUMBER_PATTERN = re.compile(r"^[0-9]{13}$")

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_DISPLAY_NAME_LENGTH = 50
MAX_ALLOWLIST_ROWS = 20000

_NAME_HEADERS = {"姓名", "name", "display_name", "display name", "学生姓名"}
_NUMBER_HEADERS = {"学号", "student_number", "student number", "student_id", "student id", "学号(13位)"}


class AllowlistError(ValueError):
    """The uploaded CSV cannot be used as an allowlist; the message says why."""


@dataclass(frozen=True)
class AllowlistRow:
    student_number: str
    display_name: str


# ---------------------------------------------------------------------------- normalization
def normalize_email(raw: str) -> str:
    return raw.strip().lower()


def normalize_username(raw: str) -> str:
    return raw.strip().lower()


def validate_username(raw: str) -> str:
    candidate = normalize_username(raw)
    if not USERNAME_PATTERN.match(candidate):
        raise unprocessable(
            "用户名需为 3–32 位小写字母、数字、点、下划线或连字符，且以字母开头（不能包含 @）。"
        )
    return candidate


def validate_password(raw: str) -> str:
    if len(raw) < MIN_PASSWORD_LENGTH:
        raise unprocessable(f"密码至少需要 {MIN_PASSWORD_LENGTH} 位。")
    if len(raw) > MAX_PASSWORD_LENGTH:
        raise unprocessable(f"密码最长 {MAX_PASSWORD_LENGTH} 位。")
    return raw


def validate_student_number(raw: str) -> str:
    candidate = raw.strip()
    if not STUDENT_NUMBER_PATTERN.match(candidate):
        raise unprocessable("学号必须是 13 位数字。")
    return candidate


def validate_display_name(raw: str) -> str:
    candidate = raw.strip()
    if not candidate:
        raise unprocessable("姓名不能为空。")
    if len(candidate) > MAX_DISPLAY_NAME_LENGTH:
        raise unprocessable(f"姓名最长 {MAX_DISPLAY_NAME_LENGTH} 个字符。")
    return candidate


# ---------------------------------------------------------------------------- codes
def generate_code() -> str:
    """A six-digit code. `randbelow` keeps every value equally likely."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code_id: uuid.UUID, code: str) -> str:
    """Digest bound to the intent's id, so a digest cannot be reused for another registration."""
    return hashlib.sha256(f"{code_id}:{code}".encode()).hexdigest()


def code_matches(code_id: uuid.UUID, code: str, stored_digest: str) -> bool:
    return hmac.compare_digest(hash_code(code_id, code), stored_digest)


# ---------------------------------------------------------------------------- allowlist CSV
def _decode_csv(content: bytes) -> str:
    """UTF-8 (with or without BOM) first, then GB18030.

    Excel on a Chinese Windows writes GBK by default, which is what the roster files in this
    project look like. A wrong guess is not silently accepted: the caller validates the header
    and the 13-digit column, so a mis-decode fails loudly instead of importing garbage.
    """
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AllowlistError("文件编码无法识别：请用 UTF-8 或 GBK 保存 CSV。")


def _normalize_header(value: str) -> str:
    return value.strip().lower().replace("　", "")


def parse_allowlist_csv(content: bytes) -> list[AllowlistRow]:
    """Parse `姓名,学号` rows located **by header name**, never by column position."""
    if not content:
        raise AllowlistError("上传的 CSV 是空文件。")

    text = _decode_csv(content)
    rows = [
        row
        for row in csv.reader(io.StringIO(text, newline=""))
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        raise AllowlistError("上传的 CSV 没有数据行。")

    header = [_normalize_header(cell) for cell in rows[0]]
    name_index = next((i for i, h in enumerate(header) if h in _NAME_HEADERS), None)
    number_index = next((i for i, h in enumerate(header) if h in _NUMBER_HEADERS), None)
    if name_index is None or number_index is None:
        raise AllowlistError(
            "CSV 表头必须包含“姓名”和“学号”两列（按列名定位，不按列序）。"
            f"实际表头：{', '.join(cell.strip() for cell in rows[0])}"
        )

    parsed: list[AllowlistRow] = []
    seen: dict[str, int] = {}
    for line_number, row in enumerate(rows[1:], start=2):
        if len(row) <= max(name_index, number_index):
            raise AllowlistError(f"第 {line_number} 行列数不足。")
        student_number = row[number_index].strip()
        display_name = row[name_index].strip()
        if not STUDENT_NUMBER_PATTERN.match(student_number):
            raise AllowlistError(f"第 {line_number} 行的学号不是 13 位数字：{student_number!r}")
        if not display_name:
            raise AllowlistError(f"第 {line_number} 行的姓名为空。")
        if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
            raise AllowlistError(f"第 {line_number} 行的姓名超过 {MAX_DISPLAY_NAME_LENGTH} 个字符。")
        if student_number in seen:
            raise AllowlistError(
                f"第 {line_number} 行与第 {seen[student_number]} 行的学号重复：{student_number}"
            )
        seen[student_number] = line_number
        parsed.append(AllowlistRow(student_number=student_number, display_name=display_name))

    if not parsed:
        raise AllowlistError("上传的 CSV 只有表头，没有学生。")
    if len(parsed) > MAX_ALLOWLIST_ROWS:
        raise AllowlistError(f"一次最多导入 {MAX_ALLOWLIST_ROWS} 行，当前 {len(parsed)} 行。")
    return parsed


def replace_allowlist(
    connection: Connection,
    *,
    filename: str,
    content: bytes,
    rows: Iterable[AllowlistRow],
    uploaded_by: uuid.UUID | None,
) -> dict[str, object]:
    """Replace the active allowlist with this file, inside the caller's transaction.

    Full replacement is the deliberate semantic: the file is a *snapshot* of a cohort, so a
    student removed from the list must also lose the ability to register. Previous batches stay
    recorded (filename, digest, row count, uploader) so "who was allowed when" remains
    answerable after the fact.
    """
    prepared = list(rows)
    batch_id = uuid.uuid4()
    digest = hashlib.sha256(content).hexdigest()
    now = datetime.now(UTC).replace(microsecond=0)

    connection.execute(
        insert(registration_allowlist_batches).values(
            batch_id=batch_id,
            filename=filename,
            content_sha256=digest,
            row_count=len(prepared),
            uploaded_by=uploaded_by,
            created_at=now,
        )
    )
    removed = connection.execute(delete(registration_allowlist)).rowcount
    connection.execute(
        insert(registration_allowlist),
        [
            {
                "entry_id": uuid.uuid4(),
                "batch_id": batch_id,
                "student_number": row.student_number,
                "display_name": row.display_name,
                "created_at": now,
            }
            for row in prepared
        ],
    )
    return {
        "batch_id": batch_id,
        "row_count": len(prepared),
        "replaced_row_count": max(0, int(removed or 0)),
        "content_sha256": digest,
    }


def allowlist_entry(
    connection: Connection, *, student_number: str, display_name: str
) -> AllowlistRow | None:
    """The allowlist row for this identity, or `None`. Name comparison ignores outer spaces."""
    row = connection.execute(
        select(
            registration_allowlist.c.student_number, registration_allowlist.c.display_name
        ).where(
            and_(
                registration_allowlist.c.student_number == student_number,
                func.btrim(registration_allowlist.c.display_name) == display_name,
            )
        )
    ).fetchone()
    return None if row is None else AllowlistRow(row[0], row[1])


def active_batch(connection: Connection) -> dict[str, object] | None:
    row = connection.execute(
        select(
            registration_allowlist_batches.c.batch_id,
            registration_allowlist_batches.c.filename,
            registration_allowlist_batches.c.row_count,
            registration_allowlist_batches.c.created_at,
        ).order_by(registration_allowlist_batches.c.created_at.desc())
    ).fetchone()
    if row is None:
        return None
    return {
        "batch_id": row.batch_id,
        "filename": row.filename,
        "row_count": row.row_count,
        "uploaded_at": row.created_at,
    }


# ---------------------------------------------------------------------------- intents
def find_open_code(connection: Connection, *, email: str) -> object | None:
    """The newest unconsumed, unexpired code row for this mailbox."""
    return connection.execute(
        select(registration_codes)
        .where(
            and_(
                registration_codes.c.email == email,
                registration_codes.c.consumed_at.is_(None),
                registration_codes.c.expires_at > datetime.now(UTC),
            )
        )
        .order_by(registration_codes.c.created_at.desc())
        .limit(1)
    ).fetchone()


def recent_code_count(connection: Connection, *, email: str, within_seconds: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=within_seconds)
    return int(
        connection.execute(
            select(func.count())
            .select_from(registration_codes)
            .where(
                and_(registration_codes.c.email == email, registration_codes.c.created_at >= cutoff)
            )
        ).scalar_one()
    )


def last_code_age_seconds(connection: Connection, *, email: str) -> int | None:
    created = connection.execute(
        select(registration_codes.c.created_at)
        .where(registration_codes.c.email == email)
        .order_by(registration_codes.c.created_at.desc())
        .limit(1)
    ).scalars().first()
    if created is None:
        return None
    return max(0, int((datetime.now(UTC) - created).total_seconds()))


# ---------------------------------------------------------------------------- account
def create_account(
    connection: Connection,
    *,
    username: str,
    email: str,
    student_number: str,
    display_name: str,
    password: str,
    now: datetime | None = None,
) -> uuid.UUID:
    """Create the account in the caller's transaction; returns the new `user_id`.

    The roster link is written here rather than left to a second import: an administrator who
    imported the roster *before* the student registered would otherwise leave a correct account
    that can sign in but sees no course at all.
    """
    moment = (now or datetime.now(UTC)).replace(microsecond=0)
    user_id = uuid.uuid4()
    connection.execute(
        insert(users).values(
            user_id=user_id,
            username=username,
            display_name=display_name,
            student_number=student_number,
            email=email,
            is_active=True,
            created_via="self",
            created_at=moment,
            updated_at=moment,
        )
    )
    hashed = hash_password(password)
    connection.execute(
        insert(credentials).values(
            credential_id=uuid.uuid4(),
            user_id=user_id,
            password_hash=hashed.hash,
            hash_algorithm=hashed.algorithm,
            created_at=moment,
            updated_at=moment,
        )
    )
    connection.execute(
        insert(roles_table).values(
            role_id=uuid.uuid4(),
            user_id=user_id,
            role="student",
            course_id=None,
            granted_by=None,
            created_at=moment,
        )
    )
    connection.execute(
        update(enrollments)
        .where(
            and_(
                enrollments.c.student_number == student_number,
                enrollments.c.user_id.is_(None),
                enrollments.c.is_active.is_(True),
            )
        )
        .values(user_id=user_id, updated_at=moment)
    )
    return user_id
