-- B3 local PostgreSQL bootstrap.
--
-- Runs once, as the container superuser, on first initialization.
-- Responsibilities: (1) create the B3-owned schema; (2) create the two
-- least-privilege login roles the application uses.
--
-- Why two roles: acceptance 3-10-3 requires that reference answers and hidden
-- test cases are readable only through an independent privilege. The ordinary
-- question-bank role (autograder_b3_app) never receives a grant on the protected
-- tables; the package compiler path connects as autograder_b3_internal. Table
-- level grants are issued by alembic/versions/20260915_0002_*.py so that the
-- privilege set stays reviewable next to the DDL.
--
-- The application must NOT connect as its owner (autograder_b3) or as a
-- superuser: see docs/architecture/boundaries.md section 3.

CREATE SCHEMA IF NOT EXISTS b3;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_app') THEN
        CREATE ROLE autograder_b3_app LOGIN PASSWORD 'change-me-app-local-only';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_internal') THEN
        CREATE ROLE autograder_b3_internal LOGIN PASSWORD 'change-me-internal-local-only';
    END IF;
END
$$;

-- Neither role may create objects or connect to other modules' schemas.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA b3 TO autograder_b3_app;
GRANT USAGE ON SCHEMA b3 TO autograder_b3_internal;

-- The owner keeps DDL rights for alembic.
ALTER SCHEMA b3 OWNER TO autograder_b3;
