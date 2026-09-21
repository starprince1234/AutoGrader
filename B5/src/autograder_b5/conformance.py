"""The three views and the differences between them.

The report answers one question: *where does the running composition differ from what
`MAIN/contracts/**` declares?* Three views are compared, and each difference names the side
that is missing something -- a count cannot be acted on, so no count is returned.

1. **contract** -- the paths declared in `MAIN/contracts/openapi/*.yaml` (`contracts.py`);
2. **host** -- the routes the assembled application serves that no loaded module declares
   (`/health/live`, `/health/ready`, the `/app` mount, FastAPI's own `/docs`). These are
   deliberately *outside* the contract: they are not module endpoints, so their absence
   from the contract is not a difference;
3. **modules** -- the route prefixes each loaded module declares in its descriptor, in load
   order (the capability DAG order).

Both directions are real failures of the same property:

* a contract path under no module prefix means the contract promises an endpoint nobody
  implements -- a generated client calls a 404;
* a module prefix with no contract path under it means a module reserves URL space the
  contract does not describe.

`consistent` is true only when the differences list is empty. Note the asymmetry with
MAIN's static gate `check_route_declarations()`: the gate *raises*, because a build must not
proceed on a broken composition; this report *returns* the disagreement, because an
inconsistency is exactly what a caller came to read.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from autograder_platform import ModuleDescriptor
from pydantic import BaseModel, ConfigDict, Field

from .contracts import ContractPath, contract_paths

CONTRACT_PATH_UNSERVED = "contract_path_unserved"
DESCRIPTOR_ROUTE_UNDECLARED = "descriptor_route_undeclared"
DETAIL_LIMIT = 500

DifferenceKind = Literal["contract_path_unserved", "descriptor_route_undeclared"]


class Difference(BaseModel):
    """One disagreement between two of the three views."""

    model_config = ConfigDict(extra="forbid")

    kind: DifferenceKind
    detail: str = Field(min_length=1, max_length=DETAIL_LIMIT)


class ConformanceReport(BaseModel):
    """`MAIN/contracts/openapi/b5.yaml` -> `ConformanceReport`."""

    model_config = ConfigDict(extra="forbid")

    consistent: bool
    loaded_modules: list[str]
    contract_path_count: int = Field(ge=0)
    differences: list[Difference]


def under(candidate: str, prefix: str) -> bool:
    """Whether `candidate` is `prefix` itself or lies below it.

    The two sides are not the same kind of string: a contract declares full paths
    (`/api/v1/auth/session`) while `module.yaml` and the descriptor declare **prefixes**
    (`/api/v1/auth`). Comparing them literally reported 27 false mismatches in MAIN's gate
    before `check_route_declarations()` was written, so the relation is containment rather
    than equality on both sides of this module too.
    """
    return candidate == prefix or candidate.startswith(prefix.rstrip("/") + "/")


def platform_routes(
    application_paths: Iterable[str], declared_prefixes: Iterable[str]
) -> tuple[str, ...]:
    """Routes the host serves itself: assembled paths no loaded module declares."""
    prefixes = tuple(declared_prefixes)
    return tuple(
        sorted(
            {
                path
                for path in application_paths
                if not any(under(path, prefix) for prefix in prefixes)
            }
        )
    )


def build_report(
    contracts_dir: Path,
    descriptors: Sequence[ModuleDescriptor],
    application_paths: Iterable[str],
) -> ConformanceReport:
    """Compare the three views and describe every disagreement."""
    declared: tuple[ContractPath, ...] = contract_paths(contracts_dir)
    prefixes = [route for descriptor in descriptors for route in descriptor.routes]
    host_routes = platform_routes(application_paths, prefixes)

    differences: list[Difference] = []
    for entry in declared:
        if any(under(entry.path, prefix) for prefix in prefixes):
            continue
        if any(under(entry.path, route) for route in host_routes):
            continue
        differences.append(
            Difference(
                kind=CONTRACT_PATH_UNSERVED,
                detail=(
                    f"{entry.document} declares {entry.path}, which no loaded module's "
                    "declared route covers"
                ),
            )
        )
    for descriptor in descriptors:
        for route in descriptor.routes:
            if any(under(entry.path, route) for entry in declared):
                continue
            differences.append(
                Difference(
                    kind=DESCRIPTOR_ROUTE_UNDECLARED,
                    detail=(
                        f"{descriptor.module} declares route {route}, but no path in "
                        "MAIN/contracts/openapi declares it"
                    ),
                )
            )

    return ConformanceReport(
        consistent=not differences,
        loaded_modules=[descriptor.module for descriptor in descriptors],
        contract_path_count=len(declared),
        differences=differences,
    )
