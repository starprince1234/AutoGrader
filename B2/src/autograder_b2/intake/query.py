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

from autograder_b2.auth.authorization import AuthorizationError
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
                    submissions.c.state,
                    submissions.c.intake,
                    submissions.c.created_at,
                ).where(submissions.c.submission_id == identifier)
            ).fetchone()
            if row is None:
                raise SubmissionNotFoundError(str(identifier))

            _require_read_authority(
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
            state=str(row[5]),
            intake=str(row[6]),
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
            created_at=row[7],
            evaluation_run_id=str(run_row[0]) if run_row is not None else None,
        )


def _require_read_authority(
    principal: Principal, *, course_id: str, section_id: str, student_id: str
) -> None:
    """读权限判定：**角色**与**课程作用域**是独立两层，即便读自己的数据也必须满足。

    顺序有意如此：角色 → 作用域 → 身份。`identifiers.md` §3 要求三层同时成立，因此
    "用只覆盖课程 A 的令牌读课程 B 里自己的提交"同样是 `403`，而"没有任何已登记角色的令牌"
    连自己的数据也不能读（认证 ≠ 授权）。

    **门禁顺序的说明（与 `MAIN/docs/architecture/models.md` §4 的箭头图）**：该图给出的是
    通用判定序列"认证 → 角色 → 作用域 → 存在性"。本端点的课程作用域**只能**来自被读资源的
    归属，而请求路径里没有 `course_id`，因此必须先把记录读出来才知道该比对哪个作用域。
    结果是：记录存在但越权 → `403`；记录不存在 → `404`（`identifiers.md` §3 明确"有权限但资源
    不存在 → 404"）。两者无法同时消除一个副作用：越权调用者能区分"存在/不存在"。
    本模块选择服从本模块验收标准（越权必须 403，不得用 404 掩盖），并把该取舍如实记录在此。
    """
    if principal.is_admin:
        return
    if not principal.has_recognized_role:
        raise AuthorizationError("the token carries no recognized platform role")
    if not principal.covers_course(course_id, section_id):
        raise AuthorizationError("course scope does not cover this submission")
    if student_id == principal.user_id:
        return
    if principal.is_course_staff:
        return
    raise AuthorizationError("the caller is not permitted to read this submission")
