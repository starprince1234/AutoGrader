-- AutoGrader platform PostgreSQL bootstrap (instance level).
--
-- Runs once as the container superuser on first initialization of the data volume.
-- Responsibilities:
--   1. create the three module schemas;
--   2. create every least-privilege login role the modules connect with;
--   3. grant each role access to **its own** schema only.
--
-- Why this file owns role creation: ADR-0001 makes "cross-schema access is denied by
-- the database, not by convention" a structural guarantee, and every module refuses to
-- boot on a superuser connection (G-SEC-07). A role that does not exist is therefore a
-- hard start-up blocker, not a detail -- this bootstrap previously created the schemas
-- only, and no module could connect to the platform database.
--
-- Division of labour, so this file does not grow into a second source of truth:
--   * instance bootstrap (this file): roles, schemas, schema-level USAGE;
--   * table-level grants: the owning module's migration, so the privilege set stays
--     reviewable next to the DDL it applies to (B3 does exactly this);
--   * post-migration table grants for modules whose migrations do not issue them:
--     `python -m autograder_b4.ops.provision` for b4; B2 uses the default privileges
--     declared below.
--
-- The application must never connect as `autograder_platform` (superuser) or as a
-- schema owner; those DSNs are for migrations and operations only.
--
-- Passwords are supplied by initialize-db.sh through psql variables. They never live in
-- this SQL file, and each runtime role receives its own secret in production.

CREATE SCHEMA IF NOT EXISTS b2;
CREATE SCHEMA IF NOT EXISTS b3;
CREATE SCHEMA IF NOT EXISTS b4;

-- Roles ---------------------------------------------------------------------------
-- B2: one role. It owns nothing; it is granted DML on the b2 tables after migration.
-- B3: two roles -- the ordinary question-bank role never receives a grant on the
--     protected tables (reference answers / hidden cases / blobs / fixtures), so a
--     public code path physically cannot read them. The internal role is used only by
--     the publish/compile path.
-- B4: one role for identity, courses, roster import and the registry.
-- All of them: NOSUPERUSER, no creation rights beyond their own schema, and a
-- connection limit so a runaway client cannot exhaust the instance's slots.
-- Keep psql's interpolation outside the PL/pgSQL block, and suppress the returned values.
\o /dev/null
SELECT set_config('autograder.b2_password', :'b2_password', false);
SELECT set_config('autograder.b3_app_password', :'b3_app_password', false);
SELECT set_config('autograder.b3_internal_password', :'b3_internal_password', false);
SELECT set_config('autograder.b4_password', :'b4_password', false);
\o

DO $$
DECLARE
    spec RECORD;
BEGIN
    FOR spec IN
        SELECT role_name, current_setting(setting_name) AS role_password
        FROM (VALUES
            ('autograder_b2',          'autograder.b2_password'),
            ('autograder_b3_app',      'autograder.b3_app_password'),
            ('autograder_b3_internal', 'autograder.b3_internal_password'),
            ('autograder_b4',          'autograder.b4_password')
        ) AS t(role_name, setting_name)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = spec.role_name) THEN
            EXECUTE format(
                'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE '
                'NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10',
                spec.role_name, spec.role_password
            );
        ELSE
            EXECUTE format(
                'ALTER ROLE %I PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE '
                'NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10',
                spec.role_name, spec.role_password
            );
        END IF;
    END LOOP;
END
$$;

-- No module may create objects in `public`, and `public` must not be a shared surface.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Per-schema privileges -------------------------------------------------------------
-- Only each module's own schema is granted; the other two are never granted. B3's
-- table-level grants (including REVOKE on the protected tables) are issued by
-- alembic/versions/20260915_0002_b3_question_bank.py.
GRANT USAGE ON SCHEMA b2 TO autograder_b2;
GRANT USAGE ON SCHEMA b3 TO autograder_b3_app;
GRANT USAGE ON SCHEMA b3 TO autograder_b3_internal;
GRANT USAGE ON SCHEMA b4 TO autograder_b4;

-- B2's and B4's migrations do not issue table grants, so their DML privileges are
-- declared here. Default privileges cover tables created by later migrations.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA b2 TO autograder_b2;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA b2 TO autograder_b2;
ALTER DEFAULT PRIVILEGES IN SCHEMA b2
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO autograder_b2;
ALTER DEFAULT PRIVILEGES IN SCHEMA b2 GRANT USAGE, SELECT ON SEQUENCES TO autograder_b2;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA b4 TO autograder_b4;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA b4 TO autograder_b4;
ALTER DEFAULT PRIVILEGES IN SCHEMA b4
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO autograder_b4;
ALTER DEFAULT PRIVILEGES IN SCHEMA b4 GRANT USAGE, SELECT ON SEQUENCES TO autograder_b4;
