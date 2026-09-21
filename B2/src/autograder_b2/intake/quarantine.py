"""隔离区复核的读路径与人工归属（`CHG-0014` 的两条 intake 端点）。

契约权威：`MAIN/contracts/openapi/b2.yaml` 的 `listIntakeQuarantines` / `resolveIntakeQuarantine`
与 `IntakeQuarantineView`。本模块是**服务层**（路由在 `intake.api`），只读写 `b2.intake_quarantines`。

## 为什么这个文件此前不存在

`b2.intake_quarantines` 自邮件入口建成起就在写（`intake.imap.QuarantineStore`），但**没有任何读出口**：
从教师的角度，未匹配的邮件"无声无息地不见了"（`CHG-0014` §1.1）。本模块就是那个出口。

## 人工归属：重跑哪些校验、为什么不重跑其余

契约要求手动归属"重跑与自动路径相同的校验（信封/摘要/作用域），只提供自动匹配没能推出的身份"。
在当前数据模型下，这句话**只能部分成立**，两部分都如实写在这里：

**能重跑、而且真的重跑了**：

1. **角色与作用域**（`auth.authorization.require_intake_quarantine_authority`）：学生一律 403；
   staff 令牌必须携带课程作用域。归属是一次复核决定，绝不因为"已经隔离了"就放宽准入。
2. **内容类闸门**：只有 `reason_code ∈ ATTRIBUTABLE_REASON_CODES`（`not_uniquely_matched` /
   `ambiguous_match`）的记录可以归属。这两条是**唯一**"身份没推出来"的失败——其余原因码全都意味着
   **报文本身**没通过自动路径的校验（体积超限、无 `Message-ID`、无附件、可执行附件、压缩炸弹、
   编码不支持……）。命名一个学生**修不好**这些，所以归属它们就是把"匹配器不确定"变成"什么都行"，
   正是 `CHG-0014` 禁止的那个后门。拒绝时对外 `422` 并在 `detail` 里指名 `reason_code`。
3. **结构**：`student_id` 必须是 UUID，`note` ≤ 500（契约 `maxLength`），未知字段一律拒绝。

**重跑不了、因此明确不做（契约缺口，已上报）**：

- **信封与摘要校验**：隔离记录**不存报文、不存附件字节、不存任何摘要**（平台有意不做第二个邮件仓库），
  所以 resolve 阶段没有任何可重算的摘要。要做到只能是"resolve 时回邮箱重新拉取该 UID 的报文再跑一遍
  完整校验"，而契约给 resolve 的响应集合是 401/403/404/409/422——**没有 503**——反证它不依赖外部
  邮箱（邮箱未配置时按项目纪律只能 503，那就是跳过校验）。
- **归属后的闭环**：契约说"归属后评测交给正常提交路径"。构造提交信封还需要
  `course_id`/`section_id`/`question_version_id`，而自动路径正是从匹配规则里取这三项；`not_matched`
  时规则表根本没得出任何身份，因此"只给一个 `student_id`"不足以重建信封。此外该 UID 的 checkpoint
  已按 `quarantined` 落库，下次拉取会直接跳过。**本模块不发明"归属即建提交"的路径**：那会绕开
  自动路径的身份判定，而契约明确要求归属与正常提交路径分开。

## 归属**不触发任何评测**（结构性保证，不是靠记得）

`resolve()` 的全部写入只有一条 `UPDATE b2.intake_quarantines` 语句：不建提交、不写
`submission_state_transitions`、不进 outbox、不调用编排层。`tests/test_intake_api.py` 用
"四张表与评估运行计数逐条为零"把这条钉住。

## 分页

契约的 `CursorPage` 明确禁止对大表用 offset 分页，本模块用 keyset 游标
`(created_at, intake_quarantine_id)`、**倒序**（契约要求"newest first"）。游标只承载"上一页最后一行
的位置"，不承载过滤条件与身份，因此伪造游标既不能越权也不能扩大范围。

为什么不复用 `grades.query` 的游标实现：那一个是 `(created_at, submission_id)` **升序**、且服务于
成绩行的 keyset 语义；两个游标的键与方向都不同，而 `intake` 反向依赖 `grades` 会把分层倒过来。
"""
from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine

from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.auth.authorization import require_intake_quarantine_authority
from autograder_b2.auth.tokens import Principal
from autograder_b2.db.schema import intake_quarantines

#: 契约 `Limit` 的默认值与边界（`common.yaml`）。
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_CURSOR_VERSION = 1
#: 契约 `IntakeQuarantineView.resolution_note.maxLength`。
MAX_RESOLUTION_NOTE = 500
#: 契约 `IntakeQuarantineView.detail.maxLength`（视图侧兜底；库列允许到 1000，见模块文档）。
MAX_VIEW_DETAIL = 500

#: **可以**被人工归属的原因码：只有"匹配不出唯一身份"这两种。
#:
#: 其余原因码（`message_too_large`/`missing_message_id`/`no_attachments`/`executable_attachment`/
#: `archive_bomb`/… 见 `intake.attachments.EmailIntakeReason`）都是**报文本身**被自动路径拒绝，
#: 归属无法修复它们；允许归属就等于给"人工"开一条绕过信封校验的通道。
ATTRIBUTABLE_REASON_CODES: tuple[str, ...] = ("not_uniquely_matched", "ambiguous_match")


class InvalidQuarantineFilterError(ValueError):
    """列表过滤参数不合契约（`limit` 越界、`reason_code` 超长、`resolved` 非布尔）→ 对外 422。"""


class InvalidQuarantineCursorError(ValueError):
    """游标不是本服务签发的形态 → 对外 422（**不**当第一页静默处理）。"""


class QuarantineNotFoundError(LookupError):
    """隔离记录不存在（标识非法与库里没有同为 404）→ 对外 404。"""


class QuarantineAlreadyResolvedError(RuntimeError):
    """该记录已经被归属过 → 对外 409（**不**覆盖上一个人的决定，也不静默返回旧值）。"""


class QuarantineNotAttributableError(ValueError):
    """该记录的原因码不是"匹配失败"，归属无法修复它 → 对外 422（人工路径不绕过信封校验）。"""


class QuarantineRequestError(ValueError):
    """请求体不合契约（`student_id` 非 UUID、`note` 超长）→ 对外 422。"""


@dataclass(frozen=True, slots=True)
class IntakeQuarantineView:
    """契约 `IntakeQuarantineView` 的一行（字段以契约为准，**不含**任何邮件正文/附件内容）。"""

    intake_quarantine_id: str
    intake: str
    reason_code: str
    detail: str
    mailbox: str
    message_id: str | None
    uid: int | None
    recorded_at: datetime
    resolved: bool
    resolved_student_id: str | None
    resolution_note: str | None


@dataclass(frozen=True, slots=True)
class IntakeQuarantinePage:
    """契约 `CursorPage` + `IntakeQuarantineView[]`：一页行 + 下一页游标。"""

    items: tuple[IntakeQuarantineView, ...]
    next_cursor: str | None


def quarantine_view_body(view: IntakeQuarantineView) -> dict[str, Any]:
    """契约 `IntakeQuarantineView` 的 JSON 形状（`additionalProperties: false`）。

    时间用毫秒 RFC 3339；`message_id`/`uid`/`resolved_student_id`/`resolution_note` 允许 `null`。
    """
    return {
        "intake_quarantine_id": view.intake_quarantine_id,
        "intake": view.intake,
        "reason_code": view.reason_code,
        "detail": view.detail,
        "mailbox": view.mailbox,
        "message_id": view.message_id,
        "uid": view.uid,
        "recorded_at": rfc3339_millis(view.recorded_at),
        "resolved": view.resolved,
        "resolved_student_id": view.resolved_student_id,
        "resolution_note": view.resolution_note,
    }


def quarantine_page_body(page: IntakeQuarantinePage) -> dict[str, Any]:
    """契约 `CursorPage` 的 JSON 形状：`{items, next_cursor}`。"""
    return {
        "items": [quarantine_view_body(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


def parse_resolved_filter(raw: str | None) -> bool | None:
    """解析 `?resolved=`：`true`/`false`（大小写不敏感）；空串=未提供；其余 → 422。

    契约把 `resolved` 声明为 `type: boolean` 且**没有** `default`（同一份文件里的 `Limit`/`view`
    都显式写了默认值），因此缺省时**不加过滤**——"未处理"只是契约描述里那句
    "the default view a teacher wants" 的提示，不是可机器判读的默认值。这一点作为契约含糊项已上报。
    """
    if raw is None or raw == "":
        return None
    lowered = raw.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise InvalidQuarantineFilterError("resolved must be true or false")


def parse_limit(raw: str | None) -> int:
    """解析 `?limit=`；范围由契约 `Limit` 决定（1..200，默认 50）。"""
    if raw is None or raw == "":
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except ValueError as error:
        raise InvalidQuarantineFilterError(
            f"limit must be an integer between 1 and {MAX_LIMIT}"
        ) from error
    if not 1 <= value <= MAX_LIMIT:
        raise InvalidQuarantineFilterError(f"limit must be between 1 and {MAX_LIMIT}")
    return value


def parse_reason_code_filter(raw: str | None) -> str | None:
    """解析 `?reason_code=`：空串=未提供；长于契约 `maxLength: 64` → 422。"""
    if raw is None or raw == "":
        return None
    if len(raw) > 64:
        raise InvalidQuarantineFilterError("reason_code must be at most 64 characters")
    return raw


def encode_cursor(*, recorded_at: datetime, intake_quarantine_id: str) -> str:
    """把"上一页最后一行"编码为不透明游标（微秒级 ISO 8601，与库里的 `timestamptz` 精度一致）。

    不用对外的毫秒 RFC 3339：游标是**严格后继**判定，毫秒截断会把同一毫秒内尚未越过的行判成已越过
    （重复返回）或反之（跳过）。
    """
    moment = recorded_at.astimezone(UTC) if recorded_at.tzinfo else recorded_at.replace(tzinfo=UTC)
    payload = json.dumps(
        {"v": _CURSOR_VERSION, "at": moment.isoformat(), "id": str(intake_quarantine_id)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """解析游标 → `(recorded_at, intake_quarantine_id)`；形态非法一律抛 `InvalidQuarantineCursorError`。"""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, UnicodeEncodeError, ValueError) as error:
        raise InvalidQuarantineCursorError("cursor is not a valid base64url JSON token") from error
    if not isinstance(payload, dict):
        raise InvalidQuarantineCursorError("cursor payload must be a JSON object")
    version = payload.get("v")
    if isinstance(version, bool) or not isinstance(version, int) or version != _CURSOR_VERSION:
        raise InvalidQuarantineCursorError("unsupported cursor version")
    moment, identifier = payload.get("at"), payload.get("id")
    if not isinstance(moment, str) or not isinstance(identifier, str):
        raise InvalidQuarantineCursorError("cursor is missing its position")
    try:
        parsed = datetime.fromisoformat(moment)
    except ValueError as error:
        raise InvalidQuarantineCursorError("cursor position is not a timestamp") from error
    if parsed.tzinfo is None:
        raise InvalidQuarantineCursorError("cursor position must carry a timezone")
    try:
        return parsed.astimezone(UTC), uuid.UUID(identifier)
    except ValueError as error:
        raise InvalidQuarantineCursorError("cursor position is not a quarantine id") from error


def validate_resolution_request(*, student_id: object, note: object) -> tuple[str, str | None]:
    """请求体校验（契约 `additionalProperties: false` 由路由层判字段集）。

    返回 `(规范化后的 student_id, note)`；`student_id` 必须是 UUID（契约 `Uuid`），
    `note` 允许省略/`null`/空串（空串按"未提供"处理），长度不得超过契约的 500。
    """
    try:
        canonical_student = str(uuid.UUID(str(student_id)))
    except (AttributeError, TypeError, ValueError) as error:
        raise QuarantineRequestError("student_id must be a UUID") from error
    if note is None:
        return canonical_student, None
    if not isinstance(note, str):
        raise QuarantineRequestError("note must be a string or null")
    if note == "":
        return canonical_student, None
    if len(note) > MAX_RESOLUTION_NOTE:
        raise QuarantineRequestError(
            f"note must be at most {MAX_RESOLUTION_NOTE} characters"
        )
    return canonical_student, note


class IntakeQuarantineService:
    """隔离记录的读取与人工归属（`listIntakeQuarantines` / `resolveIntakeQuarantine` 的服务层）。

    实例不持有调用者身份：`principal` 逐次传入，作用域判定每次重算（不存在"实例缓存了上一个调用者
    的作用域"的风险）。
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- 列表 -------------------------------------------------------------------------

    def list_quarantines(
        self,
        *,
        principal: Principal,
        reason_code: str | None = None,
        resolved: bool | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> IntakeQuarantinePage:
        """按时间倒序取一页隔离记录。

        异常：`AuthorizationError` → 路由层 403；`InvalidQuarantineFilterError` /
        `InvalidQuarantineCursorError` → 422。`next_cursor` 非空即表示**还有下一页**。
        """
        require_intake_quarantine_authority(principal)
        size = _validated_limit(limit)
        reason = parse_reason_code_filter(reason_code)
        after = None if cursor is None else decode_cursor(cursor)
        with self._engine.connect() as connection:
            rows = connection.execute(
                _page_statement(reason_code=reason, resolved=resolved, after=after, limit=size + 1)
            ).fetchall()
        items = tuple(_view_of(row) for row in rows[:size])
        next_cursor: str | None = None
        if len(rows) > size and items:
            last = items[-1]
            next_cursor = encode_cursor(
                recorded_at=last.recorded_at, intake_quarantine_id=last.intake_quarantine_id
            )
        return IntakeQuarantinePage(items=items, next_cursor=next_cursor)

    # --- 人工归属 ---------------------------------------------------------------------

    def resolve(
        self,
        *,
        principal: Principal,
        intake_quarantine_id: str,
        student_id: str,
        note: str | None = None,
    ) -> IntakeQuarantineView:
        """把一条隔离记录归属给某个学生；**只写这一行**，不建提交、不触发评测。

        异常：`AuthorizationError` → 403；`QuarantineNotFoundError` → 404；
        `QuarantineAlreadyResolvedError` → 409；`QuarantineNotAttributableError` /
        `QuarantineRequestError` → 422。
        """
        require_intake_quarantine_authority(principal)
        canonical_student, canonical_note = validate_resolution_request(
            student_id=student_id, note=note
        )
        try:
            identifier = uuid.UUID(intake_quarantine_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise QuarantineNotFoundError("malformed quarantine id") from error

        with self._engine.begin() as connection:
            # 行锁把"检查已归属"与"写入归属"变成一次原子判定：并发两次归属只会有一个成功，
            # 另一个拿到 409 而不是覆盖前一个人的决定。
            row = connection.execute(
                sa.select(
                    intake_quarantines.c.reason_code,
                    intake_quarantines.c.resolved,
                )
                .where(intake_quarantines.c.intake_quarantine_id == identifier)
                .with_for_update()
            ).fetchone()
            if row is None:
                raise QuarantineNotFoundError(str(identifier))
            if bool(row.resolved):
                raise QuarantineAlreadyResolvedError(str(identifier))
            reason_code = str(row.reason_code)
            if reason_code not in ATTRIBUTABLE_REASON_CODES:
                raise QuarantineNotAttributableError(reason_code)
            connection.execute(
                sa.update(intake_quarantines)
                .where(intake_quarantines.c.intake_quarantine_id == identifier)
                .values(
                    resolved=True,
                    resolved_student_id=uuid.UUID(canonical_student),
                    resolution_note=canonical_note,
                )
            )
            updated = connection.execute(
                _select_view().where(intake_quarantines.c.intake_quarantine_id == identifier)
            ).fetchone()
        assert updated is not None  # 同一事务内刚更新过这一行
        return _view_of(updated)


# --- SQL ----------------------------------------------------------------------------------


def _select_view() -> sa.Select[Any]:
    return sa.select(
        intake_quarantines.c.intake_quarantine_id,
        intake_quarantines.c.intake,
        intake_quarantines.c.reason_code,
        intake_quarantines.c.detail,
        intake_quarantines.c.mailbox,
        intake_quarantines.c.message_id,
        intake_quarantines.c.uid,
        intake_quarantines.c.created_at,
        intake_quarantines.c.resolved,
        intake_quarantines.c.resolved_student_id,
        intake_quarantines.c.resolution_note,
    )


def _page_statement(
    *,
    reason_code: str | None,
    resolved: bool | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> sa.Select[Any]:
    """页面查询：时间倒序 + 主键兜底（同毫秒的行必须有全序，keyset 判定才有唯一解）。"""
    statement = (
        _select_view()
        .order_by(
            intake_quarantines.c.created_at.desc(),
            intake_quarantines.c.intake_quarantine_id.desc(),
        )
        .limit(limit)
    )
    if reason_code is not None:
        statement = statement.where(intake_quarantines.c.reason_code == reason_code)
    if resolved is not None:
        statement = statement.where(intake_quarantines.c.resolved.is_(resolved))
    if after is not None:
        moment, identifier = after
        statement = statement.where(
            sa.or_(
                intake_quarantines.c.created_at < moment,
                sa.and_(
                    intake_quarantines.c.created_at == moment,
                    intake_quarantines.c.intake_quarantine_id < identifier,
                ),
            )
        )
    return statement


def _view_of(row: Any) -> IntakeQuarantineView:
    """记录 → 契约视图（只做形状映射与两个上限兜底，不做任何内容裁切）。"""
    detail = str(row.detail)
    if len(detail) > MAX_VIEW_DETAIL:
        # 库列上限是 1000（写入侧 `_bounded_detail`），契约视图上限是 500：视野内以契约上限为准。
        detail = detail[:MAX_VIEW_DETAIL]
    return IntakeQuarantineView(
        intake_quarantine_id=str(row.intake_quarantine_id),
        intake=str(row.intake),
        # 写入侧 `QuarantineStore.record` 必填 mailbox；库里若出现 NULL（手工写入的行），
        # 契约的 `mailbox` 是必填字符串，故回落成空串而不是 `null`（违反 schema 更坏）。
        mailbox="" if row.mailbox is None else str(row.mailbox),
        reason_code=str(row.reason_code),
        detail=detail,
        message_id=None if row.message_id is None else str(row.message_id),
        uid=None if row.uid is None else int(row.uid),
        recorded_at=row.created_at,
        resolved=bool(row.resolved),
        resolved_student_id=(
            None if row.resolved_student_id is None else str(row.resolved_student_id)
        ),
        resolution_note=None if row.resolution_note is None else str(row.resolution_note),
    )


def _validated_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise InvalidQuarantineFilterError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


__all__ = [
    "ATTRIBUTABLE_REASON_CODES",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_RESOLUTION_NOTE",
    "IntakeQuarantinePage",
    "IntakeQuarantineService",
    "IntakeQuarantineView",
    "InvalidQuarantineCursorError",
    "InvalidQuarantineFilterError",
    "QuarantineAlreadyResolvedError",
    "QuarantineNotAttributableError",
    "QuarantineNotFoundError",
    "QuarantineRequestError",
    "decode_cursor",
    "encode_cursor",
    "parse_limit",
    "parse_reason_code_filter",
    "parse_resolved_filter",
    "quarantine_page_body",
    "quarantine_view_body",
    "validate_resolution_request",
]
