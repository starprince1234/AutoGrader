"""Read-only probe for the mail intake: can we log in and read the mailbox?

This is a diagnostic tool, not product code. It answers three questions without importing any
B2 module:

1. do the host/port/TLS and the credential (an IMAP authorization code, not the password)
   actually authenticate?
2. does the provider require the IMAP ``ID`` extension? Several Chinese providers answer
   ``Unsafe Login`` until the client identifies itself, and stdlib ``imaplib`` never sends it.
3. what does a message look like once it lands: subject, sender, attachment names and sizes --
   which is exactly what the intake's matcher keys on.

It never downloads message bodies, never writes files and never prints the credential.

Usage (the credential is read from the environment so it stays out of the shell history)::

    $env:B2_IMAP_PASSWORD = '<授权码>'
    python MAIN/scripts/probe_imap_mailbox.py --host imap.qq.com --username 3363802491@qq.com
"""

from __future__ import annotations

import argparse
import email
import imaplib
import os
import sys
from email.header import decode_header

imaplib.Commands["ID"] = ("AUTH", "SELECTED")  # noqa: N801 - imaplib's own table


def decode(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for chunk, encoding in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(encoding or "utf-8", "replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def send_id(client: imaplib.IMAP4) -> str:
    """Some providers refuse commands until the client identifies itself."""
    try:
        client._simple_command(  # noqa: SLF001 - imaplib has no public ID helper
            "ID", '("name" "autograder-probe" "version" "1.0" "vendor" "autograder")'
        )
        client._untagged_response("OK", [None], "ID")  # noqa: SLF001
        return "accepted"
    except imaplib.IMAP4.error as error:
        return f"rejected: {error}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("B2_IMAP_HOST", ""))
    parser.add_argument("--port", type=int, default=int(os.environ.get("B2_IMAP_PORT", "993")))
    parser.add_argument("--username", default=os.environ.get("B2_IMAP_USERNAME", ""))
    parser.add_argument("--mailbox", default=os.environ.get("B2_IMAP_MAILBOX", "INBOX"))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--no-id", action="store_true", help="skip the ID extension on purpose")
    arguments = parser.parse_args(argv)

    password = os.environ.get("B2_IMAP_PASSWORD", "")
    if not arguments.host or not arguments.username:
        print("missing --host/--username (or B2_IMAP_HOST/B2_IMAP_USERNAME)", file=sys.stderr)
        return 2
    if not password:
        print("missing B2_IMAP_PASSWORD (the IMAP authorization code)", file=sys.stderr)
        return 2

    print(f"connecting {arguments.host}:{arguments.port} ssl=implicit as {arguments.username}")
    try:
        client = imaplib.IMAP4_SSL(arguments.host, arguments.port)
    except OSError as error:
        print(f"FAIL connect: {error}", file=sys.stderr)
        return 1
    try:
        print("greeting:", client.welcome.decode("utf-8", "replace")[:120])
        try:
            client.login(arguments.username, password)
        except imaplib.IMAP4.error as error:
            print(f"FAIL login: {error}", file=sys.stderr)
            print("hint: 163/QQ need an IMAP authorization code, not the account password", file=sys.stderr)
            return 1
        print("login: ok")

        if not arguments.no_id:
            print("ID extension:", send_id(client))

        status, data = client.select(arguments.mailbox, readonly=True)
        if status != "OK":
            print(f"FAIL select {arguments.mailbox}: {data}", file=sys.stderr)
            return 1
        total = int(data[0] or 0)
        print(f"mailbox {arguments.mailbox}: {total} message(s)")

        status, found = client.search(None, "ALL")
        ids = (found[0] or b"").split()
        for message_id in ids[-arguments.limit :]:
            status, payload = client.fetch(message_id, "(RFC822.HEADER)")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                print(f"  message {message_id.decode()}: header fetch failed")
                continue
            headers = email.message_from_bytes(payload[0][1])
            print(
                f"  message {message_id.decode()}: "
                f"subject={decode(headers.get('Subject'))!r} "
                f"from={decode(headers.get('From'))!r}"
            )
            status, structure = client.fetch(message_id, "(BODYSTRUCTURE)")
            if status == "OK" and structure:
                raw = structure[0] if isinstance(structure[0], bytes) else b""
                names = [part for part in raw.decode("utf-8", "replace").split('"') if "." in part]
                attachments = sorted({name for name in names if " " not in name and len(name) < 200})
                print(f"    parts that look like filenames: {attachments[:6]}")
        client.logout()
        return 0
    finally:
        try:
            client.shutdown()
        except Exception:  # noqa: BLE001 - shutdown after a failed login raises
            pass


if __name__ == "__main__":
    raise SystemExit(main())
