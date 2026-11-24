"""Tests for the AI operational status report (`POST /api/v1/ops/insights`).

The model is faked on purpose: these tests are about what this service *guarantees* -- the
report's shape, the completeness of the module partition, the scope of the facts it sends, and
the way a failure is reported -- not about how a particular provider phrases Chinese.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from autograder_host.logs import auth as logs_auth
from autograder_host.logs import insights
from autograder_host.logs.router import build_ops_logs_router, install_ops_log_exception_handler
from autograder_host.logs.schema import log_events, logs_metadata
from autograder_host.logs.sink import reset_log_sink_for_tests
from autograder_platform.llm_client import LlmCallError


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kw) -> str:
    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_sqlite(_type, _compiler, **_kw) -> str:
    return "VARCHAR(36)"


def _make_keypair_and_jwks():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    jwk_dict = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(public_key))
    jwk_dict["kid"] = "test-kid-1"
    jwk_dict["use"] = "sig"
    return private_key, {"keys": [jwk_dict]}


def _sign_token(private_key, *, sub: str, roles: list[str], courses: list[str] | None = None) -> str:
    now = int(time.time())
    payload = {
        "iss": "autograder",
        "aud": "autograder-api",
        "sub": sub,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "nbf": now - 5,
        "exp": now + 3600,
        "role": roles,
        "scope": [{"course_id": cid} for cid in (courses or [])],
    }
    return jwt.encode(payload, private_key, algorithm="EdDSA", headers={"kid": "test-kid-1"})


class _FakeModule:
    """A plugin stand-in: one descriptor name plus one health report."""

    def __init__(self, name: str, *, healthy: bool = True, detail: str = "") -> None:
        self.descriptor = SimpleNamespace(module=name)
        self._healthy = healthy
        self._detail = detail

    def health(self, _context=None):
        return SimpleNamespace(module=self.descriptor.module, healthy=self._healthy, detail=self._detail)


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch):
    private_key, jwks_doc = _make_keypair_and_jwks()
    monkeypatch.setattr(logs_auth, "_read_jwks_document", lambda: jwks_doc)
    monkeypatch.setenv("B4_JWT_ISSUER", "autograder")
    monkeypatch.setenv("B4_JWT_AUDIENCE", "autograder-api")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.xiaomimimo.com/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "mimo-v2.6-flash")
    monkeypatch.setenv("LLM_APIKEY", "test-key")
    logs_auth.clear_jwks_cache_for_tests()
    reset_log_sink_for_tests()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    translated = engine.execution_options(schema_translate_map={"logs": None})
    with translated.begin() as conn:
        logs_metadata.create_all(conn)

    app = FastAPI()
    app.state.modules = [
        _FakeModule("B2", healthy=True, detail="worker idle"),
        _FakeModule("B3", healthy=True, detail="question bank ready"),
        _FakeModule("B4", healthy=True, detail="identity ready"),
        _FakeModule("B5", healthy=True, detail="conformance ready"),
    ]
    app.state.contexts = {}
    app.include_router(build_ops_logs_router(reader_engine=translated))
    install_ops_log_exception_handler(app)

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {_sign_token(private_key, sub=str(uuid.uuid4()), roles=['admin'])}"}
    yield client, translated, headers, private_key

    reset_log_sink_for_tests()
    engine.dispose()


def _seed(engine) -> dict[str, str]:
    now = datetime.now(UTC)
    course_a = str(uuid.uuid4())
    course_b = str(uuid.uuid4())
    submission_a = str(uuid.uuid4())

    def row(level: str, event: str, producer: str, *, course: str | None, minutes: int) -> dict:
        return {
            "occurred_at": now - timedelta(minutes=minutes),
            "level": level,
            "event": event,
            "producer": producer,
            "process": "worker",
            "request_id": None,
            "correlation_id": None,
            "course_id": course,
            "submission_id": submission_a if course == course_a else None,
            "fields": {"handled": True},
            "truncated": False,
            "approx_bytes": 120,
        }

    rows = [
        row("error", "evaluation.run.failed", "B2", course=course_a, minutes=30),
        row("error", "evaluation.run.failed", "B2", course=course_a, minutes=12),
        row("warn", "sandbox.slow", "B2", course=course_a, minutes=8),
        row("error", "submission.rejected", "B2", course=course_b, minutes=4),
        row("info", "platform.tick", "MAIN", course=None, minutes=1),
    ]
    with engine.begin() as conn:
        conn.execute(log_events.insert(), rows)
    return {"course_a": course_a, "course_b": course_b, "submission_a": submission_a}


def _fake_provider(monkeypatch: pytest.MonkeyPatch, document: dict, captured: dict | None = None):
    def fake_complete_json(*, config, system_prompt, user_prompt, **kwargs):  # noqa: ARG001
        if captured is not None:
            captured["user_prompt"] = user_prompt
            captured["system_prompt"] = system_prompt
            captured["kwargs"] = kwargs
        return document, SimpleNamespace()

    monkeypatch.setattr(insights, "complete_json", fake_complete_json)


def _finding(layer: str, module: str, title: str, detail: str, suggestion: str | None = None) -> dict:
    return {
        "layer": layer,
        "module": module,
        "title": title,
        "detail": detail,
        "suggestion": suggestion,
    }


def test_the_report_accounts_for_every_module_exactly_once(api, monkeypatch):
    client, engine, headers, _key = api
    _seed(engine)
    _fake_provider(
        monkeypatch,
        {
            "summary": "整体可用，B2 有一条可以重试的评测失败。",
            "layers": {
                "critical": [],
                "warning": [
                    _finding("warning", "B2", "B2 评测失败", "最近 24 小时有 3 次评测失败，可重试。", "查看日志并重试"),
                    # A repeat of B2: the first occurrence wins.
                    _finding("warning", "B2", "重复条目", "这条应当被丢弃。"),
                ],
                "notice": [],
                "healthy": [
                    # An unknown module must be dropped rather than rendered.
                    _finding("healthy", "B9", "未知模块", "这条应当被丢弃。"),
                    # Empty text must be dropped too.
                    {"layer": "healthy", "module": "B3", "title": "  ", "detail": "x"},
                ],
            },
        },
        {"captured": None},
    )

    response = client.post("/api/v1/ops/insights", headers=headers)
    assert response.status_code == 200, response.text
    report = response.json()

    assert report["status"] == "ready"
    assert report["prompt_version"] == insights.OPS_INSIGHT_PROMPT_VERSION
    assert report["window_hours"] == insights.WINDOW_HOURS
    assert report["model"] == "mimo-v2.6-flash"
    assert report["summary"]
    # `generated_at` is a required contract field: the panel renders "生成于 {at}", so a report
    # without it shows a dash even though everything else worked.
    assert report["generated_at"].endswith("Z")

    layers = report["layers"]
    assert set(layers) == {"critical", "warning", "notice", "healthy"}
    assert [item["module"] for item in layers["warning"]] == ["B2"]
    assert layers["critical"] == []
    assert layers["notice"] == []
    # Every module appears exactly once across the four layers: the partition is complete.
    covered = [item["module"] for name in layers for item in layers[name]]
    assert sorted(covered) == sorted(insights.MODULES)
    assert len(covered) == len(set(covered))
    for name in layers:
        for item in layers[name]:
            assert item["layer"] == name
            assert item["module"] in insights.MODULES
            assert item["title"].strip() and item["detail"].strip()


def test_the_facts_given_to_the_model_are_measured_and_scoped(api, monkeypatch):
    client, engine, headers, key = api
    ids = _seed(engine)
    captured: dict = {}
    _fake_provider(
        monkeypatch,
        {
            "summary": "整体正常。",
            "layers": {
                "critical": [],
                "warning": [],
                "notice": [],
                "healthy": [
                    _finding("healthy", module, f"{module} 未见异常", "窗口内没有异常记录。")
                    for module in insights.MODULES
                ],
            },
        },
        captured,
    )

    response = client.post("/api/v1/ops/insights", headers=headers)
    assert response.status_code == 200, response.text
    prompt = captured["user_prompt"]

    # Measured facts travel: the seeded failures are in the prompt by event name and count.
    assert "evaluation.run.failed" in prompt
    assert '"occurrences": 2' in prompt
    # The answer stays inside the edge's budget and the ledger's token ceiling. Both are latency
    # levers, so they are pinned: the operator waits for this button.
    assert captured["kwargs"]["deadline_seconds"] <= 45
    assert captured["kwargs"]["max_output_tokens"] <= 800
    # The prompt tells the model to translate jargon instead of quoting it back.
    assert "level=" in captured["system_prompt"]
    assert "禁止" in captured["system_prompt"]
    # Nothing that identifies a submission or a course travels: no UUIDs at all.
    assert ids["submission_a"] not in prompt
    assert ids["course_a"] not in prompt
    assert ids["course_b"] not in prompt
    # A release directory in a plugin's health text is redacted before it can be quoted back.
    assert "/opt/autograder" not in prompt

    # A teacher scoped to one course does not see the other course's failure in their facts.
    teacher = _sign_token(
        key, sub=str(uuid.uuid4()), roles=["teacher"], courses=[ids["course_a"]]
    )
    scoped: dict = {}
    _fake_provider(
        monkeypatch,
        {
            "summary": "只看得到自己的课程。",
            "layers": {
                "critical": [],
                "warning": [],
                "notice": [],
                "healthy": [],
            },
        },
        scoped,
    )
    response = client.post(
        "/api/v1/ops/insights", headers={"Authorization": f"Bearer {teacher}"}
    )
    assert response.status_code == 200, response.text
    assert "evaluation.run.failed" in scoped["user_prompt"]
    assert '"occurrences": 3' not in scoped["user_prompt"]
    assert '"occurrences": 2' in scoped["user_prompt"]


def test_only_the_largest_problems_travel_and_paths_are_redacted(api, monkeypatch):
    """Latency and readability have the same lever: send less, and send nothing unreadable.

    A 24 h window groups into hundreds of (module, event, level) rows; listing them all made the
    call slow and the report technical. The top few plus per-module totals keep the magnitude.
    """
    client, engine, headers, _key = api
    now = datetime.now(UTC)
    rows = [
        {
            "occurred_at": now - timedelta(minutes=index + 1),
            "level": "warn",
            "event": f"demo.noise_{index:02d}",
            "producer": "B2",
            "process": "worker",
            "request_id": None,
            "correlation_id": None,
            "course_id": None,
            "submission_id": None,
            "fields": {},
            "truncated": False,
            "approx_bytes": 80,
        }
        for index in range(20)
    ]
    rows.append(
        {
            "occurred_at": now - timedelta(minutes=1),
            "level": "info",
            "event": "demo.info_noise",
            "producer": "B2",
            "process": "worker",
            "request_id": None,
            "correlation_id": None,
            "course_id": None,
            "submission_id": None,
            "fields": {},
            "truncated": False,
            "approx_bytes": 80,
        }
    )
    with engine.begin() as conn:
        conn.execute(log_events.insert(), rows)

    # A plugin whose health text names a release directory: the report must not quote it.
    client.app.state.modules = [
        _FakeModule("B2", healthy=True, detail="worker idle"),
        _FakeModule("B3", healthy=True, detail="question bank ready"),
        _FakeModule("B4", healthy=True, detail="identity ready"),
        _FakeModule(
            "B5",
            healthy=True,
            detail="103 contract paths under /opt/autograder/releases/abcdef-1/MAIN/contracts",
        ),
    ]
    captured: dict = {}
    _fake_provider(
        monkeypatch,
        {
            "summary": "整体正常。",
            "layers": {
                "critical": [],
                "warning": [],
                "notice": [],
                "healthy": [
                    _finding("healthy", module, f"{module} 未见异常", "窗口内没有异常记录。")
                    for module in insights.MODULES
                ],
            },
        },
        captured,
    )

    response = client.post("/api/v1/ops/insights", headers=headers)
    assert response.status_code == 200, response.text
    prompt = captured["user_prompt"]

    assert prompt.count('"event"') <= insights.MAX_EVENT_GROUPS
    assert "demo.info_noise" not in prompt  # info rows are not enumerated at all
    # The magnitude survives even though the long tail does not: B2's totals are present.
    assert '"warn_total": 20' in prompt
    assert "/opt/autograder" not in prompt
    assert "（部署目录）" in prompt


def test_an_unconfigured_deployment_is_a_state_not_an_error(api, monkeypatch):
    client, engine, headers, _key = api
    _seed(engine)
    for name in ("LLM_BASE_URL", "LLM_MODEL_NAME", "LLM_APIKEY"):
        monkeypatch.delenv(name, raising=False)

    response = client.post("/api/v1/ops/insights", headers=headers)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "unavailable"
    assert report["model"] is None
    assert report["layers"] == {"critical": [], "warning": [], "notice": [], "healthy": []}
    assert "没有配置模型" in report["summary"]


def test_a_provider_failure_is_502_and_carries_only_the_category(api, monkeypatch):
    client, engine, headers, _key = api
    _seed(engine)

    def failing_complete_json(**_kwargs):
        raise LlmCallError("timeout", "the provider exchange did not finish within 55 s")

    monkeypatch.setattr(insights, "complete_json", failing_complete_json)
    response = client.post("/api/v1/ops/insights", headers=headers)
    assert response.status_code == 502, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 502
    assert "timeout" in body["detail"]
    # The provider's own text is never echoed.
    assert "55 s" not in body["detail"]


def test_only_ops_scope_may_ask_for_a_report(api, monkeypatch):
    client, engine, headers, key = api
    _seed(engine)
    _fake_provider(
        monkeypatch,
        {"summary": "x", "layers": {"critical": [], "warning": [], "notice": [], "healthy": []}},
    )

    assert client.post("/api/v1/ops/insights").status_code == 401

    student = _sign_token(key, sub=str(uuid.uuid4()), roles=["student"])
    assert (
        client.post(
            "/api/v1/ops/insights", headers={"Authorization": f"Bearer {student}"}
        ).status_code
        == 403
    )


def test_a_garbage_answer_is_a_502_not_an_empty_report(api, monkeypatch):
    client, engine, headers, _key = api
    _seed(engine)
    _fake_provider(monkeypatch, {"unexpected": "shape"})

    response = client.post("/api/v1/ops/insights", headers=headers)
    assert response.status_code == 502, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
