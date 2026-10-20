#!/usr/bin/env bash
set -euo pipefail
umask 077

proxy_url=http://127.0.0.1:7893
export HTTP_PROXY="$proxy_url" HTTPS_PROXY="$proxy_url"
export http_proxy="$proxy_url" https_proxy="$proxy_url"
export NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1

fail() {
    printf '[autograder-deploy] ERROR: %s\n' "$*" >&2
    exit 1
}

sha="${1:-}"
stage="${2:-}"
[[ "$sha" =~ ^[a-f0-9]{40}$ ]] || fail "a full Git commit SHA is required"

root=/opt/autograder
stage_prefix="$root/staging/$sha-"
stage_id="${stage##*/}"
[[ "$stage" == "$stage_prefix"* ]] || fail "invalid staging path"
[[ "$stage_id" =~ ^${sha}-[1-9][0-9]*$ ]] || fail "invalid staging release id"
run_attempt="${stage_id#"$sha"-}"

release="$root/releases/$stage_id"
runtime_env=/etc/autograder/runtime.env
setup_env=/etc/autograder/setup.env
backup_dir=/var/lib/autograder/backups
database_backup=""
old_release=""
api_was_active=false
tunnel_was_active=false
database_migration_started=false
current_switched=false
deployment_succeeded=false

[[ "$release" == "$root/releases/"* ]] || fail "invalid release path"
[[ -d "$stage/MAIN" && ! -L "$stage/MAIN" ]] || fail "staged source tree is missing"
[[ -f "$stage/.autograder-source-sha" && ! -L "$stage/.autograder-source-sha" && \
    "$(<"$stage/.autograder-source-sha")" == "$sha" ]] || \
    fail "staged source does not identify the requested commit"
[[ -s /etc/autograder/cloudflared.token ]] || fail "Cloudflare Tunnel token is not installed"
for config in "$runtime_env" "$setup_env"; do
    [[ -f "$config" && ! -L "$config" && -s "$config" && \
        "$(stat -c %U:%a "$config")" == root:600 ]] || \
        fail "$config must be a non-empty root-owned 0600 file"
done
[[ ! -e "$release" ]] || fail "release already exists: $stage_id"

bash "$stage/MAIN/scripts/production-preflight.sh" "$sha" "$run_attempt"
if [[ -L "$root/current" ]]; then
    old_release="$(readlink -f "$root/current" 2>/dev/null || true)"
    [[ -n "$old_release" && -d "$old_release" ]] || fail "the current release link is broken"
    [[ "$old_release" == "$root/releases/"* ]] || fail "the current release is outside the release directory"
    systemctl is-active --quiet autograder.service && api_was_active=true
    systemctl is-active --quiet cloudflared.service && tunnel_was_active=true
elif [[ -e "$root/current" ]]; then
    fail "$root/current exists but is not a symlink"
fi

rollback() {
    status=$?
    trap - EXIT
    if [[ "$deployment_succeeded" != true ]]; then
        set +e
        systemctl stop cloudflared.service >/dev/null 2>&1
        systemctl stop autograder.service >/dev/null 2>&1

        restore_ok=true
        if [[ "$database_migration_started" == true && -n "$database_backup" && -s "$database_backup" ]]; then
            if ! docker compose --project-name autograder --env-file "$setup_env" --file "$release/MAIN/compose.production.yaml" exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --clean --if-exists --no-owner --single-transaction --exit-on-error --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' < "$database_backup"; then
                printf '[autograder-deploy] ERROR: database restore failed; API and Tunnel remain stopped\n' >&2
                restore_ok=false
            fi
        fi

        if [[ "$current_switched" == true ]]; then
            if [[ -n "$old_release" && -d "$old_release" ]]; then
                ln -sfn "$old_release" "$root/current.rollback"
                mv -Tf "$root/current.rollback" "$root/current"
            elif [[ "$(readlink -f "$root/current" 2>/dev/null || true)" == "$release" ]]; then
                rm -f -- "$root/current"
            fi
        fi

        current_after_rollback="$(readlink -f "$root/current" 2>/dev/null || true)"
        if [[ -d "$release" && "$release" == "$root/releases/"* && \
            "$release" != "$old_release" && "$current_after_rollback" != "$release" ]]; then
            rm -rf -- "$release"
        fi

        if [[ "$restore_ok" == true && "$api_was_active" == true && -n "$old_release" ]]; then
            systemctl restart autograder.service >/dev/null 2>&1
        fi
        if [[ "$restore_ok" == true && "$tunnel_was_active" == true && -n "$old_release" ]]; then
            systemctl restart cloudflared.service >/dev/null 2>&1
        fi
        if [[ -z "$old_release" ]]; then
            systemctl disable autograder.service cloudflared.service >/dev/null 2>&1
        fi
        if [[ -n "$database_backup" && -s "$database_backup" ]]; then
            printf '[autograder-deploy] database backup retained at %s\n' "$database_backup" >&2
        fi
        if [[ -n "$old_release" ]]; then
            printf '[autograder-deploy] previous release remains %s\n' "$old_release" >&2
        fi
    fi
    exit "$status"
}
trap rollback EXIT

id autograder >/dev/null 2>&1 || fail "the AutoGrader service account is missing; run host bootstrap"
id cloudflared >/dev/null 2>&1 || fail "the Tunnel service account is missing; run host bootstrap"
for command_name in uv nsjail cloudflared; do
    command -v "$command_name" >/dev/null 2>&1 || \
        fail "host dependency $command_name is missing; run host bootstrap"
done
[[ -f /etc/systemd/system/autograder.service && ! -L /etc/systemd/system/autograder.service && \
    -f /etc/systemd/system/cloudflared.service && ! -L /etc/systemd/system/cloudflared.service ]] || \
    fail "managed systemd units are missing; run host bootstrap"
cmp -s "$stage/MAIN/deploy/autograder.service" /etc/systemd/system/autograder.service || \
    fail "the host API unit differs from the release; update host configuration separately"
cmp -s "$stage/MAIN/deploy/cloudflared.service" /etc/systemd/system/cloudflared.service || \
    fail "the host Tunnel unit differs from the release; update host configuration separately"

if [[ -e "$root/releases" || -L "$root/releases" ]]; then
    [[ -d "$root/releases" && ! -L "$root/releases" && \
        "$(stat -c %U:%G:%a "$root/releases")" == root:root:755 ]] || \
        fail "$root/releases has unexpected type, ownership, or permissions"
else
    install -d -o root -g root -m 0755 "$root/releases"
fi

mv "$stage" "$release"
chown -R autograder:autograder "$release"
runuser -u autograder -- env \
    HOME=/var/lib/autograder \
    UV_PROJECT_ENVIRONMENT="$release/MAIN/.venv" \
    UV_PYTHON_INSTALL_DIR=/opt/autograder/python \
    UV_CACHE_DIR=/var/cache/autograder/uv \
    UV_PYTHON_DOWNLOADS=automatic \
    /usr/local/bin/uv sync --project "$release/MAIN" --extra modules --no-dev --locked
[[ -x "$release/MAIN/.venv/bin/python" ]] || \
    fail "uv sync completed without creating the release Python environment"
[[ -x "$release/MAIN/.venv/bin/alembic" ]] || \
    fail "the release Python environment is missing Alembic"

chown --no-dereference --recursive root:root "$release"
chmod 0755 "$release"
venv_python="$release/MAIN/.venv/bin/python"
venv_dir="$release/MAIN/.venv"
[[ -d "$venv_dir" && ! -L "$venv_dir" ]] || \
    fail "the release Python environment is not a real directory"
chgrp --no-dereference --recursive autograder "$venv_dir"
find "$venv_dir" -xdev -type d -exec chmod 0750 {} +
find "$venv_dir" -xdev -type f ! -perm /111 -exec chmod 0640 {} +
find "$venv_dir" -xdev -type f -perm /111 -exec chmod 0750 {} +
resolved_python="$(readlink -f "$venv_python" 2>/dev/null || true)"
[[ -n "$resolved_python" && -f "$resolved_python" ]] || \
    fail "the release Python executable does not resolve to a regular file"
if [[ "$resolved_python" == "$release/"* ]]; then
    [[ "$(stat -c %U:%G:%a "$resolved_python")" == root:autograder:750 ]] || \
        fail "the release-local Python executable has unexpected ownership or permissions"
elif [[ "$resolved_python" == "$root/python/"* ]]; then
    [[ "$(stat -c %U:%G "$resolved_python")" == autograder:autograder && \
        "$(stat -c %a "$resolved_python")" == 700 ]] || \
        fail "the shared uv-managed interpreter has unexpected ownership or permissions"
else
    fail "the release Python executable resolves outside the release and managed runtime"
fi
runuser -u autograder -- /usr/bin/test -x "$venv_python" || \
    fail "the release Python executable is not runnable by the API service user"
runuser -u autograder -- /usr/bin/test -x "$release/MAIN/.venv/bin/alembic" || \
    fail "the release Alembic executable is not runnable by the API service user"
[[ -d "$backup_dir" && ! -L "$backup_dir" ]] || fail "the managed database backup directory is missing"
mapfile -t postgres_containers < <(docker ps \
    --filter label=com.docker.compose.project=autograder \
    --filter label=com.docker.compose.service=postgres --format '{{.Names}}')
mapfile -t nats_containers < <(docker ps \
    --filter label=com.docker.compose.project=autograder \
    --filter label=com.docker.compose.service=nats --format '{{.Names}}')
[[ "${#postgres_containers[@]}" == 1 && "${#nats_containers[@]}" == 1 ]] || \
    fail "PostgreSQL and NATS must already be running as the managed infrastructure"
[[ "$(docker inspect --format '{{.State.Health.Status}}' "${postgres_containers[0]}")" == healthy ]] || \
    fail "the managed PostgreSQL service is not healthy"
[[ "$(docker inspect --format '{{.State.Status}}' "${nats_containers[0]}")" == running ]] || \
    fail "the managed NATS service is not running"

if systemctl is-active --quiet cloudflared.service; then
    systemctl stop cloudflared.service
fi
if systemctl is-active --quiet autograder.service; then
    systemctl stop autograder.service
fi

database_backup="$backup_dir/pre-migration-${stage_id}.dump"
[[ ! -e "$database_backup" && ! -L "$database_backup" ]] || \
    fail "the candidate migration backup path already exists"
docker compose --project-name autograder --env-file "$setup_env" --file "$release/MAIN/compose.production.yaml" exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom' > "$database_backup"
[[ -s "$database_backup" ]] || fail "database backup is empty"
chmod 0600 "$database_backup"

set -a
source "$setup_env"
source "$runtime_env"
set +a
export B4_MODULE_ROOT="$release"
database_migration_started=true
runuser --preserve-environment --user autograder -- /usr/bin/env HOME=/var/lib/autograder USER=autograder LOGNAME=autograder /bin/bash "$release/MAIN/scripts/production-prepare.sh"

ln -sfn "$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
current_switched=true
systemctl enable autograder.service
systemctl restart autograder.service

ready=false
for attempt in $(seq 1 30); do
    if curl --fail --silent --max-time 5 http://127.0.0.1:18000/health/ready 2>/dev/null | python3 -c 'import json,sys; payload=json.load(sys.stdin); modules={str(item.get("module", "")).lower(): item.get("healthy") for item in payload.get("modules", [])}; required={"b2", "b3", "b4"}; raise SystemExit(0 if payload.get("ready") is True and required <= modules.keys() and all(modules[name] is True for name in required) else 1)' 2>/dev/null; then
        ready=true
        break
    fi
    sleep 2
done
[[ "$ready" == true ]] || fail "release $stage_id did not become locally ready"

systemctl enable cloudflared.service
systemctl restart cloudflared.service
if ! systemctl is-active --quiet cloudflared.service; then
    fail "Cloudflare Tunnel connector is not active"
fi

public_ready=false
for attempt in $(seq 1 8); do
    if curl --fail --silent --show-error --location --connect-timeout 5 --max-time 20 https://api.91idc.me/health/ready | python3 -c 'import json,sys; payload=json.load(sys.stdin); modules={str(item.get("module", "")).lower(): item.get("healthy") for item in payload.get("modules", [])}; required={"b2", "b3", "b4"}; raise SystemExit(0 if payload.get("ready") is True and required <= modules.keys() and all(modules[name] is True for name in required) else 1)' 2>/dev/null; then
        public_ready=true
        break
    fi
    sleep 3
done
[[ "$public_ready" == true ]] || fail "api.91idc.me did not return a healthy readiness response"

deployment_succeeded=true

find "$backup_dir" -mindepth 1 -maxdepth 1 -type f -name 'pre-migration-*.dump' -printf '%T@ %p\n' | sort -rn | tail -n +6 | cut -d ' ' -f 2- | while IFS= read -r candidate; do
    if [[ "$candidate" == "$backup_dir/"* && -f "$candidate" ]]; then
        rm -f -- "$candidate"
    fi
done

find "$root/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | tail -n +4 | cut -d ' ' -f 2- | while IFS= read -r candidate; do
    if [[ "$candidate" == "$root/releases/"* && "$candidate" != "$release" && "$candidate" != "$old_release" && -d "$candidate" ]]; then
        rm -rf -- "$candidate"
    fi
done

printf '[autograder-deploy] release %s is healthy locally and through Cloudflare\n' "$stage_id"
