"""提交查询：所有权校验 + 状态机视图（`GET /api/v1/submissions/{submission_id}`）。

授权规则（三层独立成立，`MAIN/docs/architecture/security.md` §3.6）：

| 情形 | 结果 |
|---|---|
| 提交不存在 | `404`（已认证；不向调用方透露他人资源是否存在之外的信息） |
| 存在，但调用者无权读 | `403`（**不得**用 `404` 掩盖） |
| 学生读**本人**提交 | `200` |
| 教师/助教在其课程作用域内 | `200` |
| 教师跨课程 / 课序不符 | `403` |
| 管理员 | `200` |

视图只含元数据：文件**路径 + 摘要 + 字节数**，**不含任何文件内容**（内容读取是另一个受权端点的事）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Engine

from autograder_b2.auth.authorization import require_submission_read
from autograder_b2.auth.tokens import Principal
from autograder_b2.db.schema import artifacts, evaluation_runs, submission_files, submissions
from autograder_b2.domain.transitions import history_of


class SubmissionNotFoundError(LookupError):
    """提交不存在（已认证且有权询问）→ 对外 404。"""


@dataclass(frozen=True, slots=True)
class SubmissionFileView:
    path: str
    content_sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class StateTransitionView:
    from_state: str | None
    to_state: str
    occurred_at: datetime
    reason: str | None


@dataclass(frozen=True, slots=True)
class SubmissionView:
    submission_id: str
    student_id: str
    course_id: str
    section_id: str
    question_version_id: str
    #: 该提交回答的作业（CHG-0032）。`None` 只属于历史提交：它们没有作业归属，不猜测、不回填。
    assignment_id: str | None
    state: str
    intake: str
    files: tuple[SubmissionFileView, ...]
    state_history: tuple[StateTransitionView, ...]
    created_at: datetime
    #: The evaluation run once the orchestrator has created it, else None.
    #:
    #: Present because the run id is created *after* acceptance: the `202` from
    #: `POST /submissions` legitimately carries `evaluation_run_id: null`, and the
    #: progress stream is addressed by run id. Without this field a client has no way to
    #: get from a submission to its run and the real-time progress feature is
    #: unreachable (found during browser integration).
    evaluation_run_id: str | None = None


class SubmissionQueryService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, submission_id: str, *, principal: Principal) -> SubmissionView:
        """读取提交视图；不存在抛 `SubmissionNotFoundError`，无权读抛 `AuthorizationError`。"""
        try:
            identifier = uuid.UUID(submission_id)
        except ValueError as error:
            raise SubmissionNotFoundError("malformed submission id") from error

        with self._engine.connect() as connection:
            # 一个视图由三次查询组成（主体、文件、迁移史），REPEATABLE READ 让它们看到**同一个**
            # 快照：否则并发迁移（如编排层把提交推进到 queued）会让 state 与 state_history 互相矛盾。
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
            row = connection.execute(
                sa.select(
                    submissions.c.submission_id,
                    submissions.c.student_id,
                    submissions.c.course_id,
                    submissions.c.section_id,
                    submissions.c.question_version_id,
                    submissions.c.assignment_id,
                    submissions.c.state,
                    submissions.c.intake,
                    submissions.c.created_at,
                ).where(submissions.c.submission_id == identifier)
            ).fetchone()
            if row is None:
                raise SubmissionNotFoundError(str(identifier))

            require_submission_read(
                principal,
                course_id=str(row[2]),
                section_id=str(row[3]),
                student_id=str(row[1]),
            )

            file_rows = connection.execute(
                sa.select(
                    submission_files.c.path,
                    artifacts.c.content_sha256,
                    artifacts.c.size_bytes,
                )
                .select_from(
                    submission_files.join(
                        artifacts, submission_files.c.artifact_id == artifacts.c.artifact_id
                    )
                )
                .where(submission_files.c.submission_id == identifier)
                .order_by(submission_files.c.path)
            ).fetchall()
            history = history_of(connection, str(identifier))
            run_row = connection.execute(
                sa.select(evaluation_runs.c.evaluation_run_id)
                .where(evaluation_runs.c.submission_id == identifier)
                .order_by(evaluation_runs.c.created_at.desc())
                .limit(1)
            ).fetchone()

        return SubmissionView(
            submission_id=str(row[0]),
            student_id=str(row[1]),
            course_id=str(row[2]),
            section_id=str(row[3]),
            question_version_id=str(row[4]),
            assignment_id=None if row[5] is None else str(row[5]),
            state=str(row[6]),
            intake=str(row[7]),
            files=tuple(
                SubmissionFileView(
                    path=str(item[0]), content_sha256=str(item[1]), size_bytes=int(item[2])
                )
                for item in file_rows
            ),
            state_history=tuple(
                StateTransitionView(
                    from_state=entry["from_state"],  # type: ignore[arg-type]
                    to_state=str(entry["to_state"]),
                    occurred_at=entry["occurred_at"],  # type: ignore[arg-type]
                    reason=entry["reason"],  # type: ignore[arg-type]
                )
                for entry in history
            ),
            created_at=row[8],
            evaluation_run_id=str(run_row[0]) if run_row is not None else None,
        )


#: 读权限判定的唯一实现在 `auth.authorization.require_submission_read`（读提交元数据、附件内容、
#: 来信摘录三个端点共用同一层规则）。本模块此前自持一份同义私有函数，已删除以免两处漂移。
