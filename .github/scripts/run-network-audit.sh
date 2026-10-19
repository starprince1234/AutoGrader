#!/usr/bin/env bash
set -euo pipefail

for name in \
    DEPLOY_SSH_HOST DEPLOY_SSH_USER DEPLOY_SSH_PASSWORD DEPLOY_SSH_PORT \
    AUTOGRADER_SSH_KNOWN_HOSTS GITHUB_TOKEN TARGET_SHA BASE_SHA AUTOGRADER_REPO_URL \
    GITHUB_RUN_ID GITHUB_RUN_ATTEMPT; do
    [[ -n "${!name:-}" ]] || {
        printf '[network-audit] ERROR: required environment value %s is missing\n' "$name" >&2
        exit 1
    }
done

[[ "$GITHUB_RUN_ID" =~ ^[0-9]+$ && "$GITHUB_RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]] || {
    printf '[network-audit] ERROR: invalid GitHub run identity\n' >&2
    exit 1
}

remote_script_dir="/tmp/autograder-network-audit-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
remote_created=false
cleanup() {
    local status=$?
    trap - EXIT
    if [[ "$remote_created" == true ]]; then
        bash .github/scripts/deploy-ssh.sh ssh "$DEPLOY_SSH_USER@$DEPLOY_SSH_HOST" \
            bash -s -- "$remote_script_dir" <<'REMOTE_CLEANUP' || true
set -euo pipefail
[[ "$1" =~ ^/tmp/autograder-network-audit-[0-9]+-[1-9][0-9]*$ ]]
[[ -d "$1" && ! -L "$1" ]]
for name in network-fetch-audit.sh network-fetch-audit-runner.sh; do
    path="$1/$name"
    if [[ -e "$path" || -L "$path" ]]; then
        [[ -f "$path" && ! -L "$path" ]]
        rm -f -- "$path"
    fi
done
rmdir -- "$1"
REMOTE_CLEANUP
    fi
    exit "$status"
}
trap cleanup EXIT

[[ ! -e "$remote_script_dir" && ! -L "$remote_script_dir" ]] || {
    printf '[network-audit] ERROR: isolated server audit path already exists\n' >&2
    exit 1
}
bash .github/scripts/deploy-ssh.sh ssh "$DEPLOY_SSH_USER@$DEPLOY_SSH_HOST" \
    install -d -o root -g root -m 0700 "$remote_script_dir"
remote_created=true

bash .github/scripts/deploy-ssh.sh scp \
    .github/scripts/network-fetch-audit.sh \
    .github/scripts/network-fetch-audit-runner.sh \
    "$DEPLOY_SSH_USER@$DEPLOY_SSH_HOST:$remote_script_dir/"

{ printf '%s\n' "$GITHUB_TOKEN"; } | \
    bash .github/scripts/deploy-ssh.sh ssh "$DEPLOY_SSH_USER@$DEPLOY_SSH_HOST" \
        bash "$remote_script_dir/network-fetch-audit-runner.sh" \
        "$remote_script_dir/network-fetch-audit.sh" \
        "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" "$TARGET_SHA" "$BASE_SHA" \
        "$AUTOGRADER_REPO_URL"
