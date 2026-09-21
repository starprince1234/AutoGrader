"""The endpoint, driven over HTTP against the assembled application.

`TestClient` talks to the object `autograder_host.main.create_app()` returns -- the same
factory the running deployment serves -- so the request passes through the real router
inclusion, the real module contexts and B5's real router. No port is bound.

Both response bodies are validated against the contract files themselves rather than
against a transcription of them: a hand-written expectation would keep agreeing with the
code while the contract moved on.
"""

from __future__ import annotations

from pathlib import Path

import jsonschema
import yaml
from fastapi.testclient import TestClient

from autograder_b5.contracts import contract_paths

OPENAPI_DIRECTORY = Path(__file__).resolve().parents[2] / "MAIN" / "contracts" / "openapi"

PATH = "/api/v1/platform/conformance"


def _schema(document: str, name: str) -> dict:
    payload = yaml.safe_load((OPENAPI_DIRECTORY / document).read_text(encoding="utf-8"))
    return payload["components"]["schemas"][name]


def _assert_matches(document: str, name: str, body: object) -> None:
    jsonschema.validate(body, _schema(document, name))


def test_an_unauthenticated_caller_is_refused_with_problem_details(assembled_app) -> None:
    response = TestClient(assembled_app).get(PATH)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    _assert_matches("common.yaml", "ProblemDetails", response.json())
    assert response.json()["instance"] == PATH


def test_a_malformed_credential_is_refused_the_same_way(assembled_app) -> None:
    response = TestClient(assembled_app).get(
        PATH, headers={"Authorization": "Bearer not-a-token"}
    )
    assert response.status_code == 401
    _assert_matches("common.yaml", "ProblemDetails", response.json())


def test_a_token_for_another_audience_is_refused(assembled_app, issuer) -> None:
    """A well-formed, correctly signed token that is not for this API is still refused."""
    token = issuer.token(aud="some-other-api")
    response = TestClient(assembled_app).get(
        PATH, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
    _assert_matches("common.yaml", "ProblemDetails", response.json())


def test_an_authenticated_caller_reads_a_consistent_report(assembled_app, issuer) -> None:
    response = TestClient(assembled_app).get(
        PATH, headers={"Authorization": f"Bearer {issuer.token()}"}
    )
    assert response.status_code == 200
    body = response.json()
    _assert_matches("b5.yaml", "ConformanceReport", body)
    assert body["consistent"] is True
    assert body["differences"] == []
    assert body["loaded_modules"] == ["B4", "B3", "B2", "B5"]
    assert body["contract_path_count"] == len(
        contract_paths(OPENAPI_DIRECTORY.parent)
    )


def test_the_report_covers_b5s_own_contract_path(assembled_app, issuer) -> None:
    """B5 is inside the comparison it performs, not exempt from it.

    `b5.yaml` owns the very path being read, so a report that omitted it (or an exemption
    that skipped the caller's own module) would make `consistent` cheap to reach.
    """
    declared = {
        entry.path: entry.document
        for entry in contract_paths(OPENAPI_DIRECTORY.parent)
    }
    assert declared[PATH] == "b5.yaml"
    response = TestClient(assembled_app).get(
        PATH, headers={"Authorization": f"Bearer {issuer.token()}"}
    )
    assert response.status_code == 200
    assert response.json()["contract_path_count"] == len(declared)
