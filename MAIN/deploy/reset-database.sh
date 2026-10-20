#!/usr/bin/env bash
# One-time destructive operation, never called by normal deployment.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { printf 'Run as root.\n' >&2; exit 1; }
release="$(readlink -f "${1:?usage: reset-database.sh /opt/autograder/releases/<release>}")"
[[ "$release" == /opt/autograder/releases/* && -x "$release/MAIN/.venv/bin/python" ]]
set -a
source /etc/autograder/setup.env
source /etc/autograder/runtime.env
set +a
export B4_MODULE_ROOT="$release"
mapfile -t containers < <(docker ps \
    --filter label=com.docker.compose.project=autograder \
    --filter label=com.docker.compose.service=postgres --format '{{.Names}}')
[[ "${#containers[@]}" == 1 ]]

# Stop producers/consumers before deleting the three application schemas.
systemctl stop autograder.service
docker exec -i "${containers[0]}" sh -c \
    'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
BEGIN;
DROP SCHEMA IF EXISTS b2 CASCADE;
DROP SCHEMA IF EXISTS b3 CASCADE;
DROP SCHEMA IF EXISTS b4 CASCADE;
CREATE SCHEMA b2;
CREATE SCHEMA b3;
CREATE SCHEMA b4;
COMMIT;
SQL
# Reapply the same schema/role bootstrap used by an empty PostgreSQL volume.
docker exec "${containers[0]}" bash /docker-entrypoint-initdb.d/001-autograder-bootstrap.sh
runuser --preserve-environment --user autograder -- \
    env HOME=/var/lib/autograder USER=autograder LOGNAME=autograder \
    bash "$release/MAIN/scripts/production-prepare.sh"
systemctl start autograder.service
for attempt in $(seq 1 30); do
    if curl --fail --silent --max-time 5 http://127.0.0.1:18000/health/ready \
        | "$release/MAIN/.venv/bin/python" "$release/MAIN/deploy/check-readiness.py"; then
        printf 'Application schemas recreated and API is ready. Create the initial admin account separately.\n'
        exit 0
    fi
    sleep 2
done
printf 'Application schemas recreated, but the API did not become ready.\n' >&2
exit 1
