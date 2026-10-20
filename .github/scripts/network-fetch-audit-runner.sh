#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" != 6 ]]; then
    printf '[network-audit] ERROR: invalid runner arguments\n' >&2
    exit 64
fi

audit_script="$1"
shift
[[ "$audit_script" =~ ^/tmp/autograder-network-audit-[0-9]+-[1-9][0-9]*/network-fetch-audit\.sh$ ]] || {
    printf '[network-audit] ERROR: audit script path is outside its isolated directory\n' >&2
    exit 64
}
[[ -f "$audit_script" && ! -L "$audit_script" ]] || {
    printf '[network-audit] ERROR: audit script is missing or is not a regular file\n' >&2
    exit 66
}

exec bash "$audit_script" "$@"
