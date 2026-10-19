# AutoGrader composition host image for local integration and contract verification.
#
# Runtime note: every Python package is installed **editable**. B4 resolves `alembic.ini`,
# `alembic/` and its module root from `__file__` (`autograder_b4.db.engine.alembic_script_head`,
# `autograder_b4.plugin.module_root_of`), so a wheel copied into site-packages would break
# the migration-head probe and the operator commands. The source tree *is* the installation.

FROM python:3.12-slim

WORKDIR /app

# B2 persists submission artifacts outside the database; the workspace holds student code
# while a sandbox runs. Both are mounted in `compose.yaml` (artifacts are data, the
# workspace is scratch), so the mount points must exist here.
RUN mkdir -p /var/lib/autograder/artifacts /var/lib/autograder/workspace

# The image root is the module root: the runtime registry reads `B*/module.yaml`, each
# `B*/contracts.lock` and the `MAIN/contracts` digest (B4 `registry.py`), the composition
# host discovers the three plugins through their `autograder.modules` entry points, and the
# modules' Alembic environments live beside their `src/`.
COPY MAIN/sdk /app/MAIN/sdk
COPY MAIN/contracts /app/MAIN/contracts
COPY MAIN/host /app/MAIN/host
COPY B2 /app/B2
COPY B3 /app/B3
COPY B4 /app/B4

# `B1/module.yaml` and `B1/contracts.lock` are not part of the SPA build output, but the
# registry validates the *declared* module set, so the built stage cannot supply them.
COPY B1/module.yaml B1/contracts.lock /app/B1/

RUN pip install --no-cache-dir \
        -e /app/MAIN/sdk \
        -e /app/MAIN/host \
        -e /app/B2 \
        -e /app/B3 \
        -e /app/B4

COPY MAIN/host/entrypoint.sh MAIN/host/wait_for_postgres.py MAIN/host/write_jwks.py /app/MAIN/host/
RUN chmod +x /app/MAIN/host/entrypoint.sh

EXPOSE 8000

# Migrations, key generation and role provisioning happen here, never in the request path.
# The same script is the image's contract with whoever deploys it: see
# `MAIN/docs/runbooks/container-deployment.md`.
ENTRYPOINT ["/app/MAIN/host/entrypoint.sh"]
