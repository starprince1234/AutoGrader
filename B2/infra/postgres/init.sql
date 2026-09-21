-- B2 module-local PostgreSQL bootstrap (test / development instance).
--
-- Only the fixture role is created here. B2's test suite provisions a throwaway database
-- per test module (`tests/conftest.py::temporary_database`) and runs Alembic against it,
-- so the instance needs a role that may CREATE DATABASE -- that is what
-- `autograder_platform` is for, and it matches the platform instance's superuser name so
-- one DSN shape works in both places.
--
-- The application's least-privilege role (`autograder_b2`) is created by the platform
-- bootstrap (`MAIN/infra/postgres/init.sql`), not here: the throwaway databases are
-- migrated and then dropped within a single test session and no long-lived service ever
-- connects to them.
--
-- Local-only credentials. Never reuse these values outside a development machine.

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
