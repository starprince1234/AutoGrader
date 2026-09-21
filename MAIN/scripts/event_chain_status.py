"""One view of the whole event chain: per-module outbox backlogs, inbox receipts, JetStream state.

Each module owns its own outbox/inbox tables and its own publisher, so "is the event chain
actually working?" normally gets answered by four separate queries in four schemas plus a NATS
inspection, and the answer is assembled by hand. This script does that assembly once.

It is deliberately discovery-based: it finds outbox/inbox tables and their columns from
`information_schema`, and it reports a column as absent rather than assuming a layout. MAIN does
not own the modules' physical tables, so hard-coding table or column names here would both
duplicate module DDL and silently go stale when a module migrates.

Reported per table:
  unpublished / published   -- present only when the table has a publication timestamp
  attempts (max)            -- **failed** deliveries for the worst row, not deliveries attempted:
                               a row delivered on its first try keeps 0 (B2 starts rows at
                               `attempts=0` and only `_record_failure` increments it, while
                               `_mark_published` clears `last_error` and keeps the count). So 0 is
                               the normal healthy value and a positive value means "failed at least
                               once and was later delivered"
  dead-lettered             -- only when the table has such a column
  received / processed      -- inbox side

Usage:
    python MAIN/scripts/event_chain_status.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

MAIN_ROOT = Path(__file__).resolve().parents[1]

for raw in (MAIN_ROOT / ".env.integration").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    os.environ.setdefault(key.strip(), value.strip())

SYSTEM_SCHEMAS = ("pg_catalog", "information_schema")


def _target_description() -> str:
    """`host:port/database` only -- never the credentials."""
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    return f"{url.host}:{url.port}/{url.database}"


def _connect():
    """Connect from discrete parameters rather than the URL string.

    `DATABASE_URL` is a SQLAlchemy URL (`postgresql+psycopg://...`), which psycopg cannot parse;
    and its parse error quotes the connection string verbatim, which would print the password.
    Splitting it up avoids both problems.
    """
    import psycopg
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    return psycopg.connect(
        host=url.host,
        port=url.port,
        dbname=url.database,
        user=url.username,
        password=url.password,
        connect_timeout=10,
    )


def _module_of(schema: str) -> str:
    """Schemas are named after the owning module (`b2`, `b3`, ...); anything else stands alone."""
    return schema.upper() if len(schema) <= 3 and schema.startswith("b") else schema


def outbox_and_inbox_tables() -> list[tuple[str, str, list[str]]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            select table_schema, table_name, column_name
            from information_schema.columns
            where table_schema <> all(%s)
              and (table_name like '%%outbox%%' or table_name like '%%inbox%%')
            order by table_schema, table_name, ordinal_position
            """,
            (list(SYSTEM_SCHEMAS),),
        ).fetchall()

    tables: dict[tuple[str, str], list[str]] = {}
    for schema, table, column in rows:
        tables.setdefault((schema, table), []).append(column)
    return [(schema, table, columns) for (schema, table), columns in sorted(tables.items())]


def backlog_rows() -> list[tuple[str, str, str, str, str, str, str]]:
    report: list[tuple[str, str, str, str, str, str, str]] = []
    with _connect() as connection:
        for schema, table, columns in outbox_and_inbox_tables():
            target = f'"{schema}"."{table}"'

            def count(where: str = "") -> str:
                clause = f" where {where}" if where else ""
                value = connection.execute(f"select count(*) from {target}{clause}").fetchone()[0]
                return str(value)

            def maximum(column: str) -> str:
                value = connection.execute(
                    f'select coalesce(max("{column}"), 0) from {target}'
                ).fetchone()[0]
                return str(value)

            unpublished = count('"published_at" is null') if "published_at" in columns else "n/a"
            published = count('"published_at" is not null') if "published_at" in columns else "n/a"
            attempts = maximum("attempts") if "attempts" in columns else "n/a"
            if "dead_lettered_at" in columns:
                dead = count('"dead_lettered_at" is not null')
            elif "dead_lettered" in columns:
                dead = count('"dead_lettered"')
            else:
                dead = "n/a"
            received = count()
            processed = (
                count('"processed_at" is not null') if "processed_at" in columns else "n/a"
            )
            report.append(
                (f"{schema}.{table}", unpublished, published, attempts, dead, received, processed)
            )
    return report


async def jetstream_rows() -> list[str]:
    import nats

    client = await nats.connect(os.environ["NATS_URL"], connect_timeout=5)
    try:
        context = client.jetstream()
        lines: list[str] = []
        for info in await context.streams_info():
            state = info.state
            lines.append(
                f"  {info.config.name}: messages={state.messages} bytes={state.bytes} "
                f"subjects={list(info.config.subjects or [])}"
            )
        return lines
    finally:
        await client.close()


def main() -> int:
    print(f"event chain status (database {_target_description()})")
    print()
    print(f"{'table':<28}{'unpubl':>8}{'publ':>8}{'attempts':>10}{'dead':>7}{'recv':>8}{'proc':>8}")
    print("-" * 77)
    for row in backlog_rows():
        name, unpublished, published, attempts, dead, received, processed = row
        print(
            f"{name:<28}{unpublished:>8}{published:>8}{attempts:>10}{dead:>7}"
            f"{received:>8}{processed:>8}"
        )

    print()
    print("JetStream streams:")
    try:
        for line in asyncio.run(jetstream_rows()):
            print(line)
    except Exception as error:  # noqa: BLE001 - a status tool should report, not raise
        print(f"  unavailable: {type(error).__name__}: {error}")

    print()
    print(
        "'attempts' counts *failed* deliveries (a first-try success stays 0), so a positive value"
        " means 'failed at least once and was later delivered', not 'is failing now' -- a row still"
        " failing shows up in 'unpubl' or 'dead'. The pass condition is unpubl == 0 and dead == 0."
    )
    print(
        "Those two are necessary, not sufficient: they also hold when nothing has been enqueued for"
        " a while. To show publishing still *works*, emit one event and watch 'unpubl' return to 0"
        " (measured 2026-09-20: one submission produced 7 new b2 outbox rows, all published)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
