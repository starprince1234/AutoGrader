"""Operational insight report for the log center (`POST /api/v1/ops/insights`).

The report is deliberately *facts first*: the model sees measured event counts and module
health, never a log line, a submission id, a course id or a user id. Its answer is then
partitioned back into the four layers the UI renders, with every module accounted for exactly
once. A provider failure is a 502 problem, not an empty report.
"""

from __future__ import annotations

import json
import os
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
from .redaction import rfc3339_millis
from .schema import log_events

WINDOW_HOURS = 24
OPS_INSIGHT_PROMPT_VERSION = "ops-insights-v1"

#: Every module the report must mention. B1 and MAIN do not expose a plugin health endpoint
#: in this host, so they are rendered as healthy unless the model reports otherwise.
MODULES: tuple[str, ...] = ("B1", "B2", "B3", "B4", "B5", "MAIN")
LAYERS: tuple[str, ...] = ("critical", "warning", "notice", "healthy")

_SYSTEM_PROMPT = (
    "你是 AutoGrader 的运维分析助手。根据给定的、已经去标识化的运行事实写一份中文报告。"
    "只能使用输入中的事实；不要编造日志、课程、学生或分数。"
    "把每个模块恰好放进 critical / warning / notice / healthy 四层中的一层。"
    "返回 JSON：{\"summary\": string, \"layers\": {\"critical\": [...], "
    "\"warning\": [...], \"notice\": [...], \"healthy\": [...]}}；"
    "每个条目必须是 {\"layer\", \"module\", \"title\", \"detail\", \"suggestion\"}。"
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
            max_output_tokens=2000,
            deadline_seconds=60,
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


def _facts(
    engine: sa.Engine,
    principal: LogPrincipal,
    modules: Sequence[Any],
    contexts: Mapping[str, Any],
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=WINDOW_HOURS)
    occurrences = sa.func.count().label("occurrences")
    query = (
        sa.select(
            log_events.c.producer,
            log_events.c.event,
            log_events.c.level,
            occurrences,
        )
        .where(log_events.c.occurred_at >= since)
        .group_by(log_events.c.producer, log_events.c.event, log_events.c.level)
        .order_by(sa.desc(occurrences))
    )
    if not principal.is_admin:
        query = query.where(log_events.c.course_id.in_(sorted(principal.scoped_course_ids)))
    with engine.connect() as connection:
        event_rows = connection.execute(query).mappings().all()

    module_facts: list[dict[str, Any]] = []
    for item in modules:
        name = str(item.descriptor.module)
        try:
            report = item.health(contexts.get(name))
            module_facts.append(
                {
                    "module": name,
                    "healthy": bool(report.healthy),
                    "detail": str(report.detail),
                }
            )
        except Exception:  # noqa: BLE001 - a probe failure is itself a fact
            module_facts.append({"module": name, "healthy": False, "detail": "健康检查不可用"})

    return {
        "window_hours": WINDOW_HOURS,
        "events": [
            {
                "producer": str(row["producer"]),
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
        "以下是最近 "
        f"{WINDOW_HOURS} 小时的去标识化运行事实（只有事件名、级别、次数与模块健康）：\n"
        + json.dumps(facts, ensure_ascii=False, sort_keys=True)
        + "\n请按系统提示返回 JSON 报告。"
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
    return {
        "layer": layer,
        "module": module,
        "title": title,
        "detail": detail,
        "suggestion": suggestion if isinstance(suggestion, str) else None,
    }
