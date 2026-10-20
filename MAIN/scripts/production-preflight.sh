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

validate_managed_python_runtime() {
    local runtime_root="$1"
    local autograder_uid autograder_gid file alias_target version_dir python_executable unexpected
    local -a aliases=() versions=()

    autograder_uid="$(id -u autograder)"
    autograder_gid="$(id -g autograder)"
    for file in .gitignore .lock; do
        file="$runtime_root/$file"
        [[ -f "$file" && ! -L "$file" ]] || fail "$file is not a managed Python runtime file"
        [[ "$(stat -c %u:%g "$file")" == "$autograder_uid:$autograder_gid" ]] || \
            fail "$file is not owned by the AutoGrader user"
    done

    require_directory "$runtime_root/.temp" autograder autograder 700
    [[ -z "$(find "$runtime_root/.temp" -mindepth 1 -print -quit)" ]] || \
        fail "$runtime_root/.temp contains an incomplete Python download"

    mapfile -t aliases < <(find "$runtime_root" -mindepth 1 -maxdepth 1 \
        -type l -name 'cpython-3.12-linux-*-gnu' -print | LC_ALL=C sort)
    mapfile -t versions < <(find "$runtime_root" -mindepth 1 -maxdepth 1 \
        -type d -name 'cpython-3.12.*-linux-*-gnu' -print | LC_ALL=C sort)
    [[ "${#aliases[@]}" == 1 && "${#versions[@]}" == 1 ]] || \
        fail "$runtime_root does not contain one managed CPython 3.12 installation"

    version_dir="${versions[0]}"
    alias_target="$(readlink -f "${aliases[0]}")"
    [[ "$alias_target" == "$version_dir" ]] || \
        fail "$runtime_root CPython alias does not target its managed installation"
    [[ "$(stat -c %u:%g "${aliases[0]}")" == "$autograder_uid:$autograder_gid" ]] || \
        fail "$runtime_root CPython alias is not owned by the AutoGrader user"
    require_directory "$version_dir" autograder autograder 700

    unexpected="$(find "$runtime_root" -mindepth 1 -maxdepth 1 \
        ! -name .gitignore ! -name .lock ! -name .temp \
        ! -name 'cpython-3.12-linux-*-gnu' \
        ! -name 'cpython-3.12.*-linux-*-gnu' -print -quit)"
    [[ -z "$unexpected" ]] || fail "$runtime_root contains an unrecognized runtime entry"

    for directory in bin include lib share; do
        require_directory "$version_dir/$directory" autograder autograder 700
    done
    [[ -f "$version_dir/BUILD" && ! -L "$version_dir/BUILD" ]] || \
        fail "$version_dir is missing the uv-managed Python build marker"
    python_executable="$version_dir/bin/python3.12"
    [[ -f "$python_executable" && -x "$python_executable" && \
        "$(stat -c %a "$python_executable")" == 700 ]] || \
        fail "$python_executable is not a private executable"
    [[ "$(stat -c %u "$python_executable")" == "$autograder_uid" || \
        "$(stat -c %u "$python_executable")" == 0 ]] || \
        fail "$python_executable has an unexpected owner"
    unexpected="$(find "$version_dir" -mindepth 1 \
        ! -uid "$autograder_uid" ! -path "$python_executable" -print -quit)"
    [[ -z "$unexpected" ]] || \
        fail "$version_dir contains files outside the AutoGrader-owned runtime"

    printf '[autograder-preflight] recognized uv-managed CPython 3.12 runtime; host bootstrap will normalize the interpreter executable if needed\n'
}

validate_interrupted_compose_deployment() {
    local root="$1"
    local unit_file=/etc/systemd/system/autograder.service
    local backup_dir=/var/lib/autograder/backups
    local autograder_uid unit_path required_setting
    local compose_json compose_config failed_stage backup_path backup_name
    local record service status config_file postgres_found=false nats_found=false
    local volume volume_source volume_labels unexpected
    local -a backup_files=() backup_entries=() container_records=() project_volumes=()

    [[ -f "$unit_file" && ! -L "$unit_file" ]] || \
        fail "the interrupted API unit is missing or is not a regular file"
    [[ "$(stat -c %U:%G "$unit_file")" == root:root && \
        "$(stat -c %a "$unit_file")" == 644 ]] || \
        fail "the interrupted API unit has unexpected ownership or permissions"
    unit_path="$(systemctl show -p FragmentPath --value autograder.service)"
    [[ "$unit_path" == "$unit_file" ]] || \
        fail "the interrupted API unit is loaded from an unexpected path"
    if systemctl is-active --quiet autograder.service; then
        fail "the interrupted API service is unexpectedly active"
    fi
    for required_setting in \
        'Description=AutoGrader composition API' \
        'User=autograder' \
        'Group=autograder' \
        'WorkingDirectory=/opt/autograder/current/MAIN' \
        'EnvironmentFile=/etc/autograder/runtime.env' \
        'ExecStart=/opt/autograder/current/MAIN/.venv/bin/uvicorn autograder_host.main:app --host 127.0.0.1 --port 18000 --workers 1' \
        'ProtectSystem=strict' \
        'ProtectHome=true' \
        'NoNewPrivileges=true'; do
        grep -Fqx "$required_setting" "$unit_file" || \
            fail "the interrupted API unit is missing its managed setting"
    done

    require_directory "$backup_dir" root root 700
    mapfile -t backup_files < <(find "$backup_dir" -mindepth 1 -maxdepth 1 \
        -type f -name 'pre-migration-*.dump' -print | LC_ALL=C sort)
    [[ "${#backup_files[@]}" -ge 1 ]] || \
        fail "$backup_dir has no backup for the interrupted Compose deployment"
    mapfile -t backup_entries < <(find "$backup_dir" -mindepth 1 -maxdepth 1 \
        -print | LC_ALL=C sort)
    [[ "${#backup_entries[@]}" == "${#backup_files[@]}" ]] || \
        fail "$backup_dir contains an unrecognized entry"
    for backup_path in "${backup_files[@]}"; do
        backup_name="${backup_path##*/}"
        [[ "$backup_name" =~ ^pre-migration-([a-f0-9]{40}-[1-9][0-9]*)\.dump$ ]] || \
            fail "$backup_path has an unexpected backup name"
        [[ -s "$backup_path" && "$(stat -c %U:%G "$backup_path")" == root:root && \
            "$(stat -c %a "$backup_path")" == 600 ]] || \
            fail "$backup_path has unexpected ownership, permissions, or size"
    done

    compose_json="$(docker compose ls --all --format json)" || \
        fail "could not inspect the interrupted Compose project"
    compose_config="$(python3 -c 'import json,sys; p=[item for item in json.load(sys.stdin) if item.get("Name")=="autograder"]; sys.exit(1) if len(p)!=1 or p[0].get("Status")!="running(2)" else print(p[0].get("ConfigFiles",""))' <<<"$compose_json")" || \
        fail "the interrupted Compose project is missing, duplicated, or not running exactly two services"
    [[ "$compose_config" =~ ^${root}/releases/([a-f0-9]{40}-[1-9][0-9]*)/MAIN/compose\.production\.yaml$ ]] || \
        fail "the interrupted Compose project uses an unexpected configuration path"
    failed_stage="${BASH_REMATCH[1]}"
    [[ ! -e "$root/releases/$failed_stage" && ! -L "$root/releases/$failed_stage" ]] || \
        fail "the failed release directory unexpectedly remains present"
    backup_path="$backup_dir/pre-migration-${failed_stage}.dump"
    [[ -s "$backup_path" ]] || \
        fail "the interrupted Compose project has no matching pre-migration backup"

    mapfile -t container_records < <(docker ps -a \
        --filter label=com.docker.compose.project=autograder \
        --format '{{.Names}}|{{.Label "com.docker.compose.service"}}|{{.Status}}|{{.Label "com.docker.compose.project.config_files"}}' | LC_ALL=C sort)
    [[ "${#container_records[@]}" == 2 ]] || \
        fail "the interrupted Compose project must contain exactly two service containers"
    for record in "${container_records[@]}"; do
        IFS='|' read -r container_name service status config_file <<<"$record"
        [[ "$config_file" == "$compose_config" && "$status" == Up* ]] || \
            fail "an interrupted Compose service is not running from the expected configuration"
        case "$service" in
            postgres)
                [[ "$status" == *'(healthy)'* && "$postgres_found" == false ]] || \
                    fail "the interrupted PostgreSQL service is unhealthy or duplicated"
                [[ "$(docker port "$container_name" 5432/tcp)" == 127.0.0.1:25432 ]] || \
                    fail "the interrupted PostgreSQL container is not bound only to 127.0.0.1:25432"
                postgres_found=true
                ;;
            nats)
                [[ "$nats_found" == false ]] || fail "the interrupted NATS service is duplicated"
                [[ "$(docker port "$container_name" 4222/tcp)" == 127.0.0.1:25222 ]] || \
                    fail "the interrupted NATS container is not bound only to 127.0.0.1:25222"
                if docker port "$container_name" 8222/tcp >/dev/null 2>&1; then
                    fail "the interrupted NATS monitoring port is unexpectedly published"
                fi
                nats_found=true
                ;;
            *)
                fail "the interrupted Compose project has an unexpected service: $service"
                ;;
        esac
    done
    [[ "$postgres_found" == true && "$nats_found" == true ]] || \
        fail "the interrupted Compose project is missing PostgreSQL or NATS"

    mapfile -t project_volumes < <(docker volume ls \
        --filter label=com.docker.compose.project=autograder \
        --format '{{.Name}}' | LC_ALL=C sort)
    [[ "${#project_volumes[@]}" == 2 && \
        "${project_volumes[0]}" == autograder_production_nats && \
        "${project_volumes[1]}" == autograder_production_postgres ]] || \
        fail "the interrupted Compose project has unexpected data volumes"
    for volume_source in \
        autograder_production_nats:autograder_production_nats \
        autograder_production_postgres:autograder_production_postgres; do
        volume="${volume_source%%:*}"
        volume_labels="$(docker volume inspect --format \
            '{{index .Labels "com.docker.compose.project"}}|{{index .Labels "com.docker.compose.volume"}}' \
            "$volume")" || fail "could not inspect Compose volume $volume"
        [[ "$volume_labels" == "autograder|${volume_source#*:}" ]] || \
            fail "Compose volume $volume has unexpected project labels"
    done

    require_directory /var/cache/autograder/uv autograder autograder 750
    autograder_uid="$(id -u autograder)"
    unexpected="$(find /var/cache/autograder/uv -mindepth 1 \
        ! -uid "$autograder_uid" -print -quit)"
    [[ -z "$unexpected" ]] || \
        fail "/var/cache/autograder/uv contains entries outside the AutoGrader-owned cache"
    [[ "$(stat -c %u:%g "$backup_dir")" == "0:0" && \
        "$(stat -c %u:%g "$unit_file")" == "0:0" ]] || \
        fail "the interrupted deployment's backup or service metadata has unexpected ownership"

    printf '[autograder-preflight] recognized inactive API unit, healthy PostgreSQL/NATS, and the matching preserved backup; no existing data will be removed\n'
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

for path in /etc/autograder/runtime.env /etc/autograder/setup.env; do
    [[ -s "$path" && "$(stat -c %U "$path")" == root && \
        "$(stat -c %a "$path")" == 600 ]] || \
        fail "$path must be a non-empty root-owned 0600 file"
done

for command_name in git flock tar timeout; do
    command -v "$command_name" >/dev/null 2>&1 || \
        fail "required server-side deployment command is missing: $command_name"
done
for command_name in uv nsjail cloudflared; do
    command -v "$command_name" >/dev/null 2>&1 || \
        fail "host dependency $command_name is missing; run host bootstrap separately"
done

root=/opt/autograder
current="$root/current"
[[ ! -L "$root" ]] || fail "$root must not be a symbolic link"
first_deployment=false
partial_first_deployment=false
partial_python_runtime=false
partial_compose_recovery=false
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
        if [[ -n "$(find "$root/python" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
            printf '[autograder-preflight] interrupted first-deployment service metadata:\n'
            docker compose ls --all --format json | python3 -c \
                'import json,sys; [print("  compose {} {} {}".format(p.get("Name"),p.get("Status"),p.get("ConfigFiles"))) for p in json.load(sys.stdin) if p.get("Name") == "autograder"]' || true
            docker ps -a --filter label=com.docker.compose.project=autograder \
                --format '  container {{.Names}} {{.Status}} {{.Label "com.docker.compose.service"}} {{.Label "com.docker.compose.project.config_files"}}' || true
            for volume in autograder_production_postgres autograder_production_nats; do
                docker volume inspect --format '  volume {{.Name}} {{json .Labels}}' "$volume" 2>/dev/null || true
            done
            for path in /etc/systemd/system/autograder.service \
                /var/lib/autograder/backups /var/cache/autograder/uv; do
                if [[ -e "$path" && -d "$path" ]]; then
                    find "$path" -mindepth 1 -maxdepth 2 \
                        -printf '  %p %y %m %u:%g %s bytes\n' | LC_ALL=C sort
                elif [[ -e "$path" ]]; then
                    stat -c '  %n %F %a %U:%G %s bytes' "$path"
                    grep -E '^(Description|User|Group|WorkingDirectory|EnvironmentFile|ExecStart)=' \
                        "$path" || true
                fi
            done
        fi
        [[ -e "$root/python" || -L "$root/python" ]] || \
            fail "$root exists without the interrupted deployment's Python runtime directory"
        require_directory "$root/python" autograder autograder 750
        if [[ -n "$(find "$root/python" -mindepth 1 -print -quit)" ]]; then
            validate_managed_python_runtime "$root/python"
            partial_python_runtime=true
        fi
        if systemctl cat autograder.service >/dev/null 2>&1; then
            [[ "$partial_python_runtime" == true ]] || \
                fail "autograder.service exists without a recognized interrupted Python runtime"
            validate_interrupted_compose_deployment "$root"
            partial_compose_recovery=true
        fi
        unexpected="$(find "$root" -mindepth 1 -maxdepth 1 \
            ! -name python ! -name incoming ! -name staging ! -name releases ! -name git-cache -print -quit)"
        [[ -z "$unexpected" ]] || fail "$root contains an unrecognized first-deployment entry"

        if [[ -e "$root/git-cache" || -L "$root/git-cache" ]]; then
            require_directory "$root/git-cache" root root 700
            [[ -f "$root/git-cache/config" && \
                "$(git --git-dir="$root/git-cache" rev-parse --is-bare-repository 2>/dev/null || true)" == true ]] || \
                fail "$root/git-cache is not the managed bare source cache"
            [[ "$(git --git-dir="$root/git-cache" remote get-url origin 2>/dev/null || true)" == \
                https://github.com/starprince1234/AutoGrader.git ]] || \
                fail "$root/git-cache has an unexpected origin"
        fi

        if [[ -e "$root/incoming" || -L "$root/incoming" ]]; then
            require_directory "$root/incoming" root root 755
            [[ -z "$(find "$root/incoming" -mindepth 1 -print -quit)" ]] || \
                fail "$root/incoming contains stale uploaded content; inspect it before deployment"
            [[ -z "$sha" ]] || fail "the legacy incoming directory must be removed before a server-side release"
        fi

        for directory in staging releases; do
            path="$root/$directory"
            if [[ -e "$path" || -L "$path" ]]; then
                require_directory "$path" root root 755
                if [[ -z "$sha" ]]; then
                    [[ -z "$(find "$path" -mindepth 1 -print -quit)" ]] || \
                        fail "$path contains data outside the active server-side release"
                fi
            fi
        done

        if [[ -n "$sha" ]]; then
            expected_stage="$root/staging/${sha}-${run_attempt}"
            [[ -d "$expected_stage" && ! -L "$expected_stage" ]] || \
                fail "the server-fetched staging directory is missing or not a real directory"
            unexpected_staging="$(find "$root/staging" -mindepth 1 -maxdepth 1 \
                ! -name "${sha}-${run_attempt}" -print -quit)"
            [[ -z "$unexpected_staging" ]] || fail "$root/staging contains an unexpected deployment"
            if [[ -d "$root/releases" ]]; then
                [[ -z "$(find "$root/releases" -mindepth 1 -print -quit)" ]] || \
                    fail "$root/releases contains a release without a current link"
            fi
        fi
    elif [[ -n "$sha" ]]; then
        fail "$root is missing before the server-side deployment"
    fi

    if systemctl cat autograder.service >/dev/null 2>&1; then
        [[ "$partial_compose_recovery" == true ]] || \
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
            [[ "$partial_compose_recovery" == true ]] || \
                fail "Docker volume $volume already exists without an AutoGrader current release"
        fi
    done
    compose_projects="$(docker compose ls --all --format json)" || fail "could not inspect existing Compose projects"
    compose_conflict="$(python3 -c 'import json,sys; projects=json.load(sys.stdin); print(str(any(item.get("Name") == "autograder" for item in projects)).lower())' <<< "$compose_projects")" || \
        fail "could not parse the existing Compose project list"
    [[ "$compose_conflict" != true || "$partial_compose_recovery" == true ]] || \
        fail "a Compose project named autograder already exists"
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
            [[ "$partial_compose_recovery" == true || \
                -z "$(find /var/lib/autograder/backups -mindepth 1 -print -quit)" ]] || \
                fail "/var/lib/autograder/backups contains data without a current release"
        fi
    fi
    if [[ -e /var/cache/autograder/uv || -L /var/cache/autograder/uv ]]; then
        [[ "$partial_first_deployment" == true ]] || \
            fail "/var/cache/autograder/uv exists without an AutoGrader current release"
        require_directory /var/cache/autograder/uv autograder autograder 750
        [[ "$partial_compose_recovery" == true || \
            -z "$(find /var/cache/autograder/uv -mindepth 1 -print -quit)" ]] || \
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
        if [[ "$partial_compose_recovery" == true && \
            ( "$port" == 25432 || "$port" == 25222 ) ]]; then
            continue
        fi
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
