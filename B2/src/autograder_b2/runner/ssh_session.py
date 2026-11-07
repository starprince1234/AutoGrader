"""`ssh_session` 的固定驱动、严格命令校验与证据解析（设计文档 §3.3/§7）。

本模块把"一次真实 SSH 会话"拆成三块**可独立测试**的部分（真正的执行在 `runner.run_cases`）：

| 部分 | 职责 | 是否启动进程 |
|---|---|---|
| `parse_student_ssh_command` / `render_ssh_client_argv` | 学生首行 argv 的严格白名单校验 + 固定 ssh 客户端参数 | 否 |
| `render_ssh_driver_program` | B2 固定在 nsjail 内运行的会话驱动程序（起 sshd、在 client chroot 里降权运行真实 ssh 客户端、读 sshd 日志） | 否（只是源码） |
| `parse_ssh_evidence` | 从驱动 stderr（带运行级 nonce）解析证据 | 否 |

安全边界：

- 学生脚本被当成**交互式会话指令序列**：只有首行是 `ssh [user@]127.0.0.1` 的严格 argv 会被执行；
  后续行只在登录成功后写入远端会话；
- 驱动以用户命名空间 root 起 sshd，客户端与远端会话都降权到 subordinate uid；会话 chroot 到
  最小练习根（沙箱根本身）；
- 口令只经驱动 stdin 传入（不进 argv/env/文件），任何证据/日志都不含口令与宿主绝对路径；
- 证据只写驱动 stderr（`B2SSH-EVIDENCE <nonce> key=value`）。学生可见输出只写驱动 stdout。
"""

from __future__ import annotations

import json
import re
import secrets
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from autograder_b2.domain.evidence import SandboxUnavailableError
from autograder_b2.runner.package import PackageSshConfig
from autograder_b2.runner.policy import SshSandboxPaths

#: 驱动 stderr 上的证据行前缀（真正的前缀还带运行级 nonce）。
SSH_EVIDENCE_PREFIX = "B2SSH-EVIDENCE"

#: 驱动退出码（宿主据此区分"学生侧失败"与"环境起不来"）。
SSH_DRIVER_EXIT_INVALID_SCRIPT = 20
SSH_DRIVER_EXIT_SSHD_FAILED = 21
SSH_DRIVER_EXIT_BINARY_MISSING = 22
SSH_DRIVER_EXIT_LOGIN_TIMEOUT = 23
SSH_DRIVER_EXIT_SESSION_TIMEOUT = 24
SSH_DRIVER_EXIT_DRIVER_ERROR = 25

#: sshd 就绪等待上限（秒）：超过即判"练习环境起不来"（基础设施故障），不折算成学生失败。
SSH_SERVICE_READY_SECONDS = 5.0

#: 驱动内固定路径（沙箱内视图；口令/密钥/日志全部落在 tmpfs）。
SSH_RUNTIME_DIR = "/tmp/b2-ssh"
SSH_SSHD_CONFIG = SSH_RUNTIME_DIR + "/sshd_config"
SSH_SSHD_LOG = SSH_RUNTIME_DIR + "/sshd.log"
SSH_HOST_KEY = SSH_RUNTIME_DIR + "/ssh_host_ed25519_key"
SSH_CLIENT_ROOT = "/client"

#: 首行最大字符数与 token 数（严格 argv 的上界，超出即拒绝）。
MAX_SSH_COMMAND_LENGTH = 512
MAX_SSH_COMMAND_TOKENS = 8

#: 允许的 SSH 用户名（POSIX 的保守子集；不允许空格、斜杠、控制字符）。
SSH_USERNAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")
#: 严格形如 `[user@]127.0.0.1`。
SSH_TARGET_PATTERN = re.compile(r"^(?:(?P<user>[A-Za-z_][A-Za-z0-9_.-]{0,31})@)?127\.0\.0\.1$")
_SSH_HOST = "127.0.0.1"

#: 驱动注入的固定客户端选项：不校验/不记录 host key、只允许密码认证、不读本地 ssh_config。
SSH_FIXED_CLIENT_OPTIONS: tuple[str, ...] = (
    "-F",
    "/dev/null",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "GlobalKnownHostsFile=/dev/null",
    "-o",
    "PreferredAuthentications=password",
    "-o",
    "PubkeyAuthentication=no",
    "-o",
    "PasswordAuthentication=yes",
    "-o",
    "NumberOfPasswordPrompts=1",
    "-o",
    "ForwardAgent=no",
    "-o",
    "ForwardX11=no",
)


@dataclass(frozen=True, slots=True)
class SshLoginCommand:
    """学生首行解析出的登录参数（`user`/`port` 可为 None = 采用 ssh 客户端默认行为）。"""

    user: str | None
    port: int | None


def parse_student_ssh_command(line: str) -> tuple[SshLoginCommand | None, str | None]:
    """严格校验学生首行；返回 `(command, error)`。

    只接受 `ssh [user@]127.0.0.1`，可选 `-p <端口>`、`-l <用户>` 各一次。**任何**其他 token
    （`-o`、`ProxyCommand`、重定向、第二条命令、路径形式的 ssh……）都拒绝：它们要么能在本地
    执行任意命令，要么会改变登录目标，都会让"真实 SSH 客户端"这一判定失去意义。
    """
    if not isinstance(line, str):
        return None, "the ssh command line is not text"
    stripped = line.strip()
    if not stripped:
        return None, "the first line is empty; it must be an ssh command"
    if len(stripped) > MAX_SSH_COMMAND_LENGTH:
        return None, "the ssh command is too long"
    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        return None, "the ssh command cannot be parsed"
    if not tokens or tokens[0] != "ssh":
        return None, "the first line must begin with the ssh command"
    if len(tokens) > MAX_SSH_COMMAND_TOKENS:
        return None, "the ssh command has too many arguments"
    user: str | None = None
    port: int | None = None
    target_seen = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-p":
            if port is not None:
                return None, "the ssh command repeats -p"
            index += 1
            if index >= len(tokens):
                return None, "the ssh command is missing the -p value"
            value = tokens[index]
            if not value.isdigit() or not 1 <= int(value) <= 65535:
                return None, "the ssh command has an invalid port"
            port = int(value)
            index += 1
            continue
        if token == "-l":
            if user is not None:
                return None, "the ssh command repeats the user"
            index += 1
            if index >= len(tokens):
                return None, "the ssh command is missing the -l value"
            value = tokens[index]
            if not SSH_USERNAME_PATTERN.match(value):
                return None, "the ssh command has an invalid user name"
            user = value
            index += 1
            continue
        if target_seen:
            return None, "the ssh command has an unexpected extra argument"
        match = SSH_TARGET_PATTERN.match(token)
        if match is None:
            return None, "the ssh target must be exactly 127.0.0.1 (optionally user@)"
        inline_user = match.group("user")
        if inline_user is not None:
            if user is not None:
                return None, "the ssh command declares the user twice"
            user = inline_user
        target_seen = True
        index += 1
    if not target_seen:
        return None, "the ssh command has no 127.0.0.1 target"
    return SshLoginCommand(user=user, port=port), None


def render_ssh_client_argv(
    ssh_client_path: str, command: SshLoginCommand, *, connect_timeout_seconds: float
) -> list[str]:
    """固定 argv：只来自驱动 + 严格校验后的学生参数（绝无 `-o`/shell 字符串）。"""
    argv = [ssh_client_path, *SSH_FIXED_CLIENT_OPTIONS]
    argv.extend(
        [
            "-o",
            f"ConnectTimeout={max(1, round(connect_timeout_seconds))}",
        ]
    )
    if command.port is not None:
        argv.extend(["-p", str(command.port)])
    if command.user is not None:
        argv.extend(["-l", command.user])
    argv.append(_SSH_HOST)
    return argv


def new_ssh_nonce() -> str:
    """运行级 nonce（每次批量执行一个；证据行只在带这个 nonce 时被采信）。"""
    return secrets.token_hex(16)


def render_ssh_driver_program() -> str:
    """固定会话驱动程序源码（同输入逐字节相同；提交侧无法替换）。"""
    return _SSH_DRIVER_TEMPLATE


def build_ssh_driver_config(
    *,
    nonce: str,
    entry_name: str,
    ssh: PackageSshConfig,
    sshd_path: str,
    ssh_client_path: str,
    ssh_keygen_path: str,
    max_output_bytes: int,
) -> dict[str, object]:
    """驱动的 stdin 配置（**含口令**：只经 stdin 传给驱动，绝不进 argv/env/文件）。"""
    return {
        "nonce": nonce,
        "entry_name": entry_name,
        "port": ssh.port,
        "username": ssh.username,
        "password": ssh.password,
        "login_timeout_seconds": ssh.login_timeout_seconds,
        "session_timeout_seconds": ssh.session_timeout_seconds,
        "max_output_bytes": max_output_bytes,
        "sshd_path": sshd_path,
        "ssh_client_path": ssh_client_path,
        "ssh_keygen_path": ssh_keygen_path,
    }


def dump_ssh_driver_config(config: dict[str, object]) -> str:
    return json.dumps(config, sort_keys=True, separators=(",", ":"))


# --- NSS 文件（宿主侧生成；每次运行一份，运行后删除） ---------------------------------------


def _shadow_hash(password: str) -> str:
    """SHA-512 crypt 口令哈希（Linux 的 `/etc/shadow` 格式）。"""
    try:
        import crypt
    except ImportError as error:  # pragma: no cover - 生产 Linux 才有
        raise SandboxUnavailableError(
            "ssh practice environment requires Python's crypt module for /etc/shadow"
        ) from error
    try:
        method = crypt.METHOD_SHA512
    except AttributeError as error:  # pragma: no cover - 极老平台
        raise SandboxUnavailableError("ssh practice environment lacks sha512 crypt") from error
    return crypt.crypt(password, crypt.mksalt(method))


def render_ssh_nss_files(username: str, password: str) -> tuple[str, str, str]:
    """生成运行时 `/etc/passwd`、`/etc/group`、`/etc/shadow` 文本（不含口令明文）。

    两条入口都映射到同一个被囚 uid 65534（subuid 只有一个，见 `policy.profile_id_maps`）：
    练习账号会先出现，使客户端在未显式指定用户时把本地用户名解析为练习账号；`sshd` 账号供
    privileged-separation 子进程使用（其 uid 与练习账号相同，实测 setuid 成功）。

    `root`（uid 0）条目是必需的：驱动在用户命名空间内以 uid 0 起 sshd，而 OpenSSH 的
    `ssh-keygen` 会用 `getpwuid(getuid())` 解析当前用户，缺少 uid 0 的 passwd 条目时直接
    `fatal("No user exists for uid 0")`（退出码 255）——生产环境实测的 sshd 起不来根因。
    """
    passwd = (
        f"{username}:x:65534:65534:Practice Account:/home/{username}:/bin/bash\n"
        "sshd:x:65534:65534:Privilege separation user:/usr/share/empty.sshd:/usr/sbin/nologin\n"
        "root:x:0:0:namespace root:/root:/bin/bash\n"
    )
    group = f"{username}:x:65534:\nsshd:x:65534:\nroot:x:0:\n"
    shadow = (
        f"{username}:{_shadow_hash(password)}:19000:0:99999:7:::\n"
        "sshd:!*:19000:0:99999:7:::\n"
        "root:!*:19000:0:99999:7:::\n"
    )
    return passwd, group, shadow


def prepare_ssh_nss_files(paths: SshSandboxPaths, username: str, password: str) -> None:
    """把 NSS 文件写到运行目录（0600/0644；由 nsjail bind 覆盖沙箱内 `/etc/*`）。"""
    passwd, group, shadow = render_ssh_nss_files(username, password)
    paths.passwd_path.write_text(passwd, encoding="utf-8")
    paths.group_path.write_text(group, encoding="utf-8")
    paths.shadow_path.write_text(shadow, encoding="utf-8")
    paths.passwd_path.chmod(0o644)
    paths.group_path.chmod(0o644)
    paths.shadow_path.chmod(0o600)


# --- 证据解析 -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SshRunEvidence:
    """从驱动 stderr 解析出的一次 SSH 运行事实（缺失项为 `None` = 驱动没报告）。"""

    nonce: str
    service_ready: bool | None = None
    auth_succeeded: bool | None = None
    session_closed: bool | None = None
    ssh_exit_code: int | None = None
    ssh_killed: bool = False
    invalid_first_line: bool = False
    driver_error: str | None = None
    sshd_log_digest: str | None = None
    #: 原始证据键值（诊断用；**不进**证据摘要，可能含非确定值）。
    fields: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def evidence_complete(self) -> bool:
        """驱动是否完整报告了三条判定所需的全部事实。"""
        return (
            self.service_ready is not None
            and self.auth_succeeded is not None
            and self.session_closed is not None
        )


def _as_bool(value: str) -> bool | None:
    if value in ("1", "true", "True"):
        return True
    if value in ("0", "false", "False"):
        return False
    return None


def _as_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def parse_ssh_evidence(stderr_text: str, nonce: str) -> SshRunEvidence:
    """解析驱动 stderr 里带**本次运行 nonce** 的证据行；其他行（traceback 等）一律忽略。

    nonce 是"驱动自有证据"与"学生可控文本"之间的边界：只有驱动知道这个随机值，且学生代码
    只会在远端会话里执行（输出经 sshd 回到驱动 stdout），写不到驱动 stderr。
    """
    prefix = f"{SSH_EVIDENCE_PREFIX} {nonce} "
    values: dict[str, str] = {}
    ordered: list[tuple[str, str]] = []
    for line in stderr_text.split("\n"):
        line = line.rstrip("\r")
        if not line.startswith(prefix):
            continue
        payload = line[len(prefix) :]
        key, _separator, value = payload.partition("=")
        if not key or not _separator:
            continue
        # 同一 key 以最后一次为准（驱动只在末尾报告最终判定；早期只报 service_ready）。
        values[key] = value
        ordered.append((key, value))
    return SshRunEvidence(
        nonce=nonce,
        service_ready=_as_bool(values.get("service_ready", "")),
        auth_succeeded=_as_bool(values.get("auth_succeeded", "")),
        session_closed=_as_bool(values.get("session_closed", "")),
        ssh_exit_code=_as_int(values.get("ssh_exit_code", "")),
        ssh_killed=values.get("ssh_killed") == "1",
        invalid_first_line=values.get("invalid_first_line") == "1",
        driver_error=values.get("driver_error") or None,
        sshd_log_digest=values.get("sshd_log_digest") or None,
        fields=tuple(ordered),
    )


def ssh_check_passed(
    check: str, evidence: SshRunEvidence, *, ssh_client_success: bool
) -> bool:
    """一条 `ssh_check` 是否成立（设计文档 §3.3 的判定依据）。

    - `service_ready`：sshd 真的监听在 `127.0.0.1:<port>`（起不来在宿主侧已升级为基础设施故障）；
    - `auth_succeeded`：sshd 记录 `Accepted password for <username> from 127.0.0.1`；
    - `session_closed`：sshd 记录该用户 session closed，且客户端以成功状态退出、不是被强杀。
    """
    if check == "service_ready":
        return evidence.service_ready is True
    if check == "auth_succeeded":
        return evidence.auth_succeeded is True
    if check == "session_closed":
        return (
            evidence.session_closed is True
            and ssh_client_success
            and not evidence.ssh_killed
        )
    return False


def ssh_check_failure_message(check: str) -> str:
    """学生侧失败时的脱敏消息（只描述未达成的事实，不给出口令或期望值）。"""
    if check == "service_ready":
        return "the ssh practice service did not report a listener on 127.0.0.1"
    if check == "auth_succeeded":
        return (
            "the real ssh client did not complete password authentication"
            " (sshd recorded no 'Accepted password for <user> from 127.0.0.1')"
        )
    return (
        "the ssh session did not close cleanly: sshd recorded no session close,"
        " or the client did not exit successfully within the limit"
    )


def ssh_env_problems(paths: SshSandboxPaths) -> tuple[str, ...]:
    """运行目录布局的最小就绪检查（缺失即基础设施故障，不是学生失败）。"""
    problems: list[str] = []
    for path in (
        paths.practice_root,
        paths.empty_root,
        paths.client_root,
        paths.passwd_path,
        paths.group_path,
        paths.shadow_path,
    ):
        if not Path(path).exists():
            problems.append(f"ssh sandbox path missing: {Path(path).name}")
    return tuple(problems)


_SSH_DRIVER_TEMPLATE = r'''"""B2 SSH 会话驱动程序（服务端固定生成；提交侧不得提供或替换同名文件）。

由 runner 写进工作区、经 nsjail 以“用户命名空间内的 root”启动。职责：

1. 从 stdin 读取本次运行配置（**含练习口令**；口令只走 stdin，不进 argv/env/文件）；
2. 在沙箱内生成临时 `/etc` 覆盖之外的运行时材料（sshd 配置、主机密钥）并启动真实 sshd；
3. 严格校验学生首行 ssh 命令（宿主已校验一次，这里是纵深防御）；
4. fork 一个子进程：chroot 到最小客户端根、降权到 subordinate uid，在 PTY 中运行真实 ssh 客户端；
5. 在密码提示出现时输入口令；登录成功后把学生后续行依次写入远端会话；
6. 读取 sshd 自有日志判定 service_ready / auth_succeeded / session_closed，把证据写到本进程
   stderr（带运行级 nonce）；学生可见输出只写 stdout；
7. 超时/异常时清理整棵进程树。

证据行格式：`B2SSH-EVIDENCE <nonce> key=value`。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import select
import signal
import subprocess
import sys
import termios
import time

EVIDENCE_PREFIX = "B2SSH-EVIDENCE"
RUNTIME_DIR = "/tmp/b2-ssh"
SSHD_CONFIG = RUNTIME_DIR + "/sshd_config"
SSHD_LOG = RUNTIME_DIR + "/sshd.log"
HOST_KEY = RUNTIME_DIR + "/ssh_host_ed25519_key"
CLIENT_ROOT = "/client"
WORKSPACE = "/workspace"
JAILED_UID = 65534
JAILED_GID = 65534
DEVNULL = os.devnull

EXIT_INVALID_SCRIPT = 20
EXIT_SSHD_FAILED = 21
EXIT_BINARY_MISSING = 22
EXIT_LOGIN_TIMEOUT = 23
EXIT_SESSION_TIMEOUT = 24
EXIT_DRIVER_ERROR = 25

SERVICE_READY_TIMEOUT = 5.0
USERNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")
TARGET_RE = re.compile(r"^(?:(?P<user>[A-Za-z_][A-Za-z0-9_.-]{0,31})@)?127\.0\.0\.1$")
PASSWORD_PROMPT_RE = re.compile(rb"password:\s*$", re.IGNORECASE)
FIXED_OPTIONS = [
    "-F", "/dev/null",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "GlobalKnownHostsFile=/dev/null",
    "-o", "PreferredAuthentications=password",
    "-o", "PubkeyAuthentication=no",
    "-o", "PasswordAuthentication=yes",
    "-o", "NumberOfPasswordPrompts=1",
    "-o", "ForwardAgent=no",
    "-o", "ForwardX11=no",
]

_nonce = ""
_password = ""


def emit(**fields) -> None:
    for key, value in fields.items():
        sys.stderr.write(EVIDENCE_PREFIX + " " + _nonce + " " + key + "=" + str(value) + "\n")
    sys.stderr.flush()


def fail(code: int, reason: str) -> None:
    emit(driver_error=reason)
    sys.exit(code)


def read_config() -> dict:
    raw = sys.stdin.buffer.read(65536)
    if not raw:
        fail(EXIT_DRIVER_ERROR, "empty_config")
    try:
        config = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        fail(EXIT_DRIVER_ERROR, "invalid_config")
    if not isinstance(config, dict):
        fail(EXIT_DRIVER_ERROR, "invalid_config")
    return config


def parse_login(line: str):
    if not isinstance(line, str):
        return None
    stripped = line.strip()
    if not stripped or len(stripped) > 512:
        return None
    try:
        import shlex
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        return None
    if not tokens or tokens[0] != "ssh" or len(tokens) > 8:
        return None
    user = None
    port = None
    target = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-p":
            if port is not None:
                return None
            index += 1
            if index >= len(tokens) or not tokens[index].isdigit():
                return None
            port = int(tokens[index])
            if not 1 <= port <= 65535:
                return None
            index += 1
            continue
        if token == "-l":
            if user is not None:
                return None
            index += 1
            if index >= len(tokens) or not USERNAME_RE.match(tokens[index]):
                return None
            user = tokens[index]
            index += 1
            continue
        if target:
            return None
        match = TARGET_RE.match(token)
        if match is None:
            return None
        if match.group("user") is not None:
            if user is not None:
                return None
            user = match.group("user")
        target = True
        index += 1
    if not target:
        return None
    return {"user": user, "port": port}


def write_sshd_config(config: dict) -> None:
    port = int(config["port"])
    username = str(config["username"])
    text = "\n".join(
        [
            "Port " + str(port),
            "ListenAddress 127.0.0.1",
            "HostKey " + HOST_KEY,
            "PidFile " + RUNTIME_DIR + "/sshd.pid",
            "LogLevel VERBOSE",
            "SyslogFacility AUTH",
            "UsePAM no",
            "PasswordAuthentication yes",
            "KbdInteractiveAuthentication no",
            "ChallengeResponseAuthentication no",
            "PubkeyAuthentication no",
            "AuthorizedKeysFile none",
            "PermitRootLogin no",
            "PermitEmptyPasswords no",
            "StrictModes no",
            "UseDNS no",
            "PrintMotd no",
            "AllowUsers " + username,
            "ChrootDirectory /",
            "AllowTcpForwarding no",
            "AllowAgentForwarding no",
            "X11Forwarding no",
            "PermitTunnel no",
            "GatewayPorts no",
            "MaxStartups 2",
            "MaxSessions 1",
            "LoginGraceTime 5",
            "ClientAliveInterval 0",
            "",
        ]
    )
    with open(SSHD_CONFIG, "w", encoding="utf-8") as handle:
        handle.write(text)


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(262144)
    except OSError:
        return ""


def start_sshd(config: dict) -> subprocess.Popen:
    os.makedirs(RUNTIME_DIR, mode=0o700, exist_ok=True)
    os.makedirs("/run/sshd", mode=0o755, exist_ok=True)
    keygen = str(config["ssh_keygen_path"])
    result = subprocess.run(
        [keygen, "-q", "-t", "ed25519", "-N", "", "-f", HOST_KEY],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    if result.returncode != 0 or not os.path.exists(HOST_KEY):
        emit(service_ready=0)
        fail(EXIT_SSHD_FAILED, "host_key_generation_failed")
    write_sshd_config(config)
    log_handle = open(SSHD_LOG, "wb")
    process = subprocess.Popen(
        [str(config["sshd_path"]), "-f", SSHD_CONFIG, "-D", "-e"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=log_handle,
        start_new_session=True,
        env={
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
        },
    )
    deadline = time.monotonic() + SERVICE_READY_TIMEOUT
    listening = re.compile(
        r"Server listening on 127\.0\.0\.1 port " + str(int(config["port"]))
    )
    while time.monotonic() < deadline:
        if listening.search(read_text(SSHD_LOG)):
            emit(service_ready=1)
            return process
        if process.poll() is not None:
            emit(service_ready=0)
            fail(EXIT_SSHD_FAILED, "sshd_exited")
        time.sleep(0.05)
    emit(service_ready=0)
    fail(EXIT_SSHD_FAILED, "sshd_listen_timeout")
    raise SystemExit(EXIT_SSHD_FAILED)


def client_argv(config: dict, login: dict) -> list:
    argv = [str(config["ssh_client_path"]), *FIXED_OPTIONS]
    timeout = max(1, int(round(float(config["login_timeout_seconds"]))))
    argv.extend(["-o", "ConnectTimeout=" + str(timeout)])
    if login.get("port") is not None:
        argv.extend(["-p", str(int(login["port"]))])
    if login.get("user") is not None:
        argv.extend(["-l", str(login["user"])])
    argv.append("127.0.0.1")
    return argv


def spawn_client(config: dict, login: dict):
    master, slave = os.openpty()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(master)
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            for fd in (0, 1, 2):
                os.dup2(slave, fd)
            if slave > 2:
                os.close(slave)
            os.chroot(CLIENT_ROOT)
            os.chdir("/")
            os.setgroups([])
            os.setgid(JAILED_GID)
            os.setuid(JAILED_UID)
            environ = {
                "PATH": "/usr/bin:/bin",
                "HOME": "/",
                "TERM": "xterm",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            }
            argv = client_argv(config, login)
            os.execve(argv[0], argv, environ)
        except BaseException:
            try:
                os.write(2, b"exec-failed")
            finally:
                os._exit(EXIT_BINARY_MISSING)
    os.close(slave)
    return pid, master


def kill_group(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except OSError:
            return
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                reaped, _status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return
            if reaped == pid:
                return
            time.sleep(0.02)


def run_session(config: dict, login: dict, script_lines: list) -> int:
    global _password
    max_output = int(config["max_output_bytes"])
    login_timeout = float(config["login_timeout_seconds"])
    session_timeout = float(config["session_timeout_seconds"])
    pid, master = spawn_client(config, login)
    os.set_blocking(master, False)
    started = time.monotonic()
    login_deadline = started + login_timeout
    session_deadline = None
    authenticated = False
    password_sent = False
    lines_sent = False
    killed = False
    exit_code = None
    forwarded = 0
    pending = b""
    while True:
        now = time.monotonic()
        reaped, status = (0, 0)
        try:
            reaped, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            reaped = pid
        if reaped == pid:
            if os.WIFEXITED(status):
                exit_code = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                exit_code = 128 + os.WTERMSIG(status)
            else:
                exit_code = 255
            break
        if now - started > login_timeout + session_timeout + 10:
            killed = True
            break
        if not authenticated and now > login_deadline:
            killed = True
            break
        if authenticated and session_deadline is not None and now > session_deadline:
            killed = True
            break
        readable, _w, _x = select.select([master], [], [], 0.05)
        if readable:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                chunk = b""
            if chunk:
                pending = (pending + chunk)[-256:]
                if not password_sent and PASSWORD_PROMPT_RE.search(pending):
                    os.write(master, (_password + "\n").encode("utf-8"))
                    password_sent = True
                if forwarded < max_output:
                    room = max_output - forwarded
                    piece = chunk[:room]
                    try:
                        sys.stdout.buffer.write(piece)
                        sys.stdout.buffer.flush()
                    except OSError:
                        pass
                    forwarded += len(piece)
        if not authenticated:
            auth_pattern = (
                r"Accepted password for "
                + re.escape(str(config["username"]))
                + r" from 127\.0\.0\.1"
            )
            if re.search(auth_pattern, read_text(SSHD_LOG)):
                authenticated = True
                session_deadline = time.monotonic() + session_timeout
                emit(auth_succeeded=1)
                if not lines_sent and len(script_lines) > 1:
                    for line in script_lines[1:]:
                        try:
                            os.write(master, (line + "\n").encode("utf-8"))
                        except OSError:
                            break
                        time.sleep(0.05)
                lines_sent = True
    if killed:
        kill_group(pid)
        try:
            os.close(master)
        except OSError:
            pass
        emit(
            auth_succeeded=0 if not authenticated else 1,
            ssh_killed=1,
            ssh_exit_code=exit_code if exit_code is not None else -1,
        )
        return EXIT_LOGIN_TIMEOUT if not authenticated else EXIT_SESSION_TIMEOUT
    try:
        os.close(master)
    except OSError:
        pass
    log_text = read_text(SSHD_LOG)
    accepted = re.search(
        r"Accepted password for "
        + re.escape(str(config["username"]))
        + r" from 127\.0\.0\.1",
        log_text,
    )
    session_closed = re.search(
        r"(session closed for user |Close session: user |Disconnected from user )"
        + re.escape(str(config["username"])),
        log_text,
    )
    emit(
        auth_succeeded=1 if accepted is not None else 0,
        session_closed=1 if session_closed is not None else 0,
        ssh_exit_code=exit_code if exit_code is not None else -1,
        ssh_killed=0,
        sshd_log_digest=hashlib.sha256(log_text.encode("utf-8", "replace")).hexdigest(),
        invalid_first_line=0,
    )
    return 0


def main() -> int:
    global _nonce, _password
    config = read_config()
    _nonce = str(config.get("nonce", ""))
    if not _nonce:
        sys.exit(EXIT_DRIVER_ERROR)
    username = str(config.get("username", ""))
    if not USERNAME_RE.match(username):
        fail(EXIT_DRIVER_ERROR, "invalid_username")
    _password = str(config.get("password", ""))
    if not _password:
        fail(EXIT_DRIVER_ERROR, "empty_password")
    entry = os.path.join(WORKSPACE, str(config.get("entry_name", "")))
    try:
        with open(entry, "r", encoding="utf-8", errors="replace") as handle:
            script = handle.read(65536)
    except OSError:
        fail(EXIT_DRIVER_ERROR, "missing_entry")
    script_lines = script.splitlines()
    if not script_lines or parse_login(script_lines[0]) is None:
        emit(invalid_first_line=1)
        return EXIT_INVALID_SCRIPT
    login = parse_login(script_lines[0])
    for key in ("sshd_path", "ssh_client_path", "ssh_keygen_path"):
        path = str(config.get(key, ""))
        if not path.startswith("/") or not os.access(path, os.X_OK):
            fail(EXIT_BINARY_MISSING, "missing_" + key)
    client_path = str(config["ssh_client_path"])
    if not os.access(CLIENT_ROOT + client_path, os.X_OK):
        fail(EXIT_BINARY_MISSING, "missing_client_in_chroot")
    ssd = start_sshd(config)
    try:
        return run_session(config, login, script_lines)
    finally:
        kill_group(ssd.pid)
        for name in ("sshd_config", "ssh_host_ed25519_key", "ssh_host_ed25519_key.pub", "sshd.log"):
            try:
                os.unlink(os.path.join(RUNTIME_DIR, name))
            except OSError:
                pass


def entry() -> None:
    global _nonce
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as error:  # noqa: BLE001 - 驱动异常必须留证据而不是裸 traceback
        try:
            emit(driver_error="exception_" + type(error).__name__)
        except BaseException:
            pass
        os._exit(EXIT_DRIVER_ERROR)


if __name__ == "__main__":
    entry()
'''


__all__ = [
    "MAX_SSH_COMMAND_LENGTH",
    "MAX_SSH_COMMAND_TOKENS",
    "SSH_CLIENT_ROOT",
    "SSH_DRIVER_EXIT_BINARY_MISSING",
    "SSH_DRIVER_EXIT_DRIVER_ERROR",
    "SSH_DRIVER_EXIT_INVALID_SCRIPT",
    "SSH_DRIVER_EXIT_LOGIN_TIMEOUT",
    "SSH_DRIVER_EXIT_SESSION_TIMEOUT",
    "SSH_DRIVER_EXIT_SSHD_FAILED",
    "SSH_EVIDENCE_PREFIX",
    "SSH_FIXED_CLIENT_OPTIONS",
    "SSH_RUNTIME_DIR",
    "SSH_SERVICE_READY_SECONDS",
    "SshLoginCommand",
    "SshRunEvidence",
    "build_ssh_driver_config",
    "dump_ssh_driver_config",
    "new_ssh_nonce",
    "parse_ssh_evidence",
    "parse_student_ssh_command",
    "prepare_ssh_nss_files",
    "render_ssh_client_argv",
    "render_ssh_driver_program",
    "render_ssh_nss_files",
    "ssh_check_failure_message",
    "ssh_check_passed",
    "ssh_env_problems",
]
