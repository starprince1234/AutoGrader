#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '[autograder-preflight] ERROR: %s\n' "$*" >&2
    exit 1
}

require_directory() {
    local path="$1" owner="$2" group="$3" mode="$4"
    [[ -d "$path" && ! -L "$path" ]] || fail "$path must be a real directory"
    [[ "$(stat -c %U "$path")" == "$owner" ]] || fail "$path must be owned by $owner"
    [[ "$(stat -c %G "$path")" == "$group" ]] || fail "$path must be in group $group"
    [[ "$(stat -c %a "$path")" == "$mode" ]] || fail "$path must have mode $mode"
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

for command_name in dnf rpm docker systemctl runuser useradd install curl python3 ss stat df awk find readlink tail nproc grep id uname pgrep getent cut; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is missing: $command_name"
done
[[ -x /usr/bin/python3 ]] || fail "B2's configured runner interpreter /usr/bin/python3 is missing"
runner_python_version="$(/usr/bin/python3 --version 2>&1)"
runner_python_path="$(readlink -f /usr/bin/python3)"

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
partial_first_deployment=false
if [[ -L "$current" ]]; then
    current_release="$(readlink -f "$current")"
    [[ -n "$current_release" && -d "$current_release" ]] || fail "the existing current link is broken"
    [[ "$current_release" == "$root/releases/"* ]] || \
        fail "the existing current link does not point into this project's releases"
elif [[ -e "$current" ]]; then
    fail "$current exists but is not a symlink"
else
    first_deployment=true
    if [[ -e "$root" ]]; then
        require_directory "$root" root root 755
        partial_first_deployment=true
        [[ -e "$root/python" || -L "$root/python" ]] || \
            fail "$root exists without the interrupted deployment's Python runtime directory"
        require_directory "$root/python" autograder autograder 750
        [[ -z "$(find "$root/python" -mindepth 1 -print -quit)" ]] || \
            fail "$root/python contains runtime data without a current release"
        unexpected="$(find "$root" -mindepth 1 -maxdepth 1 \
            ! -name python ! -name incoming ! -name staging ! -name releases -print -quit)"
        [[ -z "$unexpected" ]] || fail "$root contains an unrecognized first-deployment entry"

        for directory in incoming staging releases; do
            path="$root/$directory"
            if [[ -e "$path" || -L "$path" ]]; then
                require_directory "$path" root root 755
                if [[ -z "$sha" ]]; then
                    [[ -z "$(find "$path" -mindepth 1 -print -quit)" ]] || \
                        fail "$path contains data before the deployment upload"
                fi
            fi
        done

        if [[ -n "$sha" ]]; then
            expected_stage="$root/staging/${sha}-${run_attempt}"
            [[ -d "$root/incoming/$sha" && ! -L "$root/incoming/$sha" ]] || \
                fail "the uploaded incoming directory is missing or not a real directory"
            [[ -d "$expected_stage" && ! -L "$expected_stage" ]] || \
                fail "the expected staging directory is missing or not a real directory"
            unexpected_incoming="$(find "$root/incoming" -mindepth 1 -maxdepth 1 \
                ! -name "$sha" -print -quit)"
            [[ -z "$unexpected_incoming" ]] || fail "$root/incoming contains an unexpected deployment"
            unexpected_staging="$(find "$root/staging" -mindepth 1 -maxdepth 1 \
                ! -name "${sha}-${run_attempt}" -print -quit)"
            [[ -z "$unexpected_staging" ]] || fail "$root/staging contains an unexpected deployment"
            if [[ -d "$root/releases" ]]; then
                [[ -z "$(find "$root/releases" -mindepth 1 -print -quit)" ]] || \
                    fail "$root/releases contains a release without a current link"
            fi
        fi
    elif [[ -n "$sha" ]]; then
        fail "$root is missing before the deployment upload"
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
            if [[ "$user" == autograder && "$partial_first_deployment" == true ]]; then
                account="$(getent passwd autograder)"
                [[ -n "$account" ]] || fail "the interrupted AutoGrader system account is missing"
                account_uid="$(cut -d: -f3 <<< "$account")"
                account_gid="$(cut -d: -f4 <<< "$account")"
                account_home="$(cut -d: -f6 <<< "$account")"
                account_shell="$(cut -d: -f7 <<< "$account")"
                [[ "$account_uid" =~ ^[0-9]+$ ]] && (( account_uid >= 100 && account_uid < 1000 )) || \
                    fail "the interrupted AutoGrader account is not a system user"
                [[ "$account_home" == /var/lib/autograder && "$account_shell" == /sbin/nologin ]] || \
                    fail "the interrupted AutoGrader account has unexpected settings"
                account_group="$(getent group autograder || true)"
                [[ -n "$account_group" && "$(cut -d: -f3 <<< "$account_group")" == "$account_gid" ]] || \
                    fail "the interrupted AutoGrader primary group is unexpected"
                if pgrep -u autograder >/dev/null 2>&1; then
                    fail "an AutoGrader system-user process is running without a current release"
                fi
                continue
            fi
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
    for path in /etc/autograder/runtime.env /etc/autograder/setup.env; do
        if [[ -e "$path" || -L "$path" ]]; then
            [[ "$partial_first_deployment" == true ]] || \
                fail "$path exists without an AutoGrader current release"
            [[ -f "$path" && ! -L "$path" ]] || fail "$path must be a regular file"
            [[ "$(stat -c %U "$path")" == root && "$(stat -c %a "$path")" == 600 ]] || \
                fail "$path must be root-owned with mode 0600"
        fi
    done
    if [[ -e /var/lib/autograder || -L /var/lib/autograder ]]; then
        [[ "$partial_first_deployment" == true ]] || \
            fail "/var/lib/autograder exists without an AutoGrader current release"
        require_directory /var/lib/autograder autograder autograder 750
        unexpected_state="$(find /var/lib/autograder -mindepth 1 -maxdepth 1 \
            ! -name artifacts ! -name backups ! -name jwks ! -name jwt ! -name workspace -print -quit)"
        [[ -z "$unexpected_state" ]] || fail "/var/lib/autograder contains an unrecognized first-deployment entry"
        for path in \
            /var/lib/autograder/artifacts \
            /var/lib/autograder/jwks \
            /var/lib/autograder/jwt \
            /var/lib/autograder/workspace; do
            if [[ -e "$path" || -L "$path" ]]; then
                require_directory "$path" autograder autograder 750
                [[ -z "$(find "$path" -mindepth 1 -print -quit)" ]] || \
                    fail "$path contains data and is not an empty first-deployment directory"
            fi
        done
        if [[ -e /var/lib/autograder/backups || -L /var/lib/autograder/backups ]]; then
            require_directory /var/lib/autograder/backups root root 700
            [[ -z "$(find /var/lib/autograder/backups -mindepth 1 -print -quit)" ]] || \
                fail "/var/lib/autograder/backups contains data without a current release"
        fi
    fi
    if [[ -e /var/cache/autograder/uv || -L /var/cache/autograder/uv ]]; then
        [[ "$partial_first_deployment" == true ]] || \
            fail "/var/cache/autograder/uv exists without an AutoGrader current release"
        require_directory /var/cache/autograder/uv autograder autograder 750
        [[ -z "$(find /var/cache/autograder/uv -mindepth 1 -print -quit)" ]] || \
            fail "/var/cache/autograder/uv contains cache data without a current release"
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
    "$(if [[ "$first_deployment" == true ]]; then
        if [[ "$partial_first_deployment" == true ]]; then printf first-resume; else printf first; fi
      else
        printf upgrade
      fi)"
printf '[autograder-preflight] B2 runner interpreter=%s resolved_path=%s\n' \
    "$runner_python_version" "$runner_python_path"
