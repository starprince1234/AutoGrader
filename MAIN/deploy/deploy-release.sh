#!/usr/bin/env bash
set -euo pipefail
umask 077
fail() { printf '[autograder-deploy] ERROR: %s\n' "$*" >&2; exit 1; }
sha="${1:-}"
stage="${2:-}"
[[ "$sha" =~ ^[a-f0-9]{40}$ ]] || fail "a full Git commit SHA is required"
root=/opt/autograder
stage_id="${stage##*/}"
[[ "$stage" == "$root/staging/$stage_id" && "$stage_id" =~ ^${sha}-[1-9][0-9]*-[1-9][0-9]*$ ]] || fail "invalid staging path"
release="$root/releases/$stage_id"
[[ -d "$stage/MAIN" && ! -L "$stage" && ! -e "$release" ]] || fail "invalid candidate release"

# Build before interrupting the running API. A failed candidate is kept for diagnosis.
export HTTP_PROXY=http://127.0.0.1:7893 HTTPS_PROXY=http://127.0.0.1:7893
export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY"
export NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1
install -d -m 0755 "$root/releases"
mv "$stage" "$release"
chown -R autograder:autograder "$release"
runuser -u autograder -- env HOME=/var/lib/autograder \
    UV_PROJECT_ENVIRONMENT="$release/MAIN/.venv" \
    UV_PYTHON_INSTALL_DIR="$root/python" UV_CACHE_DIR=/var/cache/autograder/uv \
    /usr/local/bin/uv sync --project "$release/MAIN" --extra modules --no-dev --locked
python="$release/MAIN/.venv/bin/python"
runuser -u autograder -- "$python" --version

set -a
source /etc/autograder/setup.env
source /etc/autograder/runtime.env
set +a
export B4_MODULE_ROOT="$release"

# Import the exact release and assemble its routers before interrupting the API.
runuser --preserve-environment --user autograder -- \
    env HOME=/var/lib/autograder USER=autograder LOGNAME=autograder B4_MODULE_ROOT="$release" \
    "$python" -c 'from autograder_host.main import app; print("candidate application assembled:", app.title)'

systemctl stop autograder.service
runuser --preserve-environment --user autograder -- \
    env HOME=/var/lib/autograder USER=autograder LOGNAME=autograder \
    bash "$release/MAIN/scripts/production-prepare.sh"

# Published application files are readable but not writable by the API service.
chown -R root:autograder "$release"
chmod -R g+rX,o-rwx "$release"
chmod 0755 "$release"
install -m 0644 "$release/MAIN/deploy/autograder.service" /etc/systemd/system/autograder.service
install -m 0644 "$release/MAIN/deploy/cloudflared.service" /etc/systemd/system/cloudflared.service
systemctl daemon-reload
ln -sfn "$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
systemctl enable autograder.service
systemctl restart autograder.service

wait_ready() {
    local url="$1" attempts="$2" attempt
    for ((attempt=1; attempt<=attempts; attempt++)); do
        if curl --fail --silent --show-error --connect-timeout 5 --max-time 10 "$url" \
            | "$python" "$release/MAIN/deploy/check-readiness.py"; then
            return 0
        fi
        sleep 2
    done
    return 1
}
wait_ready http://127.0.0.1:18000/health/ready 30 || \
    fail "API is not ready; candidate and service state retained for diagnosis"
systemctl enable cloudflared.service
systemctl restart cloudflared.service
wait_ready https://api.91idc.me/health/ready 12 || \
    fail "public API is not ready; local API remains running"
printf '[autograder-deploy] release %s is ready locally and publicly\n' "$stage_id"
