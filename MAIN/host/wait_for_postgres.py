"""Block until a PostgreSQL DSN accepts connections, or fail loudly.

    python MAIN/host/wait_for_postgres.py --dsn <sqlalchemy-dsn> --timeout 120

The container entrypoint runs this before it migrates anything. PostgreSQL starting is not
the same as PostgreSQL being ready to authenticate: `depends_on: service_healthy` covers
the compose case, but the entrypoint must also hold for `docker run` and for a database
that is merely slow, and a crash-looping host is a worse failure report than a bounded
wait that names what it waited for.

The DSN is parsed with SQLAlchemy's own URL parser and the components are handed to psycopg
as keyword arguments, mirroring `autograder_b4.ops.provision`: a password containing
spaces, quotes or `=` must not be able to corrupt a conninfo string. The DSN is never
printed -- not even its host -- because the entrypoint's output ends up in deployment logs.
"""

from __future__ import annotations

import argparse
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait for a PostgreSQL DSN to accept connections.")
    parser.add_argument("--dsn", required=True, help="SQLAlchemy DSN (postgresql+psycopg://…)")
    parser.add_argument("--timeout", type=int, default=120, help="seconds to keep retrying")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between attempts")
    arguments = parser.parse_args(argv)

    from sqlalchemy.engine import make_url

    url = make_url(arguments.dsn)
    if not url.drivername.startswith("postgresql"):
        print(f"error: not a PostgreSQL DSN (driver {url.drivername!r})", file=sys.stderr)
        return 2

    import psycopg

    deadline = time.monotonic() + arguments.timeout
    last: str = "no attempt made"
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(
                host=url.host,
                port=url.port,
                dbname=url.database,
                user=url.username,
                password=url.password,
                connect_timeout=3,
            ):
                print(f"postgres: accepting connections (database {url.database})")
                return 0
        except Exception as error:  # noqa: BLE001 - any failure means "not ready yet"
            # `type` only: the driver message repeats the role and database name, and this
            # output is deployment log material.
            last = type(error).__name__
        time.sleep(arguments.interval)

    print(
        f"error: PostgreSQL not reachable within {arguments.timeout}s (last: {last})",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
