#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '[autograder-bootstrap] ERROR: %s\n' "$*" >&2
    exit 1
}

[ "$(stat -fc %T /sys/fs/cgroup)" = "cgroup2fs" ] || \
    fail "nsjail requires a unified cgroup v2 hierarchy"

if command -v nsjail >/dev/null 2>&1; then
    exit 0
fi

dnf install -y \
    bison \
    flex \
    gcc-c++ \
    git \
    libcap-devel \
    libnl3-devel \
    libseccomp-devel \
    make \
    protobuf-compiler \
    protobuf-devel

source_dir="$(mktemp -d /usr/local/src/nsjail.XXXXXX)"
trap 'rm -rf "$source_dir"' EXIT

git -c advice.detachedHead=false clone --depth 1 --branch 3.4 \
    https://github.com/google/nsjail.git "$source_dir"
make -C "$source_dir" -j"$(nproc)"
install -o root -g root -m 0755 "$source_dir/nsjail" /usr/local/bin/nsjail

command -v nsjail >/dev/null 2>&1 || fail "nsjail installation did not produce an executable"
