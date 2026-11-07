"""Protected fixture-byte delivery (``getVersionFixtureContents``, design baseline §5).

The evaluation package declares each fixture by ``{path, content_sha256, mount}`` but
deliberately carries no bytes: a package is fetched for every evaluation, and inlining fixture
bodies would put protected content in flight on a path that does not need it. B2's runner
therefore fetches the ``mount=input`` bytes here, immediately before materialising the
workspace, and verifies each byte stream against the digest the package already declares.

Two properties are enforced here and not by convention:

* **only ``mount=input`` is readable.** An ``expected_output`` comparison needs the digest the
  package already carries, so a read channel for those bytes would widen protected content for
  no benefit.
* **bounded, never truncated.** A single fixture over 1 MiB or a bundle over 4 MiB is refused
  (``413``). Truncating would silently change what the student's code reads, which is worse
  than a stated refusal. The bounds are applied to **metadata** first, so an over-limit bundle
  is refused before its bytes are materialised.

Authorization is the API layer's job (``require_service`` before this function is reached); the
read itself must run on the protected role because ``b3.blobs`` grants it only there.
"""

from __future__ import annotations

import base64
import uuid

from ..compile.canonical import sha256_hex
from ..dto.internal import FixtureContentBundle, FixtureContentItem, FixtureInternal
from ..errors import B3Error, NotFound, PayloadTooLarge, UnprocessableEntity
from ..infra.db import Database
from ..infra.repositories import ProtectedContentRepository, QuestionBankRepository

#: The contract's delivery bounds (``FixtureContentBundle`` description): one item at most
#: 1 MiB, the whole bundle at most 4 MiB. Kept as module constants rather than settings because
#: they are a contract statement about this operation, not a deployment preference.
MAX_FIXTURE_ITEM_BYTES = 1024 * 1024
MAX_FIXTURE_BUNDLE_BYTES = 4 * 1024 * 1024
#: ``FixtureContentBundle.items`` is ``maxItems: 50`` in the contract; a version allows at most
#: 50 fixtures per upload rule, so this only trips on a row written around that rule.
MAX_FIXTURE_ITEMS = 50
#: The only mount this operation serves.
INPUT_MOUNT = "input"


def load_fixture_contents(
    database: Database,
    *,
    question_version_id: uuid.UUID,
    mount: str = INPUT_MOUNT,
) -> FixtureContentBundle:
    """Read one version's input-fixture bytes, bounded and digest-checked.

    Raises ``404`` for an unknown version, ``422`` for a mount this operation does not serve,
    ``413`` when a size or item limit would be exceeded, and a ``500`` when stored bytes
    disagree with the digest they are stored under (a corrupt row must fail the evaluation as
    an infrastructure problem, never be handed to the runner as if it were the fixture).
    """
    if mount != INPUT_MOUNT:
        raise UnprocessableEntity(
            "only mount='input' fixture bytes are delivered; expected_output comparison uses "
            "the digest already declared in the package",
            field="mount",
        )
    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        if bank.get_version(question_version_id) is None:
            raise NotFound("question version not found")
        protected = ProtectedContentRepository(session)
        fixtures = [
            fixture
            for fixture in protected.list_fixtures(question_version_id)
            if fixture.mount == INPUT_MOUNT
        ]
        _assert_bundle_bounds(fixtures)
        items = tuple(_read_item(protected, fixture) for fixture in fixtures)
    return FixtureContentBundle(question_version_id=question_version_id, items=items)


def _assert_bundle_bounds(fixtures: list[FixtureInternal]) -> None:
    """Refuse an over-limit bundle from metadata, before any bytes are read."""
    if len(fixtures) > MAX_FIXTURE_ITEMS:
        raise PayloadTooLarge(
            f"this version's input fixtures exceed the {MAX_FIXTURE_ITEMS}-item delivery limit"
        )
    total_bytes = 0
    for fixture in fixtures:
        if fixture.size_bytes > MAX_FIXTURE_ITEM_BYTES:
            raise PayloadTooLarge(
                f"fixture {fixture.path} exceeds the {MAX_FIXTURE_ITEM_BYTES}-byte item limit; "
                "the bundle is refused rather than truncated"
            )
        total_bytes += fixture.size_bytes
        if total_bytes > MAX_FIXTURE_BUNDLE_BYTES:
            raise PayloadTooLarge(
                "the input fixture bundle exceeds the "
                f"{MAX_FIXTURE_BUNDLE_BYTES}-byte delivery limit; it is refused rather than "
                "truncated"
            )


def _read_item(
    protected: ProtectedContentRepository, fixture: FixtureInternal
) -> FixtureContentItem:
    content = protected.get_blob_content(fixture.content_sha256)
    computed = sha256_hex(content)
    # The stored digest is what the package binds. Bytes that do not hash to it mean the store
    # is inconsistent; handing them to the runner would make a technical fault look like a
    # wrong answer.
    if computed != fixture.content_sha256 or len(content) != fixture.size_bytes:
        raise B3Error(
            f"stored fixture bytes for {fixture.path} do not match their recorded digest"
        )
    return FixtureContentItem(
        path=fixture.path,
        mount=fixture.mount,
        content_sha256=computed,
        size_bytes=len(content),
        content_base64=base64.b64encode(content).decode("ascii"),
    )
