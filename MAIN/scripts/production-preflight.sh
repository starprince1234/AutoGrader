#!/usr/bin/env bash
set -euo pipefail
fail() { printf '[autograder-preflight] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" == 0 ]] || fail "run as root"
for command_name in docker systemctl runuser uv nsjail cloudflared git flock tar timeout curl python3; do
    command -v "$command_name" >/dev/null || fail "missing command: $command_name"
done
[[ "$(stat -fc %T /sys/fs/cgroup)" == cgroup2fs ]] || fail "nsjail requires cgroup v2"
[[ -x /usr/bin/python3 ]] || fail "the sandbox runner needs /usr/bin/python3"
id autograder >/dev/null || fail "the autograder service account is missing"
id cloudflared >/dev/null || fail "the cloudflared service account is missing"
for config in runtime.env setup.env cloudflared.token; do
    path="/etc/autograder/$config"
    [[ -s "$path" && ! -L "$path" ]] || fail "missing configuration: $path"
    [[ "$(stat -c %u "$path")" == 0 ]] || fail "$path must be root-owned"
    mode="$(stat -c %a "$path")"
    (( (8#$mode & 0027) == 0 )) || fail "$path must not be group-writable or accessible to other users"
done
docker info >/dev/null
docker compose version >/dev/null
for service in postgres nats; do
    mapfile -t containers < <(docker ps \
        --filter label=com.docker.compose.project=autograder \
        --filter "label=com.docker.compose.service=$service" --format '{{.Names}}')
    [[ "${#containers[@]}" == 1 ]] || fail "one running $service container is required"
    if [[ "$service" == postgres ]]; then
        [[ "$(docker inspect --format '{{.State.Health.Status}}' "${containers[0]}")" == healthy ]] || \
            fail "PostgreSQL is not healthy"
    fi
done
printf '[autograder-preflight] host dependencies, configuration and infrastructure: ok\n'
