"""HTTP surface for execution declarations and the internal fixture-byte channel.

Two concerns, both about what actually travels:

* the **authoring round trip** -- ``QuestionDraftInput.execution`` and the new case fields are
  stored, read back through ``getDraftContent``, preserved by omission and reset by an explicit
  ``null``; ``QuestionVersionPublic`` exposes only the mode and never the configuration behind
  it (SSH password, expected file digests, segment marker).
* the **fixture-bytes channel** -- ``getVersionFixtureContents`` is service-only, serves only
  ``mount=input``, is bounded per item and in total, and refuses over-limit requests instead of
  truncating them.

The validation matrix itself (mode/field contradictions) is unit-tested in
``test_execution.py``; here the assertions are about responses, status codes and bytes.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime

from autograder_b3.dto.internal import PROTECTED_FIELD_NAMES

from .conftest import COURSE_A, draft_payload
from .examples import (
    EXPECTED_FIXTURE_BYTES,
    INPUT_FIXTURE_BYTES,
    INPUT_FIXTURE_SHA,
)
from .support import SERVICE_HEADERS, TEACHER_A_HEADERS, b64, http_attestation, http_publish

DELIMITER = "@@@-section-@@@"
SSH_PASSWORD = "correct horse battery staple"
SSH_CONFIG = {
    "host": "127.0.0.1",
    "port": 22,
    "username": "user01",
    "password": SSH_PASSWORD,
    "login_timeout_seconds": 10,
    "session_timeout_seconds": 10,
}
NOW = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def _create(client, payload: dict) -> tuple[dict, str]:
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=payload,
        headers=TEACHER_A_HEADERS,
    )
    assert created.status_code == 201, created.text
    question = created.json()
    return question, question["versions"][0]["question_version_id"]


def _segment_payload(*, mode: str = "single_run", delimiter: str = DELIMITER) -> dict:
    payload = draft_payload(language="python")
    payload["adapter"] = {"kind": "stdin", "stdin": "a\n", "expect_exit_code": 0}
    payload["execution"] = {"mode": mode, "segment_delimiter": delimiter}
    payload["cases"] = [
        {
            "name": "first segment",
            "ordinal": 0,
            "expected_stdout": "one\n",
            "segment": 0,
        },
        {
            "name": "second segment",
            "ordinal": 1,
            "expected_stdout": "two\n",
            "segment": 1,
        },
    ]
    return payload


def _ssh_payload() -> dict:
    payload = draft_payload(language="bash")
    payload["adapter"] = {"kind": "stdin", "stdin": "", "expect_exit_code": 0}
    payload["execution"] = {"mode": "ssh_session", "ssh": SSH_CONFIG}
    payload["cases"] = [
        {"name": "service ready", "ordinal": 0, "ssh_check": "service_ready"},
        {"name": "auth succeeded", "ordinal": 1, "ssh_check": "auth_succeeded"},
        {"name": "session closed", "ordinal": 2, "ssh_check": "session_closed"},
    ]
    return payload


# ==========================================================================
# Authoring round trip
# ==========================================================================


def test_a_version_without_a_declaration_reads_back_as_per_case(client):
    _question, version_id = _create(client, draft_payload())
    response = client.get(
        f"/api/v1/question-versions/{version_id}/draft-content",
        headers=TEACHER_A_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Always present and normalized: the authoring page never has to guess whether a
    # declaration exists.
    assert body["execution"] == {"mode": "per_case"}


def test_the_declaration_and_per_case_fields_round_trip_through_draft_content(client):
    _question, version_id = _create(client, _segment_payload())
    response = client.get(
        f"/api/v1/question-versions/{version_id}/draft-content",
        headers=TEACHER_A_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution"] == {"mode": "single_run", "segment_delimiter": DELIMITER}
    assert [case["segment"] for case in body["cases"]] == [0, 1]


def test_an_omitted_declaration_is_preserved(client):
    payload = _segment_payload()
    _question, version_id = _create(client, payload)
    # A title-only edit: no `execution` key and no `cases` key in the body.
    edited = {
        "title": "renamed only",
        "stem_markdown": payload["stem_markdown"],
        "language": "python",
        "adapter": payload["adapter"],
    }
    response = client.patch(
        f"/api/v1/questions/{_question_id(client, version_id)}",
        json=edited,
        headers=TEACHER_A_HEADERS,
    )
    assert response.status_code == 200, response.text

    readback = client.get(
        f"/api/v1/question-versions/{version_id}/draft-content",
        headers=TEACHER_A_HEADERS,
    ).json()
    assert readback["execution"] == {"mode": "single_run", "segment_delimiter": DELIMITER}
    assert [case["segment"] for case in readback["cases"]] == [0, 1]


def test_an_explicit_null_resets_the_declaration_to_per_case(client):
    payload = _segment_payload()
    _question, version_id = _create(client, payload)
    question_id = _question_id(client, version_id)

    per_case_case = {
        "name": "the only case",
        "ordinal": 0,
        "expected_stdout": "ok\n",
    }
    reset = {
        "title": payload["title"],
        "stem_markdown": payload["stem_markdown"],
        "language": "python",
        "adapter": payload["adapter"],
        "execution": None,
        "cases": [per_case_case],
    }
    response = client.patch(
        f"/api/v1/questions/{question_id}", json=reset, headers=TEACHER_A_HEADERS
    )
    assert response.status_code == 200, response.text

    readback = client.get(
        f"/api/v1/question-versions/{version_id}/draft-content",
        headers=TEACHER_A_HEADERS,
    ).json()
    assert readback["execution"] == {"mode": "per_case"}
    assert "segment" not in readback["cases"][0]


def test_an_invalid_declaration_is_a_422_problem(client):
    payload = draft_payload()
    payload["execution"] = {"mode": "per_case", "ssh": SSH_CONFIG}
    response = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=payload,
        headers=TEACHER_A_HEADERS,
    )
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["type"].endswith("/adapter-manifest-invalid")
    assert SSH_PASSWORD not in response.text


def test_a_case_level_contradiction_is_a_422_problem(client):
    payload = draft_payload()
    payload["adapter"] = {"kind": "stdin", "stdin": "a\n", "expect_exit_code": 0}
    payload["execution"] = {"mode": "single_run", "segment_delimiter": DELIMITER}
    payload["cases"][0]["segment"] = 0
    # The second case declares neither a segment nor file_checks.
    response = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=payload,
        headers=TEACHER_A_HEADERS,
    )
    assert response.status_code == 422, response.text
    assert response.json()["type"].endswith("/adapter-manifest-invalid")


# ==========================================================================
# Public DTO: mode only
# ==========================================================================


def test_the_public_version_dto_exposes_only_the_execution_mode(client):
    _question, version_id = _create(client, _ssh_payload())
    response = client.get(
        f"/api/v1/question-versions/{version_id}", headers=TEACHER_A_HEADERS
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_mode"] == "ssh_session"
    # The configuration behind the mode is protected and must not travel here: not the
    # password, not the user, not the templates, not the marker. The vocabulary check is
    # asserted as field absence (the same standard every other public DTO is held to), and the
    # raw response is searched too so a value echoed through another field is caught.
    assert SSH_PASSWORD not in response.text
    execution_config_names = {
        "execution_manifest",
        "file_checks",
        "segment_delimiter",
        "subject_template",
        "password",
    }
    assert execution_config_names <= PROTECTED_FIELD_NAMES
    assert set(body) & execution_config_names == set()
    # Exactly one execution-related key is public, and it is the enum.
    assert {key for key in body if "execution" in key} == {"execution_mode"}


def test_a_published_single_run_question_serves_the_declaration_in_its_package(client):
    question, version_id = _create(client, _segment_payload())
    published = http_publish(client, uuid.UUID(version_id), cases=2)
    assert published.status_code == 200, published.text

    package = client.get(
        f"/api/v1/question-versions/{version_id}/evaluation-package",
        headers=SERVICE_HEADERS,
    )
    assert package.status_code == 200, package.text
    body = package.json()
    assert body["execution"] == {"mode": "single_run", "segment_delimiter": DELIMITER}
    assert [entry["segment"] for entry in body["tests"]] == [0, 1]
    assert body["tests"][0]["case_id"]
    # The package still carries no reference answer (frozen contract), and the public DTO
    # reports the same mode the package declares.
    assert "reference_answer" not in body
    detail = client.get(
        f"/api/v1/question-versions/{version_id}", headers=TEACHER_A_HEADERS
    ).json()
    assert detail["status"] == "published"
    assert detail["execution_mode"] == "single_run"
    assert question["question_id"]


# ==========================================================================
# Fixture bytes channel
# ==========================================================================


def _file_question(client) -> str:
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(kind="file"),
        headers=TEACHER_A_HEADERS,
    )
    assert created.status_code == 201, created.text
    return created.json()["versions"][0]["question_version_id"]


def _upload_fixture(client, version_id: str, *, path: str, mount: str, content: bytes) -> dict:
    response = client.post(
        f"/api/v1/question-versions/{version_id}/fixtures",
        json={"path": path, "mount": mount, "content_base64": b64(content)},
        headers=TEACHER_A_HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _version_digest(client, version_id: str) -> str:
    detail = client.get(
        f"/api/v1/question-versions/{version_id}", headers=TEACHER_A_HEADERS
    )
    assert detail.status_code == 200, detail.text
    digest = detail.json()["content_digest"]
    assert digest and len(digest) == 64
    return digest


def test_uploading_a_fixture_refreshes_the_content_digest(client):
    """Fixture bytes are part of the content digest, so uploading one must rehash.

    Regression: the upload persisted the fixture without refreshing the digest, so
    ``GET /reference-answer`` kept serving the pre-fixture digest and the recorder refused
    every attestation for a fixture question with 409 — that version could never be published.
    """
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(
            kind="file",
            files=[
                {
                    "path": "week6_1.txt",
                    "mount": "input",
                    "content_sha256": INPUT_FIXTURE_SHA,
                }
            ],
        ),
        headers=TEACHER_A_HEADERS,
    )
    assert created.status_code == 201, created.text
    version_id = created.json()["versions"][0]["question_version_id"]
    before = _version_digest(client, version_id)
    _upload_fixture(
        client,
        version_id,
        path="week6_1.txt",
        mount="input",
        content=INPUT_FIXTURE_BYTES,
    )
    after = _version_digest(client, version_id)
    assert after != before
    # The digest the API serves now is the one the recorder recomputes and accepts.
    http_attestation(client, uuid.UUID(version_id), cases=2)


def test_fixture_contents_is_service_only(client):
    version_id = _file_question(client)
    _upload_fixture(
        client, version_id, path="data/input.csv", mount="input", content=INPUT_FIXTURE_BYTES
    )
    url = f"/api/v1/question-versions/{version_id}/fixture-contents"
    assert client.get(url, headers=TEACHER_A_HEADERS).status_code == 403
    assert (
        client.get(url, headers={"Authorization": "Bearer student-a"}).status_code == 403
    )
    assert client.get(url).status_code == 401
    assert client.get(url, headers=SERVICE_HEADERS).status_code == 200


def test_fixture_contents_returns_input_bytes_and_nothing_else(client):
    version_id = _file_question(client)
    _upload_fixture(
        client, version_id, path="data/input.csv", mount="input", content=INPUT_FIXTURE_BYTES
    )
    # An expected_output fixture exists for the same version and must not be served: its
    # comparison only needs the digest the package already carries.
    _upload_fixture(
        client,
        version_id,
        path="out/report.txt",
        mount="expected_output",
        content=EXPECTED_FIXTURE_BYTES,
    )

    response = client.get(
        f"/api/v1/question-versions/{version_id}/fixture-contents",
        headers=SERVICE_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"question_version_id", "items"}
    assert body["question_version_id"] == version_id
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert set(item) == {"path", "mount", "content_sha256", "size_bytes", "content_base64"}
    assert item["path"] == "data/input.csv"
    assert item["mount"] == "input"
    assert item["content_sha256"] == INPUT_FIXTURE_SHA
    assert item["size_bytes"] == len(INPUT_FIXTURE_BYTES)
    assert base64.b64decode(item["content_base64"]) == INPUT_FIXTURE_BYTES
    # The expected-output bytes are not in the response in any form.
    assert base64.b64encode(EXPECTED_FIXTURE_BYTES).decode("ascii") not in response.text


def test_fixture_contents_refuses_another_mount(client):
    version_id = _file_question(client)
    response = client.get(
        f"/api/v1/question-versions/{version_id}/fixture-contents",
        params={"mount": "expected_output"},
        headers=SERVICE_HEADERS,
    )
    assert response.status_code == 422, response.text
    assert response.json()["status"] == 422


def test_fixture_contents_unknown_version_is_404(client):
    response = client.get(
        f"/api/v1/question-versions/{uuid.uuid4()}/fixture-contents",
        headers=SERVICE_HEADERS,
    )
    assert response.status_code == 404


def test_fixture_contents_refuses_an_oversized_item_instead_of_truncating(client, database):
    version_id = _file_question(client)
    oversized = b"x" * (1024 * 1024 + 1)
    with database.protected_session() as session:
        from autograder_b3.infra.repositories import ProtectedContentRepository

        ProtectedContentRepository(session).upsert_fixture(
            question_version_id=uuid.UUID(version_id),
            path="big.bin",
            mount="input",
            content=oversized,
            content_sha256=hashlib.sha256(oversized).hexdigest(),
            now=NOW,
        )

    response = client.get(
        f"/api/v1/question-versions/{version_id}/fixture-contents",
        headers=SERVICE_HEADERS,
    )
    assert response.status_code == 413, response.text
    assert response.json()["type"].endswith("/payload-too-large")
    # Refused, not truncated: the response body is a Problem Details object, not a bundle.
    assert "items" not in response.json()


def test_fixture_contents_refuses_an_oversized_bundle(client, database):
    version_id = _file_question(client)
    payload = b"y" * (1024 * 1024)
    digest = hashlib.sha256(payload).hexdigest()
    with database.protected_session() as session:
        from autograder_b3.infra.repositories import ProtectedContentRepository

        repository = ProtectedContentRepository(session)
        for index in range(5):  # 5 MiB total, each item exactly 1 MiB
            repository.upsert_fixture(
                question_version_id=uuid.UUID(version_id),
                path=f"data/part-{index}.bin",
                mount="input",
                content=payload,
                content_sha256=digest,
                now=NOW,
            )

    response = client.get(
        f"/api/v1/question-versions/{version_id}/fixture-contents",
        headers=SERVICE_HEADERS,
    )
    assert response.status_code == 413, response.text
    assert response.json()["type"].endswith("/payload-too-large")


def test_fixture_contents_refuses_too_many_items(client, database):
    version_id = _file_question(client)
    payload = b"tiny"
    digest = hashlib.sha256(payload).hexdigest()
    with database.protected_session() as session:
        from autograder_b3.infra.repositories import ProtectedContentRepository

        repository = ProtectedContentRepository(session)
        for index in range(51):  # the contract's bundle carries at most 50 items
            repository.upsert_fixture(
                question_version_id=uuid.UUID(version_id),
                path=f"data/item-{index}.bin",
                mount="input",
                content=payload,
                content_sha256=digest,
                now=NOW,
            )

    response = client.get(
        f"/api/v1/question-versions/{version_id}/fixture-contents",
        headers=SERVICE_HEADERS,
    )
    assert response.status_code == 413, response.text
    assert response.json()["type"].endswith("/payload-too-large")


def test_fixture_contents_is_empty_for_a_version_without_input_fixtures(client):
    version_id = _file_question(client)
    response = client.get(
        f"/api/v1/question-versions/{version_id}/fixture-contents",
        headers=SERVICE_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


def _question_id(client, version_id: str) -> str:
    detail = client.get(
        f"/api/v1/question-versions/{version_id}", headers=TEACHER_A_HEADERS
    )
    assert detail.status_code == 200, detail.text
    return detail.json()["question_id"]
