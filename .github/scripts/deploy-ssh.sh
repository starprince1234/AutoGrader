#!/usr/bin/env bash
set -euo pipefail

action="${1:?expected ssh or scp}"
shift

: "${DEPLOY_SSH_HOST:?}"
: "${DEPLOY_SSH_USER:?}"
: "${DEPLOY_SSH_PASSWORD:?}"
: "${DEPLOY_SSH_PORT:?}"
: "${AUTOGRADER_SSH_KNOWN_HOSTS:?}"

export SSHPASS="$DEPLOY_SSH_PASSWORD"
ssh_options=(
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$AUTOGRADER_SSH_KNOWN_HOSTS"
  -o HostKeyAlgorithms=ssh-ed25519
  -o PreferredAuthentications=password
  -o PubkeyAuthentication=no
  -o ConnectTimeout=60
)

case "$action" in
  ssh)
    exec sshpass -e ssh -p "$DEPLOY_SSH_PORT" "${ssh_options[@]}" "$@"
    ;;
  scp)
    exec sshpass -e scp -P "$DEPLOY_SSH_PORT" "${ssh_options[@]}" "$@"
    ;;
  *)
    printf 'Unsupported SSH transport action: %s\n' "$action" >&2
    exit 64
    ;;
esac
