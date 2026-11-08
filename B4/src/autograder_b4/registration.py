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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, and_, delete, func, insert, select, update

from .classes import bind_account_to_enrollments
from .db.tables import (
    credentials,
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
_NUMBER_HEADERS = {
    "学号",
    "student_number",
    "student number",
    "student_id",
    "student id",
    "学号(13位)",
}


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
            "用户名需为 3-32 位小写字母、数字、点、下划线或连字符,且以字母开头(不能包含 @)。"
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
    raise AllowlistError("文件编码无法识别:请用 UTF-8 或 GBK 保存 CSV。")


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
            "CSV 表头必须包含“姓名”和“学号”两列(按列名定位,不按列序)。"
            f"实际表头:{', '.join(cell.strip() for cell in rows[0])}"
        )

    parsed: list[AllowlistRow] = []
    seen: dict[str, int] = {}
    for line_number, row in enumerate(rows[1:], start=2):
        if len(row) <= max(name_index, number_index):
            raise AllowlistError(f"第 {line_number} 行列数不足。")
        student_number = row[number_index].strip()
        display_name = row[name_index].strip()
        if not STUDENT_NUMBER_PATTERN.match(student_number):
            raise AllowlistError(f"第 {line_number} 行的学号不是 13 位数字:{student_number!r}")
        if not display_name:
            raise AllowlistError(f"第 {line_number} 行的姓名为空。")
        if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
            raise AllowlistError(
            f"第 {line_number} 行的姓名超过 {MAX_DISPLAY_NAME_LENGTH} 个字符。"
        )
        if student_number in seen:
            raise AllowlistError(
                f"第 {line_number} 行与第 {seen[student_number]} 行的学号重复:{student_number}"
            )
        seen[student_number] = line_number
        parsed.append(AllowlistRow(student_number=student_number, display_name=display_name))

    if not parsed:
        raise AllowlistError("上传的 CSV 只有表头,没有学生。")
    if len(parsed) > MAX_ALLOWLIST_ROWS:
        raise AllowlistError(f"一次最多导入 {MAX_ALLOWLIST_ROWS} 行,当前 {len(parsed)} 行。")
    return parsed


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
) -> tuple[uuid.UUID, int, int]:
    """Create the account in the caller's transaction; returns `(user_id, bound, mismatched)`.

    The roster link is written here rather than left to a later import: an administrator who
    imported the roster *before* the student registered would otherwise leave a correct
    account that can sign in but sees no course at all (v0.32.0: every course/section/class
    the student is on the roster for is bound at once).

    Binding is by student number **and** name: a roster row whose recorded name differs from
    the verified registration name is not bound and is counted instead. Binding on the number
    alone would merge two differently-named people, and the registration response reports the
    count so the teacher can see that a roster correction is needed.
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
    bound, mismatched = bind_account_to_enrollments(
        connection,
        user_id=user_id,
        student_number=student_number,
        display_name=display_name,
        now=moment,
    )
    return user_id, bound, mismatched


def class_name_of(filename: str) -> str:
    """Class name = file name without the extension.

    A teacher names the file after the class, so the class needs no second input; this turns
    ``01ban.csv`` into ``01ban`` and guesses nothing else.
    """
    stem = filename.strip().rsplit("/", 1)[-1]
    for suffix in (".csv", ".CSV"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)].strip() or stem
    return stem


def add_allowlist_batch(
    connection: Connection,
    *,
    filename: str,
    content: bytes,
    rows: list[AllowlistRow],
    uploaded_by: uuid.UUID | None,
) -> dict[str, object]:
    """Add one class, or replace it when a class with the same name already exists.

    Students already present in *another* class are still refused (one student, one class); rows
    that collide only with the class being replaced are not duplicates -- that is just an updated
    list for the same class.
    """
    class_name = class_name_of(filename)
    digest = hashlib.sha256(content).hexdigest()
    now = datetime.now(UTC).replace(microsecond=0)

    same_class = connection.execute(
        select(registration_allowlist_batches.c.batch_id).where(
            registration_allowlist_batches.c.filename == filename
        )
    ).fetchone()
    if same_class is None:
        # Same class even with a different extension, otherwise a .CSV copy becomes a second class.
        same_class = next(
            (
                candidate
                for candidate in connection.execute(
                    select(
                        registration_allowlist_batches.c.batch_id,
                        registration_allowlist_batches.c.filename,
                    )
                ).fetchall()
                if class_name_of(candidate.filename) == class_name
            ),
            None,
        )

    replaced = same_class is not None
    batch_id = same_class[0] if replaced else uuid.uuid4()

    if replaced:
        connection.execute(
            delete(registration_allowlist).where(registration_allowlist.c.batch_id == batch_id)
        )
        connection.execute(
            update(registration_allowlist_batches)
            .where(registration_allowlist_batches.c.batch_id == batch_id)
            .values(
                filename=filename,
                content_sha256=digest,
                row_count=0,
                uploaded_by=uploaded_by,
                created_at=now,
            )
        )
    else:
        connection.execute(
            insert(registration_allowlist_batches).values(
                batch_id=batch_id,
                filename=filename,
                content_sha256=digest,
                row_count=0,
                uploaded_by=uploaded_by,
                created_at=now,
            )
        )

    # The uniqueness that matters is within one imported class: a student may appear on the
    # class lists of several courses (v0.32.0), so duplicates are only checked inside this
    # file. Replacing an existing class deletes its rows first, so a re-export never collides
    # with its own previous version.
    duplicates: list[str] = []

    inserted = 0
    seen: set[str] = set()
    for row in rows:
        if row.student_number in seen:
            duplicates.append(row.student_number)
            continue
        seen.add(row.student_number)
        connection.execute(
            insert(registration_allowlist).values(
                entry_id=uuid.uuid4(),
                batch_id=batch_id,
                student_number=row.student_number,
                display_name=row.display_name,
                created_at=now,
            )
        )
        inserted += 1

    connection.execute(
        update(registration_allowlist_batches)
        .where(registration_allowlist_batches.c.batch_id == batch_id)
        .values(row_count=inserted)
    )
    return {
        "batch_id": batch_id,
        "class_name": class_name,
        "row_count": inserted,
        "skipped_row_count": len(duplicates),
        "duplicate_student_numbers": duplicates[:20],
        "replaced": replaced,
        "content_sha256": digest,
    }


def list_allowlist_batches(connection: Connection) -> list[dict[str, object]]:
    """Every imported class, newest first."""
    entry_count = (
        select(
            registration_allowlist.c.batch_id,
            func.count().label("entry_count"),
        )
        .group_by(registration_allowlist.c.batch_id)
        .subquery()
    )
    rows = connection.execute(
        select(
            registration_allowlist_batches.c.batch_id,
            registration_allowlist_batches.c.filename,
            func.coalesce(entry_count.c.entry_count, 0).label("row_count"),
            registration_allowlist_batches.c.created_at,
        )
        .select_from(
            registration_allowlist_batches.outerjoin(
                entry_count, entry_count.c.batch_id == registration_allowlist_batches.c.batch_id
            )
        )
        .order_by(
            registration_allowlist_batches.c.created_at.desc(),
            registration_allowlist_batches.c.batch_id.desc(),
        )
    ).fetchall()
    return [
        {
            "batch_id": row.batch_id,
            "class_name": class_name_of(row.filename),
            "filename": row.filename,
            "row_count": row.row_count,
            "uploaded_at": row.created_at,
        }
        for row in rows
    ]


def list_batch_entries(
    connection: Connection, *, batch_id: uuid.UUID
) -> list[dict[str, object]] | None:
    """Students of one class; ``None`` when the class does not exist."""
    if (
        connection.execute(
            select(registration_allowlist_batches.c.batch_id).where(
                registration_allowlist_batches.c.batch_id == batch_id
            )
        ).fetchone()
        is None
    ):
        return None
    rows = connection.execute(
        select(
            registration_allowlist.c.entry_id,
            registration_allowlist.c.student_number,
            registration_allowlist.c.display_name,
        )
        .where(registration_allowlist.c.batch_id == batch_id)
        .order_by(registration_allowlist.c.student_number)
    ).fetchall()
    return [
        {
            "entry_id": row.entry_id,
            "student_number": row.student_number,
            "display_name": row.display_name,
        }
        for row in rows
    ]


def delete_batch(connection: Connection, *, batch_id: uuid.UUID) -> bool:
    """Delete a class together with its rows: otherwise those students stay registrable."""
    if (
        connection.execute(
            select(registration_allowlist_batches.c.batch_id).where(
                registration_allowlist_batches.c.batch_id == batch_id
            )
        ).fetchone()
        is None
    ):
        return False
    connection.execute(
        delete(registration_allowlist).where(registration_allowlist.c.batch_id == batch_id)
    )
    connection.execute(
        delete(registration_allowlist_batches).where(
            registration_allowlist_batches.c.batch_id == batch_id
        )
    )
    return True


def delete_entry(connection: Connection, *, entry_id: uuid.UUID) -> bool:
    """Remove one student."""
    return (
        connection.execute(
            delete(registration_allowlist).where(registration_allowlist.c.entry_id == entry_id)
        ).rowcount
        > 0
    )
