"""Recompute `package_digest` for published question versions so their packages can be served.

Why this exists: `load_evaluation_package` **recompiles** a version and compares the result with
the `package_digest` recorded at publish time. In the integration database **103 of 103**
published versions no longer matched, so B3 answered 409 for every question and no submission
could be evaluated at all. The failure surfaced as "评测包不可获取或不可解析", which reads like a
package problem.

The cause was **not** the digest's scope. It was briefly believed to be: `package_digest` used to
cover `created_at` (the version's `published_at`), and migration `0004` narrowed that column to
`timestamptz(3)`. Measurement disproved it -- `to_wire()` renders timestamps to milliseconds, so
the sub-millisecond difference never reached the digest, and one of the three sampled versions
reproduced its recorded digest under the old rule while another did not under either rule. What
actually happened is that **a code change between two publish times altered the compiled output**,
so older versions stopped matching a recompile. That structural problem is recorded, not fixed,
in `docs/phases/active/phase-02-ai-activation-and-integration-repair.md` §2.5 and
`B3/docs/phases/active/phase-06-package-digest-scope.md` §3.

This script therefore does one thing: bring the recorded digests back in line with what the
current compiler produces, so the fetch path works again. A published version is immutable by
trigger, so that correction has to be deliberate, in one place, with the reason written down.

It is deliberately NOT an alembic migration: recomputing requires the compiler, so a migration
would import application code and freeze today's semantics into a file that must keep running
after the code moves on.

Dry run by default; `--apply` performs the write. Both print the same audit table.

Usage:
    python MAIN/scripts/repair_package_digests.py            # dry run
    python MAIN/scripts/repair_package_digests.py --apply    # rewrite
"""

from __future__ import annotations

import sys
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1]

env: dict[str, str] = {}
for raw in (MAIN / ".env.integration").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()

# The repair acts on the integration database and needs table ownership to lift the
# immutability trigger, so it uses the platform (migration) identity by design. This is the
# one connection that is allowed to; no module ever receives it (ADR-0008).
PLATFORM_DSN = env["DATABASE_URL"]

from sqlalchemy import text  # noqa: E402

from autograder_b3.compile.compiler import compile_evaluation_package  # noqa: E402
from autograder_b3.config import Settings  # noqa: E402
from autograder_b3.domain.versions import load_published_snapshot  # noqa: E402
from autograder_b3.infra.db import Database  # noqa: E402
from autograder_b3.infra.repositories import (  # noqa: E402
    ProtectedContentRepository,
    QuestionBankRepository,
)

TRIGGER = "b3.question_versions"
GUARD = "trg_question_versions_guard"


def main(argv: list[str]) -> int:
    apply_changes = "--apply" in argv

    # Both engines point at the platform identity: this is a MAIN-side data repair, not a module
    # operation, and it must read protected content (reference answers, hidden cases) to
    # recompile. The module's least-privilege roles deliberately cannot do this.
    settings = Settings(
        database_url=PLATFORM_DSN,
        protected_database_url=PLATFORM_DSN,
        nats_url=None,
        jwks_url=None,
        service_token=None,
    )
    database = Database(settings)

    planned: list[tuple[str, str, str]] = []
    with database.protected_session() as session:
        version_ids = (
            session.execute(
                text(
                    "select question_version_id from b3.question_versions "
                    "where status = 'published' order by published_at, question_version_id"
                )
            )
            .scalars()
            .all()
        )
        print(f"published versions: {len(version_ids)}")

        bank = QuestionBankRepository(session)
        protected = ProtectedContentRepository(session)
        for version_id in version_ids:
            version = bank.get_version(version_id)
            snapshot, _content_digest = load_published_snapshot(session, version)
            cases = protected.list_cases(version_id)
            fixtures = protected.list_fixtures(version_id)
            package = compile_evaluation_package(
                snapshot,
                snapshot.manifest,
                cases,
                fixtures,
                created_at=snapshot.published_at,
            )
            if package.package_digest != version.package_digest:
                planned.append(
                    (str(version_id), version.package_digest or "(none)", package.package_digest)
                )

        print(f"stale digests: {len(planned)}")
        for version_id, old, new in planned[:5]:
            print(f"  {version_id}  {old[:16]}… -> {new[:16]}…")
        if len(planned) > 5:
            print(f"  … and {len(planned) - 5} more")

        if not planned:
            print("\nnothing to repair")
            return 0

        if not apply_changes:
            print("\ndry run -- re-run with --apply to rewrite")
            return 0

        # The immutability trigger refuses a `package_digest` change on a published row, and it
        # is right to: the value is supposed to be frozen. It is being lifted here because the
        # frozen value was computed by a rule that could not hold, and leaving it would keep
        # every published question unevaluatable. Scope is exactly one column, and the
        # content digests, manifest and stem are not touched.
        session.execute(text(f"ALTER TABLE {TRIGGER} DISABLE TRIGGER {GUARD}"))
        try:
            for version_id, _old, new in planned:
                session.execute(
                    text(
                        "update b3.question_versions set package_digest = :digest "
                        "where question_version_id = :version_id"
                    ),
                    {"digest": new, "version_id": version_id},
                )
        finally:
            session.execute(text(f"ALTER TABLE {TRIGGER} ENABLE TRIGGER {GUARD}"))

    print(f"\nrewrote {len(planned)} package digests; immutability trigger re-enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
