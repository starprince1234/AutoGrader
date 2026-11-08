"""Course -> section -> class relationships and assignment notification delivery.

Revision ID: 20261108_0010
Revises: 20261029_0009

Why this migration exists (v0.32.0): the previous model treated one class as one
section, so attaching a registered class created a section of its own and two classes
could never share a 课序. This revision introduces the missing level -- `b4.classes` --
and makes the course enrollment (`b4.enrollments`) the single place that records "this
student is in this class of this section of this course".

Two rules become database facts, not conventions:

* a student has **at most one active enrollment per course** (partial unique index on
  `(course_id, student_number) WHERE is_active`). Importing a roster, attaching a
  registered class, registering an account and editing a roster row all write through
  that index, so a concurrent request cannot produce a second active section;
* a class always belongs to the section an enrollment names (composite foreign keys),
  so class/section/course cannot disagree.

Existing rows are back-filled deterministically: every current section becomes exactly
one class. The class name is taken from the section's most recent committed roster
import, else from the single registration-allowlist batch that covers all of the
section's students, else the section code. If the existing data already violates the
one-section-per-course rule the migration **refuses to run** with an aggregate error:
silently choosing one of the conflicting sections, or dropping rows, would be worse
than stopping the deploy.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from alembic import op

revision = "20261108_0010"
down_revision = "20261029_0009"
branch_labels = None
depends_on = None

SCHEMA = "b4"
TIMESTAMPTZ = "timestamptz(3)"
NOW = "date_trunc('milliseconds', now())"


def _conflicting_students() -> list[tuple[uuid.UUID, int]]:
    """`(course_id, count)` of students actively enrolled in more than one section."""
    rows = op.get_bind().execute(
        text(
            f"""
            SELECT s.course_id, count(*) AS conflicting_students
              FROM {SCHEMA}.enrollments e
              JOIN {SCHEMA}.sections s ON s.section_id = e.section_id
             WHERE e.is_active
             GROUP BY s.course_id, e.student_number
            HAVING count(DISTINCT e.section_id) > 1
            """
        )
    ).fetchall()
    grouped: dict[uuid.UUID, int] = {}
    for course_id, _student_number in rows:
        grouped[course_id] = grouped.get(course_id, 0) + 1
    return sorted(grouped.items(), key=lambda item: str(item[0]))


def upgrade() -> None:
    conflicts = _conflicting_students()
    if conflicts:
        # Aggregate only: no student numbers, no names. The operator resolves the
        # conflict with the query documented in the data-model runbook.
        detail = ", ".join(f"course {course_id}: {count}" for course_id, count in conflicts)
        raise RuntimeError(
            "refusing to migrate: the existing roster violates "
            "'one active section per student per course' "
            f"({detail}). Resolve these rows first; the migration never picks a section."
        )

    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.sections
            ADD CONSTRAINT uq_sections_section_id_course_id UNIQUE (section_id, course_id)
        """
    )
    _create_classes()
    _extend_enrollments()
    _extend_roster_imports()
    _relax_allowlist_uniqueness()
    _create_notifications()
    _grant_application_role()


def _create_classes() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.classes (
            class_id            uuid        NOT NULL,
            section_id          uuid        NOT NULL,
            class_name          text        NOT NULL,
            source              text        NOT NULL,
            allowlist_batch_id  uuid        NULL,
            created_by          uuid        NULL,
            created_at          {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            updated_at          {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_classes PRIMARY KEY (class_id),
            CONSTRAINT uq_classes_class_id_section_id UNIQUE (class_id, section_id),
            CONSTRAINT uq_classes_section_id_class_name UNIQUE (section_id, class_name),
            CONSTRAINT fk_classes_section_id FOREIGN KEY (section_id)
                REFERENCES {SCHEMA}.sections (section_id) ON DELETE CASCADE,
            CONSTRAINT fk_classes_allowlist_batch_id FOREIGN KEY (allowlist_batch_id)
                REFERENCES {SCHEMA}.registration_allowlist_batches (batch_id)
                ON DELETE SET NULL,
            CONSTRAINT fk_classes_created_by FOREIGN KEY (created_by)
                REFERENCES {SCHEMA}.users (user_id) ON DELETE SET NULL,
            CONSTRAINT ck_classes_source CHECK (
                source IN ('registered_class', 'roster_import', 'manual')
            ),
            CONSTRAINT ck_classes_class_name CHECK (
                length(btrim(class_name)) > 0 AND length(class_name) <= 64
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_classes_section_id_allowlist_batch_id
            ON {SCHEMA}.classes (section_id, allowlist_batch_id)
         WHERE allowlist_batch_id IS NOT NULL
        """
    )
    op.execute(f"CREATE INDEX ix_classes_section_id ON {SCHEMA}.classes (section_id)")


def _extend_enrollments() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.enrollments ADD COLUMN course_id uuid NULL")
    op.execute(f"ALTER TABLE {SCHEMA}.enrollments ADD COLUMN class_id uuid NULL")
    _backfill_classes()
    op.execute(
        f"""
        UPDATE {SCHEMA}.enrollments e
           SET class_id = c.class_id,
               course_id = s.course_id
          FROM {SCHEMA}.sections s
          JOIN {SCHEMA}.classes c ON c.section_id = s.section_id
         WHERE s.section_id = e.section_id
        """
    )
    op.execute(f"ALTER TABLE {SCHEMA}.enrollments ALTER COLUMN course_id SET NOT NULL")
    op.execute(f"ALTER TABLE {SCHEMA}.enrollments ALTER COLUMN class_id SET NOT NULL")

    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_enrollments_section_id_student_number")
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_enrollments_course_id_student_number_active
            ON {SCHEMA}.enrollments (course_id, student_number)
         WHERE is_active
        """
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.enrollments
            ADD CONSTRAINT fk_enrollments_class_id_section_id
                FOREIGN KEY (class_id, section_id)
                REFERENCES {SCHEMA}.classes (class_id, section_id) ON DELETE CASCADE,
            ADD CONSTRAINT fk_enrollments_section_id_course_id
                FOREIGN KEY (section_id, course_id)
                REFERENCES {SCHEMA}.sections (section_id, course_id) ON DELETE CASCADE
        """
    )
    op.execute(f"CREATE INDEX ix_enrollments_course_id ON {SCHEMA}.enrollments (course_id)")
    op.execute(f"CREATE INDEX ix_enrollments_class_id ON {SCHEMA}.enrollments (class_id)")


def _backfill_classes() -> None:
    """One deterministic class per existing section.

    Naming order: the section's most recent committed roster import file name, else the
    single registration-allowlist batch that contains every student of the section, else
    the section code. "Single" is checked for real: if two batches cover the same section
    the name is genuinely ambiguous and the section code is used instead.
    """
    connection = op.get_bind()
    sections = connection.execute(
        text(
            f"""
            SELECT section_id, course_id, section_code
              FROM {SCHEMA}.sections
             ORDER BY course_id, section_code, section_id
            """
        )
    ).fetchall()
    if not sections:
        return

    imports = connection.execute(
        text(
            f"""
            SELECT DISTINCT ON (ri.section_id)
                   ri.section_id, ri.filename
              FROM {SCHEMA}.roster_imports ri
             WHERE ri.status = 'committed'
             ORDER BY ri.section_id, ri.created_at DESC, ri.roster_import_id DESC
            """
        )
    ).fetchall()
    import_name = {row[0]: _file_stem(row[1]) for row in imports}

    enrollments = connection.execute(
        text(
            f"""
            SELECT section_id, student_number
              FROM {SCHEMA}.enrollments
             WHERE is_active
            """
        )
    ).fetchall()
    students_by_section: dict[uuid.UUID, set[str]] = {}
    for section_id, student_number in enrollments:
        students_by_section.setdefault(section_id, set()).add(student_number)

    batches = connection.execute(
        text(
            f"""
            SELECT b.batch_id, b.filename, a.student_number
              FROM {SCHEMA}.registration_allowlist_batches b
              JOIN {SCHEMA}.registration_allowlist a ON a.batch_id = b.batch_id
            """
        )
    ).fetchall()
    batch_students: dict[uuid.UUID, tuple[str, set[str]]] = {}
    for batch_id, filename, student_number in batches:
        existing = batch_students.get(batch_id)
        if existing is None:
            batch_students[batch_id] = (filename, {student_number})
        else:
            existing[1].add(student_number)

    for section_id, _course_id, section_code in sections:
        students = students_by_section.get(section_id, set())
        covering = [
            (batch_id, filename)
            for batch_id, (filename, members) in batch_students.items()
            if students and students <= members
        ]
        name = import_name.get(section_id)
        source = "roster_import"
        batch_id: uuid.UUID | None = None
        if name is None and len(covering) == 1:
            batch_id, filename = covering[0]
            name = _file_stem(filename)
            source = "registered_class"
        if name is None:
            name = section_code
            source = "manual"
        connection.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.classes
                    (class_id, section_id, class_name, source, allowlist_batch_id)
                VALUES (:class_id, :section_id, :class_name, :source, :batch_id)
                """
            ),
            {
                "class_id": uuid.uuid4(),
                "section_id": section_id,
                "class_name": name[:64],
                "source": source,
                "batch_id": batch_id,
            },
        )


def _file_stem(filename: str) -> str:
    stem = filename.strip().rsplit("/", 1)[-1]
    for suffix in (".csv", ".CSV"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)].strip() or stem
    return stem


def _extend_roster_imports() -> None:
    """Record which class an upload targeted; the section alone is no longer the target."""
    op.execute(f"ALTER TABLE {SCHEMA}.roster_imports ADD COLUMN class_id uuid NULL")
    # Existing imports were per-section, and at migration time each section has exactly one
    # back-filled class, so the mapping is unambiguous.
    op.execute(
        f"""
        UPDATE {SCHEMA}.roster_imports ri
           SET class_id = c.class_id
          FROM {SCHEMA}.classes c
         WHERE c.section_id = ri.section_id
           AND ri.class_id IS NULL
        """
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.roster_imports
            ADD CONSTRAINT fk_roster_imports_class_id FOREIGN KEY (class_id)
                REFERENCES {SCHEMA}.classes (class_id) ON DELETE SET NULL
        """
    )
    op.execute(
        f"CREATE INDEX ix_roster_imports_class_id ON {SCHEMA}.roster_imports (class_id)"
    )


def _relax_allowlist_uniqueness() -> None:
    """Allow the same student to appear in several imported classes.

    Before v0.32.0 the registration allowlist was globally unique by student number, which
    silently made "this student is in a class of course A and a class of course B" impossible:
    importing the second class skipped that student, so attaching the second class never
    enrolled them. A student belongs to several courses in the real product; the uniqueness
    that matters is within one imported class (batch), and that is what replaces the old
    constraint.
    """
    op.execute(
        f"ALTER TABLE {SCHEMA}.registration_allowlist "
        f"DROP CONSTRAINT IF EXISTS uq_registration_allowlist_student_number"
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_registration_allowlist_batch_id_student_number
            ON {SCHEMA}.registration_allowlist (batch_id, student_number)
        """
    )


def _create_notifications() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.assignment_notifications (
            notification_id  uuid        NOT NULL,
            assignment_id    uuid        NOT NULL,
            course_id        uuid        NOT NULL,
            title            text        NOT NULL,
            due_at           {TIMESTAMPTZ} NULL,
            published_at     {TIMESTAMPTZ} NOT NULL,
            status           text        NOT NULL DEFAULT 'pending',
            recipient_total  integer     NOT NULL DEFAULT 0,
            created_at       {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            updated_at       {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_assignment_notifications PRIMARY KEY (notification_id),
            CONSTRAINT uq_assignment_notifications_assignment_id UNIQUE (assignment_id),
            CONSTRAINT fk_assignment_notifications_course_id FOREIGN KEY (course_id)
                REFERENCES {SCHEMA}.courses (course_id) ON DELETE CASCADE,
            CONSTRAINT ck_assignment_notifications_status CHECK (
                status IN ('pending', 'sending', 'completed', 'partial', 'failed')
            ),
            CONSTRAINT ck_assignment_notifications_title CHECK (length(btrim(title)) > 0),
            CONSTRAINT ck_assignment_notifications_recipients CHECK (recipient_total >= 0)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_assignment_notifications_course_id "
        f"ON {SCHEMA}.assignment_notifications (course_id)"
    )
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.assignment_notification_deliveries (
            delivery_id      uuid        NOT NULL,
            notification_id  uuid        NOT NULL,
            enrollment_id    uuid        NULL,
            student_number   text        NOT NULL,
            display_name     text        NOT NULL,
            section_id       uuid        NOT NULL,
            class_id         uuid        NOT NULL,
            user_id          uuid        NULL,
            status           text        NOT NULL DEFAULT 'pending',
            reason_code      text        NULL,
            reason_detail    text        NULL,
            attempts         integer     NOT NULL DEFAULT 0,
            next_attempt_at  {TIMESTAMPTZ} NULL,
            sent_at          {TIMESTAMPTZ} NULL,
            created_at       {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            updated_at       {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_assignment_notification_deliveries PRIMARY KEY (delivery_id),
            CONSTRAINT fk_assignment_notification_deliveries_notification_id
                FOREIGN KEY (notification_id)
                REFERENCES {SCHEMA}.assignment_notifications (notification_id) ON DELETE CASCADE,
            CONSTRAINT fk_assignment_notification_deliveries_enrollment_id
                FOREIGN KEY (enrollment_id)
                REFERENCES {SCHEMA}.enrollments (enrollment_id) ON DELETE SET NULL,
            CONSTRAINT fk_assignment_notification_deliveries_user_id FOREIGN KEY (user_id)
                REFERENCES {SCHEMA}.users (user_id) ON DELETE SET NULL,
            CONSTRAINT ck_assignment_notification_deliveries_status CHECK (
                status IN ('pending', 'sending', 'sent', 'failed',
                           'skipped_unbound', 'skipped_no_contact', 'skipped_inactive')
            ),
            CONSTRAINT ck_assignment_notification_deliveries_reason_code CHECK (
                reason_code IS NULL
                OR reason_code IN ('smtp_error', 'mail_not_configured',
                                   'no_account', 'no_email', 'inactive_account')
            ),
            CONSTRAINT ck_assignment_notification_deliveries_attempts CHECK (attempts >= 0),
            CONSTRAINT ck_assignment_notification_deliveries_student_number
                CHECK (length(btrim(student_number)) > 0),
            CONSTRAINT ck_assignment_notification_deliveries_display_name
                CHECK (length(btrim(display_name)) > 0)
        )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_assignment_notification_deliveries_notification_id_enrollment_id
            ON {SCHEMA}.assignment_notification_deliveries (notification_id, enrollment_id)
         WHERE enrollment_id IS NOT NULL
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_assignment_notification_deliveries_notification_id_student_number
            ON {SCHEMA}.assignment_notification_deliveries (notification_id, student_number)
         WHERE enrollment_id IS NULL
        """
    )
    op.execute(
        f"""
        CREATE INDEX ix_assignment_notification_deliveries_status_next_attempt_at
            ON {SCHEMA}.assignment_notification_deliveries (status, next_attempt_at)
        """
    )


def _grant_application_role() -> None:
    """Grant on the new tables to the provisioned application role, when it exists."""
    op.execute(
        f"""
        DO $$
        DECLARE
            app_role text := current_setting('b4.application_role', true);
        BEGIN
            IF app_role IS NULL OR app_role = '' THEN
                RETURN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
                RETURN;
            END IF;
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {SCHEMA} TO %I',
                app_role);
        END
        $$;
        """
    )


def downgrade() -> None:
    """Remove the new structures; the back-filled class/section link is not reversed.

    Reversing the data back-fill would have to guess which class a roster row came from,
    which is exactly what this migration refuses to do. Dropping the notifications tables
    and the class table is lossless for the platform's own business data (enrollments keep
    their original columns); the course/section/class relation is simply unavailable after
    a downgrade.
    """
    op.execute(
        f"DROP TABLE IF EXISTS {SCHEMA}.assignment_notification_deliveries"
    )
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.assignment_notifications")
    op.execute(
        f"ALTER TABLE {SCHEMA}.enrollments "
        f"DROP CONSTRAINT IF EXISTS fk_enrollments_class_id_section_id, "
        f"DROP CONSTRAINT IF EXISTS fk_enrollments_section_id_course_id"
    )
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_enrollments_course_id_student_number_active")
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_enrollments_section_id_student_number
            ON {SCHEMA}.enrollments (section_id, student_number)
        """
    )
    op.execute(f"ALTER TABLE {SCHEMA}.roster_imports DROP COLUMN IF EXISTS class_id")
    op.execute(
        f"DROP INDEX IF EXISTS {SCHEMA}.uq_registration_allowlist_batch_id_student_number"
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.registration_allowlist
            ADD CONSTRAINT uq_registration_allowlist_student_number UNIQUE (student_number)
        """
    )
    op.execute(f"ALTER TABLE {SCHEMA}.enrollments DROP COLUMN IF EXISTS class_id")
    op.execute(f"ALTER TABLE {SCHEMA}.enrollments DROP COLUMN IF EXISTS course_id")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.classes")
    op.execute(
        f"ALTER TABLE {SCHEMA}.sections "
        f"DROP CONSTRAINT IF EXISTS uq_sections_section_id_course_id"
    )
