"""Manual grading: a teacher's score, stored with its author, reason and history.

Why this is not part of the deterministic score path: the machine score is recomputable from the
submission and the cases, while a teacher's score is a judgement. They are stored separately
(`b2.manual_grades`) so that "who produced this number" is answerable, and every change appends
a `b2.manual_grade_history` row -- a change without a reason is exactly the change nobody can
defend later.

Three rules the contract requires and this module is the only place that decides:

1. **Only a teacher/admin may grade.** `require_grade_authority` decides *scope* (course,
   section, and the "students only see themselves" rule); a student passing the scope check for
   their own submission must still be refused, because reading your grade is not grading it.
2. **A changed score needs a reason.** First grading with an empty reason is fine (the earlier
   `comment` is the explanation); overwriting an existing score without one is a 409, not a
   silent overwrite.
3. **`expected_updated_at` is an optimistic lock.** Two teachers opening the same submission
   must not have one silently undo the other; the stale writer gets 409 and re-reads.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, Engine, insert, select, update

from autograder_b2.auth.tokens import Principal
from autograder_b2.db.schema import (
    artifacts,
    intake_messages,
    manual_grade_history,
    manual_grades,
    submission_files,
    submissions,
)
from autograder_b2.grades.query import require_grade_authority

MAX_SCORE = Decimal(100)
MIN_SCORE = Decimal(0)

#: 角色白名单：能打分的是教师与管理员。助教/学生一律 403（见模块 docstring 第 1 条）。
GRADING_ROLES = frozenset({"teacher", "admin"})


class ManualGradingError(Exception):
    """基类：路由层据此映射状态码，不在路由里重写业务判断。"""


class SubmissionNotFound(ManualGradingError):
    """提交不存在（或不在调用者可支配的范围内，二者对外不可区分）。"""


class GradingForbidden(ManualGradingError):
    """调用者不是教师/管理员，或在课程范围之外（对外都是 403）。"""


class ManualGradeConflict(ManualGradingError):
    """乐观锁冲突，或改分缺少理由。"""


class InvalidManualGrade(ManualGradingError):
    """分数越界或格式非法。"""


@dataclass(frozen=True, slots=True)
class ManualGradeResult:
    submission_id: str
    score: float
    comment: str | None
    graded_by: str
    graded_at: datetime
    updated_at: datetime
    revision: int
    grade_status: str


def _now() -> datetime:
    return datetime.now(UTC)


def _millis(value: datetime) -> datetime:
    """与库里的 `timestamptz(3)` 对齐：不截断就会在比较时永远不相等。"""
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def _parse_score(score: float | str) -> Decimal:
    try:
        value = Decimal(str(score))
    except Exception as error:
        raise InvalidManualGrade("score must be a number") from error
    if value < MIN_SCORE or value > MAX_SCORE:
        raise InvalidManualGrade("score must be between 0 and 100")
    # 一位小数是契约的上限，多余精度直接拒绝而不是悄悄四舍五入。
    if -value.as_tuple().exponent > 1:
        raise InvalidManualGrade("score accepts at most one decimal place")
    return value


def _load_submission(
    connection: Connection, *, submission_id: str, principal: Principal
) -> tuple[str, str, str | None, str, datetime]:
    row = connection.execute(
        select(
            submissions.c.student_id,
            submissions.c.course_id,
            submissions.c.section_id,
            submissions.c.grade_status,
            submissions.c.updated_at,
        ).where(submissions.c.submission_id == submission_id)
    ).one_or_none()
    if row is None:
        raise SubmissionNotFound("The submission does not exist.")

    student_id, course_id, section_id, grade_status, updated_at = row
    if not GRADING_ROLES.intersection(principal.roles):
        # 先判角色：学生的越权请求不该走到"课程范围"这一步去试探。
        raise GradingForbidden("Only a teacher or an administrator may grade a submission.")
    require_grade_authority(
        principal,
        course_id=str(course_id),
        section_id=None if section_id is None else str(section_id),
        student_id=None,
    )
    return str(student_id), str(course_id), section_id, grade_status, updated_at


def set_manual_grade(
    engine: Engine,
    *,
    submission_id: str,
    score: float | str,
    comment: str | None,
    reason: str | None,
    expected_updated_at: datetime,
    principal: Principal,
) -> ManualGradeResult:
    """记录或修改一次人工评分；返回存下来的结果。"""
    parsed_score = _parse_score(score)
    now = _now()

    with engine.begin() as connection:
        _load_submission(connection, submission_id=submission_id, principal=principal)
        existing = connection.execute(
            select(
                manual_grades.c.manual_grade_id,
                manual_grades.c.score,
                manual_grades.c.updated_at,
                manual_grades.c.revision,
                manual_grades.c.graded_at,
            ).where(manual_grades.c.submission_id == submission_id)
        ).one_or_none()

        if existing is None:
            connection.execute(
                insert(manual_grades).values(
                    manual_grade_id=str(uuid.uuid4()),
                    submission_id=submission_id,
                    score=parsed_score,
                    comment=comment,
                    graded_by=principal.user_id,
                    graded_at=now,
                    updated_at=_millis(now),
                    revision=1,
                )
            )
            connection.execute(
                insert(manual_grade_history).values(
                    history_id=str(uuid.uuid4()),
                    submission_id=submission_id,
                    previous_score=None,
                    score=parsed_score,
                    reason=(reason or "first manual grade"),
                    graded_by=principal.user_id,
                    recorded_at=_millis(now),
                )
            )
            revision = 1
            graded_at = now
        else:
            manual_grade_id, previous_score, current_updated_at, revision, graded_at = existing
            if _millis(current_updated_at) != _millis(expected_updated_at):
                raise ManualGradeConflict(
                    "The grade changed since it was read; reload before saving."
                )
            if not (reason or "").strip():
                raise ManualGradeConflict("Changing a score requires a reason.")
            connection.execute(
                insert(manual_grade_history).values(
                    history_id=str(uuid.uuid4()),
                    submission_id=submission_id,
                    previous_score=previous_score,
                    score=parsed_score,
                    reason=reason.strip(),
                    graded_by=principal.user_id,
                    recorded_at=_millis(now),
                )
            )
            revision += 1
            connection.execute(
                update(manual_grades)
                .where(manual_grades.c.manual_grade_id == manual_grade_id)
                .values(
                    score=parsed_score,
                    comment=comment,
                    updated_at=_millis(now),
                    revision=revision,
                )
            )

        connection.execute(
            update(submissions)
            .where(submissions.c.submission_id == submission_id)
            .values(grade_status="graded", updated_at=_millis(now))
        )

    return ManualGradeResult(
        submission_id=submission_id,
        score=float(parsed_score),
        comment=comment,
        graded_by=principal.user_id,
        graded_at=graded_at,
        updated_at=_millis(now),
        revision=revision,
        grade_status="graded",
    )


def release_manual_grade(
    engine: Engine, *, submission_id: str, principal: Principal
) -> None:
    """撤回人工评分，回到 `awaiting_manual`；历史行保留（撤回本身也要能解释）。"""
    now = _now()
    with engine.begin() as connection:
        _load_submission(connection, submission_id=submission_id, principal=principal)
        existing = connection.execute(
            select(manual_grades.c.manual_grade_id, manual_grades.c.score).where(
                manual_grades.c.submission_id == submission_id
            )
        ).one_or_none()
        if existing is None:
            raise ManualGradeConflict("There is no manual grade to withdraw.")
        manual_grade_id, previous_score = existing
        connection.execute(
            insert(manual_grade_history).values(
                history_id=str(uuid.uuid4()),
                submission_id=submission_id,
                previous_score=previous_score,
                score=previous_score,
                reason="manual grade withdrawn",
                graded_by=principal.user_id,
                recorded_at=_millis(now),
            )
        )
        connection.execute(
            manual_grades.delete().where(manual_grades.c.manual_grade_id == manual_grade_id)
        )
        connection.execute(
            update(submissions)
            .where(submissions.c.submission_id == submission_id)
            .values(grade_status="awaiting_manual", updated_at=_millis(now))
        )


@dataclass(frozen=True, slots=True)
class IntakeMessage:
    subject: str | None
    sender: str
    received_at: datetime
    body_excerpt: str
    truncated: bool


def get_intake_message(
    engine: Engine, *, submission_id: str, principal: Principal
) -> IntakeMessage:
    with engine.begin() as connection:
        _load_submission(connection, submission_id=submission_id, principal=principal)
        row = connection.execute(
            select(
                intake_messages.c.subject,
                intake_messages.c.sender,
                intake_messages.c.received_at,
                intake_messages.c.body_excerpt,
                intake_messages.c.truncated,
            ).where(intake_messages.c.submission_id == submission_id)
        ).one_or_none()
    if row is None:
        raise SubmissionNotFound(
            "This submission was uploaded through the API; it has no intake message."
        )
    subject, sender, received_at, body_excerpt, truncated = row
    return IntakeMessage(
        subject=None if subject is None else str(subject),
        sender=str(sender),
        received_at=received_at,
        body_excerpt=str(body_excerpt),
        truncated=bool(truncated),
    )

def read_submission_file(
    engine: Engine,
    *,
    artifact_root: Path,
    submission_id: str,
    path: str,
    principal: Principal,
) -> tuple[Path, int]:
    """定位一个附件并校验它确实在该提交内、且落在存储根之下。

    两处校验缺一不可：**归属**（`submission_files` 里有这一行，调用者才能下载别的提交的文件）
    与**路径**（`resolve()` 之后必须仍在 `artifact_root` 下，否则 `../` 就能读到服务器任意文件）。
    返回 `(绝对路径, 字节数)`，实际读取由调用方做，以便先判大小上限。
    """
    with engine.begin() as connection:
        _load_submission(connection, submission_id=submission_id, principal=principal)
        row = connection.execute(
            select(artifacts.c.storage_path, artifacts.c.size_bytes)
            .select_from(
                submission_files.join(
                    artifacts, artifacts.c.artifact_id == submission_files.c.artifact_id
                )
            )
            .where(
                submission_files.c.submission_id == submission_id,
                submission_files.c.path == path,
            )
        ).one_or_none()

    if row is None:
        raise SubmissionNotFound("That file does not belong to this submission.")

    storage_path, size_bytes = row
    root = Path(artifact_root).resolve()
    candidate = (root / str(storage_path)).resolve()
    if not candidate.is_relative_to(root):
        # 路径逃逸：不是 404 的"找不到"，而是拒绝——这类请求值得出现在日志里。
        raise InvalidManualGrade("The stored artifact path is outside the artifact root.")
    return candidate, int(size_bytes)

def list_submission_files(
    engine: Engine, *, submission_id: str, principal: Principal
) -> list[str]:
    """该提交的文件路径列表（只返回路径，不返回内容）。

    与下载端点同一条授权路径：能看这条提交，才能看到它有哪些文件；否则"有没有附件"本身就是
    一条不该泄漏的信息。
    """
    with engine.begin() as connection:
        _load_submission(connection, submission_id=submission_id, principal=principal)
        rows = connection.execute(
            select(submission_files.c.path)
            .where(submission_files.c.submission_id == submission_id)
            .order_by(submission_files.c.path)
        ).all()
    return [str(row[0]) for row in rows]

def get_manual_grade(
    engine: Engine, *, submission_id: str, principal: Principal
) -> ManualGradeResult | None:
    """当前人工评分；`None` = 还没评过（不是错误）。

    抽屉需要它来区分"首评"与"改分"（改分必须写理由），也需要它提供 `updated_at` 作为乐观锁：
    让客户端自己拼这个值，就等于把锁交还给调用方。
    """
    with engine.begin() as connection:
        _load_submission(connection, submission_id=submission_id, principal=principal)
        row = connection.execute(
            select(
                manual_grades.c.score,
                manual_grades.c.comment,
                manual_grades.c.graded_by,
                manual_grades.c.graded_at,
                manual_grades.c.updated_at,
                manual_grades.c.revision,
            ).where(manual_grades.c.submission_id == submission_id)
        ).one_or_none()
    if row is None:
        return None
    score, comment, graded_by, graded_at, updated_at, revision = row
    return ManualGradeResult(
        submission_id=submission_id,
        score=float(score),
        comment=None if comment is None else str(comment),
        graded_by=str(graded_by),
        graded_at=graded_at,
        updated_at=updated_at,
        revision=int(revision),
        grade_status="graded",
    )
