#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '[postgres-init] ERROR: %s\n' "$*" >&2
    exit 1
}

require_safe_secret() {
    local name="$1"
    local value="${!name:-}"
    [[ "$value" =~ ^[A-Za-z0-9_-]+$ ]] || fail "$name is missing or has unsupported characters"
}

require_safe_secret AUTOGRADER_B2_DB_PASSWORD
require_safe_secret AUTOGRADER_B3_APP_DB_PASSWORD
require_safe_secret AUTOGRADER_B3_INTERNAL_DB_PASSWORD
require_safe_secret AUTOGRADER_B4_DB_PASSWORD

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<SQL
\set b2_password $AUTOGRADER_B2_DB_PASSWORD
\set b3_app_password $AUTOGRADER_B3_APP_DB_PASSWORD
\set b3_internal_password $AUTOGRADER_B3_INTERNAL_DB_PASSWORD
\set b4_password $AUTOGRADER_B4_DB_PASSWORD
\i /docker-entrypoint-initdb.d/.autograder-init.sql
SQL
