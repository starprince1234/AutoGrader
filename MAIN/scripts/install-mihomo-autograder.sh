#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() {
    printf '[mihomo-autograder] ERROR: %s\n' "$*" >&2
    exit 1
}

[[ "$(id -u)" -eq 0 ]] || fail "run this installer as root"
[[ -d /run/systemd/system ]] || fail "systemd is not the active init system"

archive="${1:-}"
config_source="${2:-}"
unit_source="${3:-}"
[[ -n "$archive" && -n "$config_source" && -n "$unit_source" ]] || \
    fail "usage: $0 <mihomo-linux-amd64-compatible-v1.19.32.gz> <mihomo-config.yaml> <mihomo-autograder.service>"

for command_name in gzip sha256sum awk install systemctl useradd id getent ss grep openssl curl stat mktemp sleep; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is missing: $command_name"
done

[[ -f "$archive" && ! -L "$archive" ]] || fail "the Mihomo release archive is missing or is a symlink"
[[ -f "$config_source" && ! -L "$config_source" ]] || fail "the Mihomo config is missing or is a symlink"
[[ -f "$unit_source" ]] || fail "mihomo-autograder.service is missing beside this installer"

expected_sha256=ba3ce607747a07f948fc35780e108a4a7c7f552a38b9bd4d115f313ebcb89c20
actual_sha256="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$actual_sha256" == "$expected_sha256" ]] || fail "the Mihomo archive checksum does not match the official v1.19.32 Linux amd64-compatible release"
gzip -t "$archive" >/dev/null 2>&1 || fail "the Mihomo release archive is corrupt"

binary=/usr/local/bin/mihomo-autograder
config_dir=/etc/mihomo-autograder
config_file="$config_dir/config.yaml"
state_dir=/var/lib/mihomo-autograder
unit_file=/etc/systemd/system/mihomo-autograder.service

[[ ! -e "$binary" && ! -L "$binary" ]] || fail "$binary already exists; inspect it before replacing anything"
[[ ! -e "$config_dir" && ! -L "$config_dir" ]] || fail "$config_dir already exists; inspect it before replacing anything"
[[ ! -e "$state_dir" && ! -L "$state_dir" ]] || fail "$state_dir already exists; inspect it before replacing anything"
[[ ! -e "$unit_file" && ! -L "$unit_file" ]] || fail "$unit_file already exists; inspect it before replacing anything"
if id mihomo-autograder >/dev/null 2>&1 || getent group mihomo-autograder >/dev/null 2>&1; then
    fail "the mihomo-autograder account or group already exists"
fi

for port in 7890 7891 7892 7893 9090; do
    if ss -H -ltn "sport = :$port" | grep -q .; then
        fail "TCP port $port is already in use"
    fi
done

temporary_dir="$(mktemp -d /var/tmp/mihomo-autograder.XXXXXX)"
trap 'rm -rf -- "$temporary_dir"' EXIT
install -d -o root -g root -m 0700 "$temporary_dir/state"
gzip -dc -- "$archive" > "$temporary_dir/mihomo"
chmod 0700 "$temporary_dir/mihomo"
if grep -Eq '^[[:space:]]*secret[[:space:]]*:' "$config_source"; then
    fail "the supplied config already defines a controller secret; inspect it before installation"
fi
controller_secret="$(openssl rand -hex 32)"
{
    printf 'secret: "%s"\n' "$controller_secret"
    cat "$config_source"
} > "$temporary_dir/config.yaml"
unset controller_secret
if ! "$temporary_dir/mihomo" -t -d "$temporary_dir/state" -f "$temporary_dir/config.yaml" \
    >"$temporary_dir/config-check.log" 2>&1; then
    fail "Mihomo rejected the supplied config; diagnostic output was withheld because the file contains proxy credentials"
fi

useradd --system --user-group --home-dir "$state_dir" --shell /sbin/nologin mihomo-autograder
install -d -o root -g mihomo-autograder -m 0750 "$config_dir"
install -o root -g mihomo-autograder -m 0640 "$temporary_dir/config.yaml" "$config_file"
install -o root -g root -m 0755 "$temporary_dir/mihomo" "$binary"
install -o root -g root -m 0644 "$unit_source" "$unit_file"

systemctl daemon-reload
systemctl start mihomo-autograder.service

ready=false
for attempt in {1..30}; do
    if systemctl is-active --quiet mihomo-autograder.service && \
        ss -H -ltn "sport = :7893" | grep -q .; then
        ready=true
        break
    fi
    sleep 1
done
[[ "$ready" == true ]] || fail "Mihomo did not become active on local mixed-port 7893; inspect its service journal"

if ! curl --fail --silent --show-error --proxy http://127.0.0.1:7893 \
    --connect-timeout 5 --max-time 20 --output /dev/null \
    https://www.gstatic.com/generate_204; then
    fail "Mihomo is listening locally, but the outbound proxy check failed; the service remains available for diagnosis"
fi

systemctl enable mihomo-autograder.service
printf '[mihomo-autograder] active and enabled; local HTTP proxy is 127.0.0.1:7893\n'
