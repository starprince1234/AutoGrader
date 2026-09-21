"""TC-VER-01..04: immutability, publish completeness, event payload, assignments.

The requirement is "a change produces a new version, never an in-place edit"
(``RQ-B-3-20`` acceptance 2). It is asserted at three levels, because one level is
never enough for the highest-risk rule in the module:

* the API answers ``409`` and explains how to proceed;
* :func:`update_question` refuses and branches only when asked to;
* PostgreSQL itself refuses - a raw ``UPDATE`` through the owner connection fails,
  so no future code path can bypass the rule.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from autograder_b3.auth import Principal
from autograder_b3.domain.assignments import create_assignment
from autograder_b3.domain.revisions import REVISION_DIFF_KEYS
from autograder_b3.domain.version_content import load_version_content
from autograder_b3.domain.versions import archive_version, delete_version
from autograder_b3.dto.internal import PROTECTED_FIELD_NAMES, AssignmentCreateInputModel
from autograder_b3.errors import (
    Conflict,
    DraftAssignmentReference,
    ImmutableVersionConflict,
    InvalidStateTransition,
    NotFound,
    ProtectedContentMissing,
    RevisionNoteMisuse,
)

from .conftest import COURSE_A, COURSE_B, SECTION_A, draft_payload
from .examples import CALLABLE_MANIFEST, FILE_MANIFEST, STDIN_MANIFEST
from .support import (
    attest,
    create_published_question,
    create_question,
    draft,
    publish,
    student_principal,
    teacher_principal,
)

PUBLISH_AT = datetime(2026, 9, 15, 8, 5, tzinfo=UTC)
B3_ROOT = Path(__file__).resolve().parents[1]


def declared_revision_diff_properties() -> set[str]:
    """Read ``RevisionDiff``'s declared key set out of the contract itself.

    Phase 03's boundary test compared the delta against a constant in this repository,
    which proves the delta is self-consistent but not that it matches what a client was
    promised. Reading the contract closes that: a key added on either side alone fails.
    """
    contract = (B3_ROOT.parent / "MAIN" / "contracts" / "openapi" / "b3.yaml").read_text(
        encoding="utf-8"
    )
    section = contract.split("\n    RevisionDiff:\n", 1)[1].split(
        "\n    ComparisonDiagnostics:", 1
    )[0]
    # Property keys are the mapping keys directly under `properties:` - 8 spaces here,
    # being 2 per nesting level. `RevisionDiff` declares every key with an inline flow
    # mapping or a nested block, and only the nested blocks are indented deeper.
    properties = section.split("      properties:\n", 1)[1]
    return set(re.findall(r"^ {8}([a-z0-9_]+):", properties, flags=re.MULTILINE))


def test_publish_freezes_content_and_records_digests(database, settings):
    question, version = create_published_question(
        database, settings, payload=draft_payload(kind="stdin")
    )
    assert version.status == "published"
    assert version.published_at is not None
    assert len(version.content_digest or "") == 64
    assert len(version.package_digest or "") == 64

    with database.app_session() as session:
        from autograder_b3.infra.repositories import QuestionBankRepository

        bank = QuestionBankRepository(session)
        refreshed = bank.get_question(question.question_id)
        assert refreshed is not None and refreshed.status == "published"


def test_publish_emits_an_outbox_event_without_protected_content(database, settings):
    question, version = create_published_question(
        database, settings, payload=draft_payload(kind="stdin")
    )
    with database.app_session() as session:
        from autograder_b3.infra.repositories import QuestionBankRepository

        pending = [
            row
            for row in QuestionBankRepository(session).pending_outbox(limit=500)
            if row.payload.get("question_version_id") == str(version.question_version_id)
        ]
    assert len(pending) == 1
    row = pending[0]
    assert row.event_type == "autograder.v1.question.version.published"
    assert row.producer == "B3"
    assert set(row.payload) == {
        "question_id",
        "question_version_id",
        "course_id",
        "content_digest",
        "published_at",
    }
    forbidden = {
        "reference_answer",
        "cases",
        "expected_stdout",
        "expected_stderr",
        "fixture",
        "test_cases",
    }
    assert set(row.payload) & forbidden == set()
    assert row.payload["question_version_id"] == str(version.question_version_id)


def test_publish_without_a_reference_answer_is_refused(database, settings):
    question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload(reference_answer=None)
    )
    with pytest.raises(ProtectedContentMissing):
        publish(database, principal=teacher_principal(), question_version_id=version.question_version_id)


def test_publish_without_any_case_is_refused(database, settings):
    question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    # A version can now legitimately carry no cases (omitting `cases` on the first write
    # does exactly that, CHG-0017); this removes them from a version that *had* them, to
    # prove the publish gate is independent of how the state was reached.
    with database.protected_session() as session:
        session.execute(
            text("DELETE FROM b3.test_cases WHERE question_version_id = :vid"),
            {"vid": version.question_version_id},
        )
    with pytest.raises(ProtectedContentMissing):
        publish(database, principal=teacher_principal(), question_version_id=version.question_version_id)


def test_publish_with_an_invalid_manifest_is_refused(database, settings):
    """A shell string where an argument array is required never reaches the database."""
    from pydantic import ValidationError

    from autograder_b3.compile import validate_adapter_manifest
    from autograder_b3.errors import ManifestInvalid

    with pytest.raises(ValidationError):
        draft(draft_payload(args="rm -rf /"))
    with pytest.raises(ManifestInvalid):
        validate_adapter_manifest({"kind": "stdin", "args": "rm -rf /", "stdin": ""})


def test_publishing_twice_is_a_conflict(database, settings):
    question, version = create_published_question(database, settings, payload=draft_payload())
    with pytest.raises(InvalidStateTransition):
        publish(
            database,
            principal=teacher_principal(),
            question_version_id=version.question_version_id,
            now=PUBLISH_AT,
        )


def test_editing_a_published_version_in_place_is_refused(database, settings):
    """TC-VER-01: 409, and the caller is told to create a new version."""
    from autograder_b3.domain.questions import update_question

    question, version = create_published_question(database, settings, payload=draft_payload())
    with pytest.raises(ImmutableVersionConflict) as error:
        update_question(
            database,
            settings,
            principal=teacher_principal(),
            question_id=question.question_id,
            payload=draft(draft_payload(stem="changed")),
            now=PUBLISH_AT,
        )
    assert "as_new_version" in str(error.value)
    with pytest.raises(ImmutableVersionConflict):
        update_question(
            database,
            settings,
            principal=teacher_principal(),
            question_id=question.question_id,
            payload=draft(draft_payload()),
            now=PUBLISH_AT,
        )


def test_as_new_version_creates_v2_and_keeps_v1_intact(database, settings):
    from autograder_b3.domain.questions import update_question

    question, version_one = create_published_question(database, settings, payload=draft_payload())
    original_stem = version_one.stem_markdown
    question_row, version_two = update_question(
        database,
        settings,
        principal=teacher_principal(),
        question_id=question.question_id,
        payload=draft(draft_payload(stem="A different stem for version two.")),
        as_new_version=True,
        now=PUBLISH_AT,
    )
    assert version_two.version == 2
    assert version_two.status == "draft"
    assert version_two.question_version_id != version_one.question_version_id

    with database.protected_session() as session:
        from autograder_b3.infra.repositories import QuestionBankRepository

        reloaded = QuestionBankRepository(session).get_version(version_one.question_version_id)
    assert reloaded is not None
    assert reloaded.stem_markdown == original_stem
    assert reloaded.status == "published"
    assert reloaded.package_digest == version_one.package_digest


def test_postgres_refuses_an_in_place_edit_of_published_content(database, settings, owner_engine):
    """The rule is a database guarantee, not only an application one."""
    question, version = create_published_question(database, settings, payload=draft_payload())
    with pytest.raises(DBAPIError) as error:
        with owner_engine.connect() as connection:
            connection.execute(
                text(
                    "UPDATE b3.question_versions SET stem_markdown = 'tampered' "
                    "WHERE question_version_id = :vid"
                ),
                {"vid": version.question_version_id},
            )
    assert getattr(error.value.orig, "sqlstate", None) == "23001"


def test_postgres_refuses_new_protected_content_on_a_published_version(
    database, settings, owner_engine
):
    question, version = create_published_question(database, settings, payload=draft_payload())
    with pytest.raises(DBAPIError) as error:
        with owner_engine.connect() as connection:
            connection.execute(
                text(
                    "UPDATE b3.reference_answers SET body = 'leaked' "
                    "WHERE question_version_id = :vid"
                ),
                {"vid": version.question_version_id},
            )
    assert getattr(error.value.orig, "sqlstate", None) == "23001"


def test_postgres_refuses_deleting_a_published_version(database, settings, owner_engine):
    question, version = create_published_question(database, settings, payload=draft_payload())
    with pytest.raises(DBAPIError) as error:
        with owner_engine.connect() as connection:
            connection.execute(
                text("DELETE FROM b3.question_versions WHERE question_version_id = :vid"),
                {"vid": version.question_version_id},
            )
    assert getattr(error.value.orig, "sqlstate", None) == "23001"


def test_archive_published_version_and_then_delete_is_refused(database, settings):
    """TC-VER-04: a published version cannot be deleted, only archived."""
    question, version = create_published_question(database, settings, payload=draft_payload())
    with pytest.raises(Conflict):
        delete_version(
            database, principal=teacher_principal(), question_version_id=version.question_version_id
        )
    archived = archive_version(
        database,
        principal=teacher_principal(),
        question_version_id=version.question_version_id,
        now=PUBLISH_AT,
    )
    assert archived.status == "archived"
    with pytest.raises(Conflict):
        delete_version(
            database, principal=teacher_principal(), question_version_id=version.question_version_id
        )


def test_archiving_a_draft_is_refused(database, settings):
    question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    with pytest.raises(InvalidStateTransition):
        archive_version(
            database,
            principal=teacher_principal(),
            question_version_id=version.question_version_id,
            now=PUBLISH_AT,
        )


def test_deleting_a_draft_is_allowed(database, settings):
    question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    delete_version(
        database, principal=teacher_principal(), question_version_id=version.question_version_id
    )
    with database.protected_session() as session:
        from autograder_b3.infra.repositories import QuestionBankRepository

        assert QuestionBankRepository(session).get_version(version.question_version_id) is None


# --- assignments ---------------------------------------------------------


def test_assignment_accepts_only_published_versions(database, settings):
    question, published = create_published_question(database, settings, payload=draft_payload())
    _, draft_version = create_question(
        database,
        settings,
        principal=teacher_principal(),
        payload=draft_payload(title="second"),
    )

    with pytest.raises(DraftAssignmentReference):
        create_assignment(
            database,
            principal=teacher_principal(),
            payload=AssignmentCreateInputModel(
                course_id=COURSE_A,
                section_id=SECTION_A,
                title="week 1",
                due_at=None,
                question_version_ids=(draft_version.question_version_id,),
            ),
            now=PUBLISH_AT,
        )

    assignment, items = create_assignment(
        database,
        principal=teacher_principal(),
        payload=AssignmentCreateInputModel(
            course_id=COURSE_A,
            section_id=SECTION_A,
            title="week 1",
            due_at=datetime(2026, 9, 20, tzinfo=UTC),
            question_version_ids=(published.question_version_id,),
        ),
        now=PUBLISH_AT,
    )
    assert assignment.title == "week 1"
    assert [item.question_version_id for item in items] == [published.question_version_id]


def test_assignment_referencing_another_course_is_not_found(database, settings):
    _, version = create_published_question(database, settings, payload=draft_payload())
    with pytest.raises(NotFound):
        create_assignment(
            database,
            principal=teacher_principal(course_ids=(COURSE_B,)),
            payload=AssignmentCreateInputModel(
                course_id=COURSE_B,
                section_id=SECTION_A,
                title="cross course",
                due_at=None,
                question_version_ids=(version.question_version_id,),
            ),
            now=PUBLISH_AT,
        )


def test_student_cannot_write_to_the_question_bank(database, settings):
    from autograder_b3.errors import Forbidden

    with pytest.raises(Forbidden):
        create_question(
            database,
            settings,
            principal=student_principal(),
            payload=draft_payload(),
        )


def test_cross_course_publish_is_forbidden_not_masked(database, settings):
    from autograder_b3.errors import Forbidden

    question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    other_teacher: Principal = teacher_principal(course_ids=(COURSE_B,))
    with pytest.raises(Forbidden):
        publish(
            database,
            principal=other_teacher,
            question_version_id=version.question_version_id,
        )


# --- adapter examples through the publish path ---------------------------


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (draft_payload(kind="stdin", language="bash"), "stdin"),
        (draft_payload(kind="callable", callable_target=CALLABLE_MANIFEST["callable"]), "callable"),
    ],
)
def test_example_questions_publish_and_yield_a_package(database, settings, payload, kind):
    from autograder_b3.domain.packages import load_evaluation_package

    question, version = create_published_question(database, settings, payload=payload)
    package = load_evaluation_package(database, question_version_id=version.question_version_id)
    assert package.adapter["kind"] == kind
    assert package.package_digest == version.package_digest
    assert len(package.tests) == 2


def test_publishing_with_a_microsecond_clock_survives_the_database_round_trip(database, settings):
    """Publish, then fetch the package back through the database. This is the path that was
    broken for **every** question (103 of 103 answered 409), so it deserves a test that walks it
    with a realistic clock.

    **It does not isolate the digest rule**, and the first version of this test wrongly claimed
    it did: `to_wire()` renders `created_at` to milliseconds, so a microsecond-vs-millisecond
    difference serialises identically and this test passes under either rule. The rule itself is
    guarded by `test_the_package_digest_does_not_depend_on_created_at`, which uses two values a
    full millisecond apart for exactly that reason.

    What earns this test its place is the round trip: `published_at` goes into a
    `timestamptz(3)` column and comes back rounded, and the assertion below pins the shape the
    suite assumes.
    """
    from autograder_b3.domain.packages import load_evaluation_package

    unaligned = datetime(2026, 9, 15, 8, 0, 0, 123_456, tzinfo=UTC)
    _question, version = create_published_question(
        database, settings, payload=draft_payload(kind="stdin"), now=unaligned
    )
    with database.protected_session() as session:
        from autograder_b3.infra.repositories import QuestionBankRepository

        stored = QuestionBankRepository(session).get_version(version.question_version_id)
    assert stored is not None and stored.published_at is not None
    assert stored.published_at.microsecond % 1000 == 0, (
        "the fixture must exercise a rounded timestamp; if the column kept microseconds this "
        "test would pass for the wrong reason"
    )
    package = load_evaluation_package(database, question_version_id=version.question_version_id)
    assert package.package_digest == stored.package_digest


def test_file_example_publishes_with_bound_fixture_digests(database, settings):
    from autograder_b3.domain.assets import upload_fixture
    from autograder_b3.domain.packages import load_evaluation_package
    from autograder_b3.dto.internal import FixtureUploadInputModel

    from .examples import (
        EXPECTED_FIXTURE_BYTES,
        EXPECTED_FIXTURE_SHA,
        INPUT_FIXTURE_BYTES,
        INPUT_FIXTURE_SHA,
    )
    from .support import b64

    question, version = create_question(
        database,
        settings,
        principal=teacher_principal(),
        payload=draft_payload(kind="file", files=FILE_MANIFEST["files"]),
    )
    upload_fixture(
        database,
        settings,
        principal=teacher_principal(),
        question_version_id=version.question_version_id,
        payload=FixtureUploadInputModel(
            path="data/input.csv", mount="input", content_base64=b64(INPUT_FIXTURE_BYTES)
        ),
        now=PUBLISH_AT,
    )
    upload_fixture(
        database,
        settings,
        principal=teacher_principal(),
        question_version_id=version.question_version_id,
        payload=FixtureUploadInputModel(
            path="out/report.txt",
            mount="expected_output",
            content_base64=b64(EXPECTED_FIXTURE_BYTES),
        ),
        now=PUBLISH_AT,
    )

    published, _content_digest, _package_digest = publish(
        database,
        principal=teacher_principal(),
        question_version_id=version.question_version_id,
        now=PUBLISH_AT,
    )
    package = load_evaluation_package(database, question_version_id=published.question_version_id)
    assert {entry["mount"] for entry in package.adapter["files"]} == {"input", "expected_output"}
    assert {entry["content_sha256"] for entry in package.adapter["files"]} == {
        INPUT_FIXTURE_SHA,
        EXPECTED_FIXTURE_SHA,
    }


def test_fixture_digest_mismatch_blocks_publication(database, settings):
    """changing declared vs stored content is detected before the package is frozen."""
    from autograder_b3.domain.assets import upload_fixture
    from autograder_b3.dto.internal import FixtureUploadInputModel
    from autograder_b3.errors import ManifestInvalid

    from .examples import INPUT_FIXTURE_BYTES
    from .support import b64

    lying_manifest = {
        "kind": "file",
        "files": [{"path": "data/input.csv", "content_sha256": "f" * 64, "mount": "input"}],
    }
    question, version = create_question(
        database,
        settings,
        principal=teacher_principal(),
        payload=draft_payload(kind="file", files=lying_manifest["files"]),
    )
    upload_fixture(
        database,
        settings,
        principal=teacher_principal(),
        question_version_id=version.question_version_id,
        payload=FixtureUploadInputModel(
            path="data/input.csv", mount="input", content_base64=b64(INPUT_FIXTURE_BYTES)
        ),
        now=PUBLISH_AT,
    )
    with pytest.raises(ManifestInvalid):
        publish(
            database,
            principal=teacher_principal(),
            question_version_id=version.question_version_id,
            now=PUBLISH_AT,
        )


def test_fixture_upload_requires_a_draft_version(database, settings):
    from autograder_b3.domain.assets import upload_fixture
    from autograder_b3.dto.internal import FixtureUploadInputModel

    from .examples import INPUT_FIXTURE_BYTES
    from .support import b64

    question, published = create_published_question(database, settings, payload=draft_payload())
    with pytest.raises(ImmutableVersionConflict):
        upload_fixture(
            database,
            settings,
            principal=teacher_principal(),
            question_version_id=published.question_version_id,
            payload=FixtureUploadInputModel(
                path="data/input.csv", mount="input", content_base64=b64(INPUT_FIXTURE_BYTES)
            ),
            now=PUBLISH_AT,
        )


def test_publish_without_an_attestation_for_the_current_content_is_refused(database, settings):
    """The gate that makes "the reference answer works" a platform property.

    Before this, the only thing that knew whether a question's own reference answer
    reproduced its expected output was a script somebody had to remember to run, and it
    left no trace - so an unverified or subsequently edited question could be published
    and reach a student.

    The refusal also has to name the *current* remedy: verification is part of the authoring
    flow now, and the message used to send the reader to that very script.
    """
    from autograder_b3.errors import MissingReferenceAttestation

    _question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    with pytest.raises(MissingReferenceAttestation) as error:
        publish(
            database,
            principal=teacher_principal(),
            question_version_id=version.question_version_id,
            attestation=False,
        )
    message = str(error.value)
    assert version.content_digest in message
    assert "verify and publish" in message
    assert "verify_question_bank" not in message


def test_attestation_of_other_content_does_not_unlock_publish(database, settings):
    """A declared digest is cross-checked, never trusted (same rule as fixtures)."""
    from autograder_b3.auth import SERVICE_PRINCIPAL
    from autograder_b3.domain.attestations import record_reference_attestation
    from autograder_b3.errors import AttestationMismatch, MissingReferenceAttestation

    _question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    with pytest.raises(AttestationMismatch):
        record_reference_attestation(
            database,
            principal=SERVICE_PRINCIPAL,
            question_version_id=version.question_version_id,
            content_digest="f" * 64,
            verifier="MAIN/scripts/verify_question_bank.py",
            cases_total=2,
            cases_passed=2,
            verified_at=None,
            notes=None,
            now=PUBLISH_AT,
        )
    with pytest.raises(MissingReferenceAttestation):
        publish(
            database,
            principal=teacher_principal(),
            question_version_id=version.question_version_id,
            attestation=False,
        )


def test_editing_content_invalidates_the_stored_attestation(database, settings):
    """No override flag exists: the only way past the gate is to re-verify."""
    from autograder_b3.domain.questions import update_question
    from autograder_b3.errors import MissingReferenceAttestation

    question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    attest(database, question_version_id=version.question_version_id)
    verified_digest = version.content_digest

    update_question(
        database,
        settings,
        principal=teacher_principal(),
        question_id=question.question_id,
        payload=draft(draft_payload(stem="Edited after verification.")),
        now=PUBLISH_AT,
    )

    with database.protected_session() as session:
        from autograder_b3.infra.models import QuestionVersion, ReferenceAttestation

        refreshed = session.get(QuestionVersion, version.question_version_id)
        assert refreshed is not None
        assert refreshed.content_digest != verified_digest
        # The old voucher still exists - it simply no longer matches any content.
        stored = {
            row.content_digest
            for row in session.query(ReferenceAttestation)
            .filter(ReferenceAttestation.question_version_id == version.question_version_id)
            .all()
        }
        assert stored == {verified_digest}

    with pytest.raises(MissingReferenceAttestation):
        publish(
            database,
            principal=teacher_principal(),
            question_version_id=version.question_version_id,
            attestation=False,
        )


def test_revision_diff_records_what_changed_and_never_contains_protected_content(
    database, settings
):
    from autograder_b3.domain.questions import update_question

    question, version_one = create_published_question(
        database, settings, payload=draft_payload()
    )
    changed_cases = draft_payload()["cases"]
    changed_cases[1]["weight"] = 5
    _question_row, version_two = update_question(
        database,
        settings,
        principal=teacher_principal(),
        question_id=question.question_id,
        payload=draft(draft_payload(stem="Version two stem.", cases=changed_cases)),
        as_new_version=True,
        now=PUBLISH_AT,
    )

    with database.protected_session() as session:
        from autograder_b3.infra.models import QuestionVersion

        row = session.get(QuestionVersion, version_two.question_version_id)
        delta = row.revision_diff
        note = row.revision_note
        serialized = json.dumps(delta, ensure_ascii=False, sort_keys=True)

    # The stored key set is exactly the contract's ``RevisionDiff`` (CHG-0013), asserted
    # against the contract file so a field added here without a declaration fails - and,
    # symmetrically, so a declared field the delta forgets to fill cannot pass unnoticed.
    # The module constant is checked against the same source, so it cannot drift either.
    assert declared_revision_diff_properties() == REVISION_DIFF_KEYS
    assert set(delta) == declared_revision_diff_properties()
    assert delta["stem_length_before"] != delta["stem_length_after"]
    assert delta["case_count_before"] == delta["case_count_after"] == 2
    assert delta["weights_changed"] is True
    assert delta["case_set_changed"] is False
    assert delta["adapter_kind_changed"] is False
    assert delta["fixtures_changed"] is False
    assert delta["size_limits_changed"] is False
    # Digests only; the reference answer is unchanged by this edit and the delta says so.
    assert delta["reference_answer_digest_before"] == delta["reference_answer_digest_after"]
    assert len(delta["reference_answer_digest_after"]) == 64
    assert note is not None and str(version_one.question_version_id)[:8] in note

    # The delta is structural: it must not be a paraphrase of the content. The hidden
    # case name is the sharpest probe - it is text that exists nowhere else.
    assert "Version two stem." not in serialized
    assert "hidden edge case: empty input" not in serialized
    assert "counts lines" not in serialized
    # Nor may the protected vocabulary appear as a key: a public DTO key named
    # `reference_answer` would be refused by the serializer, so the digest carries the
    # digest and the word appears only inside a longer, non-vocabulary name.
    assert not set(delta) & PROTECTED_FIELD_NAMES
    for name in PROTECTED_FIELD_NAMES:
        assert f'"{name}"' not in serialized, name


def test_revision_diff_on_the_first_version_is_not_comparable_rather_than_unchanged(
    database, settings
):
    """CHG-0013: ``revision_diff`` is ``null`` for a first version.

    The stored delta for a never-branched draft is null, and the *diff of a first
    version* (which the diff function can still compute) reports ``null`` for every
    before-value and every flag. ``false`` would claim "compared and equal", which is a
    statement nobody can make about a version that supersedes nothing.
    """
    from autograder_b3.domain.revisions import compute_revision_diff, snapshot_content
    from autograder_b3.infra.repositories import QuestionBankRepository

    _question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    stem = version.stem_markdown
    with database.protected_session() as session:
        stored = QuestionBankRepository(session).get_version(version.question_version_id)
        assert stored is not None
        assert stored.revision_diff is None
        assert stored.revision_note is None

        content = load_version_content(session, stored)
        first = compute_revision_diff(
            None,
            snapshot_content(
                language=stored.language,
                manifest=content.manifest,
                cases=content.cases,
                fixtures=content.fixtures,
                reference_answers=content.snapshot.reference_answers,
                stem_markdown=stored.stem_markdown,
                max_files=stored.max_files,
                max_total_bytes=stored.max_total_bytes,
            ),
        )

    assert set(first) == declared_revision_diff_properties()
    # Every *comparison* is null: there is no "before" and no flag. The `_after` values
    # still describe the version that exists - a null `case_count_after` would say the
    # version has no cases, which is a different (and false) claim.
    assert all(first[key] is None for key in first if key.endswith("_before"))
    assert all(first[key] is None for key in first if key.endswith("_changed"))
    assert first["case_count_after"] == 2
    assert first["stem_length_after"] == len(stem)
    assert first["reference_answer_digest_after"] is not None


def test_a_revision_note_can_only_be_recorded_by_superseding_a_version(database, settings):
    """The write path refuses a note it would otherwise have to drop.

    ``revision_note`` explains why a *new version* exists. An in-place draft edit creates
    no version, so accepting the field and ignoring it would report success while
    recording nothing - the quiet behaviour ``CHG-0013`` exists to prevent.
    """
    from autograder_b3.domain.questions import update_question

    question, version = create_published_question(database, settings, payload=draft_payload())
    note = "the deadline example was wrong"
    with pytest.raises(ImmutableVersionConflict):
        # No `as_new_version` and no note: the edit itself is what is refused, which is
        # where the caller's real problem is. (A note *with* no flag is a different
        # refusal - see below - so the two cases are kept apart on purpose.)
        update_question(
            database,
            settings,
            principal=teacher_principal(),
            question_id=question.question_id,
            payload=draft(draft_payload(stem="changed")),
            now=PUBLISH_AT,
        )

    draft_question, _draft_version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    with pytest.raises(RevisionNoteMisuse):
        update_question(
            database,
            settings,
            principal=teacher_principal(),
            question_id=draft_question.question_id,
            payload=draft(draft_payload() | {"revision_note": note}),
            revision_note_supplied=True,
            now=PUBLISH_AT,
        )

    # The author's own reason wins over the derived one when a version is superseded.
    _row, version_two = update_question(
        database,
        settings,
        principal=teacher_principal(),
        question_id=question.question_id,
        payload=draft(draft_payload() | {"revision_note": note}),
        as_new_version=True,
        revision_note_supplied=True,
        now=PUBLISH_AT,
    )
    assert version_two.revision_note == note


def test_a_draft_that_never_branched_has_no_revision_record(database, settings):
    from autograder_b3.infra.repositories import QuestionBankRepository

    question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    with database.protected_session() as session:
        row = QuestionBankRepository(session).get_version(version.question_version_id)
        assert row is not None
        assert row.revision_diff is None
        assert row.revision_note is None


def test_stdin_example_manifest_is_the_documented_one():
    assert STDIN_MANIFEST["kind"] == "stdin"
    assert isinstance(STDIN_MANIFEST["args"], list)


# --- CHG-0017: omission preserves the protected draft fields --------------
#
# The contract has always declared `cases` and `reference_answer` as optional on
# `QuestionDraftInput`, but the implementation made `cases` required, so *every* edit -
# including a title-only one - had to carry the hidden cases and the reference answer out
# of the bank and back. CHG-0017 fixed the direction (the implementation moved to the
# contract) and stated the semantics: omitted means "keep what is stored", supplied means
# "replace the whole set", and there is deliberately no way to clear a case set.
#
# These tests read the protected store directly, because "the cases are unchanged" is a
# statement about stored rows and the public DTOs deliberately cannot show them.


def _draft_without(key: str, **overrides) -> dict:
    """A valid draft body with one key *absent* (not null, not empty).

    Built by removing the key rather than by passing a sentinel, so the body the domain
    receives is byte-for-byte the shape an HTTP caller sends when it omits the field.
    """
    payload = draft_payload(**overrides)
    del payload[key]
    return payload


def _stored_cases(database, question_version_id) -> list[dict]:
    """Every stored field of every stored case, in ordinal order.

    Field-by-field on purpose: "still two cases" also holds for a rewrite that renumbered
    them, reset their weights or re-hid a case, so the comparison has to be the whole row
    - including the owner-generated ``case_id``, which only survives if nothing was
    rewritten.
    """
    from autograder_b3.infra.repositories import ProtectedContentRepository

    with database.protected_session() as session:
        cases = ProtectedContentRepository(session).list_cases(question_version_id)
        return [
            {
                "case_id": str(case.case_id),
                "name": case.name,
                "ordinal": case.ordinal,
                "weight": case.weight,
                "visible_to_student": case.visible_to_student,
                "stdin": case.stdin,
                "args": list(case.args),
                "expected_stdout": case.expected_stdout,
                "expected_stderr": case.expected_stderr,
                "expected_exit_code": case.expected_exit_code,
            }
            for case in cases
        ]


def _stored_answers(database, question_version_id) -> list[dict]:
    """Stored reference answers including their identifiers.

    The identifier is what turns "the answer still says the same thing" into "the answer
    row was not rewritten at all": ``upsert_reference_answer`` would replace the body and
    keep the id, so an unchanged id plus unchanged body is the strongest available reading.
    """
    from autograder_b3.infra.repositories import ProtectedContentRepository

    with database.protected_session() as session:
        answers = ProtectedContentRepository(session).list_reference_answers(
            question_version_id
        )
        return [
            {
                "reference_answer_id": str(answer.reference_answer_id),
                "language": answer.language,
                "body": answer.body,
            }
            for answer in answers
        ]


def _stored_digest(database, question_version_id) -> str | None:
    from autograder_b3.infra.repositories import QuestionBankRepository

    with database.protected_session() as session:
        row = QuestionBankRepository(session).get_version(question_version_id)
        assert row is not None
        return row.content_digest


def _content_of(cases: list[dict]) -> list[dict]:
    """The stored cases without their row identifiers.

    Used where two *versions* are compared: a case set can only be re-issued onto a new
    version as new rows, so the identifiers must differ there while everything a student or
    a grader would see stays the same. Within one version the identifiers are part of the
    comparison instead - that is what proves nothing was rewritten.
    """
    return [
        {key: value for key, value in case.items() if key != "case_id"} for case in cases
    ]


def test_omitting_cases_keeps_the_stored_case_set_unchanged(database, settings):
    """CHG-0017 §5.1 and §5.2: an omitted set is preserved, and nothing is rewritten.

    Asserted on an **in-place draft edit** because that keeps ``question_version_id`` and
    ``version`` - both of which are part of the content digest - so the digest is also
    comparable: an untouched digest proves the whole protected half is untouched, not just
    the rows this test happens to name.
    """
    from autograder_b3.domain.questions import update_question

    question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    before_cases = _stored_cases(database, version.question_version_id)
    before_answers = _stored_answers(database, version.question_version_id)
    before_digest = _stored_digest(database, version.question_version_id)
    assert len(before_cases) == 2 and before_digest is not None

    edited_question, edited_version = update_question(
        database,
        settings,
        principal=teacher_principal(),
        question_id=question.question_id,
        payload=draft(_draft_without("cases", title="Renamed only")),
        cases_supplied=False,
        now=PUBLISH_AT,
    )

    assert edited_version.question_version_id == version.question_version_id
    assert edited_question.title == "Renamed only"
    # Identifiers included: an in-place omission writes no case row at all.
    assert _stored_cases(database, version.question_version_id) == before_cases
    assert _stored_answers(database, version.question_version_id) == before_answers
    assert _stored_digest(database, version.question_version_id) == before_digest


def test_supplying_cases_replaces_the_whole_set_rather_than_adding_to_it(database, settings):
    """CHG-0017 §5.3: a supplied set replaces the stored one."""
    from autograder_b3.domain.questions import update_question

    _question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    before = _stored_cases(database, version.question_version_id)
    replacement = [
        {
            "name": "the only case",
            "ordinal": 0,
            "weight": 3,
            "visible_to_student": True,
            "stdin": "replaced\n",
            "args": [],
            "expected_stdout": "1\n",
            "expected_stderr": None,
            "expected_exit_code": 0,
        }
    ]

    _question_row, edited_version = update_question(
        database,
        settings,
        principal=teacher_principal(),
        question_id=_question.question_id,
        payload=draft(draft_payload(cases=replacement)),
        cases_supplied=True,
        now=PUBLISH_AT,
    )

    after = _stored_cases(database, edited_version.question_version_id)
    assert len(after) == 1
    assert [case["name"] for case in after] == ["the only case"]
    assert after[0]["weight"] == 3 and after[0]["visible_to_student"] is True
    assert after[0]["stdin"] == "replaced\n"
    # Replaced, not appended: none of the previous cases survives, by id or by name.
    assert {case["case_id"] for case in before} & {case["case_id"] for case in after} == set()
    assert {case["name"] for case in before} & {case["name"] for case in after} == set()


def test_a_title_only_edit_leaves_the_reference_answer_row_untouched(database, settings):
    """CHG-0017 §5.5, the case that motivated the change.

    A caller correcting a title must not have to read the reference answer out of the bank
    and send it back. Proving it by "the response was 200" would be proving nothing, so the
    stored answer row (identifier and body) is compared instead: an upsert would have shown
    up here. Both protected fields are absent from the submitted body, which is the shape
    B1's authoring form can produce only once the implementation honours omission.
    """
    from autograder_b3.domain.questions import update_question

    question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    before_answers = _stored_answers(database, version.question_version_id)
    before_cases = _stored_cases(database, version.question_version_id)
    assert before_answers and before_answers[0]["body"].strip()

    body = _draft_without("cases", title="Better title", reference_answer=None)
    assert "cases" not in body and "reference_answer" not in body
    edited_question, edited_version = update_question(
        database,
        settings,
        principal=teacher_principal(),
        question_id=question.question_id,
        payload=draft(body | {"tags": ["unix"]}),
        cases_supplied=False,
        now=PUBLISH_AT,
    )

    assert edited_question.title == "Better title"
    assert edited_question.tags == ["unix"]
    assert _stored_answers(database, edited_version.question_version_id) == before_answers
    assert _stored_cases(database, edited_version.question_version_id) == before_cases

    # A blank answer is not a way to remove it either (CHG-0017 §3): the stored row is
    # untouched, exactly as if the field had been omitted.
    blank_question, blank_version = update_question(
        database,
        settings,
        principal=teacher_principal(),
        question_id=question.question_id,
        payload=draft(_draft_without("cases", reference_answer="")),
        cases_supplied=False,
        now=PUBLISH_AT,
    )
    assert blank_question.title == "Line counter"
    assert _stored_answers(database, blank_version.question_version_id) == before_answers
    assert _stored_cases(database, blank_version.question_version_id) == before_cases


def test_a_branched_version_that_omits_cases_records_no_case_change(database, settings):
    """The revision delta must describe the row it belongs to, not the omission.

    Branching a new version from a published one reads the previous cases on the protected
    role and reports "the case set did not change" - because that is what the new version
    actually holds. Reporting the omitted field as "zero cases" would store a delta that
    contradicts the version it describes, and the new version's stored cases are therefore
    compared against the previous version's as well.
    """
    from autograder_b3.domain.questions import update_question
    from autograder_b3.infra.repositories import QuestionBankRepository

    question, version_one = create_published_question(
        database, settings, payload=draft_payload()
    )
    before_cases = _stored_cases(database, version_one.question_version_id)

    _question_row, version_two = update_question(
        database,
        settings,
        principal=teacher_principal(),
        question_id=question.question_id,
        payload=draft(
            _draft_without(
                "cases", title="Superseding the published one", reference_answer=None
            )
        ),
        as_new_version=True,
        cases_supplied=False,
        now=PUBLISH_AT,
    )

    with database.protected_session() as session:
        row = QuestionBankRepository(session).get_version(version_two.question_version_id)
        assert row is not None and row.revision_diff is not None
        delta = row.revision_diff

    assert delta["case_count_before"] == delta["case_count_after"] == 2
    assert delta["case_set_changed"] is False
    assert delta["weights_changed"] is False
    assert delta["adapter_kind_changed"] is False
    assert delta["size_limits_changed"] is False
    # The reference answer was omitted too, and the delta says so rather than claiming a
    # change nobody made.
    assert delta["reference_answer_digest_before"] == delta["reference_answer_digest_after"]

    # The new version really carries those cases: same content, re-issued as its own rows
    # (the previous version keeps its own, frozen, so the identifiers must differ).
    after_cases = _stored_cases(database, version_two.question_version_id)
    assert _content_of(after_cases) == _content_of(before_cases)
    assert {case["case_id"] for case in after_cases} & {
        case["case_id"] for case in before_cases
    } == set()
    # And the reference answer came along, which is what makes the branched draft
    # publishable without the caller ever sending it.
    assert _stored_answers(database, version_two.question_version_id)
    assert _stored_digest(database, version_two.question_version_id) is not None


def test_a_version_created_without_cases_cannot_be_published(database, settings):
    """The one way to reach a case-less version, and the gate that keeps it away.

    Omitting ``cases`` on a *first* write preserves nothing, so it produces a draft with no
    cases: a legitimate starting state, and one that must never become publishable, because
    a version with no cases cannot be evaluated. It also stays digest-less, so it cannot be
    attested either.
    """
    question, version = create_question(
        database,
        settings,
        principal=teacher_principal(),
        payload=draft(_draft_without("cases")),
        cases_supplied=False,
    )
    assert version.question_version_id is not None
    assert _stored_cases(database, version.question_version_id) == []
    assert _stored_digest(database, version.question_version_id) is None
    # `attestation=False` on purpose: the refusal has to come from the publish gate
    # itself, not from the test helper that records an attestation before publishing.
    with pytest.raises(ProtectedContentMissing):
        publish(
            database,
            principal=teacher_principal(),
            question_version_id=version.question_version_id,
            attestation=False,
        )
    # The question itself exists and is a draft; nothing was half-written.
    with database.protected_session() as session:
        from autograder_b3.infra.repositories import QuestionBankRepository

        row = QuestionBankRepository(session).get_question(question.question_id)
        assert row is not None and row.status == "draft"


def test_declaring_cases_omitted_while_sending_them_is_refused(database, settings):
    """The internal contradiction is refused, not resolved.

    Neither resolution is acceptable for protected content: honouring ``supplied=False``
    would silently drop cases the caller did send, and honouring the payload would make
    "supplied" meaningless. Unreachable over HTTP (the model rejects ``[]`` and ``null``),
    so reaching it is a programming error and is reported as one.
    """
    from autograder_b3.domain.questions import update_question
    from autograder_b3.errors import ManifestInvalid

    question, version = create_question(
        database, settings, principal=teacher_principal(), payload=draft_payload()
    )
    before_cases = _stored_cases(database, version.question_version_id)

    with pytest.raises(ManifestInvalid):
        update_question(
            database,
            settings,
            principal=teacher_principal(),
            question_id=question.question_id,
            payload=draft(draft_payload()),
            cases_supplied=False,
            now=PUBLISH_AT,
        )
    with pytest.raises(ManifestInvalid):
        update_question(
            database,
            settings,
            principal=teacher_principal(),
            question_id=question.question_id,
            payload=draft(_draft_without("cases")),
            cases_supplied=True,
            now=PUBLISH_AT,
        )
    assert _stored_cases(database, version.question_version_id) == before_cases
