"""Probe the mail endpoints without any credential: TLS, greetings, capability, ID ordering.

Why this exists separately from `probe_imap_mailbox.py`: most mail-intake problems are not
"wrong password" but "the provider is unreachable from this host", "the port is wrong", or "the
client must send ID before the provider will talk". Those are answerable with no secret at all,
and answering them first keeps a failed login from being blamed on the wrong thing.

Usage::

    python MAIN/scripts/probe_mail_endpoints.py --imap-host imap.qq.com --smtp-host smtp.qq.com
"""

from __future__ import annotations

import argparse
import imaplib
import smtplib
import socket
import ssl

imaplib.Commands["ID"] = ("AUTH", "SELECTED")


def probe_tls(host: str, port: int) -> tuple[bool, str]:
    """Bare TLS handshake: is the endpoint reachable and does its certificate validate?"""
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=15) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return True, f"{tls.version()} {tls.cipher()[0]}"
    except Exception as error:  # noqa: BLE001 - 任何失败都要如实报出来
        return False, f"{type(error).__name__}: {error}"


def probe_imap(host: str, port: int) -> None:
    print(f"--- IMAP {host}:{port} ---")
    ok, detail = probe_tls(host, port)
    print("tls:", "ok" if ok else "failed", "|", detail)
    if not ok:
        return
    try:
        client = imaplib.IMAP4_SSL(host, port, timeout=20)
    except Exception as error:  # noqa: BLE001
        print("connect:", type(error).__name__, error)
        return
    print("greeting:", client.welcome.decode("utf-8", "replace").strip()[:120])
    try:
        typ, data = client.capability()
        caps = b" ".join(part or b"" for part in data).decode("utf-8", "replace")
        print("capability:", typ, caps[:400])
    except Exception as error:  # noqa: BLE001
        print("capability failed:", type(error).__name__, error)
    # ID 命令的合法状态因服务商而异：163 只在 AUTH/SELECTED 允许，QQ 与之不同。
    try:
        client._simple_command(  # noqa: SLF001 - imaplib 没有公开的 ID 辅助
            "ID", '("name" "autograder-probe" "version" "1.0")'
        )
        typ, data = client._untagged_response("OK", [None], "ID")  # noqa: SLF001
        print("ID before login:", typ, data)
    except imaplib.IMAP4.error as error:
        print("ID before login rejected:", error)
    try:
        client.logout()
    except Exception:  # noqa: BLE001
        pass


def probe_smtp(host: str, port: int) -> None:
    print(f"--- SMTP {host}:{port} ---")
    ok, detail = probe_tls(host, port)
    print("tls:", "ok" if ok else "failed", "|", detail)
    if not ok:
        return
    try:
        client = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        code, message = client.ehlo()
        print("ehlo:", code, message.decode("utf-8", "replace")[:300])
        print("starttls advertised:", client.has_extn("starttls"), "| auth:", client.esmtp_features.get("auth"))
        client.quit()
    except Exception as error:  # noqa: BLE001
        print("smtp failed:", type(error).__name__, error)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--imap-host", default="imap.qq.com")
    parser.add_argument("--imap-port", type=int, default=993)
    parser.add_argument("--smtp-host", default="smtp.qq.com")
    parser.add_argument("--smtp-port", type=int, default=465)
    arguments = parser.parse_args(argv)

    probe_imap(arguments.imap_host, arguments.imap_port)
    probe_smtp(arguments.smtp_host, arguments.smtp_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
