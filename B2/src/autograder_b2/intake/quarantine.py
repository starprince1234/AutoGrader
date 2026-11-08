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
- **归属后的闭环**：自动路径构造提交信封需要 `assignment_id` + 题目映射，而"归属"只补一个学生
  **推不出来**；`complete`（匹配用户）是后来补上的完整闭环：教师显式选作业，取回原邮件、按附件
  名（`N.txt`）映射逐题走与自动路径**同一个** `build_submission_envelope` + `persist`，
  并且只在**全部题目成功**后才把这一行标记 `completed`。`resolve` 仍然保留"只记归属不建提交"
  的语义（设计 §6.5 明确要求），两者是不同动作。

## 归属**不触发任何评测**（结构性保证，不是靠记得）

`resolve()` 的全部写入只有一条 `UPDATE b2.intake_quarantines` 语句：不建提交、不写
`submission_state_transitions`、不进 outbox、不调用编排层。`tests/test_intake_api.py` 用
"四张表与评估运行计数逐条为零"把这条钉住。

## 行级课程范围（P13 的越权修复）

隔离记录从迁移 `20260915_0015` 起带 `scope_course_id/scope_section_id`（成对为空或非空）。
这两个字段的**唯一**来源是**一次性核验码挑战**（服务端签发事实：挑战本身绑定学生/课程/课序/
作业），不是本模块从请求、邮箱或身份里推出来的。据此本模块把授权做到行级：

- **列表**：`require_intake_quarantine_authority` 只判"角色 + staff 至少一个课程作用域"这一层；
  真正的行级过滤是 `_scope_predicate(principal)` 翻译出的 SQL 条件：staff 只保留
  `scope_course_id/scope_section_id` 被某个令牌作用域覆盖的行，NULL scope 行对 staff 不可见；
  管理员不加限制（未关联行只出现在管理员视图）。谓词与用户的 `course_id/section_id` 筛选一起
  施加在 `ORDER BY`/`LIMIT`/游标判定**之前**，因此分页、`next_cursor` 与响应都不会暗示
  存在被隐藏的行。
- **查询参数**：`course_id/section_id` 只缩小当前调用者可见的范围，不是授权依据。staff 传入
  令牌作用域之外的课程/课序得到 `403`（而不是"空列表"）；不带筛选时仍然只看自己 scope 内的行。
- **归属**：`resolve` 在 `SELECT ... FOR UPDATE` 锁住记录之后调用
  `require_quarantine_row_authority`，对**这一行**再判一次：staff 对 NULL scope 或跨课记录
  一律 `403`；管理员可处置未关联行。写入只发生在授权通过之后，不存在"先按旧值授权、再用新值
  写入"的窗口。

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
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.auth.authorization import (
    AuthorizationError,
    require_intake_quarantine_authority,
    require_quarantine_row_authority,
)
from autograder_b2.auth.tokens import Principal
from autograder_b2.db.schema import intake_quarantines
from autograder_b2.intake.attachments import AttachmentRejected
from autograder_b2.intake.imap import ImapProtocolError
from autograder_b2.integration.assignments import AssignmentUnavailable
from autograder_b2.integration.b4_authorization import (
    AuthorizationUnavailable,
    UserAuthorizationDirectory,
)

if TYPE_CHECKING:
    from autograder_b2.intake.imap import EmailIntakeService

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


class QuarantineEmailUnavailable(RuntimeError):
    """匹配用户需要回邮箱取原邮件，但本部署没有配置邮件入口 → 对外 503。"""


class QuarantineAssignmentUnavailable(RuntimeError):
    """匹配用户需要作业受众，但本部署没有配置/连不上作业目录 → 对外 503（系统故障，不是学生错）。"""


class QuarantineAuthorizationUnavailable(RuntimeError):
    """匹配用户需要 B4 权威归属，但授权目录没有配置/连不上 → 对外 503（系统故障，不是学生错）。"""


class QuarantineMessageGone(RuntimeError):
    """原邮件已不在邮箱里（UIDVALIDITY 变化/被删除/取信失败）→ 对外 409。"""


class QuarantineContentRejected(ValueError):
    """原邮件按当前策略仍不能作为提交（附件被拒/报文畸形）→ 对外 422。"""


class QuarantineTargetMismatch(ValueError):
    """选择的课程/课序/作业与记录、或与作业受众不一致 → 对外 422。"""


@dataclass(frozen=True, slots=True)
class CompletionTarget:
    """匹配用户时的目标身份（全部由调用方显式选择，不从邮件内容推断）。

    设计 §6.5：作业模型下人工复核选定的是**一个作业**，题目由附件名（`N.txt`）映射，
    不再手工输入 `question_version_id`。
    """

    student_id: str
    course_id: str
    section_id: str
    assignment_id: str


@dataclass(frozen=True, slots=True)
class IntakeQuarantineView:
    """契约 `IntakeQuarantineView` 的一行（字段以契约为准，**不含**任何邮件正文/附件内容）。

    `subject` 是例外中的**主题头**（有界、可打印、≤200 字），只用于让老师认出邮件；它仍不是正文，
    也不参与任何授权判定。`resolution`/`resolved_at`/`resolved_by` 是人工处置留痕（0016）。
    """

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
    #: 核验码挑战（服务端签发事实）给出的课程/课序；成对为空（未关联）或非空。未关联行只对管理员可见。
    scope_course_id: str | None = None
    scope_section_id: str | None = None
    #: 邮件主题头（有界、无正文）；历史行为 `None`。
    subject: str | None = None
    #: 人工处置结果：`attributed`（旧人工归属）/`completed`（匹配用户并生成提交）/
    #: `dismissed`（标记作废）/`None`（未处理或历史遗留）。
    resolution: str | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    #: 匹配用户后生成的提交；只有 `resolution='completed'` 非空。
    resolved_submission_id: str | None = None
    #: 暂存：仍未处理，稍后再决定；与 `resolved` 互斥。
    held: bool = False
    held_at: datetime | None = None
    held_by: str | None = None


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
        "scope_course_id": view.scope_course_id,
        "scope_section_id": view.scope_section_id,
        "subject": view.subject,
        "resolution": view.resolution,
        "resolved_at": None if view.resolved_at is None else rfc3339_millis(view.resolved_at),
        "resolved_by": view.resolved_by,
        "resolved_submission_id": view.resolved_submission_id,
        "held": view.held,
        "held_at": None if view.held_at is None else rfc3339_millis(view.held_at),
        "held_by": view.held_by,
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


def parse_held_filter(raw: str | None) -> bool | None:
    """解析 `?held=`：`true` 只看暂存，`false` 排除暂存，省略不筛。"""
    if raw is None or raw == "":
        return None
    lowered = raw.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise InvalidQuarantineFilterError("held must be true or false")


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


def parse_course_filter(raw: str | None) -> str | None:
    """解析 `?course_id=`：空串=未提供；非 UUID → 422；返回规范化小写形式。

    规范化是必须的：令牌侧已规范化，而请求侧合法写法不唯一（大写、花括号、32 位无连字符）。
    直接按字符串比较会让一份完全合法的请求被误判为越权。
    """
    if raw is None or raw == "":
        return None
    try:
        return str(uuid.UUID(raw))
    except (AttributeError, TypeError, ValueError) as error:
        raise InvalidQuarantineFilterError("course_id must be a UUID") from error


def parse_section_filter(raw: str | None) -> str | None:
    """解析 `?section_id=`：同 `parse_course_filter`；必须与 `course_id` 同时提供。"""
    if raw is None or raw == "":
        return None
    try:
        return str(uuid.UUID(raw))
    except (AttributeError, TypeError, ValueError) as error:
        raise InvalidQuarantineFilterError("section_id must be a UUID") from error


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


def validate_resolution_note(note: object) -> str | None:
    """处置备注校验：`None`/空串按"未提供"，其余必须是 ≤500 的字符串。

    归属与驳回共用（`note` 是人工写的上下文，可能含学生信息，因此**只存用户提交的这段文本**，
    不回读邮件内容）。
    """
    if note is None:
        return None
    if not isinstance(note, str):
        raise QuarantineRequestError("note must be a string or null")
    if note == "":
        return None
    if len(note) > MAX_RESOLUTION_NOTE:
        raise QuarantineRequestError(
            f"note must be at most {MAX_RESOLUTION_NOTE} characters"
        )
    return note


def validate_resolution_request(*, student_id: object, note: object) -> tuple[str, str | None]:
    """请求体校验（契约 `additionalProperties: false` 由路由层判字段集）。

    返回 `(规范化后的 student_id, note)`；`student_id` 必须是 UUID（契约 `Uuid`），
    `note` 允许省略/`null`/空串（空串按"未提供"处理），长度不得超过契约的 500。
    """
    try:
        canonical_student = str(uuid.UUID(str(student_id)))
    except (AttributeError, TypeError, ValueError) as error:
        raise QuarantineRequestError("student_id must be a UUID") from error
    return canonical_student, validate_resolution_note(note)


def validate_completion_request(
    *,
    student_id: object,
    course_id: object,
    section_id: object,
    assignment_id: object,
    note: object,
) -> tuple[CompletionTarget, str | None]:
    """匹配用户的请求体：四个 UUID + 可选备注；任何非法值都是 422。"""

    def canonical(value: object, field: str) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except (AttributeError, TypeError, ValueError) as error:
            raise QuarantineRequestError(f"{field} must be a UUID") from error

    return (
        CompletionTarget(
            student_id=canonical(student_id, "student_id"),
            course_id=canonical(course_id, "course_id"),
            section_id=canonical(section_id, "section_id"),
            assignment_id=canonical(assignment_id, "assignment_id"),
        ),
        validate_resolution_note(note),
    )


class IntakeQuarantineService:
    """隔离记录的读取与人工归属（`listIntakeQuarantines` / `resolveIntakeQuarantine` 的服务层）。

    实例不持有调用者身份：`principal` 逐次传入，作用域判定每次重算（不存在"实例缓存了上一个调用者
    的作用域"的风险）。
    """

    def __init__(
        self,
        engine: Engine,
        *,
        email: EmailIntakeService | None = None,
        authorization_directory: UserAuthorizationDirectory | None = None,
    ) -> None:
        self._engine = engine
        self._email = email
        #: B4 权威授权目录。未显式注入时取邮件入口所用的同一个实例（两者必须给出同一答案：
        #: "这个学生现在是否属于这门课"）。`None` = 未配置 → 匹配用户返回 503。
        self._authorization = authorization_directory

    def _authorization_directory(self) -> UserAuthorizationDirectory | None:
        if self._authorization is not None:
            return self._authorization
        if self._email is None:
            return None
        return getattr(self._email, "authorization_directory", None)

    # --- 列表 -------------------------------------------------------------------------

    def list_quarantines(
        self,
        *,
        principal: Principal,
        course_id: str | None = None,
        section_id: str | None = None,
        reason_code: str | None = None,
        resolved: bool | None = None,
        held: bool | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> IntakeQuarantinePage:
        """按时间倒序取一页**调用者可见**的隔离记录。

        可见性由 SQL 谓词决定（管理员：全部；staff：scope 非空且被令牌作用域覆盖），
        并且与 `course_id/section_id` 筛选一起施加在排序、`limit` 与游标判定**之前**——
        分页与 `next_cursor` 只基于可见行。

        异常：`AuthorizationError` → 路由层 403；`InvalidQuarantineFilterError` /
        `InvalidQuarantineCursorError` → 422。`next_cursor` 非空即表示**还有下一页**。
        """
        require_intake_quarantine_authority(principal)
        size = _validated_limit(limit)
        reason = parse_reason_code_filter(reason_code)
        wanted_course = parse_course_filter(course_id)
        wanted_section = parse_section_filter(section_id)
        if wanted_section is not None and wanted_course is None:
            raise InvalidQuarantineFilterError("section_id requires course_id")
        _require_query_scope_authority(principal, wanted_course, wanted_section)
        after = None if cursor is None else decode_cursor(cursor)
        with self._engine.connect() as connection:
            rows = connection.execute(
                _page_statement(
                    principal=principal,
                    course_id=wanted_course,
                    section_id=wanted_section,
                    reason_code=reason,
                    resolved=resolved,
                    held=held,
                    after=after,
                    limit=size + 1,
                )
            ).fetchall()
        items = tuple(_view_of(row) for row in rows[:size])
        next_cursor: str | None = None
        if len(rows) > size and items:
            last = items[-1]
            next_cursor = encode_cursor(
                recorded_at=last.recorded_at, intake_quarantine_id=last.intake_quarantine_id
            )
        return IntakeQuarantinePage(items=items, next_cursor=next_cursor)

    # --- 人工处置 ---------------------------------------------------------------------

    def _lock_authorized_row(
        self,
        connection: Connection,
        principal: Principal,
        identifier: uuid.UUID,
    ) -> Any:
        """在行锁内取记录并重判行级授权；返回锁住的行（调用方只看 `reason_code/resolved`）。

        **顺序是安全性质**：先锁行、再按这一行的 scope 判权、最后才看"是否已处理"。
        反过来（先 409 再看权限）会让越权者从"已处理"与"未处理"的差异里读到不该知道的记录状态。
        """
        row = connection.execute(
            sa.select(
                intake_quarantines.c.reason_code,
                intake_quarantines.c.resolved,
                intake_quarantines.c.scope_course_id,
                intake_quarantines.c.scope_section_id,
                intake_quarantines.c.mailbox,
                intake_quarantines.c.uidvalidity,
                intake_quarantines.c.uid,
                intake_quarantines.c.message_id,
                intake_quarantines.c.resolved_submission_id,
            )
            .where(intake_quarantines.c.intake_quarantine_id == identifier)
            .with_for_update()
        ).fetchone()
        if row is None:
            raise QuarantineNotFoundError(str(identifier))
        require_quarantine_row_authority(
            principal,
            scope_course_id=None if row.scope_course_id is None else str(row.scope_course_id),
            scope_section_id=(
                None if row.scope_section_id is None else str(row.scope_section_id)
            ),
        )
        return row

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
            # 行锁把"检查已处理"与"写入决定"变成一次原子判定：并发两次处置只会有一个成功，
            # 另一个拿到 409 而不是覆盖前一个人的决定。
            row = self._lock_authorized_row(connection, principal, identifier)
            if bool(row.resolved):
                raise QuarantineAlreadyResolvedError(str(identifier))
            reason_code = str(row.reason_code)
            if reason_code not in ATTRIBUTABLE_REASON_CODES:
                raise QuarantineNotAttributableError(reason_code)
            connection.execute(
                sa.update(intake_quarantines)
                .where(
                    sa.and_(
                        intake_quarantines.c.intake_quarantine_id == identifier,
                        # 兜底：即使锁语义在某些隔离级别下退化，也不会覆盖已有处置。
                        intake_quarantines.c.resolved.is_(False),
                    )
                )
                .values(
                    resolved=True,
                    resolved_student_id=uuid.UUID(canonical_student),
                    resolution_note=canonical_note,
                    resolution="attributed",
                    resolved_at=datetime.now(UTC),
                    resolved_by=uuid.UUID(principal.user_id),
                    resolved_submission_id=None,
                    held=False,
                    held_at=None,
                    held_by=None,
                )
            )
            updated = connection.execute(
                _select_view().where(intake_quarantines.c.intake_quarantine_id == identifier)
            ).fetchone()
        assert updated is not None  # 同一事务内刚更新过这一行
        return _view_of(updated)

    def dismiss(
        self,
        *,
        principal: Principal,
        intake_quarantine_id: str,
        note: str | None = None,
    ) -> IntakeQuarantineView:
        """驳回一条隔离记录（不归属学生）；**只写这一行**，不建提交、不触发评测。

        与 `resolve` 的区别只有一处：驳回**不要求** `reason_code` 属于可归属集合。原因：
        归属会写入一个学生身份，因此只能修"身份没推出来"；驳回只是"这条记录我处理过了、
        不归属任何人"，对结构/附件类拒绝同样成立，而且这正是老师清空待处理列表的出口。

        异常：`AuthorizationError` → 403；`QuarantineNotFoundError` → 404；
        `QuarantineAlreadyResolvedError` → 409；`QuarantineRequestError` → 422。
        """
        require_intake_quarantine_authority(principal)
        canonical_note = validate_resolution_note(note)
        try:
            identifier = uuid.UUID(intake_quarantine_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise QuarantineNotFoundError("malformed quarantine id") from error

        with self._engine.begin() as connection:
            row = self._lock_authorized_row(connection, principal, identifier)
            if bool(row.resolved):
                raise QuarantineAlreadyResolvedError(str(identifier))
            connection.execute(
                sa.update(intake_quarantines)
                .where(
                    sa.and_(
                        intake_quarantines.c.intake_quarantine_id == identifier,
                        intake_quarantines.c.resolved.is_(False),
                    )
                )
                .values(
                    resolved=True,
                    # 驳回不能留下"学生"：否则"已驳回"与"已归属"在数据上不可区分。
                    resolved_student_id=None,
                    resolution_note=canonical_note,
                    resolution="dismissed",
                    resolved_at=datetime.now(UTC),
                    resolved_by=uuid.UUID(principal.user_id),
                    resolved_submission_id=None,
                    held=False,
                    held_at=None,
                    held_by=None,
                )
            )
            updated = connection.execute(
                _select_view().where(intake_quarantines.c.intake_quarantine_id == identifier)
            ).fetchone()
        assert updated is not None  # 同一事务内刚更新过这一行
        return _view_of(updated)


    def complete(
        self,
        *,
        principal: Principal,
        intake_quarantine_id: str,
        student_id: str,
        course_id: str,
        section_id: str,
        assignment_id: str,
        note: str | None = None,
    ) -> IntakeQuarantineView:
        """匹配用户：按选中的作业，把原邮件逐题（附件名 `N.txt`）生成该学生的提交。

        这是"人工归属"的完整闭环：检查行级 scope → 校验学生在作业受众内 → 取回原邮件 →
        重跑附件闸门与题目映射 → 走与自动路径**同一个** `build_submission_envelope` + `persist`
        → 同一行标记 `completed`（`resolved_submission_id` 取第一条提交）。

        依赖：邮件入口（没有 IMAP 时 503，不从缓存补附件字节）与作业目录（读不到受众时 503，
        绝不跳过受众校验）。

        异常：403 越权；404 不存在；409 已处理/原邮件不可定位；422 请求/目标/附件不合规；
        `QuarantineEmailUnavailable` / `QuarantineAssignmentUnavailable` /
        `QuarantineAuthorizationUnavailable` → 503。
        """
        target, canonical_note = validate_completion_request(
            student_id=student_id,
            course_id=course_id,
            section_id=section_id,
            assignment_id=assignment_id,
            note=note,
        )
        identifier = self._identifier(intake_quarantine_id)

        with self._engine.begin() as connection:
            row = self._lock_authorized_row(connection, principal, identifier)
            if bool(row.resolved):
                raise QuarantineAlreadyResolvedError(str(identifier))
            self._require_completion_target(principal, target, row)
            if self._email is None:
                raise QuarantineEmailUnavailable("email intake is not configured")
            mailbox = None if row.mailbox is None else str(row.mailbox)
            uidvalidity = None if row.uidvalidity is None else int(row.uidvalidity)
            uid = None if row.uid is None else int(row.uid)
            message_id = None if row.message_id is None else str(row.message_id)

        if mailbox is None or uidvalidity is None or uid is None:
            raise QuarantineMessageGone("the record has no mailbox position")

        audience = self._load_audience(target)
        # 与网页/邮件入口同口径：这一行的课程/课序必须与 B4 的**当前**归属一致。
        authoritative_section = self._authoritative_section(target)

        try:
            raw = self._email.fetch_raw(uidvalidity=uidvalidity, uid=uid)
        except ImapProtocolError as error:
            raise QuarantineMessageGone(str(error)) from error

        try:
            submission_ids = self._email.ingest_raw_for_assignment(
                raw=raw,
                student_id=target.student_id,
                course_id=target.course_id,
                section_id=authoritative_section,
                assignment_id=target.assignment_id,
                audience=audience,
                mailbox=mailbox,
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=message_id,
            )
        except AttachmentRejected as error:
            raise QuarantineContentRejected(f"{error.reason}: {error.detail}") from error
        if not submission_ids:
            raise QuarantineContentRejected(
                "attachment_mapping_invalid: the original message carries no mapped answer file"
            )
        first_submission_id = submission_ids[0]

        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            row = self._lock_authorized_row(connection, principal, identifier)
            if bool(row.resolved):
                current = connection.execute(
                    _select_view().where(
                        intake_quarantines.c.intake_quarantine_id == identifier
                    )
                ).fetchone()
                assert current is not None
                if str(current.resolved_submission_id or "") == first_submission_id:
                    return _view_of(current)
                raise QuarantineAlreadyResolvedError(str(identifier))
            self._require_completion_target(principal, target, row)
            connection.execute(
                sa.update(intake_quarantines)
                .where(
                    sa.and_(
                        intake_quarantines.c.intake_quarantine_id == identifier,
                        intake_quarantines.c.resolved.is_(False),
                    )
                )
                .values(
                    resolved=True,
                    resolved_student_id=uuid.UUID(target.student_id),
                    resolution_note=canonical_note,
                    resolution="completed",
                    resolved_at=now,
                    resolved_by=uuid.UUID(principal.user_id),
                    resolved_submission_id=uuid.UUID(first_submission_id),
                    held=False,
                    held_at=None,
                    held_by=None,
                )
            )
            self._email.mark_checkpoint_processed(
                connection,
                mailbox=mailbox,
                uidvalidity=uidvalidity,
                uid=uid,
                submission_id=first_submission_id,
                now=now,
            )
            updated = connection.execute(
                _select_view().where(intake_quarantines.c.intake_quarantine_id == identifier)
            ).fetchone()
        assert updated is not None
        return _view_of(updated)

    def _load_audience(self, target: CompletionTarget):
        """读取并校验人工复核选定的作业（与自动路径**同一个**目录与判定）。

        - 目录未配置/不可达 → `QuarantineAssignmentUnavailable`（503，系统故障不是学生错）；
        - 作业不存在、不属于目标课程、或作业不再 open → 422 `QuarantineTargetMismatch`
          （人工复核不能把学生塞进不属于他的作业）。

        课程级作业没有 section/student 名单；"学生是否在课程里、当前课序是哪个"由
        `_authoritative_section` 按 B4 的当前归属判定（与网页/邮件入口同口径）。
        """
        assert self._email is not None
        directory = self._email.assignment_directory
        if directory is None:
            raise QuarantineAssignmentUnavailable("assignment directory is not configured")
        try:
            audience = directory.get(target.assignment_id)
        except AssignmentUnavailable as error:
            raise QuarantineAssignmentUnavailable(str(error)) from error
        if audience is None:
            raise QuarantineTargetMismatch("the selected assignment does not exist")
        if not audience.open:
            raise QuarantineTargetMismatch("the selected assignment is not open")
        if audience.course_id != target.course_id:
            raise QuarantineTargetMismatch(
                "the selected assignment belongs to another course"
            )
        return audience

    def _authoritative_section(self, target: CompletionTarget) -> str:
        """按 B4 当前归属校验/补全目标学生的 section（不一致 → 422）。

        人工复核由 staff 显式指定 `(student, course, section, assignment)`；服务端必须再按 B4 的
        **当前**归属判一次，保证与网页/邮件入口同口径（设计 §4）：

        - 授权目录未配置/不可达 → `QuarantineAuthorizationUnavailable`（503）；
        - 目标学生当前没有该课程的 enrollment → 422（学生不在课程里，人工不能凭空放行）；
        - 学生当前 enrollment 的 section 与 staff 指定的 section 不一致 → 422（显式提示，
          不静默改写一个 staff 明确作出的选择）；
        - 一致 → 用权威值（此时与指定值相同）落库。

        课程 staff 作为目标学生时没有学生选课，保留 staff 指定的 section（与 staff 自助提交同口径）。
        """
        directory = self._authorization_directory()
        if directory is None:
            raise QuarantineAuthorizationUnavailable(
                "the B4 user authorization directory is not configured"
            )
        try:
            authorization = directory.get(target.student_id)
        except AuthorizationUnavailable as error:
            raise QuarantineAuthorizationUnavailable(str(error)) from error
        if authorization.is_course_staff_for(target.course_id):
            return target.section_id
        enrollment = authorization.enrollment_for_course(target.course_id)
        if enrollment is None:
            raise QuarantineTargetMismatch(
                "the selected student is not currently enrolled in the course"
            )
        if enrollment.section_id != target.section_id:
            raise QuarantineTargetMismatch(
                "the selected section does not match the student's current enrollment section"
            )
        return enrollment.section_id

    def set_held(
        self,
        *,
        principal: Principal,
        intake_quarantine_id: str,
        held: bool,
    ) -> IntakeQuarantineView:
        """暂存/恢复；暂存行仍未处理，可继续匹配用户或标记作废。"""
        require_intake_quarantine_authority(principal)
        identifier = self._identifier(intake_quarantine_id)
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            row = self._lock_authorized_row(connection, principal, identifier)
            if bool(row.resolved):
                raise QuarantineAlreadyResolvedError(str(identifier))
            connection.execute(
                sa.update(intake_quarantines)
                .where(intake_quarantines.c.intake_quarantine_id == identifier)
                .values(
                    held=held,
                    held_at=now if held else None,
                    held_by=uuid.UUID(principal.user_id) if held else None,
                )
            )
            updated = connection.execute(
                _select_view().where(intake_quarantines.c.intake_quarantine_id == identifier)
            ).fetchone()
        assert updated is not None
        return _view_of(updated)

    @staticmethod
    def _identifier(intake_quarantine_id: str) -> uuid.UUID:
        try:
            return uuid.UUID(intake_quarantine_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise QuarantineNotFoundError("malformed quarantine id") from error

    @staticmethod
    def _require_completion_target(
        principal: Principal,
        target: CompletionTarget,
        row: Any,
    ) -> None:
        """目标课程/课序必须在调用者 scope 内，且与记录已证明的 scope 不冲突。"""
        if not principal.is_admin and not principal.covers_course(
            target.course_id, target.section_id
        ):
            raise AuthorizationError("the target course/section is outside the token scope")
        if row.scope_course_id is not None and str(row.scope_course_id) != target.course_id:
            raise QuarantineTargetMismatch(
                "target course does not match the record's verified scope"
            )
        if row.scope_section_id is not None and str(row.scope_section_id) != target.section_id:
            raise QuarantineTargetMismatch(
                "target section does not match the record's verified scope"
            )


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
        intake_quarantines.c.scope_course_id,
        intake_quarantines.c.scope_section_id,
        intake_quarantines.c.subject,
        intake_quarantines.c.resolution,
        intake_quarantines.c.resolved_at,
        intake_quarantines.c.resolved_by,
        intake_quarantines.c.resolved_submission_id,
        intake_quarantines.c.held,
        intake_quarantines.c.held_at,
        intake_quarantines.c.held_by,
    )


def _scope_predicate(principal: Principal) -> sa.ColumnElement[bool]:
    """行级可见性谓词（**必须在排序、limit 与游标之前施加**）。

    - 管理员：`true`（全局，含未关联行——这正是管理员视图存在的理由）；
    - staff：`scope_course_id` 非空且被某个令牌作用域覆盖。作用域条目未限定课序时覆盖该课程
      的任意课序（与 `Principal.covers_course` 同一语义），限定课序时要求精确相等；
    - 空作用域：`false`（fail-closed；入口层已拒绝这种 staff 令牌，这里再兜一次）。
    """
    if principal.is_admin:
        return sa.true()
    clauses: list[sa.ColumnElement[bool]] = []
    for scope in principal.scopes:
        condition: sa.ColumnElement[bool] = (
            intake_quarantines.c.scope_course_id == uuid.UUID(scope.course_id)
        )
        if scope.section_id is not None:
            condition = sa.and_(
                condition,
                intake_quarantines.c.scope_section_id == uuid.UUID(scope.section_id),
            )
        clauses.append(condition)
    if not clauses:
        return sa.false()
    return sa.and_(intake_quarantines.c.scope_course_id.is_not(None), sa.or_(*clauses))


def _require_query_scope_authority(
    principal: Principal, course_id: str | None, section_id: str | None
) -> None:
    """`course_id/section_id` 只是筛选：staff 传了作用域之外的课程/课序 → 403。

    不带课程筛选是合法的（此时仍由 `_scope_predicate` 把范围限制在令牌作用域内）；
    显式指定课程时，显式越界必须失败，而不是静默返回空列表——"空结果"与"无权"是两件事。
    """
    if principal.is_admin or course_id is None:
        return
    if not principal.covers_course(course_id, section_id):
        raise AuthorizationError("the requested course/section is outside the token scope")


def _page_statement(
    *,
    principal: Principal,
    course_id: str | None,
    section_id: str | None,
    reason_code: str | None,
    resolved: bool | None,
    held: bool | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> sa.Select[Any]:
    """页面查询：**先过滤**（scope 谓词 + 用户筛选 + 游标）再排序取页。

    时间倒序 + 主键兜底（同毫秒的行必须有全序，keyset 判定才有唯一解）。所有 `where` 都在
    `.limit()` 之前组合，SQL 语义与"先过滤后分页"一致。
    """
    statement = _select_view().where(_scope_predicate(principal))
    if course_id is not None:
        statement = statement.where(intake_quarantines.c.scope_course_id == uuid.UUID(course_id))
    if section_id is not None:
        statement = statement.where(intake_quarantines.c.scope_section_id == uuid.UUID(section_id))
    if reason_code is not None:
        statement = statement.where(intake_quarantines.c.reason_code == reason_code)
    if resolved is not None:
        statement = statement.where(intake_quarantines.c.resolved.is_(resolved))
    if held is not None:
        statement = statement.where(intake_quarantines.c.held.is_(held))
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
    return statement.order_by(
        intake_quarantines.c.created_at.desc(),
        intake_quarantines.c.intake_quarantine_id.desc(),
    ).limit(limit)


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
        scope_course_id=None if row.scope_course_id is None else str(row.scope_course_id),
        scope_section_id=None if row.scope_section_id is None else str(row.scope_section_id),
        subject=None if row.subject is None else str(row.subject),
        resolution=None if row.resolution is None else str(row.resolution),
        resolved_at=row.resolved_at,
        resolved_by=None if row.resolved_by is None else str(row.resolved_by),
        resolved_submission_id=(
            None if row.resolved_submission_id is None else str(row.resolved_submission_id)
        ),
        held=bool(row.held),
        held_at=row.held_at,
        held_by=None if row.held_by is None else str(row.held_by),
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
    "QuarantineAssignmentUnavailable",
    "QuarantineAuthorizationUnavailable",
    "QuarantineNotAttributableError",
    "QuarantineNotFoundError",
    "QuarantineRequestError",
    "decode_cursor",
    "encode_cursor",
    "parse_course_filter",
    "parse_limit",
    "parse_reason_code_filter",
    "parse_resolved_filter",
    "parse_section_filter",
    "quarantine_page_body",
    "quarantine_view_body",
    "validate_resolution_request",
]
