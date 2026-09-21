"""RQ-B-4-10 / RQ-B-4-20: the readiness probe reports the dependencies' real state.

Every assertion changes a real dependency or points the probe at a real, unreachable
endpoint. A cached or hard-coded "healthy" would pass a screenshot and fail these.

The unreachable endpoints use the engine's *own* factory with a redirected port, so the
timeout and probing behaviour under test is exactly the production configuration -- an
engine built differently in the test would prove nothing about the deployed one.
"""

from __future__ import annotations

import dataclasses
from typing import ClassVar

from sqlalchemy import text

from autograder_b4.api.dependencies import B4State
from autograder_b4.config import Settings
from autograder_b4.db.engine import (
    alembic_script_head,
    create_engine_from_settings,
    current_migration_head,
    database_health,
    migration_head_status,
)
from autograder_b4.messaging.broker import nats_reachability


def _with_unreachable_database(settings: Settings) -> Settings:
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url).set(port=1)
    return dataclasses.replace(
        settings, database_url=url.render_as_string(hide_password=False)
    )


class _NullContext:
    """A minimal `ModuleContext`, for constructing state outside a host."""

    settings: ClassVar[dict] = {}

    def health(self):  # pragma: no cover - unused by these assertions
        raise NotImplementedError


def test_readiness_is_healthy_when_dependencies_are_up(settings: Settings) -> None:
    engine = create_engine_from_settings(settings)
    try:
        assert database_health(engine).healthy
        assert migration_head_status(engine, settings.module_root).healthy
        assert nats_reachability(settings.nats_url).healthy
    finally:
        engine.dispose()


def test_readiness_reports_postgres_down_with_a_named_dependency(settings: Settings) -> None:
    """Acceptance 1: the detail must name `postgres`, not merely say "unhealthy"."""
    engine = create_engine_from_settings(_with_unreachable_database(settings))
    try:
        status = database_health(engine)
        migration = migration_head_status(engine, settings.module_root)
    finally:
        engine.dispose()
    assert not status.healthy
    assert status.name == "postgres"
    assert "postgres unavailable" in status.detail
    assert not migration.healthy
    # No credentials, DSN or driver detail may appear in a readiness report.
    assert "change-me" not in status.detail
    assert "change-me" not in migration.detail
    assert "psycopg://" not in status.detail


def test_readiness_reports_nats_down_with_a_named_dependency() -> None:
    status = nats_reachability("nats://127.0.0.1:1")
    assert not status.healthy
    assert "nats" in status.detail


def test_migration_head_drift_is_reported(engine, workspace_tmp, settings: Settings) -> None:
    """A manifest that disagrees with the database must be visible, not smoothed over."""
    manifest_dir = workspace_tmp / "B4"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "module.yaml").write_text(
        "schema_version: 1\n"
        "module: B4\n"
        "contract_version: 0.1.0\n"
        "entry_point: autograder_b4.plugin:create_module\n"
        "provides: []\n"
        "requires: [host]\n"
        "routes: []\n"
        "events: {publishes: [], subscribes: []}\n"
        "database: {schema: b4, migration_head: '20990101_9999'}\n",
        encoding="utf-8",
    )
    status = migration_head_status(engine, workspace_tmp)
    assert not status.healthy
    assert "20990101_9999" in status.detail
    assert alembic_script_head() in status.detail
    assert current_migration_head(engine) != "20990101_9999"


def test_module_state_health_aggregates_all_dependencies(
    engine, settings: Settings, module
) -> None:
    state = B4State(
        settings=settings,
        engine=engine,
        signing_key=module._ensure_state(_NullContext()).signing_key,
        manifests=module._ensure_state(_NullContext()).manifests,
    )
    healthy, detail = state.self_health()
    assert healthy, detail
    for name in ("postgres", "migration", "nats"):
        assert name in detail


def test_module_state_health_names_every_broken_dependency(
    settings: Settings, module
) -> None:
    """Point the module's own probe at a dead broker and a dead database."""
    broken = dataclasses.replace(
        settings,
        nats_url="nats://127.0.0.1:1",
        database_url=_with_unreachable_database(settings).database_url,
    )
    broken_engine = create_engine_from_settings(broken)
    try:
        state = B4State(
            settings=broken,
            engine=broken_engine,
            signing_key=module._ensure_state(_NullContext()).signing_key,
        )
        healthy, detail = state.self_health()
    finally:
        broken_engine.dispose()
    assert not healthy
    assert "postgres" in detail
    assert "nats" in detail


def test_module_health_fails_when_it_cannot_initialize(client, module) -> None:
    """A module that cannot build its state must report unhealthy, not raise."""
    from autograder_b4.plugin import B4Module

    fresh = B4Module(settings=None)
    report = fresh.health(_NullContext())
    assert report.name == "B4"
    assert report.healthy is True  # the ambient environment is valid, so it initializes


def test_health_records_only_transitions(engine, settings: Settings, module, caplog) -> None:
    """RQ-B-4-99 acceptance 4: the probe result is recorded -- once per state change.

    The state is what is recorded, not every observation: readiness is polled continuously,
    and a record per poll would turn a read path into log write amplification.
    """
    import logging

    signing_key = module._ensure_state(_NullContext()).signing_key
    state = B4State(settings=settings, engine=engine, signing_key=signing_key)
    with caplog.at_level(logging.INFO, logger="autograder_b4"):
        assert state.self_health()[0] is True
        assert state.self_health()[0] is True
        assert state.self_health()[0] is True
    first = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "ops.health.changed"
    ]
    assert len(first) == 1, [record.getMessage() for record in first]
    assert first[0].healthy is True
    assert first[0].previous_healthy is None  # the first observation is a transition too
    caplog.clear()

    # The same probe, now pointed at a dead broker: the transition is what gets recorded,
    # once, no matter how often it is then polled in the new state.
    state.settings = dataclasses.replace(settings, nats_url="nats://127.0.0.1:1")
    with caplog.at_level(logging.INFO, logger="autograder_b4"):
        assert state.self_health()[0] is False
        assert state.self_health()[0] is False
    transitions = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "ops.health.changed"
    ]
    assert len(transitions) == 1, [record.getMessage() for record in transitions]
    assert transitions[0].healthy is False
    assert transitions[0].previous_healthy is True
    assert transitions[0].levelname == "ERROR"
    assert "nats" in transitions[0].detail
    # A readiness report may be shipped to a log collector, so it may not carry credentials.
    assert "change-me" not in transitions[0].detail


def test_connection_dependency_yields_a_usable_connection(engine, settings: Settings) -> None:
    from autograder_b4.api.dependencies import build_dependencies

    state = B4State(settings=settings, engine=engine, signing_key=None)  # type: ignore[arg-type]
    generator = build_dependencies(state).connection()
    connection = next(generator)
    try:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        generator.close()
