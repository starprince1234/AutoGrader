"""Validate the host's actual HealthReport JSON on stdin."""

from __future__ import annotations

import json
import sys


def is_ready(payload: dict) -> bool:
    reports = payload.get("modules", [])
    names = {report.get("name") for report in reports}
    return (
        payload.get("ready") is True
        and {"B2", "B3", "B4"} <= names
        and all(report.get("healthy") is True for report in reports)
    )


def main() -> None:
    payload = json.load(sys.stdin)
    if not is_ready(payload):
        # Details may contain internal settings; print names and health flags only.
        print(
            "API not ready: "
            + repr([(item.get("name"), item.get("healthy")) for item in payload.get("modules", [])]),
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
