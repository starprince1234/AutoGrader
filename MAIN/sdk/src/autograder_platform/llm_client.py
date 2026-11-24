"""The one place that talks to the model provider.

Every AI feature sends the same shape of request to the same endpoint with the same credential,
so the request construction lives here and nowhere else: the two B3 features -- structured draft
generation (``AI-FLOW-01A``) and prose advice (``RQ-T-10``) -- and the host's operational
status report (``ops-insights``), which runs in a different process from B3. That is why this
module belongs to the Platform SDK rather than to a module: the provider is platform
infrastructure, not one module's private detail.

The provider is **MiMo's OpenAI-Responses-compatible API**
(``https://api.xiaomimimo.com/v1/responses``). Two facts from that specification decide the
code below:

* the output text is not in ``choices[0].message.content`` (that is Chat Completions); it is
  ``output[] -> content[] -> {type: "output_text", text}``, and the aggregated shortcut
  ``output_text`` exists only in some SDKs, so both are read;
* only documented parameters are processed -- anything else is filtered or rejected. The
  documented set used here is ``model``, ``instructions``, ``input``, ``reasoning.effort``,
  ``max_output_tokens`` and ``temperature``. ``response_format`` / ``text.format`` are *not*
  documented for this endpoint, so the JSON shape is requested in the prompt and the answer
  is extracted defensively instead of pretending the API enforces it.

Nothing here logs, stores or re-raises a response body: a provider error page can echo the
request, and the request carries the teacher's own text.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from collections.abc import Mapping, Sequence

#: Suffix this module appends to a configured base URL. The deployment's secret is a *base*
#: (``https://api.xiaomimimo.com`` or ``.../v1``); the endpoint is always ``/responses``.
_RESPONSES_SUFFIX = "/responses"
_CHAT_SUFFIX = "/chat/completions"

#: Upper bound for one call. The composed request is a few kilobytes; the answer is a JSON
#: object, so this only stops a runaway generation, not a legitimate one.
MAX_OUTPUT_TOKENS = 12288

#: Failure categories worth a single fast retry inside multi-stage generation.
_RETRYABLE_CATEGORIES = frozenset(
    {"timeout", "network", "http_429", "http_502", "http_503", "http_504", "incomplete", "not_json"}
)

#: A retry shorter than this is a guaranteed second timeout, so it is not attempted: reporting
#: the first failure immediately is what keeps a synchronous request inside its own budget.
MIN_RETRY_SECONDS = 40.0


@dataclass(frozen=True)
class TokenUsage:
    """Whatever the provider reported. ``None`` means "not reported", never "zero"."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


def combine_usage(*usages: TokenUsage) -> TokenUsage:
    """Sum reported token counts across DAG stages/retries; stay ``None`` when none reported."""

    def sum_field(values: Sequence[int | None]) -> int | None:
        reported = [value for value in values if value is not None]
        return sum(reported) if reported else None

    return TokenUsage(
        prompt_tokens=sum_field([item.prompt_tokens for item in usages]),
        completion_tokens=sum_field([item.completion_tokens for item in usages]),
        total_tokens=sum_field([item.total_tokens for item in usages]),
    )


class LlmCallError(RuntimeError):
    """The call failed. ``category`` is safe to log; the body never travels with it."""

    def __init__(self, category: str, detail: str, *, usage: TokenUsage | None = None) -> None:
        super().__init__(detail)
        self.category = category
        self.usage = usage


@dataclass(frozen=True)
class LlmConfig:
    """Everything needed to make one call, and nothing else."""

    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: float = 90.0
    provider_name: str = "openai-compatible"

    def endpoint(self) -> str:
        return responses_endpoint(self.base_url)


def responses_endpoint(base_url: str) -> str:
    """Turn a configured base URL into the Responses endpoint.

    Accepted forms (all of them are shapes an operator actually pastes into a secret):

    ==========================================  ======================================
    ``https://api.xiaomimimo.com``              ``https://api.xiaomimimo.com/v1/responses``
    ``https://api.xiaomimimo.com/v1``           ``https://api.xiaomimimo.com/v1/responses``
    ``https://api.xiaomimimo.com/v1/``          ``https://api.xiaomimimo.com/v1/responses``
    ``https://api.xiaomimimo.com/v1/chat/completions``  ``.../v1/responses``
    ``https://api.xiaomimimo.com/v1/responses``  unchanged (never doubled)
    ==========================================  ======================================
    """
    base = base_url.strip().rstrip("/")
    if not base:
        raise LlmCallError("configuration", "the provider base URL is empty")
    lowered = base.lower()
    if lowered.endswith(_CHAT_SUFFIX):
        return base[: -len(_CHAT_SUFFIX)] + _RESPONSES_SUFFIX
    if lowered.endswith(_RESPONSES_SUFFIX):
        return base
    if _ends_with_version_segment(lowered):
        return base + _RESPONSES_SUFFIX
    return base + "/v1" + _RESPONSES_SUFFIX


def _ends_with_version_segment(url: str) -> bool:
    """``.../v1`` (or ``/v2``, ``/v4``) -- an OpenAI-style version root, not a bare host."""
    tail = url.rsplit("/", 1)[-1]
    return len(tail) >= 2 and tail[0] == "v" and tail[1:].isdigit()


def provider_name_for(base_url: str) -> str:
    """A short, human-meaningful provider label for provenance (never the URL or the key)."""
    host = base_url.strip().lower()
    # Match the vendor's own hosts, not any substring: a proxy host that happens to contain
    # "mimo" somewhere is not the MiMo platform.
    if "xiaomimimo" in host or host.startswith(("mimo.", "http://mimo.", "https://mimo.")):
        return "xiaomi-mimo"
    if "//mimo." in host or "://mimo." in host:
        return "xiaomi-mimo"
    return "openai-compatible"


def build_request_body(
    *,
    config: LlmConfig,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
    reasoning_effort: str = "none",
) -> dict[str, Any]:
    """The exact body sent to the provider.

    ``instructions`` carries the static system text and ``input`` the teacher-derived prompt,
    so teacher input stays *data*: an objective that says "ignore your rules" is a string in
    a field, not an instruction the model follows at the same level as the system text.
    ``reasoning.effort = "none"`` disables thinking for latency; this is a synchronous teacher
    action, and the ceiling on ``max_output_tokens`` bounds the cost either way.
    """
    return {
        "model": config.model,
        "instructions": system_prompt,
        "input": user_prompt,
        "reasoning": {"effort": reasoning_effort},
        "max_output_tokens": max_output_tokens,
        "temperature": 0,
    }


def _exchange_with_deadline(request: urllib.request.Request, *, timeout_seconds: float) -> bytes:
    """Run the whole HTTP exchange in a worker thread and enforce a wall-clock bound.

    ``urlopen(timeout=...)`` bounds a single socket operation and nothing else: the name lookup,
    the TLS handshake and a body that trickles each of them can outlive it. Measured in
    production: an exchange with an 85 s budget ran 171 s (the teacher got an edge `524`, and the
    backend logged the failure long after), and an earlier 55 s ceiling became a 146 s request.
    Waiting on a worker thread with ``join(timeout)`` is the only way to bound every phase of the
    exchange without adding a second HTTP library.

    The worker is a daemon and owns the response object, so an abandoned attempt closes its socket
    when the exchange finally ends (the socket timeout is the same value, so it cannot linger
    forever).
    """
    outcome: dict[str, object] = {}

    def worker() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                outcome["body"] = response.read()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the calling thread
            outcome["error"] = exc

    thread = threading.Thread(target=worker, name="llm-exchange", daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise TimeoutError(
            f"the provider exchange did not finish within {timeout_seconds:.0f} s"
        )
    error = outcome.get("error")
    if isinstance(error, BaseException):
        raise error
    body = outcome.get("body")
    if not isinstance(body, bytes):  # pragma: no cover - the worker either sets body or error
        raise OSError("the provider exchange produced no body")
    return body


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, TimeoutError):
        return True
    return "timed out" in str(reason or exc).lower()


def _complete_json_once(
    *,
    config: LlmConfig,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    timeout_seconds: float | None,
) -> tuple[Mapping[str, Any], TokenUsage]:
    body = json.dumps(
        build_request_body(
            config=config,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=max_output_tokens,
        ),
        ensure_ascii=False,
    ).encode("utf-8")

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.api_key:
        # The API documents `api-key:` and `Authorization: Bearer` as equivalent; Bearer is
        # the form proxies and OpenAI-compatible clients already send.
        headers["Authorization"] = f"Bearer {config.api_key}"

    request = urllib.request.Request(  # noqa: S310 - configured provider endpoint
        config.endpoint(), data=body, headers=headers, method="POST"
    )
    effective_timeout = config.timeout_seconds if timeout_seconds is None else timeout_seconds
    try:
        raw = _exchange_with_deadline(request, timeout_seconds=effective_timeout).decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise LlmCallError(f"http_{exc.code}", f"the provider answered HTTP {exc.code}") from exc
    except (TimeoutError, urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        category = "timeout" if _is_timeout_error(exc) else "network"
        raise LlmCallError(category, f"the provider call failed ({type(exc).__name__})") from exc

    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise LlmCallError("not_json", "the provider answered with something that is not JSON") from exc

    if not isinstance(document, Mapping):
        raise LlmCallError("not_json", "the provider answered with something that is not an object")

    usage = usage_from(document)
    error = document.get("error")
    if error:
        code = error.get("code") if isinstance(error, Mapping) else None
        raise LlmCallError(
            "response_error", f"the provider reported an error ({code or 'unknown'})", usage=usage
        )

    status = document.get("status")
    if status in {"failed", "incomplete", "cancelled"}:
        reason = document.get("incomplete_details")
        reason_text = reason.get("reason") if isinstance(reason, Mapping) else None
        raise LlmCallError(
            "incomplete",
            f"the provider response is {status} ({reason_text or 'no reason given'})",
            usage=usage,
        )

    text = response_text(document)
    if text.strip() == "":
        raise LlmCallError(
            "not_json", "the provider response contains no output text", usage=usage
        )

    try:
        return extract_json_object(text), usage
    except LlmCallError as exc:
        raise LlmCallError(exc.category, str(exc), usage=usage) from exc


def complete_json(
    *,
    config: LlmConfig,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
    timeout_seconds: float | None = None,
    max_attempts: int = 1,
    deadline_seconds: float | None = None,
    min_retry_seconds: float = MIN_RETRY_SECONDS,
) -> tuple[Mapping[str, Any], TokenUsage]:
    """Call the provider and return ``(json object, usage)``.

    ``timeout_seconds`` bounds one attempt (one HTTP exchange); ``deadline_seconds`` bounds the
    **whole call, retries included**. The second one is what a synchronous request needs: a fast
    retry only helps when there is time left for it, and a slow attempt must not eat the budget a
    second attempt was supposed to have (measured in production: without a shared deadline a
    retried call ran 155 s and the browser saw an edge `524` instead of any answer).

    ``min_retry_seconds`` is how much budget a retry needs to be worth attempting. The default
    (40 s) is tuned for a single large answer; a caller whose answer takes ~15 s can pass a
    lower floor, because for it a 20 s retry is a real chance rather than a guaranteed timeout.

    Failures raise :class:`LlmCallError` with a *classification*, never with the response
    body: ``configuration`` / ``network`` / ``timeout`` / ``http_<status>`` / ``response_error``
    / ``incomplete`` / ``not_json``.
    """
    attempts = max(1, max_attempts)
    deadline = None if deadline_seconds is None else time.monotonic() + deadline_seconds
    accumulated: list[TokenUsage] = []
    for attempt in range(1, attempts + 1):
        per_attempt = timeout_seconds
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise LlmCallError(
                    "timeout",
                    "the provider budget was exhausted before another attempt could start",
                )
            per_attempt = remaining if per_attempt is None else min(per_attempt, remaining)
        try:
            document, usage = _complete_json_once(
                config=config,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=max_output_tokens,
                timeout_seconds=per_attempt,
            )
            accumulated.append(usage)
            return document, combine_usage(*accumulated)
        except LlmCallError as exc:
            if exc.usage is not None:
                accumulated.append(exc.usage)
            if attempt >= attempts or exc.category not in _RETRYABLE_CATEGORIES:
                raise LlmCallError(
                    exc.category,
                    str(exc),
                    usage=combine_usage(*accumulated) if accumulated else exc.usage,
                ) from exc
            if deadline is not None and deadline - time.monotonic() < min_retry_seconds:
                # A retry shorter than the caller's floor is a guaranteed second timeout; report
                # the first failure now so the caller can answer inside its own budget.
                raise LlmCallError(
                    exc.category,
                    f"{exc} (no budget left for a retry)",
                    usage=combine_usage(*accumulated) if accumulated else exc.usage,
                ) from exc
            time.sleep(0.5 if exc.category == "http_429" else 0.15)
    raise AssertionError("unreachable")  # pragma: no cover


def response_text(document: Mapping[str, Any]) -> str:
    """Concatenate the assistant's text items from a Responses document.

    Reads the aggregated ``output_text`` when a compatible gateway provides it, and otherwise
    walks ``output[]`` looking *only* for ``message`` items with ``output_text`` content --
    never ``reasoning`` items, whose text is not part of the answer.
    """
    aggregated = document.get("output_text")
    if isinstance(aggregated, str) and aggregated.strip() != "":
        return aggregated

    chunks: list[str] = []
    output = document.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, Mapping):
                    continue
                if part.get("type") != "output_text":
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    return "".join(chunks)


def extract_json_object(text: str) -> Mapping[str, Any]:
    """Find the JSON object in a provider answer.

    Models wrap JSON in ```json fences or add a sentence before it, and this endpoint does not
    document a structured-output switch, so the extraction is defensive: parse as-is, then
    strip a fence, then take the first balanced ``{...}`` run. Whatever comes out still goes
    through each feature's own field-by-field validation -- extraction never *repairs*
    content, it only locates it.
    """
    candidate = text.strip()

    direct = _loads_object(candidate)
    if direct is not None:
        return direct

    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 2:
            inner = "\n".join(lines[1:])
            if inner.rstrip().endswith("```"):
                inner = inner.rstrip()[: -len("```")]
            fenced = _loads_object(inner.strip())
            if fenced is not None:
                return fenced

    braced = _first_balanced_object(candidate)
    if braced is not None:
        parsed = _loads_object(braced)
        if parsed is not None:
            return parsed

    raise LlmCallError("not_json", "the provider answered with something that is not a JSON object")


def _loads_object(text: str) -> Mapping[str, Any] | None:
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _first_balanced_object(text: str) -> str | None:
    """Return the first ``{...}`` run, string-aware, so braces inside strings do not mislead."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def usage_from(document: Mapping[str, Any]) -> TokenUsage:
    """Map the Responses usage fields onto the ledger's columns. Never estimated."""

    raw = document.get("usage")
    if not isinstance(raw, Mapping):
        return TokenUsage()

    def number(*names: str) -> int | None:
        for name in names:
            value = raw.get(name)
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and value >= 0:
                return value
        return None

    # Responses reports input/output; Chat Completions calls them prompt/completion. Both
    # spellings are read from the same field so a gateway that translates shapes still
    # fills the ledger.
    return TokenUsage(
        prompt_tokens=number("input_tokens", "prompt_tokens"),
        completion_tokens=number("output_tokens", "completion_tokens"),
        total_tokens=number("total_tokens"),
    )
