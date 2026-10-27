#!/usr/bin/env python3
"""Ask the configured model provider one question and report only what an operator needs.

Why this exists: whether the AI authoring features work depends on three values that live in
``/etc/autograder/runtime.env``, and "the file carries them" is not the same fact as "the
running service sees them", nor as "this deployment's credential and endpoint are accepted".
Before this script, the only way to tell those apart was the teacher-facing panel, which
cannot say which layer failed.

It prints at most two lines and never a credential, a full request body or a model answer:

    selfcheck label=direct endpoint=https://host/v1/responses model=... timeout=60.0s proxy=none
    provider_direct=ok reply_keys=1 tokens=57

A failure prints the layer instead:

    provider_direct=failed category=http_401 endpoint=...      # credential or model access
    provider_direct=failed category=network endpoint=...       # DNS / egress / proxy
    provider_direct=failed category=configuration              # a malformed value
    provider_direct=failed category=importing                 # the release's package is broken

Run it with the release's own interpreter, so the imported ``autograder_b3`` is the code the
service runs:

    set -a; . /etc/autograder/runtime.env; set +a
    /opt/autograder/current/MAIN/.venv/bin/python MAIN/deploy/verify_provider.py direct
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

#: The smallest instruction that still proves the whole path: the endpoint accepts the body,
#: the credential is honoured and the answer parses as a JSON object.
SELF_CHECK_SYSTEM = "You answer with one JSON object and nothing else."
SELF_CHECK_USER = 'Self-check. Reply with the JSON object {"json": true}.'
SELF_CHECK_MAX_OUTPUT_TOKENS = 64


def sanitized(endpoint: str) -> str:
    """Scheme + host + port + path only: a base URL may carry a query string, a log line may not."""
    parts = urlsplit(endpoint)
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{parts.hostname}{port}{parts.path}"


def proxy_state() -> str:
    for name in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        if (os.environ.get(name) or "").strip():
            return "set"
    return "none"


def main(argv: list[str]) -> int:
    label = argv[1] if len(argv) > 1 else "direct"

    try:
        from autograder_b3.config import Settings
        from autograder_b3.llm_client import (
            LlmCallError,
            LlmConfig,
            complete_json,
            provider_name_for,
        )
    except Exception as error:  # noqa: BLE001 - reported as a category, never as a crash
        print(f"provider_{label}=failed category=importing detail={type(error).__name__}")
        return 0

    try:
        settings = Settings.from_env()
    except ValueError as error:
        print(f"provider_{label}=failed category=configuration detail={type(error).__name__}")
        return 0

    if not settings.llm_configured:
        missing = ",".join(settings.llm_missing_configuration) or "unknown"
        print(f"provider_{label}=unconfigured missing={missing}")
        return 0

    base_url = settings.llm_base_url or ""
    model = settings.llm_model or ""
    config = LlmConfig(
        base_url=base_url,
        model=model,
        api_key=settings.llm_api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        provider_name=provider_name_for(base_url),
    )
    print(
        f"selfcheck label={label} endpoint={sanitized(config.endpoint())} model={model} "
        f"timeout={settings.llm_timeout_seconds}s proxy={proxy_state()}"
    )

    try:
        document, usage = complete_json(
            config=config,
            system_prompt=SELF_CHECK_SYSTEM,
            user_prompt=SELF_CHECK_USER,
            max_output_tokens=SELF_CHECK_MAX_OUTPUT_TOKENS,
        )
    except LlmCallError as error:
        print(
            f"provider_{label}=failed category={error.category} "
            f"endpoint={sanitized(config.endpoint())}"
        )
        return 0

    tokens = "-" if usage.total_tokens is None else usage.total_tokens
    print(f"provider_{label}=ok reply_keys={len(document)} tokens={tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
