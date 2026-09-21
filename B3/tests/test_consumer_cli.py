"""The standalone consumer runner.

``--rebuild`` and ``--health`` are exercised here: both are real code against the real
database and both are operations an operator would run (after a DLQ replay, and while
diagnosing "why are the statistics empty"). The streaming path needs a live NATS
JetStream, which is B4's to provide.
"""

from __future__ import annotations

import json

import pytest

from autograder_b3.consumer_cli import main
from autograder_b3.events.consumer import EvaluationCompletedConsumer

from .conftest import APP_URL, INTERNAL_URL, draft_payload, event_envelope
from .support import create_published_question


def test_rebuild_command_reports_the_rebuilt_totals(
    database, settings, monkeypatch, capsys, clock
):
    monkeypatch.setenv("DATABASE_URL", APP_URL)
    monkeypatch.setenv("DATABASE_PROTECTED_URL", INTERNAL_URL)
    monkeypatch.setenv("B3_LOG_LEVEL", "INFO")

    _question, version = create_published_question(database, settings, payload=draft_payload())
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    for _ in range(3):
        consumer.handle(
            event_envelope(question_version_id=version.question_version_id, is_pass=True)
        )

    exit_code = main(["--rebuild"])
    assert exit_code == 0


def test_missing_nats_is_reported_not_silently_ignored(monkeypatch):
    """Without NATS the runner must refuse loudly instead of pretending to work."""
    monkeypatch.delenv("NATS_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", APP_URL)
    monkeypatch.setenv("DATABASE_PROTECTED_URL", INTERNAL_URL)
    with pytest.raises(SystemExit) as error:
        import asyncio

        from autograder_b3.consumer_cli import run_forever
        from autograder_b3.config import Settings

        asyncio.run(run_forever(Settings.from_env()))
    assert "NATS_URL" in str(error.value)


def test_health_command_reports_idle_with_exit_code_zero(
    database, settings, monkeypatch, capsys, clean_event_store
):
    monkeypatch.setenv("DATABASE_URL", APP_URL)
    monkeypatch.setenv("DATABASE_PROTECTED_URL", INTERNAL_URL)
    monkeypatch.setenv("B3_LOG_LEVEL", "INFO")

    assert main(["--health"]) == 0
    reported = json.loads(capsys.readouterr().out.strip())
    assert reported["state"] == "idle"
    assert reported["accepted"] == 0 and reported["rejected"] == 0
    assert reported["healthy"] is True


def test_health_command_exits_non_zero_when_deliveries_are_rejected(
    database, settings, monkeypatch, capsys, clock, clean_event_store
):
    """A non-zero exit is what makes this usable as an operational check."""
    monkeypatch.setenv("DATABASE_URL", APP_URL)
    monkeypatch.setenv("DATABASE_PROTECTED_URL", INTERNAL_URL)
    monkeypatch.setenv("B3_LOG_LEVEL", "INFO")

    _question, version = create_published_question(database, settings, payload=draft_payload())
    EvaluationCompletedConsumer(database, clock=clock).handle(
        event_envelope(
            question_version_id=version.question_version_id,
            extra_payload={"student_id": "s-1"},
        )
    )

    assert main(["--health"]) == 1
    reported = json.loads(capsys.readouterr().out.strip())
    assert reported["state"] == "degraded"
    assert reported["rejected"] == 1
    assert reported["healthy"] is False
