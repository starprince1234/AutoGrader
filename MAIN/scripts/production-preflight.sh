#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '[autograder-preflight] ERROR: %s\n' "$*" >&2
    exit 1
}

sha="${1:-}"
run_attempt="${2:-}"
if [[ -n "$sha" || -n "$run_attempt" ]]; then
    [[ "$sha" =~ ^[a-f0-9]{40}$ ]] || fail "a full Git commit SHA is required"
    [[ "$run_attempt" =~ ^[1-9][0-9]*$ ]] || fail "a positive GitHub run attempt is required"
fi

[[ "$(id -u)" -eq 0 ]] || fail "deployment requires root SSH access"
[[ -r /etc/os-release ]] || fail "/etc/os-release is not readable"
# shellcheck disable=SC1091
. /etc/os-release

for command_name in dnf rpm docker systemctl runuser useradd install curl python3 ss stat df awk find readlink tail nproc grep id uname; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is missing: $command_name"
done

[[ -d /run/systemd/system ]] || fail "systemd is not the active init system"
[[ "$(stat -fc %T /sys/fs/cgroup)" == "cgroup2fs" ]] || \
    fail "a unified cgroup v2 hierarchy is required"
[[ -r /sys/fs/cgroup/cgroup.controllers ]] || \
    fail "the cgroup v2 controllers are not readable"

case "$(uname -m)" in
    x86_64|aarch64) ;;
    *) fail "unsupported server architecture: $(uname -m)" ;;
esac

docker info >/dev/null 2>&1 || fail "the Docker daemon is unavailable"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
systemctl is-active --quiet mihomo-autograder.service || \
    fail "the AutoGrader-local Mihomo proxy service is not active"
curl --fail --silent --show-error --proxy http://127.0.0.1:7893 \
    --connect-timeout 5 --max-time 20 --output /dev/null \
    https://www.gstatic.com/generate_204 || \
    fail "the AutoGrader-local Mihomo proxy cannot reach its health-check endpoint"

for image in postgres:17-alpine nats:2.10-alpine; do
    docker image inspect "$image" >/dev/null 2>&1 || \
        fail "Docker image $image is not cached; the isolated Mihomo proxy does not proxy Docker daemon pulls"
done

memory_total_kib="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)"
memory_available_kib="$(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo)"
[[ "$memory_total_kib" =~ ^[0-9]+$ ]] || fail "could not read total memory"
[[ "$memory_available_kib" =~ ^[0-9]+$ ]] || fail "could not read available memory"
(( memory_total_kib >= 524288 )) || fail "at least 512 MiB of RAM is required"
(( memory_available_kib >= 262144 )) || fail "less than 256 MiB of RAM is currently available"

read -r _ disk_total_kib _ disk_available_kib _ _ < <(df -Pk /var/lib | tail -n 1)
[[ "$disk_total_kib" =~ ^[0-9]+$ && "$disk_available_kib" =~ ^[0-9]+$ ]] || \
    fail "could not read /var/lib disk capacity"
(( disk_total_kib >= 10485760 )) || fail "/var/lib must have at least 10 GiB total capacity"
(( disk_available_kib >= 1048576 )) || fail "/var/lib must have at least 1 GiB free"

[[ ! -L /etc/autograder ]] || fail "/etc/autograder must not be a symbolic link"
[[ -d /etc/autograder ]] || fail "/etc/autograder must be a directory"
for path in /etc/autograder/cloudflared.token /etc/autograder/runtime.env /etc/autograder/setup.env; do
    [[ ! -L "$path" ]] || fail "$path must not be a symbolic link"
    [[ ! -e "$path" || -f "$path" ]] || fail "$path must be a regular file"
done
unexpected_config="$(find /etc/autograder -mindepth 1 -maxdepth 1 \
    ! -name cloudflared.token ! -name runtime.env ! -name setup.env -print -quit)"
[[ -z "$unexpected_config" ]] || \
    fail "/etc/autograder contains files outside this deployment's expected configuration"

[[ -s /etc/autograder/cloudflared.token ]] || \
    fail "install the Cloudflare Tunnel token at /etc/autograder/cloudflared.token first"
token_size="$(stat -c %s /etc/autograder/cloudflared.token)"
[[ "$token_size" =~ ^[0-9]+$ ]] || fail "could not determine the Tunnel token size"
(( token_size >= 100 )) || \
    fail "the Tunnel token is too short; use the Connector Token, not the Tunnel ID"
token_owner="$(stat -c %u /etc/autograder/cloudflared.token)"
token_mode="$(stat -c %a /etc/autograder/cloudflared.token)"
[[ "$token_owner" == "0" ]] || fail "the Tunnel token must be owned by root"
[[ "$token_mode" == "600" || "$token_mode" == "640" ]] || \
    fail "the Tunnel token mode must be 0600 or 0640"

root=/opt/autograder
current="$root/current"
[[ ! -L "$root" ]] || fail "$root must not be a symbolic link"
first_deployment=false
if [[ -L "$current" ]]; then
    current_release="$(readlink -f "$current")"
    [[ -n "$current_release" && -d "$current_release" ]] || fail "the existing current link is broken"
    [[ "$current_release" == "$root/releases/"* ]] || \
        fail "the existing current link does not point into this project's releases"
elif [[ -e "$current" ]]; then
    fail "$current exists but is not a symlink"
else
    first_deployment=true
    if [[ -n "$sha" ]]; then
        expected_stage="$root/staging/${sha}-${run_attempt}"
        [[ -d "$root/incoming/$sha" ]] || fail "the uploaded incoming directory is missing"
        [[ -d "$expected_stage" ]] || fail "the expected staging directory is missing"
        unexpected="$(find "$root" -mindepth 1 -maxdepth 1 \
            ! -name incoming ! -name staging -print -quit 2>/dev/null || true)"
        [[ -z "$unexpected" ]] || fail "$root contains data outside this deployment's incoming/staging directories"
    elif [[ -e "$root" ]]; then
        fail "$root already exists without an AutoGrader current release"
    fi

    if systemctl cat autograder.service >/dev/null 2>&1; then
        fail "autograder.service already exists without an AutoGrader current release"
    fi
    cloudflared_unit_managed=false
    cloudflared_unit=/etc/systemd/system/cloudflared.service
    if systemctl cat cloudflared.service >/dev/null 2>&1; then
        unit_path="$(systemctl show -p FragmentPath --value cloudflared.service)"
        [[ "$unit_path" == "$cloudflared_unit" && -f "$cloudflared_unit" && ! -L "$cloudflared_unit" ]] || \
            fail "cloudflared.service is not the dedicated AutoGrader Tunnel unit"
        for required_setting in \
            "Description=Cloudflare Tunnel connector for AutoGrader API" \
            "User=cloudflared" \
            "Group=cloudflared" \
            "StateDirectory=cloudflared" \
            "ExecStartPre=/usr/bin/test -s /etc/autograder/cloudflared.token" \
            "ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate run --token-file /etc/autograder/cloudflared.token" \
            "ProtectSystem=strict" \
            "ProtectHome=true" \
            "NoNewPrivileges=true"; do
            grep -Fqx "$required_setting" "$cloudflared_unit" || \
                fail "cloudflared.service is missing its dedicated AutoGrader setting"
        done
        cloudflared_unit_managed=true
    elif [[ -e "$cloudflared_unit" || -L "$cloudflared_unit" ]]; then
        fail "$cloudflared_unit exists but is not loaded by systemd"
    fi
    for user in autograder cloudflared; do
        if id "$user" >/dev/null 2>&1; then
            if [[ "$user" == cloudflared && "$cloudflared_unit_managed" == true ]]; then
                continue
            fi
            fail "system user $user already exists without an AutoGrader current release"
        fi
    done
    for volume in autograder_production_postgres autograder_production_nats; do
        if docker volume inspect "$volume" >/dev/null 2>&1; then
            fail "Docker volume $volume already exists without an AutoGrader current release"
        fi
    done
    compose_projects="$(docker compose ls --all --format json)" || fail "could not inspect existing Compose projects"
    compose_conflict="$(python3 -c 'import json,sys; projects=json.load(sys.stdin); print(str(any(item.get("Name") == "autograder" for item in projects)).lower())' <<< "$compose_projects")" || \
        fail "could not parse the existing Compose project list"
    [[ "$compose_conflict" != true ]] || fail "a Compose project named autograder already exists"
    if [[ -e /etc/autograder/runtime.env || -e /etc/autograder/setup.env ]]; then
        fail "/etc/autograder already contains an environment file without an AutoGrader current release"
    fi
    if [[ -e /var/lib/autograder || -L /var/lib/autograder ]]; then
        fail "/var/lib/autograder already exists without an AutoGrader current release"
    fi
    if [[ -e /var/lib/cloudflared || -L /var/lib/cloudflared ]]; then
        [[ "$cloudflared_unit_managed" == true && -d /var/lib/cloudflared && ! -L /var/lib/cloudflared ]] || \
            fail "/var/lib/cloudflared exists without the dedicated AutoGrader Tunnel unit"
        [[ "$(stat -c %U /var/lib/cloudflared)" == cloudflared ]] || \
            fail "/var/lib/cloudflared is not owned by the cloudflared service user"
    fi
fi

if [[ "$first_deployment" == true ]]; then
    for port in 18000 25432 25222; do
        if ss -H -ltn "sport = :$port" | grep -q .; then
            fail "TCP port $port is already in use"
        fi
    done
fi

printf '[autograder-preflight] passed: os=%s version=%s kernel=%s architecture=%s cpu=%s memory_mib=%s available_mib=%s disk_total_gib=%s disk_free_gib=%s mode=%s\n' \
    "${ID:-unknown}" "${VERSION_ID:-unknown}" "$(uname -r)" "$(uname -m)" \
    "$(nproc)" "$((memory_total_kib / 1024))" "$((memory_available_kib / 1024))" \
    "$((disk_total_kib / 1048576))" "$((disk_available_kib / 1048576))" \
    "$(if [[ "$first_deployment" == true ]]; then printf first; else printf upgrade; fi)"
