#!/usr/bin/env python3
"""Ensure the NATS JetStream stream that carries platform events exists.

B2 publishes its outbox to `autograder.v1.*` and B3 consumes from the same subjects. The
stream itself is deployment infrastructure (RQ-B-4-20 assigns MQ operations to the platform
deployment, not to the producers): without it every publish fails with
`NoStreamResponseError`, the events sit in the outbox until their retry budget is exhausted,
and the resulting dead letters make the B2 readiness probe fail the next deployment.

This helper is idempotent and intentionally does not recreate/delete a stream that already
exists. A stream whose subjects no longer cover `autograder.>` is reported as an error
instead of being mutated: silently rewriting the topology would hide the mismatch from the
operator who needs to reconcile it.
"""

from __future__ import annotations

import asyncio
import os
import sys

import nats
from nats.js.errors import NotFoundError

DEFAULT_URL = "nats://127.0.0.1:25222"
STREAM = "AUTOGRADER"
SUBJECTS = ["autograder.>"]


async def ensure_stream(url: str) -> None:
    client = await nats.connect(url, connect_timeout=10, max_reconnect_attempts=2)
    try:
        jetstream = client.jetstream()
        try:
            info = await jetstream.stream_info(STREAM)
        except NotFoundError:
            await jetstream.add_stream(name=STREAM, subjects=SUBJECTS)
            print(f"[autograder-deploy] event stream {STREAM} created for {SUBJECTS}")
            return
        configured = list(info.config.subjects or [])
        if configured != SUBJECTS:
            raise SystemExit(
                f"[autograder-deploy] event stream {STREAM} has subjects {configured}, "
                f"expected {SUBJECTS}; refusing to rewrite an existing stream"
            )
        print(f"[autograder-deploy] event stream {STREAM} already present")
    finally:
        await client.close()


def main() -> int:
    url = os.environ.get("B4_NATS_URL") or os.environ.get("NATS_URL") or DEFAULT_URL
    asyncio.run(ensure_stream(url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
