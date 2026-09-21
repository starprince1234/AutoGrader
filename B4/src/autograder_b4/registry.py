"""The runtime module registry (RQ-B-4-40).

Design-time authority over the interfaces belongs to MAIN (`MAIN/contracts/**`,
ADR-0003). The *runtime* half -- discovering what the host actually loaded, and proving
that it agrees with each module's `module.yaml` and SDK `descriptor` -- belongs to B4 and
is implemented here.

The registry reports what is true rather than asserting it in prose, so disagreement is a
loud failure in all three directions:

* a module loaded without a manifest, or a manifest the module contradicts;
* a capability required by a loaded module that nothing provides;
* the same route prefix claimed twice;
* a contract digest that no longer matches the locked baseline.

Validation runs while the host is wiring routers, so drift stops startup instead of
producing a running system whose registry quietly lies.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from .logging import get_logger

logger = get_logger("registry")

MODULE_GROUP = "autograder.modules"
CONTRACTS_DIRECTORY = Path("MAIN") / "contracts"
BASE_CAPABILITY = "host"


class RegistryError(RuntimeError):
    """Registry drift: the message names the module and the exact disagreement."""


class _Described(Protocol):
    descriptor: object


@dataclass(frozen=True)
class ModuleManifest:
    """The machine-readable declaration in `B*/module.yaml`."""

    module: str
    contract_version: str
    entry_point: str
    provides: tuple[str, ...]
    requires: tuple[str, ...]
    routes: tuple[str, ...]
    publishes: tuple[str, ...]
    subscribes: tuple[str, ...]
    database_schema: str
    migration_head: str
    path: Path

    @property
    def events(self) -> tuple[str, ...]:
        """Union of both directions; the SDK descriptor is flat, so only this is comparable."""
        return tuple(sorted(set(self.publishes) | set(self.subscribes)))


@dataclass(frozen=True)
class RegisteredModule:
    """One module as the registry reports it (the OpenAPI `RegisteredModule` shape)."""

    module: str
    contract_version: str
    provides: list[str]
    requires: list[str]
    routes: list[str]
    publishes: list[str]
    subscribes: list[str]
    database_schema: str
    migration_head: str
    healthy: bool
    detail: str = ""

    def as_document(self) -> dict[str, object]:
        return {
            "module": self.module,
            "contract_version": self.contract_version,
            "provides": self.provides,
            "requires": self.requires,
            "routes": self.routes,
            "publishes": self.publishes,
            "subscribes": self.subscribes,
            "database_schema": self.database_schema,
            "migration_head": self.migration_head,
            "healthy": self.healthy,
            "detail": self.detail,
        }


def load_manifest(path: Path) -> ModuleManifest:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RegistryError(f"{path}: module.yaml is not a mapping")
    database = document.get("database") or {}
    events = document.get("events") or {}
    return ModuleManifest(
        module=str(document["module"]),
        contract_version=str(document["contract_version"]),
        entry_point=str(document["entry_point"]),
        provides=tuple(document.get("provides") or ()),
        requires=tuple(document.get("requires") or ()),
        routes=tuple(document.get("routes") or ()),
        publishes=tuple(events.get("publishes") or ()),
        subscribes=tuple(events.get("subscribes") or ()),
        database_schema=str(database.get("schema", "none")),
        migration_head=str(database.get("migration_head", "none")),
        path=path,
    )


def discover_manifests(module_root: Path) -> dict[str, ModuleManifest]:
    """Every `B*/module.yaml` under the repository root, keyed by module name."""
    manifests: dict[str, ModuleManifest] = {}
    for path in sorted(module_root.glob("B*/module.yaml")):
        manifest = load_manifest(path)
        manifests[manifest.module] = manifest
    if not manifests:
        raise RegistryError(f"no B*/module.yaml manifests found under {module_root}")
    return manifests


def contract_digest(contracts_root: Path) -> str:
    """SHA-256 over the whole contracts tree.

    This mirrors MAIN's own `contract_digest()` (host/src/autograder_host/checks.py)
    byte for byte -- sorted relative POSIX paths, a NUL separator, then the bytes -- so
    that the value B4 computes is comparable with the one MAIN locked into each
    `contracts.lock`. `tests/test_registry.py` asserts the mirror equals the digests MAIN
    recorded, which is what makes this a check rather than a restatement.
    """
    digest = hashlib.sha256()
    relative_paths = sorted(
        path.relative_to(contracts_root).as_posix()
        for path in contracts_root.rglob("*")
        if path.is_file()
    )
    for relative in relative_paths:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((contracts_root / relative).read_bytes())
    return digest.hexdigest()


def verify_contract_lock(manifest: ModuleManifest, module_root: Path) -> None:
    """The manifest must agree with the module's own contract lock, and the lock with reality."""
    lock_path = manifest.path.parent / "contracts.lock"
    if not lock_path.is_file():
        raise RegistryError(f"{manifest.module}: contracts.lock is missing")
    lock = yaml.safe_load(lock_path.read_text(encoding="utf-8")) or {}
    if lock.get("contract_version") != manifest.contract_version:
        raise RegistryError(
            f"{manifest.module}: module.yaml declares contract_version "
            f"{manifest.contract_version} but contracts.lock declares "
            f"{lock.get('contract_version')!r}"
        )
    contracts_root = module_root / CONTRACTS_DIRECTORY
    if not contracts_root.is_dir():
        raise RegistryError(f"{manifest.module}: contracts tree not found at {contracts_root}")
    live = contract_digest(contracts_root)
    recorded = str(lock.get("digest") or "")
    if recorded and recorded != live:
        raise RegistryError(
            f"{manifest.module}: contract digest drift -- contracts.lock records {recorded} "
            f"but the live contract tree hashes to {live}"
        )


def descriptor_view(descriptor: object) -> dict[str, object]:
    """Read the SDK descriptor without depending on its concrete class."""
    return {
        "module": str(descriptor.module),
        "contract_version": str(descriptor.contract_version),
        "provides": tuple(getattr(descriptor, "provides", ()) or ()),
        "requires": tuple(getattr(descriptor, "requires", ()) or ()),
        "routes": tuple(getattr(descriptor, "routes", ()) or ()),
        "events": tuple(getattr(descriptor, "events", ()) or ()),
    }


def compare_descriptor_to_manifest(manifest: ModuleManifest, descriptor: object) -> list[str]:
    """Return the specific disagreements between a runtime descriptor and its manifest."""
    view = descriptor_view(descriptor)
    problems: list[str] = []
    if view["module"] != manifest.module:
        problems.append(
            f"descriptor.module={view['module']!r} but module.yaml declares {manifest.module!r}"
        )
    if view["contract_version"] != manifest.contract_version:
        problems.append(
            f"descriptor.contract_version={view['contract_version']!r} but module.yaml declares "
            f"{manifest.contract_version!r}"
        )
    for field in ("provides", "requires", "routes"):
        from_descriptor = sorted(view[field])
        from_manifest = sorted(getattr(manifest, field))
        if tuple(from_descriptor) != tuple(from_manifest):
            problems.append(
                f"{field}: descriptor={from_descriptor} module.yaml={from_manifest}"
            )
    descriptor_events = set(view["events"])
    manifest_events = set(manifest.events)
    unexpected = descriptor_events - manifest_events
    if unexpected:
        problems.append(
            f"descriptor.events declares {sorted(unexpected)} which module.yaml does not"
        )
    missing = manifest_events - descriptor_events
    if missing:
        problems.append(f"descriptor.events omits {sorted(missing)} declared in module.yaml")
    return problems


def resolve_declared_modules(
    environ: dict[str, str] | None = None,
) -> list[object]:
    """Resolve the modules the host is about to load, and return their descriptors.

    The host discovers modules through the `autograder.modules` entry-point group and the
    `AUTOGRADER_MODULES` override. Its entry-point *name* is not the module identity --
    B4's entry point is named `b4` while its manifest says `B4` -- so the registry resolves
    each declaration to a real descriptor and compares on `descriptor.module`. That is the
    same object the host will load, and it is already imported by the time B4's router is
    built, so this costs no extra import.

    A declaration name that appears twice is a **loud failure** (ADR-0013 decision 5). The
    host resolves the group into a dict keyed by entry-point name, so a duplicate silently
    replaces one descriptor with the other -- and which one survived depended on the
    discovery order, while this function used to keep the first. Two opposite answers for
    the same input is how an audit scenario got a real descriptor swapped out.
    """
    source = os.environ if environ is None else environ
    import importlib.metadata

    descriptors: list[object] = []
    seen_modules: set[str] = set()
    seen_declarations: set[str] = set()

    def _register(label: str, factory) -> None:
        try:
            module = factory()
        except Exception as error:
            raise RegistryError(
                f"{label} is declared as a loadable module but could not be constructed: "
                f"{type(error).__name__}: {error}"
            ) from error
        descriptor = getattr(module, "descriptor", None)
        if descriptor is None:
            raise RegistryError(f"{label} does not expose a ModuleDescriptor")
        name = str(getattr(descriptor, "module", ""))
        if not name:
            raise RegistryError(f"{label} exposes a descriptor without a module name")
        if label in seen_declarations:
            raise RegistryError(
                f"{label} is declared twice as a loadable module; which declaration is loaded "
                "would depend on discovery order, so the registry refuses to choose"
            )
        seen_declarations.add(label)
        if name not in seen_modules:
            seen_modules.add(name)
            descriptors.append(descriptor)

    try:
        entries = list(importlib.metadata.entry_points(group=MODULE_GROUP))
    except Exception as error:
        raise RegistryError(
            f"the {MODULE_GROUP!r} entry-point index could not be read: {type(error).__name__}"
        ) from error
    for entry in entries:
        # `EntryPoint.load()` returns the *factory* the entry point names
        # (`autograder_b4.plugin:create_module`), so it has to be called once more to get
        # the module object -- matching what the host's loader does.
        _register(str(entry.name), _entry_factory(entry))

    configured = (source.get("AUTOGRADER_MODULES") or "").strip()
    if configured:
        for spec in configured.split(","):
            spec = spec.strip()
            if not spec or ":" not in spec:
                continue
            module_name, function_name = spec.split(":", 1)
            _register(spec, _import_factory(module_name.strip(), function_name.strip()))
    return descriptors


def _import_factory(module_name: str, function_name: str):
    def _factory():
        module = __import__(module_name, fromlist=[function_name])
        return getattr(module, function_name)()

    return _factory


def _entry_factory(entry):
    def _factory():
        return entry.load()()

    return _factory


def validate_registry(
    module_root: Path,
    *,
    loaded: Sequence[object] = (),
    declared: Iterable[object] | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, ModuleManifest]:
    """Validate manifest/descriptor/capability/route/digest consistency.

    Raises `RegistryError` naming the module and the disagreement. Called while the host
    wires routers, so a drift aborts startup rather than being served.
    """
    manifests = discover_manifests(module_root)
    descriptors = list(declared) if declared is not None else resolve_declared_modules(environ)

    by_name: dict[str, object] = {}
    for module in [*descriptors, *loaded]:
        descriptor = getattr(module, "descriptor", module)
        view = descriptor_view(descriptor)
        by_name[view["module"]] = descriptor

    for name, descriptor in sorted(by_name.items()):
        manifest = manifests.get(name)
        if manifest is None:
            raise RegistryError(
                f"{name} is declared as a loadable module but has no module.yaml under "
                f"{module_root}; the registry cannot describe it"
            )
        problems = compare_descriptor_to_manifest(manifest, descriptor)
        if problems:
            raise RegistryError(
                f"{name}: module.yaml and the runtime descriptor disagree -- " + "; ".join(problems)
            )

    _assert_unique_routes(manifests, by_name)
    _assert_capability_closure(manifests, by_name)

    for manifest in manifests.values():
        verify_contract_lock(manifest, module_root)

    logger.info(
        "registry.validated",
        extra={
            "event": "registry.validated",
            "manifests": sorted(manifests),
            "loaded": sorted(by_name),
        },
    )
    return manifests


def _assert_unique_routes(manifests: dict[str, ModuleManifest], loaded: dict[str, object]) -> None:
    owners: dict[str, str] = {}
    for name in sorted(loaded):
        manifest = manifests[name]
        for route in manifest.routes:
            if route in owners:
                raise RegistryError(
                    f"duplicate route {route!r} declared by {owners[route]} and {name}"
                )
            owners[route] = name
    # Manifests that are present but not loaded still must not overlap each other: two
    # modules claiming one prefix is a design conflict regardless of what is installed.
    declared_owners: dict[str, str] = {}
    for name, manifest in sorted(manifests.items()):
        for route in manifest.routes:
            if route in declared_owners and declared_owners[route] != name:
                raise RegistryError(
                    f"duplicate route {route!r} declared by {declared_owners[route]} and {name} "
                    f"in their module.yaml files"
                )
            declared_owners[route] = name


def _assert_capability_closure(
    manifests: dict[str, ModuleManifest], loaded: dict[str, object]
) -> None:
    provided = {BASE_CAPABILITY}
    for manifest in manifests.values():
        provided.update(manifest.provides)
    # A capability required by a module that is actually going to run must be provided by
    # something in this composition; otherwise "plug and play" would start and then fail
    # at the first call.
    for name in sorted(loaded):
        manifest = manifests.get(name)
        if manifest is None:
            continue
        missing = sorted(set(manifest.requires) - provided)
        if missing:
            raise RegistryError(f"{name} requires capabilities that no module provides: {missing}")


def build_registry(
    *,
    manifests: dict[str, ModuleManifest],
    loaded: Sequence[object],
    health_lookup: dict[str, tuple[bool, str]] | None = None,
) -> list[RegisteredModule]:
    """Describe the modules the host loaded, enriched from their manifests."""
    health = health_lookup or {}
    described: dict[str, object] = {}
    for module in loaded:
        descriptor = getattr(module, "descriptor", module)
        described[str(descriptor.module)] = descriptor

    registry: list[RegisteredModule] = []
    for name in sorted(described):
        manifest = manifests.get(name)
        if manifest is None:
            raise RegistryError(
                f"{name} was loaded but has no module.yaml; the registry refuses to guess"
            )
        problems = compare_descriptor_to_manifest(manifest, described[name])
        if problems:
            raise RegistryError(
                f"{name}: registry drift detected at read time -- " + "; ".join(problems)
            )
        healthy, detail = health.get(name, (True, ""))
        registry.append(
            RegisteredModule(
                module=name,
                contract_version=manifest.contract_version,
                provides=list(manifest.provides),
                requires=list(manifest.requires),
                routes=list(manifest.routes),
                publishes=list(manifest.publishes),
                subscribes=list(manifest.subscribes),
                database_schema=manifest.database_schema,
                migration_head=manifest.migration_head,
                healthy=healthy,
                detail=detail,
            )
        )
    return registry


def manifest_migration_heads(manifests: dict[str, ModuleManifest]) -> dict[str, str]:
    """`module -> migration_head`, for the cross-check against the live databases."""
    return {name: manifest.migration_head for name, manifest in manifests.items()}
