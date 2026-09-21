-- B4 module-local PostgreSQL bootstrap (test / development instance).
--
-- This instance exists because B4's suite truncates every `b4.*` table before each test
-- (`tests/conftest.py::clean_database`) and drops and re-migrates the schema for the
-- "migrate from zero" gate. Running that against the shared platform database on 55432
-- deletes the integration fixtures the composition host and the browser E2E run against,
-- and the loss is indistinguishable from "the seed never ran". Same reasoning, and the
-- same split, as B3's module-local database on 55434.
--
-- Division of labour (mirrors MAIN/infra/postgres/init.sql so the two do not drift):
--   * instance bootstrap (this file): the `autograder_b4` login role, schema-level USAGE;
--   * table-level grants: `python -m autograder_b4.ops.provision`, which runs after the
--     migration so the privilege set stays reviewable next to the DDL it applies to.
--
-- `b2` and `b3` are created **empty and ungranted** on purpose: B4's cross-schema
-- isolation tests assert `permission denied for schema b2/b3`, and that assertion is only
-- meaningful when the schema actually exists -- otherwise the test would pass on a
-- "schema does not exist" error and prove nothing.
--
-- Local-only credentials. Never reuse these values outside a development machine.

CREATE SCHEMA IF NOT EXISTS b2;
CREATE SCHEMA IF NOT EXISTS b3;
CREATE SCHEMA IF NOT EXISTS b4;

DO $$
DECLARE
    spec RECORD;
BEGIN
    FOR spec IN
        SELECT * FROM (VALUES ('autograder_b4', 'change-me')) AS t(role_name, role_password)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = spec.role_name) THEN
            EXECUTE format(
                'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE '
                'NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10',
                spec.role_name, spec.role_password
            );
        ELSE
            EXECUTE format(
                'ALTER ROLE %I NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
                'NOBYPASSRLS CONNECTION LIMIT 10',
                spec.role_name
            );
        END IF;
    END LOOP;
END
$$;

-- No module may create objects in `public`, and `public` must not be a shared surface.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Only B4's own schema is granted. b2/b3 stay ungranted so the denial is structural.
GRANT USAGE ON SCHEMA b4 TO autograder_b4;
