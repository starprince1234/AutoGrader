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
incoming="$root/incoming/$sha"
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

[[ "$incoming" == "$root/incoming/"* ]] || fail "invalid incoming path"
[[ "$release" == "$root/releases/"* ]] || fail "invalid release path"
[[ -d "$stage/MAIN" ]] || fail "staged source tree is missing"
[[ -f "$incoming/runtime.env" ]] || fail "runtime environment file is missing"
[[ -f "$incoming/setup.env" ]] || fail "setup environment file is missing"
[[ -s /etc/autograder/cloudflared.token ]] || fail "Cloudflare Tunnel token is not installed"
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

if ! id autograder >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/autograder --shell /sbin/nologin autograder
fi
if ! id cloudflared >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/cloudflared --shell /sbin/nologin cloudflared
fi

install -d -o root -g root -m 0755 "$root/releases" "$root/staging"
install -d -o root -g root -m 0700 "$root/incoming" "$incoming"
chmod 0700 "$incoming"
install -d -o autograder -g autograder -m 0750 /opt/autograder/python /var/cache/autograder/uv /var/lib/autograder /var/lib/autograder/jwt /var/lib/autograder/jwks /var/lib/autograder/artifacts /var/lib/autograder/workspace
install -d -o root -g root -m 0700 "$backup_dir"
install -d -o root -g cloudflared -m 0750 /etc/autograder

mv "$stage" "$release"
chown -R autograder:autograder "$release"
install -o root -g root -m 0600 "$incoming/runtime.env" "$runtime_env"
install -o root -g root -m 0600 "$incoming/setup.env" "$setup_env"

if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/0.13.1/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi
if ! command -v nsjail >/dev/null 2>&1; then
    /bin/bash "$release/MAIN/scripts/bootstrap-nsjail.sh"
fi
if ! command -v cloudflared >/dev/null 2>&1; then
    /bin/bash "$release/MAIN/scripts/bootstrap-cloudflared.sh"
fi

runuser -u autograder -- env UV_PYTHON_INSTALL_DIR=/opt/autograder/python UV_CACHE_DIR=/var/cache/autograder/uv UV_PYTHON_DOWNLOADS=automatic /usr/local/bin/uv sync --project "$release/MAIN" --extra modules --no-dev --locked

chown -R root:root "$release"
chmod 0755 "$release"
install -o root -g root -m 0644 "$release/MAIN/deploy/autograder.service" /etc/systemd/system/autograder.service
install -o root -g root -m 0644 "$release/MAIN/deploy/cloudflared.service" /etc/systemd/system/cloudflared.service
chown root:cloudflared /etc/autograder/cloudflared.token
chmod 0640 /etc/autograder/cloudflared.token
systemctl daemon-reload

docker compose --project-name autograder --env-file "$setup_env" --file "$release/MAIN/compose.production.yaml" up --detach --wait --wait-timeout 180

if systemctl is-active --quiet cloudflared.service; then
    systemctl stop cloudflared.service
fi
if systemctl is-active --quiet autograder.service; then
    systemctl stop autograder.service
fi

database_backup="$backup_dir/pre-migration-${stage_id}.dump"
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

rm -rf -- "$incoming"
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
