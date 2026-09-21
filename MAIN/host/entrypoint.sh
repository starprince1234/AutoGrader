#!/usr/bin/env bash
# AutoGrader composition host -- container first-boot sequence.
#
# A from-scratch deployment is not "start uvicorn". The bootstrap `init.sql` creates the
# schemas, the least-privilege roles and schema-level USAGE only; table-level grants come
# from each module's migration, B4's tables are granted by
# `python -m autograder_b4.ops.provision`, and B4 signs access tokens with an Ed25519 key
# that must exist before the process starts (a missing key is a hard startup failure by
# design -- see `autograder_b4.plugin`). The order below is therefore load-bearing:
#
#   1. wait for PostgreSQL   -- a failed first start must name the reason, not race it
#   2. signing key + JWKS    -- B4 signs with the key, B2 verifies against the JWKS
#   3. alembic upgrade head  -- b2, b3, b4 independently; the roles have CONNECT/USAGE
#   4. ops.provision         -- B4's table grants, via B4's own operator command
#   5. host                  -- the composition host itself
#
# Every credential comes from the environment. Nothing here has a fallback value: a missing
# variable is a hard error, because "the env var happens to be unset so we used something
# else" is exactly the class of silent degradation this project forbids.
#
# Step 3 runs as the platform superuser. That DSN (`PLATFORM_DATABASE_URL`) is the
# *migration* identity and is never handed to a module: the host's settings view is built
# from the `<MODULE>_`-prefixed variables only (ADR-0008), so B2/B3/B4 each connect as
# their own least-privilege role and B4's over-privilege guard stays satisfied.

set -euo pipefail

log() { printf '[entrypoint] %s\n' "$*"; }
fail() { printf '[entrypoint] ERROR: %s\n' "$*" >&2; exit 1; }

require_env() {
    local name
    for name in "$@"; do
        [ -n "${!name:-}" ] || fail "$name is required and must not be empty"
    done
}

require_env PLATFORM_DATABASE_URL \
            B2_DATABASE_URL B3_DATABASE_URL B3_DATABASE_PROTECTED_URL B4_DATABASE_URL \
            B4_ADMIN_DATABASE_URL B4_DB_PASSWORD B4_MODULE_ROOT JWT_PRIVATE_KEY_FILE \
            B2_JWKS_PATH B2_ARTIFACT_ROOT B2_WORKSPACE_ROOT

[ -d "${B4_MODULE_ROOT}/MAIN/contracts" ] || fail \
    "B4_MODULE_ROOT=${B4_MODULE_ROOT} does not contain MAIN/contracts"
[ -d "${B4_MODULE_ROOT}/B4" ] || fail \
    "B4_MODULE_ROOT=${B4_MODULE_ROOT} does not contain the B4 module root"

wait_for_postgres() {
    log "waiting for PostgreSQL (timeout ${POSTGRES_WAIT_SECONDS}s)"
    python /app/MAIN/host/wait_for_postgres.py \
        --dsn "${PLATFORM_DATABASE_URL}" --timeout "${POSTGRES_WAIT_SECONDS}"
}

ensure_signing_key() {
    mkdir -p "$(dirname "${JWT_PRIVATE_KEY_FILE}")"
    mkdir -p "$(dirname "${B2_JWKS_PATH}")"
    if [ -s "${JWT_PRIVATE_KEY_FILE}" ]; then
        log "signing key already present at ${JWT_PRIVATE_KEY_FILE}; not regenerating"
    else
        # B4's own operator command. It refuses to overwrite an existing key, so the
        # "already present" branch above is a decision, not a convenience.
        log "generating the B4 Ed25519 signing key"
        python -m autograder_b4.ops.generate_key
    fi
    # B2 verifies tokens against the *public* half and takes it from a file, so the JWKS is
    # derived from the same key object B4 signs with rather than re-implemented.
    python /app/MAIN/host/write_jwks.py "${JWT_PRIVATE_KEY_FILE}" "${B2_JWKS_PATH}"
    log "JWKS written to ${B2_JWKS_PATH}"
}

run_migrations() {
    local module="$1" url="$2"
    log "migrating ${module}"
    # The module's own alembic.ini and env.py; DATABASE_URL selects the connection. The
    # URL only ever reaches alembic's process environment, never a log line.
    ( cd "${B4_MODULE_ROOT}/${module}" && DATABASE_URL="${url}" alembic upgrade head )
}

provision_b4_role() {
    # B4 owns the table grants for its own schema, and this is the only B4 entry point that
    # opens a privileged connection. It is idempotent and re-verifies the role afterwards.
    log "provisioning the B4 least-privilege role"
    python -m autograder_b4.ops.provision
}

start_host() {
    log "starting the composition host on 0.0.0.0:8000"
    exec uvicorn autograder_host.main:app --host 0.0.0.0 --port 8000 --workers 1
}

main() {
    : "${POSTGRES_WAIT_SECONDS:=120}"
    wait_for_postgres
    ensure_signing_key
    # b2 and b4 first: `init.sql` grants DML on tables that do not exist yet and relies on
    # the bootstrap role's ALTER DEFAULT PRIVILEGES to cover tables created later. Running
    # both as that same role keeps the default-privilege chain valid. b3 grants its own
    # table privileges inside its migration.
    run_migrations B2 "${PLATFORM_DATABASE_URL}"
    run_migrations B4 "${PLATFORM_DATABASE_URL}"
    run_migrations B3 "${PLATFORM_DATABASE_URL}"
    provision_b4_role
    start_host
}

main "$@"
