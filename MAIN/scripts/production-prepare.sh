#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '[autograder] ERROR: %s\n' "$*" >&2
    exit 1
}

require_env() {
    local name value
    for name in "$@"; do
        value="$(printenv "$name" 2>/dev/null || true)"
        [ -n "$value" ] || fail "$name is required and must not be empty"
    done
}

require_env PLATFORM_DATABASE_URL B4_ADMIN_DATABASE_URL B4_DB_PASSWORD \
    B2_DATABASE_URL B2_NATS_URL B2_ARTIFACT_ROOT B2_WORKSPACE_ROOT B2_JWKS_PATH \
    B2_B3_BASE_URL B2_B3_AUTHORIZATION B3_DATABASE_URL B3_DATABASE_PROTECTED_URL \
    B3_NATS_URL B3_SERVICE_TOKEN B4_DATABASE_URL B4_NATS_URL B4_JWKS_URL \
    B4_MODULE_ROOT JWT_PRIVATE_KEY_FILE AUTOGRADER_CORS_ORIGINS POSTGRES_WAIT_SECONDS

VENV="$B4_MODULE_ROOT/MAIN/.venv"
PYTHON="$VENV/bin/python"
ALEMBIC="$VENV/bin/alembic"

[ -x "$PYTHON" ] || fail "production Python environment is missing at $PYTHON"
[ -x "$ALEMBIC" ] || fail "Alembic executable is missing at $ALEMBIC"
command -v nsjail >/dev/null 2>&1 || fail "nsjail is required; refusing to start without the sandbox"
[ "$(stat -fc %T /sys/fs/cgroup)" = "cgroup2fs" ] || \
    fail "cgroup v2 is required; refusing to start without resource enforcement"
[ -r /sys/fs/cgroup/cgroup.controllers ] || \
    fail "the delegated cgroup v2 subtree is not readable"

mkdir -p "$(dirname "$JWT_PRIVATE_KEY_FILE")" \
    "$(dirname "$B2_JWKS_PATH")" \
    "$B2_ARTIFACT_ROOT" "$B2_WORKSPACE_ROOT"

if [ ! -s "$JWT_PRIVATE_KEY_FILE" ]; then
    "$PYTHON" -m autograder_b4.ops.generate_key
fi
"$PYTHON" "$B4_MODULE_ROOT/MAIN/host/write_jwks.py" \
    "$JWT_PRIVATE_KEY_FILE" "$B2_JWKS_PATH"

"$PYTHON" "$B4_MODULE_ROOT/MAIN/host/wait_for_postgres.py" \
    --dsn "$PLATFORM_DATABASE_URL" --timeout "$POSTGRES_WAIT_SECONDS"

for module in B2 B4 B3; do
    printf '[autograder] migrating %s\n' "$module"
    (
        cd "$B4_MODULE_ROOT/$module"
        DATABASE_URL="$PLATFORM_DATABASE_URL" "$ALEMBIC" upgrade head
    )
done

printf '[autograder] provisioning B4 least-privilege role\n'
"$PYTHON" -m autograder_b4.ops.provision
