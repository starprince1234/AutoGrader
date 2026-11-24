"""Operational insight report for the log center (`POST /api/v1/ops/insights`).

The report is deliberately *facts first*: the model sees measured event counts and module
health, never a log line, a submission id, a course id or a user id. Its answer is then
partitioned back into the four layers the UI renders, with every module accounted for exactly
once. A provider failure is a 502 problem, not an empty report.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from autograder_platform.llm_client import (
    LlmCallError,
    LlmConfig,
    complete_json,
    provider_name_for,
)

from .auth import LogPrincipal, OpsLogAuthError
from .redaction import rfc3339_millis, sanitize_text
from .schema import log_events

WINDOW_HOURS = 24
OPS_INSIGHT_PROMPT_VERSION = "ops-insights-v2-plain"

#: Every module the report must mention. B1 and MAIN do not expose a plugin health endpoint
#: in this host, so they are rendered as healthy unless the model reports otherwise.
MODULES: tuple[str, ...] = ("B1", "B2", "B3", "B4", "B5", "MAIN")
LAYERS: tuple[str, ...] = ("critical", "warning", "notice", "healthy")

#: How many distinct warn/error event kinds travel to the model. The answer only needs the
#: biggest few: a 24 h window can group into hundreds of (module, event, level) rows, and sending
#: all of them bought nothing the reader could use while making every call slower (the prompt is
#: the input half of the latency) and the report more technical.
MAX_EVENT_GROUPS = 10

#: The answer is a short report: three fields per module, four layers, ~200 Chinese characters
#: in total. A tight ceiling is also the biggest single latency lever -- output tokens are what
#: the model spends its time producing.
MAX_OUTPUT_TOKENS = 800

#: Wall-clock budget for the whole call. Well inside the edge's ~100 s request budget, and
#: shorter than the previous 60 s so a slow provider fails fast enough to retry by hand.
DEADLINE_SECONDS = 45.0

#: One retry inside that budget. A cut-off JSON answer (the model occasionally truncates) is
#: worth resending; a slow one is not, so the retry also needs MIN_RETRY_SECONDS of room.
MAX_ATTEMPTS = 2
MIN_RETRY_SECONDS = 15.0

_SYSTEM_PROMPT = (
    "你是 AutoGrader 的运维助手，读者是不懂技术的教务老师或管理员。"
    "你要用**日常中文**告诉他：平台现在能不能用、哪里不对劲、他该做什么。\n"
    "硬性要求（违反即视为错误）：\n"
    "1. 只使用给定事实；不编造事件、模块、原因或处理办法。事实解释不了的，就说"
    "「原因不明」，不要猜。\n"
    "2. **禁止出现技术黑话**：不要写英文事件名（request.failed、api_error、auth.token_rejected"
    " 等）、不要写 `level=error`、不要写代码标识符（如 JWKS）、不要写服务器路径（如 /opt/…）"
    "、不要写 UUID。必须提到某个技术名词时，先用中文说清它是什么，"
    "例如把 auth.token_rejected 说成「登录凭证被拒绝」、把 evaluation.run_failed 说成"
    "「一次作业评测没跑完」。\n"
    "3. 每条：title（≤24 字，说清哪个模块 + 用户会遇到什么）、detail（≤80 字，一句人话，"
    "说清影响和次数）、suggestion（≤60 字，一个具体动作；没事可做就给 null）。\n"
    "4. summary ≤100 字：先给结论（能不能正常用），再说最值得注意的一件事。\n"
    "5. 每个模块 B1、B2、B3、B4、B5、MAIN 恰好出现在四层之一："
    "critical（用户现在用不了 / 可能丢数据）、warning（用户会碰到但不影响整体）、"
    "notice（苗头、趋势、偶发）、healthy（没发现问题）。没问题的模块放 healthy，别漏也别重复。\n"
    "5b. 结论要和分层一致：只要 critical / warning / notice 里有任何一条，summary 就不能说"
    "「全部正常」「都没问题」，要如实说「整体可用，但…」。\n"
    "6. 只输出一个 JSON 对象，不要 markdown、不要解释："
    '{"summary": "...", "layers": {"critical": [], "warning": [], "notice": [], "healthy": []}}；'
    "每个条目是 {\"layer\", \"module\", \"title\", \"detail\", \"suggestion\"}。\n"
    "对照示例——\n"
    "反面（太技术，读者看不懂）：title「api_error 警告 108 次」，"
    "detail「B1 模块产生 108 次 level=warn 的 api_error 事件，与 request.completed 5031 次"
    "相比占比约 2%」。\n"
    "正面：title「B1：前端有 108 次请求没成功」，"
    "detail「近 24 小时里用户打开页面时偶尔会加载不出内容，重试一般能好；这是本次最多的告警」，"
    "suggestion「先看集中在哪几个页面，把那几个页面修掉」。\n"
    "读者看完应该知道：现在能不能用、哪里有问题、下一步做什么。"
)


def generate_report(
    *,
    reader_engine: sa.Engine,
    principal: LogPrincipal,
    modules: Sequence[Any],
    contexts: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the facts, ask the model, and return the layered report."""
    config = _config_from_environment()
    if config is None:
        return {
            "status": "unavailable",
            "generated_at": rfc3339_millis(datetime.now(UTC)),
            "prompt_version": OPS_INSIGHT_PROMPT_VERSION,
            "window_hours": WINDOW_HOURS,
            "model": None,
            "summary": "没有配置模型，暂时无法生成运维报告。",
            "layers": {layer: [] for layer in LAYERS},
        }

    facts = _facts(reader_engine, principal, modules, contexts)
    user_prompt = _user_prompt(facts)
    try:
        document, _usage = complete_json(
            config=config,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            deadline_seconds=DEADLINE_SECONDS,
            # One retry, and only while a second ~17 s answer still fits: a cut-off JSON answer
            # is worth resending, a slow one is not.
            max_attempts=MAX_ATTEMPTS,
            min_retry_seconds=MIN_RETRY_SECONDS,
        )
    except LlmCallError as error:
        # The category is safe to return; provider text and request bodies are not.
        raise OpsLogAuthError(
            502,
            "Bad Gateway",
            f"运维报告生成失败：{error.category}",
        ) from error

    layers = _normalise_layers(document)
    healthy_modules = {
        str(item["module"])
        for name in LAYERS
        for item in layers[name]
    }
    for module in MODULES:
        if module not in healthy_modules:
            layers["healthy"].append(
                _finding(
                    "healthy",
                    module,
                    f"{module} 未见异常",
                    "窗口内没有异常记录。",
                )
            )
    return {
        "status": "ready",
        "generated_at": rfc3339_millis(datetime.now(UTC)),
        "prompt_version": OPS_INSIGHT_PROMPT_VERSION,
        "window_hours": WINDOW_HOURS,
        "model": config.model,
        "summary": str(document.get("summary", "")).strip() or "本次运维报告已生成。",
        "layers": layers,
    }


def _config_from_environment() -> LlmConfig | None:
    base = os.environ.get("LLM_BASE_URL", "").strip()
    model = os.environ.get("LLM_MODEL_NAME", "").strip()
    key = os.environ.get("LLM_APIKEY", "").strip()
    if not base or not model or not key:
        return None
    return LlmConfig(
        base_url=base,
        model=model,
        api_key=key,
        provider_name=provider_name_for(base),
    )


_PATH_RE = re.compile(r"/[A-Za-z0-9._\-/]{4,}")


def _plain_detail(text: str) -> str:
    """Strip things a non-engineer cannot read from a health sentence.

    A plugin's health text is written for operators: it names release directories and contract
    paths ("103 contract paths under /opt/autograder/releases/<sha>/MAIN/contracts"). The model
    copies what it is given, so the report ended up quoting paths at a teacher. Redacting the
    paths here -- not in the prompt -- is what makes that impossible rather than discouraged.
    """
    cleaned = _PATH_RE.sub("（部署目录）", sanitize_text(text, max_length=160))
    return cleaned.strip()


def _facts(
    engine: sa.Engine,
    principal: LogPrincipal,
    modules: Sequence[Any],
    contexts: Mapping[str, Any],
) -> dict[str, Any]:
    """The measured picture, small on purpose.

    Only warn/error rows and only the largest few of them travel: a 24 h window groups into
    hundreds of (module, event, level) rows, most of them info level, and none of the long tail
    changes the conclusion. Per-module totals keep the magnitude visible even when an event kind
    is not in the top list, so "smaller is faster" does not become "smaller is blind".
    """
    since = datetime.now(UTC) - timedelta(hours=WINDOW_HOURS)
    occurrences = sa.func.count().label("occurrences")
    query = (
        sa.select(
            log_events.c.producer,
            log_events.c.event,
            log_events.c.level,
            occurrences,
        )
        .where(log_events.c.occurred_at >= since, log_events.c.level.in_(("warn", "error")))
        .group_by(log_events.c.producer, log_events.c.event, log_events.c.level)
        .order_by(sa.desc(occurrences))
        .limit(MAX_EVENT_GROUPS)
    )
    totals_query = sa.select(
        log_events.c.producer,
        sa.func.sum(sa.case((log_events.c.level == "warn", 1), else_=0)).label("warn_total"),
        sa.func.sum(sa.case((log_events.c.level == "error", 1), else_=0)).label("error_total"),
        sa.func.count().label("event_total"),
    ).where(log_events.c.occurred_at >= since).group_by(log_events.c.producer)
    if not principal.is_admin:
        scoped = log_events.c.course_id.in_(sorted(principal.scoped_course_ids))
        query = query.where(scoped)
        totals_query = totals_query.where(scoped)
    with engine.connect() as connection:
        event_rows = connection.execute(query).mappings().all()
        total_rows = connection.execute(totals_query).mappings().all()

    totals = {
        str(row["producer"]): {
            "warn_total": int(row["warn_total"] or 0),
            "error_total": int(row["error_total"] or 0),
            "event_total": int(row["event_total"] or 0),
        }
        for row in total_rows
    }
    module_facts: list[dict[str, Any]] = []
    for item in modules:
        name = str(item.descriptor.module)
        counts = totals.get(name, {"warn_total": 0, "error_total": 0, "event_total": 0})
        try:
            report = item.health(contexts.get(name))
            module_facts.append(
                {
                    "module": name,
                    "healthy": bool(report.healthy),
                    "detail": _plain_detail(str(report.detail)),
                    **counts,
                }
            )
        except Exception:  # noqa: BLE001 - a probe failure is itself a fact
            module_facts.append(
                {
                    "module": name,
                    "healthy": False,
                    "detail": "这个模块的健康检查没有响应",
                    **counts,
                }
            )

    return {
        "window_hours": WINDOW_HOURS,
        "biggest_warn_error_events": [
            {
                "module": str(row["producer"]),
                "event": str(row["event"]),
                "level": str(row["level"]),
                "occurrences": int(row["occurrences"]),
            }
            for row in event_rows
        ],
        "modules": module_facts,
    }


def _user_prompt(facts: Mapping[str, Any]) -> str:
    return (
        f"最近 {WINDOW_HOURS} 小时的运行情况（已去标识化：只有模块健康、每个模块的告警/错误"
        "次数、以及告警和错误里最大的几项；`occurrences` 是次数）：\n"
        + json.dumps(facts, ensure_ascii=False, sort_keys=True)
        + "\n请写给人看的报告：不要出现英文事件名、level=、路径或代码标识符；"
        "把每件事都翻译成「用户会遇到什么」。只输出 JSON。"
    )


def _normalise_layers(document: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw_layers = document.get("layers")
    if not isinstance(raw_layers, Mapping):
        raise OpsLogAuthError(502, "Bad Gateway", "运维报告生成失败：shape")
    result: dict[str, list[dict[str, Any]]] = {layer: [] for layer in LAYERS}
    seen: set[str] = set()
    for layer in LAYERS:
        entries = raw_layers.get(layer)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            module = str(entry.get("module", "")).strip()
            title = str(entry.get("title", "")).strip()
            detail = str(entry.get("detail", "")).strip()
            if module not in MODULES or module in seen or not title or not detail:
                continue
            seen.add(module)
            result[layer].append(
                _finding(layer, module, title, detail, entry.get("suggestion"))
            )
    return result


def _finding(
    layer: str,
    module: str,
    title: str,
    detail: str,
    suggestion: Any = None,
) -> dict[str, Any]:
    # A blank string is not a suggestion: the panel shows nothing for it, and "there is nothing
    # to do" belongs in the contract as null rather than as an empty sentence.
    cleaned = suggestion.strip() if isinstance(suggestion, str) else None
    return {
        "layer": layer,
        "module": module,
        "title": title,
        "detail": detail,
        "suggestion": cleaned or None,
    }
