#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '[cloudflared-bootstrap] ERROR: %s\n' "$*" >&2
    exit 1
}

case "$(uname -m)" in
    x86_64) package_name=cloudflared-linux-x86_64.rpm ;;
    aarch64) package_name=cloudflared-linux-aarch64.rpm ;;
    *) fail "unsupported server architecture: $(uname -m)" ;;
esac

temporary_dir="$(mktemp -d /usr/local/src/cloudflared.XXXXXX)"
trap 'rm -rf "$temporary_dir"' EXIT

curl --fail --location --silent --show-error --retry 3 \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/$package_name" \
    --output "$temporary_dir/cloudflared.rpm"
dnf install -y "$temporary_dir/cloudflared.rpm"
command -v cloudflared >/dev/null 2>&1 || fail "cloudflared installation did not produce an executable"
