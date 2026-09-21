"""B2 运行配置：全部来自环境变量，缺失即明确报错（无隐藏默认、无行为开关）。

密钥类信息**不落库、不进日志、不进事件**；本模块只把配置读成冻结数据类。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from autograder_b2.auth.tokens import DEFAULT_AUDIENCE, DEFAULT_ISSUER
from autograder_b2.commentary.provider import (
    # 超时的**默认值只定义一次**（在 provider 里），这里复用常量而不是再写一遍 30。
    DEFAULT_TIMEOUT_SECONDS,
)
from autograder_b2.integration.bindings import (
    # 环境标签的字符集规则取自 `integration.bindings`：那里是这条规则的**唯一**定义处
    # （`environment_digest` 的公式用它）。在这里再写一遍正则就是同一事实的第二份定义。
    _ENVIRONMENT_TAG_RE,
    UNSET_ENVIRONMENT_TAG,
)

#: 单文件解码后字节上限（与契约 `submission-envelope` 的 `content_base64.maxLength` 一致：
#: 2097152 个 base64 字符 ⇒ 1572864 字节）。
MAX_FILE_BYTES = 1572864
#: 提交文件数上限（与契约 `files.maxItems` 一致）。
MAX_FILES = 100
#: 提交总字节上限的部署默认值；题目级 `max_total_bytes` 在取得 evaluation package 后收紧。
DEFAULT_MAX_TOTAL_BYTES = 4 * 1024 * 1024

#: 内容寻址存储的**保留窗口**（天）：内容失去全部引用之后，至少还要再保留这么久才允许被回收。
#: 配置面是 `B2_ARTIFACT_RETENTION_DAYS`；`0` 是默认值，也是本模块一直以来的行为（一失去引用即可
#: 回收）。方向是**单向**的：调大只会让回收更保守，**不可能**让任何此前被保留的内容开始被删除。
DEFAULT_ARTIFACT_RETENTION_DAYS = 0

#: IMAP 端口：明文 143 / 隐式 TLS 993。**不是隐藏开关**——由 `B2_IMAP_USE_SSL` 显式选择。
IMAP_PLAIN_PORT = 143
IMAP_SSL_PORT = 993
#: 单次拉取的邮件数上限（防止一个塞满的邮箱把一次拉取变成无界循环）。
DEFAULT_IMAP_MAX_MESSAGES = 50
#: **单封原始报文**的字节上限。依据：生产默认单文件 1.5 MB、单提交总预算 4 MB，而邮件里的附件是
#: base64 编码（膨胀 4/3）且外面还有 MIME 头与分界线，因此 8 MiB 给包装留了足够余量。
#: 这道闸只拦"单封报文的绝对大小"，**不**拦"大量小部件"（2 万个部件实测解析 4.3 s，属 stdlib
#: `email` 的解析成本，本工作项不处理）。
DEFAULT_IMAP_MAX_MESSAGE_BYTES = 8 * 1024 * 1024
#: 邮箱**标签**的允许形态。它是服务端配置（不是邮件头），进 checkpoint 主键、隔离记录与信封
#: provenance；限定字符集与长度是为了让幂等键可确定复现，且不引入控制字符。
MAILBOX_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_.@+-]{1,100}$")

#: 与 IMAP 接收有关的全部环境变量名；用于区分"完全未配置"与"半配置"。
_IMAP_VARIABLES = (
    "B2_IMAP_HOST",
    "B2_IMAP_PORT",
    "B2_IMAP_USERNAME",
    "B2_IMAP_PASSWORD",
    "B2_IMAP_PASSWORD_FILE",
    "B2_IMAP_MAILBOX",
    "B2_IMAP_USE_SSL",
    "B2_IMAP_PROCESSED_MAILBOX",
    "B2_IMAP_MATCHING_JSON",
    "B2_IMAP_MATCHING_PATH",
    "B2_IMAP_MAX_MESSAGES",
    "B2_IMAP_MAX_MESSAGE_BYTES",
    "B2_IMAP_TIMEOUT_SECONDS",
)

#: 评语轨（`RQ-T-20`）的全部环境变量名。**默认一个都不给**——`ADR-0009` 决策 4 要求默认关闭，
#: 且关闭状态必须与"没有这条轨"时的系统行为完全一致（尤其：没有任何出网调用点）。
_COMMENTARY_VARIABLES = (
    "B2_COMMENTARY_PROVIDER",
    "B2_COMMENTARY_BASE_URL",
    "B2_COMMENTARY_MODEL",
    "B2_COMMENTARY_API_KEY",
    "B2_COMMENTARY_API_KEY_FILE",
    "B2_COMMENTARY_TIMEOUT_SECONDS",
)


class ConfigurationError(RuntimeError):
    """配置缺失或非法；启动/首次使用即失败，不做静默兜底。"""


class EvaluationChainNotConfigured(ConfigurationError):
    """评测链**完全未配置**（一个相关环境变量都没给）——与"配了但配错/不可用"是两件事。

    为什么值得一个独立类型：这是"有意的部署选择"（本实例只做接收与成绩），不是故障；健康状态据此
    把它报成"未配置"而不是"不可用"（见 `runtime.health_items`）。用类型而不是比对错误文本，是因为
    文本会漂移，而这两种事实的区分是**语义**，不是措辞。
    """


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    artifact_root: Path
    jwt_issuer: str
    jwt_audience: str
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    #: 内容寻址存储的保留窗口（天，见 `DEFAULT_ARTIFACT_RETENTION_DAYS`）。取默认值的是**解析器**
    #: （`B2_ARTIFACT_RETENTION_DAYS` 未配置时给 0），而不是数据类字段——配置缺失不该被字段默认值
    #: 悄悄吸收掉。
    artifact_retention_days: int
    environment: str
    nats_url: str | None = None
    #: 取包端点（B3）的定位与凭据。**读入时不做语义校验**：这些取值只影响评测链一个入口，
    #: 而 `settings_from_env` 抛出的 `ConfigurationError` 会被 `intake.runtime` 统一转成
    #: "整个 B2 不可用"——那会把"评测链没配好"谎报成"提交接收也挂了"。校验见
    #: `evaluation_chain_settings`。
    b3_base_url: str | None = None
    #: 服务间凭据。`repr=False` 的理由与 `ImapSettings.password` 相同：数据类的 `repr` 会把它
    #: 打进任何一次 `print(settings)`、日志或异常链。
    b3_authorization: str | None = field(default=None, repr=False)
    #: 沙箱工作区根。由 `evaluation_chain_settings` 保证与 `artifact_root` 是**不相交**的两棵树。
    workspace_root: Path | None = None
    #: 环境标签（`bindings.environment_digest` 的输入之一）。未配置时是 `unset`——一个诚实的
    #: "未声明"标记，不是"任何环境"的同义词（见 `integration.bindings` 的诚实边界）。
    environment_tag: str = UNSET_ENVIRONMENT_TAG

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}


def settings_from_env(environ: Mapping[str, str] | None = None) -> Settings:
    """从环境构造配置。`DATABASE_URL` 与 `B2_ARTIFACT_ROOT` 必填。

    可选取值（取包端点、工作区根、环境标签）**只读取、不判断**：它们的语义校验在
    `evaluation_chain_settings`，这样"评测链缺配置"不会连累提交接收入口。
    """
    source = os.environ if environ is None else environ

    database_url = (source.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise ConfigurationError("DATABASE_URL is required")

    artifact_root = (source.get("B2_ARTIFACT_ROOT") or "").strip()
    if not artifact_root:
        raise ConfigurationError("B2_ARTIFACT_ROOT is required (artifacts are stored outside the database)")

    workspace_root = (source.get("B2_WORKSPACE_ROOT") or "").strip()

    return Settings(
        database_url=database_url,
        artifact_root=Path(artifact_root),
        jwt_issuer=(source.get("B4_JWT_ISSUER") or DEFAULT_ISSUER).strip(),
        jwt_audience=(source.get("B4_JWT_AUDIENCE") or DEFAULT_AUDIENCE).strip(),
        max_files=_positive_int(source, "B2_MAX_FILES", MAX_FILES),
        max_file_bytes=_positive_int(source, "B2_MAX_FILE_BYTES", MAX_FILE_BYTES),
        max_total_bytes=_positive_int(source, "B2_MAX_TOTAL_BYTES", DEFAULT_MAX_TOTAL_BYTES),
        artifact_retention_days=artifact_retention_days(source),
        environment=(source.get("B2_ENV") or "development").strip(),
        nats_url=(source.get("NATS_URL") or "").strip() or None,
        b3_base_url=(source.get("B2_B3_BASE_URL") or "").strip() or None,
        b3_authorization=_optional_secret(
            source, "B2_B3_AUTHORIZATION", "B2_B3_AUTHORIZATION_FILE"
        ),
        workspace_root=Path(workspace_root) if workspace_root else None,
        environment_tag=(source.get("B2_ENVIRONMENT_TAG") or "").strip() or UNSET_ENVIRONMENT_TAG,
    )


@dataclass(frozen=True, slots=True)
class EvaluationChainSettings:
    """评测链的配置（取包端点定位 + 凭据 + 工作区根 + 环境标签）。

    `b3_authorization` 同样 `repr=False`：凭据不得出现在任何 repr / 日志 / 事件 / 问题详情里。
    """

    b3_base_url: str
    b3_authorization: str = field(repr=False)
    workspace_root: Path
    environment_tag: str


def evaluation_chain_settings(settings: Settings) -> EvaluationChainSettings:
    """校验并取出评测链配置；不完整或非法即 `ConfigurationError`（原因可读、不含地址与凭据）。

    **为什么校验不放在 `settings_from_env`**：那里（以及 `load_jwks`）抛出的 `ConfigurationError`
    会被 `intake.runtime.build_runtime` 统一转成"整个 B2 不可用"（提交接收也会 503）。评测链缺
    配置只应让评测路由 503，成绩查询与提交接收与它无关——把三件事的可用性绑成一件就是对运维的
    谎报。因此这里读的是同一个 `Settings`，但校验发生在装配评测链时。

    **工作区根必须与内容寻址存储根不相交**：CAS 的孤儿回收会把"没有元数据引用的内容文件"当孤儿
    清除，而工作区里的文件正是没有元数据的——把工作区放进 CAS 树下等于让回收器去删正在执行的
    工作区。`CasWorkspaceProvider` 构造时也会拒绝同一种配置；两层都拦是为了让原因出现在**装配**
    阶段（可读、可诊断），而不是等第一次评测才炸。
    """
    missing = [
        name
        for name, value in (
            ("B2_B3_BASE_URL", settings.b3_base_url),
            ("B2_B3_AUTHORIZATION（或 B2_B3_AUTHORIZATION_FILE）", settings.b3_authorization),
            ("B2_WORKSPACE_ROOT", settings.workspace_root),
        )
        if not value
    ]
    if missing:
        if len(missing) == 3:
            # "完全未配置"与"半配置"分开说：前者是有意的部署选择（本实例只跑接收/成绩），
            # 后者是操作者漏配了一项——两者的处置完全不同，混成一句会把后者藏起来。
            raise EvaluationChainNotConfigured(
                "评测链未配置：需要 B2_B3_BASE_URL、B2_B3_AUTHORIZATION"
                "（或 B2_B3_AUTHORIZATION_FILE）与 B2_WORKSPACE_ROOT"
            )
        raise ConfigurationError("评测链配置不完整，缺少：" + "、".join(missing))

    base_url = str(settings.b3_base_url)
    if not base_url.startswith(("http://", "https://")):
        # 不回显取值：配置里可能带主机名或误把凭据写进了这一项。
        raise ConfigurationError("B2_B3_BASE_URL 必须是 http(s) 绝对地址")

    root = Path(str(settings.workspace_root)).resolve()
    artifact_root = Path(settings.artifact_root).resolve()
    if root == artifact_root or artifact_root in root.parents or root in artifact_root.parents:
        raise ConfigurationError(
            "B2_WORKSPACE_ROOT 必须与 B2_ARTIFACT_ROOT 互不包含"
            "（工作区与内容寻址存储必须是两棵树，否则孤儿回收会删除正在使用的工作区）"
        )

    if not _ENVIRONMENT_TAG_RE.match(settings.environment_tag):
        raise ConfigurationError(
            "B2_ENVIRONMENT_TAG 必须是 1-200 位的受限字符集（镜像摘要/版本号/短标识）"
        )

    return EvaluationChainSettings(
        b3_base_url=base_url,
        b3_authorization=str(settings.b3_authorization),
        workspace_root=root,
        environment_tag=settings.environment_tag,
    )


@dataclass(frozen=True, slots=True)
class CommentarySettings:
    """评语轨的 provider 配置（`RQ-T-20` / `ADR-0009` 决策 4）。

    `api_key` 声明 `repr=False`：与 `ImapSettings.password`、`Settings.b3_authorization` 同一条理由
    ——数据类的 `repr` 会把凭据打进任何一次 `print`、日志或异常链。
    """

    provider: str
    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: float


def load_commentary_settings(
    environ: Mapping[str, str] | None = None,
) -> tuple[CommentarySettings | None, str | None]:
    """读取评语 provider 配置 → `(配置, 未配置原因)`。

    三种情形，**都不产生任何出网调用**：

    | 情形 | 返回 | 对外表现 |
    |---|---|---|
    | 一个相关变量都没给（默认） | `(None, "…需要 A、B、C、D")` | `GET` → `unavailable`；`POST` → `409` |
    | 给了但缺项/取值非法 | `(None, "…缺少 X" / 非法原因)` | 同上，且原因指名缺什么 |
    | 齐备且合法 | `(settings, None)` | 可以生成 |

    为什么把"没配"与"配错"都收敛成 `None` + 原因：契约只声明了 `unavailable`（没有 provider 被配置）
    与 `409`（指名缺什么）两种表达，而"配错了"同样不会产生任何评语——把它报成 `unavailable`
    并且**在 reason 里说清楚哪里不对**，比发明第三种状态诚实。
    """
    source = os.environ if environ is None else environ
    required = {
        "B2_COMMENTARY_PROVIDER": (source.get("B2_COMMENTARY_PROVIDER") or "").strip(),
        "B2_COMMENTARY_BASE_URL": (source.get("B2_COMMENTARY_BASE_URL") or "").strip(),
        "B2_COMMENTARY_MODEL": (source.get("B2_COMMENTARY_MODEL") or "").strip(),
        "B2_COMMENTARY_API_KEY": _optional_secret(
            source, "B2_COMMENTARY_API_KEY", "B2_COMMENTARY_API_KEY_FILE"
        )
        or "",
    }
    missing = tuple(name for name, value in required.items() if not value)
    if missing:
        return None, _commentary_reason(missing)
    base_url = required["B2_COMMENTARY_BASE_URL"]
    if not base_url.startswith(("http://", "https://")):
        # 不回显取值：配置里可能带主机名或误把凭据写进了这一项。
        return None, "B2_COMMENTARY_BASE_URL 必须是 http(s) 绝对地址"
    timeout = _positive_float(source, "B2_COMMENTARY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    return (
        CommentarySettings(
            provider=required["B2_COMMENTARY_PROVIDER"],
            base_url=base_url,
            model=required["B2_COMMENTARY_MODEL"],
            api_key=required["B2_COMMENTARY_API_KEY"],
            timeout_seconds=timeout,
        ),
        None,
    )


def _commentary_reason(missing: tuple[str, ...]) -> str:
    """给运维读的一句话：指名缺哪些变量（不回显任何取值）。"""
    return "评语轨未配置：需要 " + "、".join(missing)


def _optional_secret(source: Mapping[str, str], inline_name: str, file_name: str) -> str | None:
    """可选凭据：`<inline>` 优先，其次 `<file>`；都没有或文件读不到时返回 `None`。

    与 `_imap_password` 的两点不同，都是有意为之：①`load_imap_settings` 在"半配置"时抛
    `ConfigurationError`，而它被 `_wire_email_intake` 单独捕获、只影响邮件入口；这里若抛出就会
    经由 `settings_from_env` 打到整个模块上。②内联值同样**不做 strip**（凭据可以合法地以空格
    开头/结尾），只有文件的结尾换行被去掉。
    """
    inline = source.get(inline_name)
    if inline:
        return inline
    path = (source.get(file_name) or "").strip()
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        return None
    secret = file.read_text(encoding="utf-8").rstrip("\r\n")
    return secret or None


def artifact_retention_days(environ: Mapping[str, str] | None = None) -> int:
    """读取内容寻址存储的保留窗口：`B2_ARTIFACT_RETENTION_DAYS`（天，默认 0）。

    为什么可以单独读这一项（而不是要求调用方先拿到整套 `Settings`）：孤儿回收器只需要这一项配置，
    而 `settings_from_env` 还要求 `DATABASE_URL`/`B2_ARTIFACT_ROOT` 齐备（缺一项即抛）。把回收窗口
    绑在"整套配置齐备"上，会让一次回收被迫依赖与它无关的配置。变量名、默认值与解析规则仍然只在
    本模块定义一次（`settings_from_env` 复用同一个解析器，不另写一遍）。
    """
    source = os.environ if environ is None else environ
    return _non_negative_int(source, "B2_ARTIFACT_RETENTION_DAYS", DEFAULT_ARTIFACT_RETENTION_DAYS)


def _positive_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw = (source.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive")
    return value


def _non_negative_int(source: Mapping[str, str], name: str, default: int) -> int:
    """同 `_positive_int`，但**允许 0**：保留窗口 0 是合法且有意义的取值（＝一失去引用即可回收）。"""
    raw = (source.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if value < 0:
        raise ConfigurationError(f"{name} must be zero or positive")
    return value


def load_jwks(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """读取 B4 的公钥集：`B2_JWKS_JSON`（内联）优先，其次 `B2_JWKS_PATH`（文件）。

    两者都没有时明确失败——**绝不**退化为"不验签"。
    """
    source = os.environ if environ is None else environ
    inline = (source.get("B2_JWKS_JSON") or "").strip()
    if inline:
        try:
            document = json.loads(inline)
        except json.JSONDecodeError as error:
            raise ConfigurationError("B2_JWKS_JSON is not valid JSON") from error
        if not isinstance(document, dict):
            raise ConfigurationError("B2_JWKS_JSON must be a JSON object")
        return document
    path = (source.get("B2_JWKS_PATH") or "").strip()
    if path:
        file = Path(path)
        if not file.is_file():
            raise ConfigurationError("B2_JWKS_PATH does not exist")
        document = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ConfigurationError("JWKS file must contain a JSON object")
        return document
    raise ConfigurationError("no JWKS configured: set B2_JWKS_JSON or B2_JWKS_PATH")


@dataclass(frozen=True, slots=True)
class ImapSettings:
    """IMAP 接收配置：**全部**来自环境或口令文件，没有隐藏默认、没有行为开关。

    `password` 声明 `repr=False`：数据类默认的 `repr` 会把口令打进任何一次 `print(settings)`、
    日志或异常链——这是最容易发生的凭据泄漏路径，在类型层面直接堵掉。
    """

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    mailbox: str
    use_ssl: bool
    matching: Mapping[str, Mapping[str, str]]
    max_messages_per_pull: int
    max_message_bytes: int
    timeout_seconds: float
    processed_mailbox: str | None = None


def load_imap_settings(environ: Mapping[str, str] | None = None) -> ImapSettings | None:
    """读取 IMAP 接收配置。**未配置**（无 `B2_IMAP_HOST` 且无任何 `B2_IMAP_*`）返回 `None`。

    "未配置"与"半配置"被严格区分：前者让邮件入口明确不可用（API 入口不受影响），后者是配置
    错误并当场报出缺失项——绝不退化为"拿默认参数去连一个猜出来的邮箱"。
    """
    source = os.environ if environ is None else environ
    host = (source.get("B2_IMAP_HOST") or "").strip()
    if not host:
        present = sorted(name for name in _IMAP_VARIABLES if (source.get(name) or "").strip())
        if present:
            raise ConfigurationError(f"B2_IMAP_HOST is required when {'/'.join(present)} is set")
        return None

    username = (source.get("B2_IMAP_USERNAME") or "").strip()
    if not username:
        raise ConfigurationError("B2_IMAP_USERNAME is required")

    mailbox = (source.get("B2_IMAP_MAILBOX") or "").strip()
    if not mailbox:
        # 标签是 checkpoint 主键与 provenance 的一部分：给一个隐式默认会让两个真实邮箱在
        # 同一张表里共用一个键，重复投递就可能被误判为已处理。必须显式配置。
        raise ConfigurationError("B2_IMAP_MAILBOX is required (it labels checkpoints and provenance)")
    if not MAILBOX_LABEL_PATTERN.match(mailbox):
        raise ConfigurationError("B2_IMAP_MAILBOX must match ^[A-Za-z0-9_.@+-]{1,100}$")

    use_ssl = _boolean(source, "B2_IMAP_USE_SSL", default=False)
    port = _positive_int(source, "B2_IMAP_PORT", IMAP_SSL_PORT if use_ssl else IMAP_PLAIN_PORT)

    processed = (source.get("B2_IMAP_PROCESSED_MAILBOX") or "").strip()
    if processed and not MAILBOX_LABEL_PATTERN.match(processed):
        raise ConfigurationError("B2_IMAP_PROCESSED_MAILBOX must match ^[A-Za-z0-9_.@+-]{1,100}$")

    return ImapSettings(
        host=host,
        port=port,
        username=username,
        password=_imap_password(source),
        mailbox=mailbox,
        use_ssl=use_ssl,
        matching=load_imap_matching(source),
        max_messages_per_pull=_positive_int(
            source, "B2_IMAP_MAX_MESSAGES", DEFAULT_IMAP_MAX_MESSAGES
        ),
        max_message_bytes=_positive_int(
            source, "B2_IMAP_MAX_MESSAGE_BYTES", DEFAULT_IMAP_MAX_MESSAGE_BYTES
        ),
        timeout_seconds=_positive_float(source, "B2_IMAP_TIMEOUT_SECONDS", 30.0),
        processed_mailbox=processed or None,
    )


def load_imap_matching(environ: Mapping[str, str] | None = None) -> dict[str, dict[str, str]]:
    """读取邮件 → 提交身份的匹配规则：`B2_IMAP_MATCHING_JSON`（内联）优先，其次文件路径。

    规则原文（键形态与"不唯一即隔离"的判定）由 `intake.matching` 解释；这里只保证"读到了一份
    结构上是对象套对象的配置"。两者都没有时明确失败——**绝不**退化为"任何邮件都当成某个学生"。
    """
    source = os.environ if environ is None else environ
    inline = (source.get("B2_IMAP_MATCHING_JSON") or "").strip()
    if inline:
        document = _json_object(inline, "B2_IMAP_MATCHING_JSON")
    else:
        path = (source.get("B2_IMAP_MATCHING_PATH") or "").strip()
        if not path:
            raise ConfigurationError(
                "no matching rules configured: set B2_IMAP_MATCHING_JSON or B2_IMAP_MATCHING_PATH"
            )
        file = Path(path)
        if not file.is_file():
            raise ConfigurationError("B2_IMAP_MATCHING_PATH does not exist")
        document = _json_object(file.read_text(encoding="utf-8"), "B2_IMAP_MATCHING_PATH")

    rules: dict[str, dict[str, str]] = {}
    for key, value in document.items():
        if not isinstance(value, dict):
            raise ConfigurationError(f"matching rule {key!r} must be a JSON object")
        row: dict[str, str] = {}
        for field_name, field_value in value.items():
            if not isinstance(field_value, str):
                raise ConfigurationError(f"matching rule {key!r} field {field_name!r} must be a string")
            row[str(field_name)] = field_value
        rules[str(key)] = row
    return rules


def _imap_password(source: Mapping[str, str]) -> str:
    """口令来源：`B2_IMAP_PASSWORD`（内联）优先，其次 `B2_IMAP_PASSWORD_FILE`（文件）。

    内联值**不做 strip**（口令可以合法地以空格开头/结尾）；只有文件的结尾换行被去掉。
    """
    inline = source.get("B2_IMAP_PASSWORD")
    if inline:
        return inline
    path = (source.get("B2_IMAP_PASSWORD_FILE") or "").strip()
    if path:
        file = Path(path)
        if not file.is_file():
            raise ConfigurationError("B2_IMAP_PASSWORD_FILE does not exist")
        secret = file.read_text(encoding="utf-8").rstrip("\r\n")
        if not secret:
            raise ConfigurationError("B2_IMAP_PASSWORD_FILE is empty")
        return secret
    raise ConfigurationError("no IMAP password configured: set B2_IMAP_PASSWORD or B2_IMAP_PASSWORD_FILE")


def _json_object(text: str, name: str) -> dict[str, object]:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"{name} is not valid JSON") from error
    if not isinstance(document, dict):
        raise ConfigurationError(f"{name} must contain a JSON object")
    return document


def _boolean(source: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = (source.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean (true/false)")


def _positive_float(source: Mapping[str, str], name: str, default: float) -> float:
    raw = (source.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a number") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive")
    return value
