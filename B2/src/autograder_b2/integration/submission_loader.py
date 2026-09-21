"""从 `b2` 库里的行重建冻结的 `SubmissionEnvelope`（评测阶段的**唯一**输入路径）。

存在的唯一理由：冻结信封必须**可往返**。`SubmissionEnvelope` 是 API 与邮件两条入口归一化后的唯一
内部形态，而评测编排器发事件时必须带 `submission.correlation_id`（`outbox_events.correlation_id`
是 `NOT NULL`）。所以"接收并存储"只有在"能按同一个类型读回来"之后才成立——本模块就是那条读回来的
路径（迁移 `20260915_0008` 补上了 `correlation_id` 与 provenance 这两类此前根本没落库的字段，
本模块才可能存在）。

本模块**不做**什么：

- **不落盘、不做任何写操作**：只有只读 SELECT，且只碰 `b2` schema；
- **不执行任何东西**：不导入 `runner`/`evaluation`/`grades`/`intake` 等业务模块（依赖面只有
  `domain/*` 冻结类型、`db/schema.py` 表映像，以及 `storage.paths` 的纯路径派生规则）；
- **不做授权判定**：谁能读这份提交由调用方在路由层决定——本模块不知道调用者是谁，
  所以它绝不能变成"绕过鉴权读提交"的通道；
- **不修数据**：不一致就抛错，不猜、不补、不静默纠正。

自校验的语义（为什么必须有）：装载器是评测的唯一输入。若它静默装出一个与落库内容不一致的信封，
"同一输入必得同一分数"这条不变量就成了谎话。因此装载时重算两件事并当场比对：

1. `submission_digest`：按写入端同一条规则（`"<path> <digest>"` 行按 path 排序后以换行连接、
   再取 SHA-256，见 `intake/service.py:submission_digest_of`）在**装载侧独立重算**，与库里的值不等
   即抛 `SubmissionDigestMismatch`。这里刻意不在装载侧调用写入端的函数：独立重算才谈得上自校验
   （代价是同一条规则有两处实现，因此重算规则一旦漂移就会**当场失败**而不是静默通过）；
2. 每个文件的 `storage_ref` 与 `content_sha256` 的派生关系：`artifacts.storage_path` 必须等于
   `storage_path_for(content_sha256)`（即 `ab/cd/<完整摘要>`）。不满足即 `SubmissionInconsistent`——
   否则装载出来的信封会指向一份"按摘要取不到"的内容。

文件顺序：按 `submission_files.path` 升序。信封里的 `files` 是有序元组，而库里并不存在"原始上传
顺序"这一事实，因此装载顺序必须由一条**确定的**规则派生：按 path 排序保证同一行数据永远装出同一个
信封（可重放），而不是依赖 PostgreSQL 的返回顺序。

时间：`received_at` 取 `submissions.created_at`。这是**同一个事实**——接收路径把契约里命名的接收
时刻直接写进 `created_at`（`intake/service.py`：`accepted_at = envelope.received_at`，随后
`created_at=accepted_at`）。契约把这一时刻命名为 `created_at`，本模块不为它引入第二个名字或别名。
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import Connection

from autograder_b2.db.schema import artifacts, submission_files, submissions
from autograder_b2.domain.clock import sha256_hex
from autograder_b2.domain.envelope import (
    ApiProvenance,
    EmailProvenance,
    EnvelopeFile,
    IntakeProvenance,
    SubmissionEnvelope,
    redact_path_for_message,
)
from autograder_b2.storage.paths import InvalidDigestError, storage_path_for

#: 装载信封所需的**全部**提交列（多取一列就等于把"库里有什么"重新定义一遍，少取一列就装不出类型）。
_SUBMISSION_COLUMNS = (
    submissions.c.submission_id,
    submissions.c.student_id,
    submissions.c.course_id,
    submissions.c.section_id,
    submissions.c.question_version_id,
    submissions.c.idempotency_key,
    submissions.c.intake,
    submissions.c.correlation_id,
    submissions.c.request_id,
    submissions.c.mailbox,
    submissions.c.uidvalidity,
    submissions.c.uid,
    submissions.c.message_id,
    submissions.c.submission_digest,
    submissions.c.created_at,
)


class SubmissionLoadError(Exception):
    """装载冻结信封失败（本模块所有失败的基类）。"""


class SubmissionNotFound(SubmissionLoadError):
    """库里没有这条提交。

    刻意**不返回 `None`**：`None` 会让调用方无法区分"没有这份提交"与"装载失败"，于是最省事的写法
    就是两者都当成"空"处理——那正是把"未检查"说成"安全"的同一种错。
    """


class SubmissionDigestMismatch(SubmissionLoadError):
    """重算出的 `submission_digest` 与库里的值不一致：内容与摘要已经对不上，绝不能拿去评测。"""


class SubmissionInconsistent(SubmissionLoadError):
    """行与冻结信封的形态不符（provenance 缺列、文件清单为空、存储引用与摘要的派生关系被破坏等）。

    这些情形在正常写入路径下不可达（数据库 CHECK/外键与写入端共同保证）。真出现就说明行被绕过写入了
    或约束被摘掉了，因此只报告事实、不尝试修复。
    """


def load_submission_envelope(connection: Connection, submission_id: str) -> SubmissionEnvelope:
    """把 `b2.submissions` 的一行（连同文件清单）重建成冻结的 `SubmissionEnvelope`。

    `connection` 是只读使用的 SQLAlchemy Core `Connection`（事务与生命周期由调用方持有：本模块
    不开事务、不提交、不回滚）。找不到提交抛 `SubmissionNotFound`；装出来的信封自校验不过抛
    `SubmissionDigestMismatch` / `SubmissionInconsistent`。
    """
    row = connection.execute(
        sa.select(*_SUBMISSION_COLUMNS).where(submissions.c.submission_id == _as_uuid(submission_id))
    ).one_or_none()
    if row is None:
        raise SubmissionNotFound("no submission with this identifier exists")

    files = tuple(_load_files(connection, row.submission_id))
    if not files:
        # 写入端从不产生空文件集（`empty_fileset` 是稳定拒绝原因），所以这行不是一份合法提交。
        raise SubmissionInconsistent("submission has no files; an envelope requires at least one file")

    digest = _recompute_submission_digest(files)
    if digest != row.submission_digest:
        raise SubmissionDigestMismatch(
            "recomputed submission_digest does not match the stored value;"
            " the stored file list and the stored digest disagree"
        )

    return SubmissionEnvelope(
        submission_id=str(row.submission_id),
        student_id=str(row.student_id),
        course_id=str(row.course_id),
        section_id=str(row.section_id),
        question_version_id=str(row.question_version_id),
        idempotency_key=row.idempotency_key,
        intake=row.intake,
        provenance=_provenance_from_row(row),
        correlation_id=str(row.correlation_id),
        files=files,
        submission_digest=row.submission_digest,
        received_at=row.created_at,
    )


def _as_uuid(submission_id: str) -> uuid.UUID:
    """提交标识按 UUID 解析；解析不了就不存在这样一份提交（不把解析失败伪装成别的错误）。"""
    try:
        return uuid.UUID(submission_id)
    except ValueError as error:
        raise SubmissionNotFound("submission identifier is not a UUID") from error


def _load_files(connection: Connection, submission_id: uuid.UUID) -> list[EnvelopeFile]:
    """按 `path` 升序读出文件清单，并从 `artifacts` 取摘要/字节数/存储引用。

    `submission_files` 只存 `(submission_id, path, artifact_id)`——摘要与字节数的唯一来源是
    `artifacts`（"同一事实不两处存放"，迁移 0002 的约束意图），本函数据此做内连接取值，
    并逐项校验存储引用确实是按摘要派生的。
    """
    rows = connection.execute(
        sa.select(
            submission_files.c.path,
            artifacts.c.content_sha256,
            artifacts.c.size_bytes,
            artifacts.c.storage_path,
        )
        .select_from(
            submission_files.join(
                artifacts, submission_files.c.artifact_id == artifacts.c.artifact_id
            )
        )
        .where(submission_files.c.submission_id == submission_id)
        .order_by(submission_files.c.path)
    ).fetchall()

    files: list[EnvelopeFile] = []
    for path, content_sha256, size_bytes, storage_path in rows:
        try:
            derived = storage_path_for(content_sha256)
        except InvalidDigestError as error:
            raise SubmissionInconsistent(
                f"stored artifact digest for file {redact_path_for_message(path)}"
                " is not a usable digest"
            ) from error
        if storage_path != derived:
            raise SubmissionInconsistent(
                f"stored storage reference for file {redact_path_for_message(path)}"
                " is not derived from its content digest"
            )
        files.append(
            EnvelopeFile(
                path=path,
                content_sha256=content_sha256,
                size_bytes=int(size_bytes),
                storage_ref=storage_path,
            )
        )
    return files


def _provenance_from_row(row: sa.Row) -> IntakeProvenance:
    """把 provenance 列按 `intake` 取值还原成冻结联合类型（显式分支，不用 `getattr` 猜）。

    这里只检查"构造该形态所必需的值是否存在"；"intake 与形态互斥"这条规则由数据库 CHECK
    `ck_submissions_provenance_shape` 保证，本模块不再实现第二遍（同一条规则两处实现必然分叉）。
    """
    if row.intake == "api":
        if row.request_id is None:
            raise SubmissionInconsistent("api submission row has no request_id")
        return ApiProvenance(request_id=row.request_id)
    if row.intake == "email":
        missing = [
            name
            for name, value in (
                ("mailbox", row.mailbox),
                ("uidvalidity", row.uidvalidity),
                ("uid", row.uid),
                ("message_id", row.message_id),
            )
            if value is None
        ]
        if missing:
            raise SubmissionInconsistent(
                "email submission row is missing provenance fields: " + ", ".join(missing)
            )
        return EmailProvenance(
            mailbox=row.mailbox,
            uidvalidity=int(row.uidvalidity),
            uid=int(row.uid),
            message_id=row.message_id,
        )
    # `ck_submissions_intake` 只放行 api/email，因此这条分支在正常库里不可达。
    raise SubmissionInconsistent(f"unknown intake value on submission row: {row.intake!r}")


def _recompute_submission_digest(files: tuple[EnvelopeFile, ...]) -> str:
    """独立重算五项绑定摘要之一（规则与写入端一致：`"<path> <digest>"` 行按 path 排序后取 SHA-256）。"""
    canonical = "\n".join(
        f"{path} {digest}" for path, digest in sorted((item.path, item.content_sha256) for item in files)
    )
    return sha256_hex(canonical.encode("utf-8"))
