"""进程内最小 IMAP4rev1 服务器（**真实 TCP socket + 真实协议行**）。

本环境没有可用的外部 IMAP 邮箱（真实服务器、TLS、生产邮箱行为都**未**验证），因此按
[docs/testing/strategy.md](../docs/testing/strategy.md) 的口径，以"隔离不可控外部服务"的夹具替代。
它**真的**说 IMAP：客户端是 stdlib `imaplib`（不是替身），走真实 socket 收发真实协议行，
包括 `{n}` 字面量（`RFC822` 报文就是这样过去的）。

实现的子集（= 被测实现用到的全部）：

| 命令 | 说明 |
|---|---|
| `CAPABILITY` | 只声明真的实现了的 `IMAP4rev1` |
| `LOGIN` | 校验用户名/口令；失败回 `NO [AUTHENTICATIONFAILED]` |
| `SELECT` | `FLAGS` / `EXISTS` / `RECENT` / `OK [UIDVALIDITY n]` / `OK [UIDNEXT n]` / `OK [READ-WRITE]` |
| `UID SEARCH ALL`（及 `SEARCH ALL`） | 返回全部 UID |
| `UID FETCH <uid> <items>` | `RFC822` / `BODY[]` / `BODY.PEEK[]` / `RFC822.HEADER` / `RFC822.SIZE` / `UID` / `FLAGS` |
| `UID STORE <uid> <+FLAGS> <flags>` | 更新标志并回一条 untagged `FETCH` |
| `NOOP` / `LOGOUT` | 常规收尾 |

**没有**实现（因此也谈不上验证）：TLS、`AUTHENTICATE`、`IDLE`、`CONDSTORE`、`MOVE`、`COPY`、
多邮箱、单连接多会话、字面量输入（`LITERAL+`）。任何未实现的命令一律回 `BAD`——夹具不猜。
"""

from __future__ import annotations

import contextlib
import socket
import threading
from dataclasses import dataclass, field
from typing import Self

CRLF = b"\r\n"
#: 一行命令的上限（防御性：夹具不该被一个畸形客户端拖死）。
MAX_LINE_BYTES = 64 * 1024
#: 只声明真的实现了的能力。
CAPABILITIES = "IMAP4rev1"
_KNOWN_FLAGS = ("\\Answered", "\\Flagged", "\\Deleted", "\\Seen", "\\Draft")


@dataclass
class FixtureMessage:
    """邮箱里的一封邮件（`raw` 是完整 MIME 报文，原样存原样发）。"""

    uid: int
    raw: bytes
    flags: set[str] = field(default_factory=set)


class ImapFixtureServer:
    """线程内跑的最小 IMAP 服务器；`port` 为内核分配的端口（测试端口用 0 绑定）。"""

    def __init__(
        self,
        *,
        messages: list[FixtureMessage] | None = None,
        uidvalidity: int = 1,
        username: str = "student",
        password: str = "secret",
        mailbox: str = "INBOX",
    ) -> None:
        self._messages = list(messages or [])
        self._uidvalidity = uidvalidity
        self._username = username
        self._password = password
        self._mailbox = mailbox
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []
        self._connections: list[socket.socket] = []
        self._accepted = 0

    # --- 生命周期 --------------------------------------------------------------

    def start(self) -> Self:
        if self._listener is not None:
            return self
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.settimeout(0.2)
        self._listener = listener
        self._thread = threading.Thread(target=self._accept_loop, name="imap-fixture", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            with contextlib.suppress(OSError):
                self._listener.close()
        for connection in list(self._connections):
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
        for thread in list(self._threads):
            thread.join(timeout=5)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exception: object) -> None:
        self.stop()

    # --- 状态 ------------------------------------------------------------------

    @property
    def port(self) -> int:
        if self._listener is None:
            raise RuntimeError("the fixture server is not started")
        return int(self._listener.getsockname()[1])

    @property
    def uidvalidity(self) -> int:
        return self._uidvalidity

    @property
    def mailbox(self) -> str:
        return self._mailbox

    @property
    def credentials(self) -> tuple[str, str]:
        """夹具账号（服务端侧读取；测试用它与被测实现的配置对齐）。"""
        return self._username, self._password

    @property
    def messages(self) -> tuple[FixtureMessage, ...]:
        return tuple(self._messages)

    @property
    def accepted_connections(self) -> int:
        """已被接受的连接数——用于证明"第二次拉取真的又连了一次"。"""
        return self._accepted

    def message(self, uid: int) -> FixtureMessage | None:
        for candidate in self._messages:
            if candidate.uid == uid:
                return candidate
        return None

    def flags_of(self, uid: int) -> frozenset[str]:
        message = self.message(uid)
        return frozenset() if message is None else frozenset(message.flags)

    def append(self, uid: int, raw: bytes, flags: set[str] | None = None) -> FixtureMessage:
        """投递一封新邮件（测试用它模拟"邮箱里又来了一封"）。"""
        message = FixtureMessage(uid=uid, raw=raw, flags=set(flags or ()))
        self._messages.append(message)
        return message

    # --- 连接处理 --------------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                connection, _address = listener.accept()
            except OSError:
                continue
            self._accepted += 1
            self._connections.append(connection)
            worker = threading.Thread(
                target=self._serve_connection, args=(connection,), daemon=True
            )
            self._threads.append(worker)
            worker.start()

    def _serve_connection(self, connection: socket.socket) -> None:
        writer = _Writer(connection)
        try:
            with connection, connection.makefile("rb") as reader:
                writer.line(f"* OK [CAPABILITY {CAPABILITIES}] fixture ready")
                session = _Session(self, writer)
                while not session.closed:
                    line = reader.readline(MAX_LINE_BYTES)
                    if not line:
                        break
                    session.dispatch(line.rstrip(b"\r\n"))
        except OSError:
            # 客户端提前断开（例如 pull 结束后 logout）：夹具按正常收尾处理。
            pass
        finally:
            with contextlib.suppress(ValueError):
                self._connections.remove(connection)


class _Writer:
    """按行发送（字面量用 `literal` 单独发送，协议要求它紧跟 `{n}` 行）。"""

    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection
        self._lock = threading.Lock()

    def line(self, text: str) -> None:
        self.raw(text.encode("ascii", "replace") + CRLF)

    def raw(self, payload: bytes) -> None:
        with self._lock:
            self._connection.sendall(payload)

    def literal_line(self, prefix: str, payload: bytes, suffix: str) -> None:
        """`<prefix> {n}\\r\\n<payload><suffix>\\r\\n` —— imaplib 依赖 `{n}` 结尾来读字面量。"""
        head = f"{prefix} {{{len(payload)}}}".encode("ascii") + CRLF
        self.raw(head + payload + suffix.encode("ascii") + CRLF)


class _Session:
    """一条连接上的会话状态机（认证前/后、邮箱、UIDVALIDITY）。"""

    def __init__(self, server: ImapFixtureServer, writer: _Writer) -> None:
        self._server = server
        self._writer = writer
        self._authenticated = False
        self._selected = False
        self.closed = False

    # --- 分发 ------------------------------------------------------------------

    def dispatch(self, line: bytes) -> None:
        text = line.decode("ascii", "replace")
        tag, separator, rest = text.partition(" ")
        if not separator or not tag:
            self._writer.line("* BAD malformed command line")
            return
        tokens = _tokenize(rest)
        if not tokens:
            self._writer.line(f"{tag} BAD empty command")
            return
        name = tokens[0].upper()
        arguments = tokens[1:]
        handler = {
            "CAPABILITY": self._capability,
            "NOOP": self._noop,
            "LOGIN": self._login,
            "SELECT": self._select,
            "UID": self._uid,
            "STORE": self._store,
            "LOGOUT": self._logout,
        }.get(name)
        if handler is None:
            self._writer.line(f"{tag} BAD unsupported command {name}")
            return
        handler(tag, arguments)

    # --- 命令 ------------------------------------------------------------------

    def _capability(self, tag: str, arguments: list[str]) -> None:
        self._writer.line(f"* CAPABILITY {CAPABILITIES}")
        self._writer.line(f"{tag} OK CAPABILITY completed")

    def _noop(self, tag: str, arguments: list[str]) -> None:
        self._writer.line(f"{tag} OK NOOP completed")

    def _login(self, tag: str, arguments: list[str]) -> None:
        if len(arguments) != 2:
            self._writer.line(f"{tag} BAD LOGIN requires two arguments")
            return
        if arguments[0] != self._server.credentials[0] or arguments[1] != self._server.credentials[1]:
            self._writer.line(f"{tag} NO [AUTHENTICATIONFAILED] invalid credentials")
            return
        self._authenticated = True
        self._writer.line(f"{tag} OK LOGIN completed")

    def _select(self, tag: str, arguments: list[str]) -> None:
        if not self._authenticated:
            self._writer.line(f"{tag} NO not authenticated")
            return
        if not arguments or arguments[0] != self._server.mailbox:
            self._writer.line(f"{tag} NO no such mailbox")
            return
        self._selected = True
        messages = self._server.messages
        next_uid = max((message.uid for message in messages), default=0) + 1
        self._writer.line(f"* FLAGS ({' '.join(_KNOWN_FLAGS)})")
        self._writer.line(f"* {len(messages)} EXISTS")
        self._writer.line("* 0 RECENT")
        self._writer.line(f"* OK [UIDVALIDITY {self._server.uidvalidity}] UIDs valid")
        self._writer.line(f"* OK [UIDNEXT {next_uid}] Predicted next UID")
        self._writer.line(f"{tag} OK [READ-WRITE] SELECT completed")

    def _uid(self, tag: str, arguments: list[str]) -> None:
        if not self._authenticated or not self._selected:
            self._writer.line(f"{tag} NO mailbox is not selected")
            return
        if not arguments:
            self._writer.line(f"{tag} BAD UID requires a subcommand")
            return
        subcommand = arguments[0].upper()
        rest = arguments[1:]
        if subcommand == "SEARCH":
            self._search(tag, rest)
        elif subcommand == "FETCH":
            self._fetch(tag, rest)
        elif subcommand == "STORE":
            self._store(tag, rest)
        else:
            self._writer.line(f"{tag} BAD unsupported UID subcommand {subcommand}")

    def _search(self, tag: str, arguments: list[str]) -> None:
        criteria = [item.upper() for item in arguments]
        if criteria != ["ALL"]:
            self._writer.line(f"{tag} BAD only SEARCH ALL is implemented")
            return
        uids = " ".join(str(message.uid) for message in self._server.messages)
        self._writer.line(f"* SEARCH {uids}".rstrip())
        self._writer.line(f"{tag} OK SEARCH completed")

    def _fetch(self, tag: str, arguments: list[str]) -> None:
        if len(arguments) < 2:
            self._writer.line(f"{tag} BAD FETCH requires a uid and an item list")
            return
        try:
            uid = int(arguments[0])
        except ValueError:
            self._writer.line(f"{tag} BAD FETCH uid must be numeric")
            return
        message = self._server.message(uid)
        items = _split_items(" ".join(arguments[1:]))
        if message is None:
            # 真实服务器对不存在的 UID 回 OK 且不带数据；夹具照做（由客户端判定"没有报文"）。
            self._writer.line(f"{tag} OK FETCH completed")
            return

        sequence = 1 + [item.uid for item in self._server.messages].index(message.uid)
        plain: list[str] = []
        literal_name: str | None = None
        literal: bytes | None = None
        for item in items:
            upper = item.upper()
            if upper == "UID":
                plain.append(f"UID {message.uid}")
            elif upper == "FLAGS":
                plain.append(f"FLAGS ({' '.join(sorted(message.flags))})")
            elif upper == "RFC822.SIZE":
                plain.append(f"RFC822.SIZE {len(message.raw)}")
            elif upper in {"RFC822", "BODY[]", "BODY.PEEK[]"}:
                literal_name, literal = "RFC822", message.raw
                if upper != "BODY.PEEK[]":
                    # 真实服务器上 BODY[]/RFC822 会置 \Seen，PEEK 不会；夹具照做。
                    message.flags.add("\\Seen")
            elif upper == "RFC822.HEADER":
                literal_name = "RFC822.HEADER"
                literal = _headers_of(message.raw)
            else:
                self._writer.line(f"{tag} BAD unsupported FETCH item {item}")
                return

        if literal is None:
            self._writer.line(f"* {sequence} FETCH ({' '.join(plain)})")
        else:
            prefix = f"* {sequence} FETCH ({' '.join([*plain, literal_name])}"
            self._writer.literal_line(prefix, literal, ")")
        self._writer.line(f"{tag} OK FETCH completed")

    def _store(self, tag: str, arguments: list[str]) -> None:
        if len(arguments) < 3:
            self._writer.line(f"{tag} BAD STORE requires a uid, an operation and a flag list")
            return
        try:
            uid = int(arguments[0])
        except ValueError:
            self._writer.line(f"{tag} BAD STORE uid must be numeric")
            return
        operation = arguments[1].upper()
        if operation not in {"+FLAGS", "-FLAGS", "FLAGS"}:
            self._writer.line(f"{tag} BAD unsupported STORE operation {operation}")
            return
        message = self._server.message(uid)
        if message is None:
            self._writer.line(f"{tag} OK STORE completed")
            return
        requested = {flag for flag in _split_items(" ".join(arguments[2:]))}
        if operation == "FLAGS":
            message.flags = set(requested)
        elif operation == "+FLAGS":
            message.flags |= requested
        else:
            message.flags -= requested
        sequence = 1 + [item.uid for item in self._server.messages].index(message.uid)
        self._writer.line(
            f"* {sequence} FETCH (UID {message.uid} FLAGS ({' '.join(sorted(message.flags))}))"
        )
        self._writer.line(f"{tag} OK STORE completed")

    def _logout(self, tag: str, arguments: list[str]) -> None:
        self._writer.line("* BYE fixture logging out")
        self._writer.line(f"{tag} OK LOGOUT completed")
        self.closed = True


def _tokenize(text: str) -> list[str]:
    """切分命令行：支持双引号字符串（含 `\\"` 转义）、括号分组与裸 atom。"""
    tokens: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == " ":
            index += 1
            continue
        if character == "(":
            depth = 0
            start = index
            while index < length:
                if text[index] == "(":
                    depth += 1
                elif text[index] == ")":
                    depth -= 1
                    if depth == 0:
                        index += 1
                        break
                index += 1
            tokens.append(text[start:index])
            continue
        if character == '"':
            index += 1
            buffer: list[str] = []
            while index < length and text[index] != '"':
                if text[index] == "\\" and index + 1 < length:
                    index += 1
                buffer.append(text[index])
                index += 1
            index += 1
            tokens.append("".join(buffer))
            continue
        start = index
        while index < length and text[index] != " ":
            index += 1
        tokens.append(text[start:index])
    return tokens


def _split_items(value: str) -> list[str]:
    """把 `(RFC822 BODY[])` / `+FLAGS (\\Seen)` 的条目列表拆成 token。"""
    stripped = value.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        stripped = stripped[1:-1]
    return [item for item in stripped.split(" ") if item]


def _headers_of(raw: bytes) -> bytes:
    """报文头部（`RFC822.HEADER`）：到第一个空行为止，含该空行——与真实服务器一致。"""
    marker = raw.find(b"\r\n\r\n")
    if marker < 0:
        return raw
    return raw[: marker + 4]
