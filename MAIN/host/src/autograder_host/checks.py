from __future__ import annotations

import ast
import hashlib
import importlib
import json
import re
import sys
from pathlib import Path

import yaml
from autograder_platform import contract_digest as platform_contract_digest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]

#: The project's fixed status vocabulary. A status cell is identified by holding exactly one
#: of these, which keeps the comparison independent of each table's column count.
REQUIREMENT_STATUSES = ("骨架", "待实现", "已完成", "阻塞")
_REQUIREMENT = re.compile(r"`(RQ-B-[0-9]{1,3}-\d{2})`")
_REQUIREMENT_OWNER = re.compile(r"^RQ-B-([0-9]{1,3})-")


def _requirement_row(line: str) -> str | None:
    """The requirement id when it is the row's *first* cell, else None.

    Requiring the id to be first keeps this away from two other kinds of line that also
    contain ids: prose that cross-references another requirement, and the range-summary row
    in section 1 (`| 全体 | \\`RQ-B-0-10\\`～\\`RQ-B-0-30\\` | 3 |`), which carries no status.
    """
    if not line.lstrip().startswith("|"):
        return None
    cells = line.split("|")
    if len(cells) < 2:
        return None
    match = _REQUIREMENT.search(cells[1])
    return match.group(1) if match else None


def _status_word(cell: str) -> str | None:
    """The status word if `cell` is a status cell, else None.

    A module may qualify its status -- B4's `RQ-B-4-20` reads
    `已完成（配额为配置值，未做长时间计量——见下注）`, which is *more* honest than a bare
    `已完成`. The qualification is the owner's business and is never overwritten here.
    """
    stripped = cell.strip().strip("`* ")
    for candidate in REQUIREMENT_STATUSES:
        if stripped.startswith(candidate):
            return candidate
    return None


def module_requirement_statuses(module: str) -> dict[str, str]:
    """`{requirement: status}` exactly as the owning module's matrix declares it."""
    path = ROOT.parent / module / "docs" / "requirements" / "traceability.md"
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        requirement = _requirement_row(line)
        if requirement is None:
            continue
        identified = [
            item for item in (_status_word(cell) for cell in line.split("|")) if item
        ]
        if len(identified) != 1:
            raise ValueError(
                f"{path}: cannot identify exactly one status for {requirement}: {line!r}"
            )
        found.setdefault(requirement, identified[0])
    return found


def aggregated_requirement_statuses() -> dict[str, str]:
    """Merge every module's declaration; one source of truth per requirement."""
    merged: dict[str, str] = {}
    conflicts: list[str] = []
    for module in discovered_modules():
        for requirement, status in module_requirement_statuses(module).items():
            owner = _REQUIREMENT_OWNER.match(requirement)
            owner_module = f"B{owner.group(1)}" if owner else ""
            # Only the owner's declaration counts; modules legitimately cross-reference
            # `RQ-B-0-*` (全体) but must not redefine another module's requirement.
            if owner_module not in ("", "B0") and owner_module != module:
                continue
            if requirement in merged and merged[requirement] != status:
                conflicts.append(
                    f"{requirement}: {merged[requirement]} vs {status} (also declared by {module})"
                )
                continue
            merged.setdefault(requirement, status)
    if conflicts:
        raise ValueError(
            "modules disagree on requirement status: " + "; ".join(conflicts)
        )
    return merged


def main_declared_statuses() -> dict[str, str]:
    """Statuses MAIN declares for the cross-cutting requirements no single module owns.

    `RQ-B-0-20` ("toolchain/language/runtime match the course documents") is declared by **no
    module**: it is about the shared technology baseline, and MAIN owns that baseline
    (`../requirements/rq-catalog.md` §1's frozen table, `srs.md` §7). Rather than have this
    tool invent a value for it, those rows are read from MAIN's own matrix and excluded from
    drift detection -- MAIN is their owner, so comparing them against themselves would prove
    nothing. The matrix says so next to the table.
    """
    matrix = ROOT / "docs" / "requirements" / "traceability.md"
    declared: dict[str, str] = {}
    for line in matrix.read_text(encoding="utf-8").splitlines():
        requirement = _requirement_row(line)
        if requirement is None or not requirement.startswith("RQ-B-0-"):
            continue
        identified = [
            item for item in (_status_word(cell) for cell in line.split("|")) if item
        ]
        if len(identified) == 1:
            declared[requirement] = identified[0]
    return declared


def main_matrices() -> tuple[Path, ...]:
    """Every MAIN document that states a requirement status.

    There are two, and checking only one is how the second drifted for a whole phase:
    `traceability.md` is the global matrix, while `rq-catalog.md` §0 is the requirement
    *catalogue* -- the file whose own header calls it the authoritative catalogue, and
    therefore the first one a reviewer opens. `requirement_status_drift()` covered only the
    matrix, so the catalogue kept the Phase 00 snapshot in which all 32 requirements read
    `待实现`/`骨架` long after the modules had delivered, and additionally used `契约已锁定`,
    which is not one of `REQUIREMENT_STATUSES` at all.
    """
    requirements = ROOT / "docs" / "requirements"
    return (requirements / "traceability.md", requirements / "rq-catalog.md")


def requirement_status_drift() -> list[str]:
    """Requirements whose generated status in a MAIN matrix differs from the owner's.

    MAIN's matrices are a *view*, not a second source. The global matrix was hand-filled once
    during Phase 00 and never updated, so when every module had delivered, all 32 rows still
    read `骨架`/`待实现` -- the first document a reviewer opens claimed nothing was built. The
    column is now generated by `scripts/sync_requirement_status.py` and this function is what
    makes the generation mandatory rather than optional.

    Two properties are enforced per requirement row, in every matrix:

    * it carries **exactly one** legal status word -- so an invented value such as
      `契约已锁定` is a failure rather than something that quietly parses as "no status";
    * that word equals the owner's declaration, or MAIN's own declaration for `RQ-B-0-*`
      (which no module owns -- see `main_declared_statuses`).
    """
    statuses = aggregated_requirement_statuses()
    main_owned = main_declared_statuses()
    # MAIN-owned rows are compared against MAIN's own declaration rather than skipped. For
    # `traceability.md` -- where that declaration is read from -- this is a tautology, but for
    # `rq-catalog.md` it is a real comparison, which is the point.
    targets = {**statuses, **main_owned}

    drift: list[str] = []
    for matrix in main_matrices():
        name = matrix.name
        for line in matrix.read_text(encoding="utf-8").splitlines():
            requirement = _requirement_row(line)
            if requirement is None:
                continue
            identified = [
                item for item in (_status_word(cell) for cell in line.split("|")) if item
            ]
            if len(identified) > 1:
                raise ValueError(
                    f"{name}: cannot identify exactly one status for {requirement}: {line!r}"
                )
            if not identified:
                drift.append(
                    f"{name}: {requirement}: no legal status word "
                    f"(one of {'/'.join(REQUIREMENT_STATUSES)}) in {line.strip()!r}"
                )
                continue
            if requirement not in targets:
                drift.append(f"{name}: {requirement}: declared but by no owner")
                continue
            if identified[0] != targets[requirement]:
                drift.append(
                    f"{name}: {requirement}: states {identified[0]} but the owner "
                    f"declares {targets[requirement]}"
                )
    return drift


def check_decision_references() -> None:
    """Every cited ADR and CHG must actually exist somewhere in the repository.

    This defect happened twice, and both times it was invisible:

      * `ADR-0008` was cited by `host/src/autograder_host/main.py`, `B3/plugin.py`,
        `B4/plugin.py` and a B4 phase document to justify a configuration convention that had
        **no ADR at all**. The mentions are prose, so nothing checked them.
      * `docs/requirements/rq-catalog.md` pointed at `MAIN/docs/contracts/changes/CHG-B3-0001`
        and `CHG-B3-0003`, which live in the **B3** repository, not under that MAIN path. The
        references were in backticks rather than markdown links, so the link checker could not
        see them either.

    A dangling citation is not cosmetic: it means a reader chasing "why is it like this" finds
    nothing, and a decision that was never actually recorded looks recorded.

    Note the deliberate strictness: any `ADR-NNNN`-shaped token counts as a citation, so prose
    that *describes* this check must not spell out a nonexistent id -- the first version of the
    phase record did, and tripped it. Describe it obliquely ("an id that does not exist"), the
    same way `check_deprecated_b3_artifacts` requires for the removed requirement number.
    """
    adr_root = ROOT / "docs" / "adr"
    known_adrs = {path.name.split("-")[1] for path in adr_root.glob("ADR-*.md")}
    # A change id is `CHG-0008` (MAIN-owned) or `CHG-B3-0002` (module-owned), so the id ends at
    # the numeric group and the slug after it is not part of it.
    change_file = re.compile(r"^(CHG-(?:[A-Za-z0-9]+-)?\d+)")
    change_roots = [
        ROOT / "docs" / "contracts" / "changes",
        *(
            ROOT.parent / module / "docs" / "contracts" / "changes"
            for module in discovered_modules()
        ),
    ]
    known_changes = {
        match.group(1)
        for root in change_roots
        if root.is_dir()
        for change in root.glob("CHG-*.md")
        if (match := change_file.match(change.name))
    }

    adr_pattern = re.compile(r"ADR-(\d{4})")
    # `CHG-000*.md` is a glob used to describe the directory, not a citation of a change.
    change_pattern = re.compile(r"CHG-(?:[A-Za-z0-9]+-)?\d+(?![*\w])")
    problems: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".md", ".yaml", ".yml", ".json"}:
            continue
        if any(part in {"node_modules", ".venv", "__pycache__", ".runtime"} for part in path.parts):
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative = path.relative_to(ROOT.parent)
        for number in sorted(set(adr_pattern.findall(body))):
            if number not in known_adrs:
                problems.append(f"{relative}: cites ADR-{number}, which does not exist")
        for change in sorted(set(change_pattern.findall(body))):
            if change not in known_changes:
                problems.append(f"{relative}: cites {change}, which exists in no module")

    if problems:
        raise ValueError(
            "dangling decision references (a citation must resolve or the decision is not "
            "actually recorded):\n  " + "\n  ".join(problems)
        )


def check_route_declarations() -> None:
    """Contract paths and `module.yaml` routes must describe the same endpoint set.

    The two sides are not the same kind of string, which is the whole subtlety: a contract
    declares full paths (`/api/v1/auth/session`), while `module.yaml` declares **prefixes**
    (`/api/v1/auth`). Comparing them literally reported 27 false mismatches; the real relation
    is "is this path under that prefix".

    Two directions, both real:

      * a contract path under no declared prefix means the contract promises an endpoint nobody
        implements -- a client generated from it calls a 404;
      * a declared prefix with no contract path under it means a module reserves URL space the
        contract does not describe.

    `/health/*` is deliberately outside the contract: it is host-level liveness/readiness, not
    a module endpoint, so it appears in no `module.yaml` and is invisible to both directions.
    """
    contract_paths: dict[str, str] = {}
    for path in sorted((ROOT / "contracts" / "openapi").glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for route in (document.get("paths") or {}):
            contract_paths.setdefault(route, path.name)
    declared: dict[str, str] = {}
    for path in sorted((ROOT.parent).glob("B*/module.yaml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for route in manifest.get("routes") or []:
            declared.setdefault(route, manifest.get("module", path.parent.name))

    def under(candidate: str, prefix: str) -> bool:
        return candidate == prefix or candidate.startswith(prefix.rstrip("/") + "/")

    problems: list[str] = []
    for route, source in sorted(contract_paths.items()):
        if not any(under(route, prefix) for prefix in declared):
            problems.append(f"{source}: contract path {route} is under no module route prefix")
    for prefix, module in sorted(declared.items()):
        if not any(under(route, prefix) for route in contract_paths):
            problems.append(f"{module}/module.yaml: route prefix {prefix} covers no contract path")
    if problems:
        raise ValueError(
            "contract and module route declarations disagree:\n  " + "\n  ".join(problems)
        )


def discovered_modules() -> list[str]:
    """Every module the repository actually contains, in stable order.

    ADR-0013. Three of these gates used to iterate a literal `("B1", "B2", "B3", "B4")` while
    the manifest and route checks discovered modules with `glob("B*/module.yaml")`. The split
    was worse than being merely inconsistent: a new module was **loaded by the host and checked
    for contract ownership, but silently escaped documentation/encoding/port governance**.
    Silently ungoverned is more dangerous than loudly rejected, so the module list is now
    discovered once and used everywhere.
    """
    return sorted(
        path.parent.name for path in (ROOT.parent).glob("B*/module.yaml")
    )


def module_schema_owners() -> list[str]:
    """Modules that own a database schema (i.e. everything except pure clients)."""
    owners: list[str] = []
    for name in discovered_modules():
        manifest = yaml.safe_load((ROOT.parent / name / "module.yaml").read_text(encoding="utf-8"))
        if (manifest.get("database") or {}).get("schema", "none") != "none":
            owners.append(name)
    return owners


def check_module_descriptors() -> None:
    """Each module's runtime descriptor must agree with its `module.yaml`.

    Added after this exact drift bit: bumping the contract to 0.4.0 updated the four
    `module.yaml` files and the four locks, but the modules' **descriptors** still declared
    the old version. `checks.py` was happy, and the failure only appeared when the host tried
    to start -- B4's registry check raised
    `B2: module.yaml and the runtime descriptor disagree -- descriptor.contract_version='0.1.0'
    but module.yaml declares '0.4.0'`. A version that four files agree on and a fifth does not
    is exactly the "documented but untrue" class this project keeps hitting.

    The descriptor is read by **calling the module's declared factory**, not by regexing the
    source: `build_module()` constructs the descriptor without touching the database (only
    `router()` needs configuration), so this gives the real value rather than a guess about
    where it is written.
    """
    problems: list[str] = []
    for path in sorted((ROOT.parent).glob("B*/module.yaml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        module_name = manifest.get("module", path.parent.name)
        entry_point = str(manifest.get("entry_point", ""))
        if entry_point.startswith("static:"):
            # ADR-0007: B1 is a static client the host serves, not a plugin, so it has no
            # Python descriptor to compare against. This is the documented asymmetry (the
            # host does not even consume `static:`), not an exception invented here.
            continue
        if ":" not in entry_point:
            problems.append(f"{module_name}: module.yaml has no importable entry_point")
            continue
        target, function_name = entry_point.split(":", 1)
        try:
            module = importlib.import_module(target)
            factory = getattr(module, function_name)
            descriptor = factory().descriptor
        except Exception as error:  # noqa: BLE001 - any failure here is a real problem
            problems.append(f"{module_name}: cannot build descriptor from {entry_point}: {error}")
            continue
        declared = manifest.get("contract_version")
        if descriptor.contract_version != declared:
            problems.append(
                f"{module_name}: descriptor.contract_version={descriptor.contract_version!r}"
                f" but module.yaml declares {declared!r}"
            )
        if descriptor.module != module_name:
            problems.append(
                f"{module_name}: descriptor.module={descriptor.module!r} does not match the manifest"
            )
    if problems:
        raise ValueError(
            "module descriptors disagree with their manifests:\n  " + "\n  ".join(problems)
        )


#: A change record's status must begin with one of these. `已采纳` is deliberately distinct from
#: `已生效`: "ruled, not yet implemented" is exactly the state the audit found mis-described
#: (contracts already changed and relocked while every record still read "待判定"), so the two
#: must not be spelled the same way. The status is the leading word -- anything after it is the
#: author's qualifier and is preserved (`已采纳，实施排期在下一轮契约变更`).
CHANGE_STATUSES = ("提议", "已生效", "已采纳", "已回滚")


def _change_status(text: str) -> str | None:
    """The declared status of a change record, from its `- 状态：…` line.

    Two spellings exist in this repository and both are accepted: the older
    `- 状态：**已生效（判定：v4，改实现）**` and the newer `- **状态**：已裁决 / 已生效`.
    Only the leading status word is interpreted; the qualifier after it is the author's and is
    never rewritten here.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].replace("*", "").strip()
        if not body.startswith("状态"):
            continue
        _, _, value = body.partition("：")
        if not value:
            _, _, value = body.partition(":")
        cleaned = value.strip()
        for candidate in CHANGE_STATUSES:
            if cleaned.startswith(candidate):
                return candidate
    return None


def check_change_records() -> None:
    """Every change record must be findable, status-bearing, and reflected where it claims.

    Three real defects motivated this, all found by the integration audit:

      * `CHG-0001`…`0005` carried **no machine-readable status at all** -- their status lived
        only in a hand-written row of `changes/README.md`, so the file and the index were two
        copies that could disagree (and the index stopped at 0005 while five more records
        existed).
      * A change that **changed the contract** must appear in the contract CHANGELOG, because
        the CHANGELOG is what a contract consumer reads. Note the distinction the older records
        make and this check preserves: `已生效` does **not** imply a contract change --
        `CHG-0001`/`0002`/`0004`/`0005` were all ruled in effect by changing *implementation
        and documentation*, with `contracts/**` untouched. Only a declared version transition
        (`契约版本：A → B`) requires a CHANGELOG entry.
    """
    changes_root = ROOT / "docs" / "contracts" / "changes"
    index_path = changes_root / "README.md"
    index = index_path.read_text(encoding="utf-8")
    changelog = (ROOT / "contracts" / "CHANGELOG.md").read_text(encoding="utf-8")

    problems: list[str] = []
    records = sorted(changes_root.glob("CHG-*.md"))
    if not records:
        problems.append("no change records found")
    for path in records:
        change_id = path.name.split("-", 1)[0] + "-" + path.name.split("-", 2)[1]
        if not re.match(r"^CHG-(?:[A-Za-z0-9]+-)?\d+$", change_id):
            change_id = path.name[:9]
        text = path.read_text(encoding="utf-8")
        status = _change_status(text)
        if status is None:
            problems.append(
                f"{path.name}: no `- 状态：…` line (expected one of {CHANGE_STATUSES})"
            )
        if change_id not in index:
            problems.append(f"{path.name}: not listed in changes/README.md")
        # The version field is mandatory and fixed. An earlier version of this check accepted
        # any phrasing and only enforced the CHANGELOG direction when a record happened to
        # write `A → B`; `CHG-0007` wrote its version without an arrow, so the direction was
        # silently skipped and a probe that removed every mention from the CHANGELOG still
        # passed. A required field cannot be skipped by phrasing.
        # `\**` tolerates the markdown bold the label is written with (`- **契约版本**：…`),
        # which the first version of this regex missed on every single record.
        version_field = re.search(r"契约版本\**\s*[：:]\s*(.+)", text)
        if version_field is None:
            problems.append(
                f"{path.name}: no `- 契约版本：…` line (write `A → B` when the contract"
                " changed, or `A（未改契约）` when it did not)"
            )
            continue
        declared = version_field.group(1)
        transition = re.search(r"(\d+\.\d+\.\d+)\s*→\s*\**\s*(\d+\.\d+\.\d+)", declared)
        if transition and transition.group(1) != transition.group(2):
            if change_id not in changelog:
                problems.append(
                    f"{path.name}: declares {transition.group(1)} → {transition.group(2)}"
                    " but is not recorded in contracts/CHANGELOG.md"
                )
        elif "未改契约" not in declared and "未改" not in declared:
            problems.append(
                f"{path.name}: version field {declared.strip()!r} is neither a transition"
                " (`A → B`) nor marked as not changing the contract"
            )
    if problems:
        raise ValueError("change records are inconsistent:\n  " + "\n  ".join(problems))


def check_module_installations() -> None:
    """Each module's *installed* package must match its working tree.

    Found while proving the digest gate: that gate imports `autograder_b4.registry`, which
    resolves to `site-packages/autograder_b4/registry.py`, **not** to `B4/src/...`. Perturbing
    the source file therefore changed nothing, and the gate reported success. The general
    hazard is bigger than one check: with a stale install, every gate that imports module code
    verifies yesterday's bytes while the reviewer reads today's source.

    So the two are compared explicitly. A module whose installed copy differs from its tree is
    reported with the command that fixes it, because "the check passed" must not be true of a
    different revision than the one on disk.
    """
    problems: list[str] = []
    for module in discovered_modules():
        source_root = ROOT.parent / module / "src"
        if not source_root.is_dir():
            continue  # a static client such as B1 has no Python package to install
        # Only the module's own distribution package. B1's `src/` holds TypeScript feature
        # directories (`api/`, `app/`, `ui/`…), and sweeping those matched unrelated
        # site-packages entries -- including the standard library's `logging`, which produced a
        # nonsense "logging is stale" finding on the first run of this check.
        for package in sorted(
            path
            for path in source_root.iterdir()
            if path.is_dir() and path.name.startswith("autograder_")
        ):
            installed = None
            for candidate in sys.path:
                candidate_path = Path(candidate) / package.name
                if (candidate_path / "__init__.py").is_file():
                    installed = candidate_path
                    break
            if installed is None:
                problems.append(
                    f"{package.name}: not installed (run `uv sync --all-groups --extra modules`)"
                )
                continue
            source_files = {
                path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in package.rglob("*.py")
                if "__pycache__" not in path.parts
            }
            installed_files = {
                path.relative_to(installed).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in installed.rglob("*.py")
                if "__pycache__" not in path.parts
            }
            if source_files != installed_files:
                differing = sorted(
                    name
                    for name in set(source_files) | set(installed_files)
                    if source_files.get(name) != installed_files.get(name)
                )
                problems.append(
                    f"{package.name}: installed copy is stale or divergent"
                    f" ({', '.join(differing[:4])}{'…' if len(differing) > 4 else ''})"
                    f" -- run `uv sync --all-groups --extra modules"
                    f" --reinstall-package {package.name.replace('_', '-')}`"
                    f" (the `--extra modules` is not decorative: the four modules are installed"
                    f" *by* that extra, so without it uv concludes they are no longer required and"
                    f" uninstalls all of them, turning 'stale' into 'not installed')"
                )
    if problems:
        raise ValueError(
            "installed module packages do not match the working tree; every gate that imports"
            " module code would be verifying a different revision:\n  " + "\n  ".join(problems)
        )


def check_contract_digest_implementations() -> None:
    """Every module-side contract-digest implementation must equal the SDK's canonical value.

    The algorithm existed in four places (MAIN, B2 twice, B4) with nothing checking that they
    agreed. MAIN now delegates to `autograder_platform.contracts`, so there is one canonical
    implementation; this gate exists because the module copies have not been migrated yet, and
    an unverified copy is exactly the hazard: one divergent iteration order and a module keeps
    reporting "in sync" against a value MAIN never produces.

    The implementations are **called** rather than pattern-matched -- a regex over source would
    prove nothing about the value. When a module is migrated to import the SDK helper directly,
    its entry disappears from this list and the gate gets smaller on its own.
    """
    canonical = platform_contract_digest(ROOT / "contracts")
    contracts_root = ROOT / "contracts"
    targets = [
        ("B2 src/autograder_b2/contracts.py", "autograder_b2.contracts", "contract_digest"),
        ("B4 src/autograder_b4/registry.py", "autograder_b4.registry", "contract_digest"),
    ]
    problems: list[str] = []
    for label, module_name, function_name in targets:
        try:
            module = importlib.import_module(module_name)
            function = getattr(module, function_name)
        except Exception as error:  # noqa: BLE001 - a missing implementation is a finding too
            problems.append(f"{label}: cannot import {module_name}.{function_name}: {error}")
            continue
        try:
            produced = function(contracts_root)
        except TypeError:
            produced = function()
        if produced != canonical:
            problems.append(
                f"{label}: computes {produced[:16]}… but the canonical digest is"
                f" {canonical[:16]}…"
            )

    # A standalone verification script cannot be imported as a module; load it by path.
    script = ROOT.parent / "B2" / "verify" / "contract_lock_check.py"
    if script.is_file():
        try:
            spec = importlib.util.spec_from_file_location("_b2_contract_lock_check", script)
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            produced = module.contract_digest(contracts_root)
            if produced != canonical:
                problems.append(
                    f"B2 verify/contract_lock_check.py: computes {produced[:16]}… but the"
                    f" canonical digest is {canonical[:16]}…"
                )
        except Exception as error:  # noqa: BLE001
            problems.append(f"B2 verify/contract_lock_check.py: cannot evaluate: {error}")

    if problems:
        raise ValueError(
            "contract digest implementations disagree with the SDK (they must be byte-identical;"
            " migrate them to `autograder_platform.contract_digest`):\n  "
            + "\n  ".join(problems)
        )


def check_contract_examples() -> None:
    """Every example payload must be valid JSON *and* satisfy its owning schema.

    Checking the schema alone is not enough: an example can drift out of sync with
    the schema it documents and still be syntactically valid JSON. The mapping is
    explicit because example names describe the event, not the envelope schema.
    """
    examples = ROOT / "contracts" / "examples"
    schemas = ROOT / "contracts" / "schemas"
    owners = {
        "event-evaluation-completed.json": "event-envelope.schema.json",
        "submission-envelope.json": "submission-envelope.schema.json",
        "evaluation-package.json": "evaluation-package.schema.json",
        "evaluation-report.json": "evaluation-report.schema.json",
        "roster-import.json": "roster-import.schema.json",
    }
    for path in sorted(examples.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        schema_name = owners.get(path.name)
        if schema_name is None:
            raise ValueError(f"{path.name}: no owning schema declared in check_contract_examples()")
        schema = json.loads((schemas / schema_name).read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            raise ValueError(f"{path.name} does not satisfy {schema_name}: {errors[0].message}")
    missing = sorted(set(owners) - {p.name for p in examples.glob("*.json")})
    if missing:
        raise ValueError(f"declared examples are absent: {missing}")


def declared_contract_version() -> str:
    """The version the contract files themselves declare, all of which must agree.

    Added because the gate had a hole of the "checks presence, not truth" kind: it compared
    two *hand-maintained* fields (each `contracts.lock` and each `module.yaml`) against each
    other and never against the contracts. Both could therefore still say `0.1.0` while five
    contract files said `0.2.0`, and every gate stayed green — which is exactly what happened
    when CHG-0006 bumped the version. The version is now read from `info.version`, and the
    versions in the locks and manifests must match it.
    """
    found: dict[str, str] = {}
    for path in sorted((ROOT / "contracts").rglob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        version = (document.get("info") or {}).get("version")
        if version is not None:
            found[path.name] = str(version)
    if not found:
        raise ValueError("no contract file declares info.version")
    distinct = set(found.values())
    if len(distinct) != 1:
        raise ValueError(f"contract files disagree on info.version: {found}")
    return distinct.pop()


def contract_digest() -> str:
    """Delegate to the SDK's canonical implementation (see `autograder_platform.contracts`).

    This function used to hold its own copy of the algorithm. It was one of **four**
    (a second in B2's `contracts.py`, a third in `B2/verify/contract_lock_check.py`, a fourth
    in B4's `registry.py`) and nothing checked that the four agreed -- a hazard rather than a
    tidiness issue, because a single divergent iteration order would leave every module
    reporting "in sync" while comparing against a value MAIN never produces. MAIN now holds
    zero copies, and `check_contract_digest_implementations()` proves the module-side ones
    still agree with the canonical value.
    """
    return platform_contract_digest(ROOT / "contracts")


def descriptor_contract_version(module_directory: Path) -> str | None:
    """The `contract_version` a module hands the host in its `ModuleDescriptor`, read statically.

    Deliberately parsed rather than imported. `check_module_descriptors()` imports the *installed*
    packages, which is the right question at assembly time but blind during development: a module
    whose source and manifest were both updated without reinstalling still looks correct there,
    because both sides being compared come from the stale install. This reads the source, so a
    forgotten half of the update is caught before anything is installed.

    That gap is not hypothetical. Moving the contract to 0.11.0 (CHG-0016) updated the four locks
    and the five manifests, but each backend module *also* carries its own copy of the version as
    a literal in `plugin.py` -- fourteen places for one value, of which the descriptors were the
    ones missed. Nothing failed until a reinstall, at which point the host would simply refuse to
    assemble.

    Returns `None` for a module that ships no descriptor (B1 is a static SPA with no routes).
    """
    candidates = sorted((module_directory / "src").glob("autograder_*/plugin.py"))
    if not candidates:
        return None
    found: set[str] = set()
    for path in candidates:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name != "ModuleDescriptor":
                continue
            for keyword in node.keywords:
                if keyword.arg == "contract_version" and isinstance(keyword.value, ast.Constant):
                    found.add(str(keyword.value.value))
    if len(found) > 1:
        raise ValueError(
            f"{module_directory.name}: descriptors disagree on contract_version: {sorted(found)}"
        )
    return next(iter(found)) if found else None


def check_manifests() -> None:
    schema = json.loads((ROOT / "contracts" / "manifest.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    expected_version = declared_contract_version()
    routes: dict[str, str] = {}
    schemas: dict[str, str] = {}
    for path in sorted((ROOT.parent).glob("B*/module.yaml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        errors = sorted(validator.iter_errors(manifest), key=lambda error: list(error.path))
        if errors:
            raise ValueError(f"{path}: {errors[0].message}")
        module = manifest["module"]
        for route in manifest["routes"]:
            if route in routes:
                raise ValueError(f"route {route} owned by {routes[route]} and {module}")
            routes[route] = module
        schema_name = manifest["database"]["schema"]
        if schema_name != "none":
            if schema_name in schemas:
                raise ValueError(f"database schema {schema_name} owned by {schemas[schema_name]} and {module}")
            schemas[schema_name] = module
        lock_path = path.parent / "contracts.lock"
        if not lock_path.is_file():
            # A module without a lock used to crash this gate with a raw FileNotFoundError,
            # which reads like a bug in the checker rather than a missing artefact in the
            # module. Every module must pin the contract it was built against.
            raise ValueError(
                f"{lock_path} is missing: run `python MAIN/scripts/relock_contracts.py` "
                f"after creating {module}/module.yaml"
            )
        lock = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
        if lock.get("contract_version") != expected_version:
            raise ValueError(
                f"{lock_path}: contract_version {lock.get('contract_version')!r}"
                f" != contracts info.version {expected_version!r}"
            )
        if manifest["contract_version"] != expected_version:
            raise ValueError(
                f"{path}: contract_version {manifest['contract_version']!r}"
                f" != contracts info.version {expected_version!r}"
            )
        # The third copy of the same value, and the one that used to be ungated: the descriptor
        # the module hands the host. See `descriptor_contract_version`.
        descriptor_version = descriptor_contract_version(path.parent)
        if descriptor_version is not None and descriptor_version != expected_version:
            raise ValueError(
                f"{module}: the ModuleDescriptor in src/autograder_{module.lower()}/plugin.py"
                f" declares contract_version {descriptor_version!r} but contracts info.version is"
                f" {expected_version!r} -- update the descriptor too, not only the lock and"
                f" module.yaml. No reinstall can fix this; the running host would refuse to"
                f" assemble once the installed copy caught up."
            )
        if lock.get("digest") != contract_digest():
            raise ValueError(f"{lock_path}: contract digest mismatch")


def check_requirement_baseline() -> None:
    """The 32 course requirements must appear in MAIN's traceability matrix.

    The list is authoritative in docs/requirements/rq-catalog.md and mirrors
    MAIN/课程项目需求列表.xlsx (worksheet "AutoGrader作业代码自动评分系统").

    Team-authored increments (`RQ-T-*`, ruled by ADR-0009) are checked *separately* and must
    not be folded into the course count: the 32 is a fact about the assignment, and quietly
    growing it would make "32/32 delivered" unverifiable.
    """
    text = (ROOT / "docs" / "requirements" / "traceability.md").read_text(encoding="utf-8")
    expected = [
        *(f"RQ-B-0-{n:02d}" for n in (10, 20, 30)),
        *(f"RQ-B-1-{n:02d}" for n in (10, 20, 30, 40, 50, 60, 99)),
        *(f"RQ-B-2-{n:02d}" for n in (10, 20, 30, 40, 50, 60, 70, 80, 99)),
        *(f"RQ-B-3-{n:02d}" for n in (10, 20, 30, 40, 50, 60, 99)),
        *(f"RQ-B-4-{n:02d}" for n in (10, 20, 30, 40, 50, 99)),
    ]
    missing = [item for item in expected if item not in text]
    if missing:
        raise ValueError(f"missing requirement IDs: {missing}")

    # `RQ-T-*` has no external authority, so the only property worth asserting is that the
    # same set is declared in the catalogue, the matrix and here. A new one that reaches
    # only two of the three places is exactly the numbering drift this project forbids.
    catalogue = (ROOT / "docs" / "requirements" / "rq-catalog.md").read_text(encoding="utf-8")
    team_expected = ("RQ-T-10", "RQ-T-20", "RQ-T-30")
    drift = [item for item in team_expected if not (item in text and item in catalogue)]
    if drift:
        raise ValueError(
            "team-authored requirement missing from rq-catalog.md and/or traceability.md: "
            f"{drift}"
        )
    check_deprecated_b3_artifacts()
    # The status column is generated from the owning modules (see requirement_status_drift).
    # Checking it here is what makes the generation mandatory: the matrix claimed "nothing is
    # implemented" for as long as nobody looked, because a hand-filled column cannot notice
    # that the code moved on.
    status_drift = requirement_status_drift()
    if status_drift:
        raise ValueError(
            "requirement statuses in MAIN's matrix disagree with the owning modules "
            "(run `python MAIN/scripts/sync_requirement_status.py`): "
            + "; ".join(status_drift)
        )


def check_deprecated_b3_artifacts() -> None:
    """B3 must not re-declare or re-implement the removed scoring/evidence requirement.

    The pre-baseline B3 design owned scoring, evidence and reporting; ADR-0002 moved
    those to B2 and required the old B3 path to be deleted. Documentation *of that
    removal* is required by MAIN/docs/collaboration/module-doc-spec.md (a module must
    record what it deliberately does not do), so a narrative mention is history and is
    allowed. What is not allowed is the identifier coming back as a requirement, or
    appearing anywhere in the implementation surface:

      * source, manifest and migration files: no mention at all;
      * the traceability matrix: not as a requirement declaration (a table row or a
        line beginning with the identifier), while a prose sentence such as
        "this project has no RQ-B-3-70" stays legal;
      * compiled artifacts: no mention either. Deleting the .py files was NOT enough
        the first time this happened: the removed design survived as __pycache__/*.pyc
        containing EvaluationRun, evaluation_runs and the literal "evidence-scoring".
        Orphaned bytecode without forbidden content is harmless build residue and is
        deliberately not flagged here -- what is flagged is removed *design* still
        present on disk in any artifact surface.
    """
    b3 = ROOT.parent / "B3"
    implementation_terms = ("RQ-B-3-70", "evidence-scoring", "evaluation_runs")
    implementation_surfaces = [
        b3 / "module.yaml",
        *sorted((b3 / "src").rglob("*")),
        *sorted((b3 / "alembic").rglob("*")),
    ]
    for path in implementation_surfaces:
        if not path.is_file():
            continue
        if path.suffix == ".pyc":
            compiled = path.read_bytes()
            for term in implementation_terms:
                if term.encode() in compiled:
                    raise ValueError(
                        f"{path.relative_to(ROOT.parent).as_posix()}: "
                        f"deprecated B3 scoring artifact resurfaced in compiled bytecode "
                        f"({term}); the source was deleted but the .pyc was not"
                    )
            continue
        if "__pycache__" in path.parts:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for term in implementation_terms:
            if term in content:
                raise ValueError(
                    f"{path.relative_to(ROOT.parent).as_posix()}: "
                    f"deprecated B3 scoring artifact resurfaced ({term})"
                )

    traceability = b3 / "docs" / "requirements" / "traceability.md"
    if traceability.is_file():
        declaration = re.compile(r"^\s*[|\-*]?\s*`?(RQ-B-3-70)`?\s*[|\s]")
        for number, line in enumerate(traceability.read_text(encoding="utf-8").splitlines(), 1):
            if declaration.match(line):
                raise ValueError(
                    "B3/docs/requirements/traceability.md:"
                    f"{number}: deprecated requirement RQ-B-3-70 is declared again"
                )


def check_module_governance() -> None:
    """Every module project must expose its entry points and its documentation set.

    This is the documentation gate: a module that cannot be onboarded from its own
    directory (README + AGENTS + docs index) cannot be developed in parallel.
    """
    required_root = ["AGENTS.md", "README.md", "module.yaml", "contracts.lock", "docs/README.md"]
    required_docs = [
        "docs/requirements/scope.md",
        "docs/requirements/traceability.md",
        "docs/requirements/acceptance.md",
        "docs/architecture/overview.md",
        "docs/architecture/components.md",
        "docs/architecture/models.md",
        "docs/architecture/boundaries.md",
        "docs/api/contract.md",
        "docs/api/events.md",
        "docs/database/schema.md",
        "docs/database/migrations.md",
        "docs/testing/strategy.md",
        "docs/testing/cases.md",
        "docs/adr/README.md",
        "docs/runbooks/local-development.md",
        "docs/runbooks/troubleshooting.md",
        "docs/phases/active",
        "docs/phases/archive/README.md",
        "docs/collaboration/onboarding.md",
        "docs/collaboration/workpackages.md",
        "docs/collaboration/interfaces.md",
        "docs/collaboration/definition-of-done.md",
        "docs/collaboration/glossary.md",
        "docs/knowledge/README.md",
    ]
    problems: list[str] = []
    for module in discovered_modules():
        root = ROOT.parent / module
        for relative in required_root + required_docs:
            if not (root / relative).exists():
                problems.append(f"{module}/{relative} is missing")
        if len(list((root / "docs" / "phases" / "active").glob("*.md"))) < 1:
            problems.append(f"{module}/docs/phases/active has no phase record")
    if problems:
        raise ValueError("module documentation is incomplete: " + "; ".join(problems))


def check_document_encoding_and_links() -> None:
    """Markdown files must be readable UTF-8 and must not carry dead relative links."""
    problems: list[str] = []
    roots = [ROOT, *(ROOT.parent / name for name in discovered_modules())]
    link_pattern = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for root in roots:
        for path in sorted(root.rglob("*.md")):
            parts = set(path.parts)
            if parts & {"node_modules", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build", "taskone"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                problems.append(f"{path.relative_to(ROOT.parent).as_posix()}: not valid UTF-8 ({error})")
                continue
            if text.startswith("\ufeff"):
                problems.append(f"{path.relative_to(ROOT.parent).as_posix()}: UTF-8 BOM present")
            for target in link_pattern.findall(text):
                target = target.split("#", 1)[0].strip()
                if not target or target.startswith(("http://", "https://", "mailto:", "/", "#")):
                    continue
                if not (path.parent / target).exists():
                    problems.append(
                        f"{path.relative_to(ROOT.parent).as_posix()}: dead link -> {target}"
                    )
    if problems:
        raise ValueError("documentation link/encoding problems:\n  " + "\n  ".join(problems))


def check_no_replacement_characters() -> None:
    """No text artefact may contain U+FFFD, the Unicode replacement character.

    `check_document_encoding_and_links` verifies that a file *decodes* as UTF-8, but U+FFFD is
    itself valid UTF-8 -- so a file whose bytes were decoded with the wrong codec decodes
    perfectly and still carries a permanent record that something was destroyed. The gate said
    nothing while three of them sat in a MAIN document; they were found by reading, not by
    checking.

    The damage is unambiguous and the diagnosis is in the evidence: the character 经 is three
    UTF-8 bytes (`E7 BB 8F`) and it appeared as **three** replacement characters, so the bytes
    had been decoded one at a time. This project writes Chinese throughout, so U+FFFD is never
    intentional here -- there is no legitimate use to preserve, which is what makes it safe to
    treat as a hard error rather than a warning.

    Scope is wider than the link checker on purpose: the same corruption in a contract
    description, a schema title or a source comment is equally real, and
    `check_contract_parses_strictly` cannot see it either, because U+FFFD is valid YAML.
    """
    suffixes = {".md", ".yaml", ".yml", ".json", ".py", ".ts", ".tsx", ".css", ".sh", ".ps1"}
    skip = {
        "node_modules", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
        "dist", "build", "taskone", ".runtime", ".git", ".pytest-tmp",
    }
    problems: list[str] = []
    for root in [ROOT, *(ROOT.parent / name for name in discovered_modules())]:
        for path in sorted(root.rglob("*")):
            if set(path.parts) & skip:
                continue
            try:
                if not path.is_file() or path.suffix not in suffixes:
                    continue
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                # Two distinct cases, both handled by not raising here:
                #   * not readable UTF-8 -- the link checker's finding, not this one;
                #   * `OSError` from `is_file()` -- pytest leaves symlinks under
                #     `.pytest-tmp/` that Windows cannot stat (`WinError 1920`), and a scan
                #     that crashes on a build artefact is worse than one that skips it.
                continue
            if "\ufffd" in text:
                line = next(
                    (number for number, body in enumerate(text.splitlines(), 1) if "\ufffd" in body),
                    "?",
                )
                problems.append(
                    f"{path.relative_to(ROOT.parent).as_posix()}:{line}: U+FFFD replacement "
                    f"character present ({text.count(chr(0xFFFD))} occurrence(s)) -- the bytes "
                    f"were decoded with the wrong codec and content has been lost"
                )
    if problems:
        raise ValueError("replacement characters found:\n  " + "\n  ".join(problems))


def check_contract_parses_strictly() -> None:
    """Parse every contract file with duplicate mapping keys rejected.

    PyYAML -- used by the other checks -- resolves a duplicated mapping key silently with
    last-one-wins, so a contract can carry two `additionalProperties:` keys and still
    "load". js-yaml, which B1's OpenAPI type generator and most other tooling use, rejects
    it outright; the defect therefore only surfaced when B1 regenerated types.

    A validator more permissive than its consumers is worse than no validator: it
    certifies a file the consumers cannot read. This check keeps the gate at least as
    strict as the strictest consumer.
    """
    problems: list[str] = []

    class StrictLoader(yaml.SafeLoader):
        name = "<contract>"

    def _reject_duplicates(loader, node, deep=False):
        seen: set[object] = set()
        for key_node, _value in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                problems.append(
                    f"{loader.name}:{key_node.start_mark.line + 1}: "
                    f"duplicated mapping key {key!r}"
                )
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep)

    StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _reject_duplicates
    )

    for path in sorted((ROOT / "contracts").rglob("*")):
        if not path.is_file() or path.suffix not in {".yaml", ".yml", ".json"}:
            continue
        loader = StrictLoader(path.read_text(encoding="utf-8"))
        loader.name = path.relative_to(ROOT.parent).as_posix()
        try:
            loader.get_single_data()
        except yaml.YAMLError as error:
            problems.append(f"{loader.name}: {str(error).splitlines()[0]}")
        finally:
            loader.dispose()

    if problems:
        raise ValueError(
            "contract files are not strictly parseable:\n  " + "\n  ".join(problems)
        )


#: Value sets that the contracts must state in more than one document.
#:
#: OpenAPI cannot `$ref` into a JSON Schema file under `schemas/` (a different dialect in a
#: different document), so any vocabulary that both surfaces have to name is necessarily written
#: twice. `language` already was written twice before this check existed; `CHG-0019` adds
#: `failure_category` for the same reason.
#:
#: Nothing else in the gate tree compares the two copies. A one-sided edit would therefore drift
#: silently, and the failure mode is nasty: a payload the schema document permits would be
#: rejected by anyone validating against the OpenAPI document, and each document would look
#: internally consistent. This check turns the duplication into an enforced invariant, so that
#: `CHG-0019`'s promise to "reuse the existing failure-category vocabulary rather than invent one"
#: is a fact a tool checks rather than a claim a reviewer has to trust.
#:
#: Each entry is (document A, path within it, document B, path within it). Both sides are loaded
#: by suffix, so a pair may be OpenAPI-to-JSON-Schema (the usual case, since OpenAPI cannot `$ref`
#: into `schemas/*.json`) or OpenAPI-to-OpenAPI (`CHG-0019` had to repeat B3's `adapter_kind`
#: rather than cross-reference `b3.yaml`, because B2's contract tests resolve refs from
#: `common.yaml` only and a cross-module `$ref` would break them).
#: **When a new vocabulary is duplicated, add its pair here in the same change.**
SHARED_VOCABULARIES: tuple[
    tuple[str, tuple[str, ...], str, tuple[str, ...]], ...
] = (
    (
        "openapi/common.yaml",
        ("components", "schemas", "Language", "enum"),
        "schemas/evaluation-package.schema.json",
        ("properties", "language", "enum"),
    ),
    (
        "openapi/b2.yaml",
        (
            "components", "schemas", "ReferenceVerification",
            "properties", "cases", "items", "properties", "failure_category", "anyOf", 0, "enum",
        ),
        "schemas/evaluation-report.schema.json",
        ("properties", "case_evidence", "items", "properties", "failure_category", "enum"),
    ),
    (
        "openapi/b2.yaml",
        ("components", "schemas", "ReferenceVerification", "properties", "adapter_kind", "enum"),
        "openapi/b3.yaml",
        ("components", "schemas", "QuestionVersionPublic", "properties", "adapter_kind", "enum"),
    ),
    (
        "openapi/b3.yaml",
        ("components", "schemas", "ReferenceAnswer", "properties", "adapter_kind", "enum"),
        "openapi/b3.yaml",
        ("components", "schemas", "QuestionVersionPublic", "properties", "adapter_kind", "enum"),
    ),
)


def _load_contract_document(name: str) -> object:
    """Load a contract document by suffix: `.json` as JSON, anything else as YAML."""
    text = (ROOT / "contracts" / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else yaml.safe_load(text)


def _descend(document: object, path: tuple[str, ...], where: str) -> object:
    """Walk a mapping/index path, naming the first missing step rather than raising `KeyError`."""
    cursor = document
    for step in path:
        if isinstance(step, int):
            if not isinstance(cursor, list) or step >= len(cursor):
                raise ValueError(f"{where}: no value at {'.'.join(str(p) for p in path)}")
            cursor = cursor[step]
            continue
        if not isinstance(cursor, dict) or step not in cursor:
            raise ValueError(f"{where}: no value at {'.'.join(str(p) for p in path)}")
        cursor = cursor[step]
    return cursor


def check_shared_vocabularies() -> None:
    """Every duplicated value set agrees across the documents that declare it.

    Nullability is compared separately from the value set, and the reason is *not* a dialect
    limitation: these documents are OpenAPI 3.1, which does allow `null` as an `enum` member. It is
    that the two sides may legitimately spell the same nullability differently -- a JSON Schema can
    list `null` inside `enum`, while an OpenAPI schema written for tooling that predates 3.1 uses
    `anyOf: [ ..., {type: "null"} ]`. Comparing literally would report that difference as a
    vocabulary disagreement, and a gate that cries wolf gets silenced. Nullability is therefore
    stripped and the remaining members must match exactly; whether each side *can* express a null
    member is a separate question this check does not adjudicate.
    """
    problems: list[str] = []

    for first_name, first_path, second_name, second_path in SHARED_VOCABULARIES:
        first_values = _descend(
            _load_contract_document(first_name), first_path, first_name
        )
        second_values = _descend(
            _load_contract_document(second_name), second_path, second_name
        )
        if not isinstance(first_values, list) or not isinstance(second_values, list):
            problems.append(
                f"{first_name} vs {second_name}: both sides must be lists, got "
                f"{type(first_values).__name__} and {type(second_values).__name__}"
            )
            continue

        first_members = {value for value in first_values if value is not None}
        second_members = {value for value in second_values if value is not None}
        if first_members == second_members:
            continue

        label = f"{first_name} vs {second_name} at {'.'.join(str(p) for p in second_path)}"
        if missing := second_members - first_members:
            label += f"; declared by the second document but not the first: {sorted(missing)}"
        if extra := first_members - second_members:
            label += f"; declared by the first document but not the second: {sorted(extra)}"
        problems.append(label)

    if problems:
        raise ValueError(
            "a duplicated contract vocabulary has drifted between its documents:\n  "
            + "\n  ".join(problems)
        )


def check_local_configuration_consistency() -> None:
    """Local configuration must agree with the manifest and the compose ports.

    Two drifts have already cost real debugging time in this project:
      * module.yaml advertised a migration_head that no Alembic revision matched, so
        "the head disagrees with the manifest" could never be detected; and
      * module .env.example / alembic.ini pointed at the container ports (5432, 4222)
        while compose publishes different host ports, so following the module docs
        produced a connection failure.
    Both are mechanical, so they are checked rather than trusted.
    """
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    published: dict[str, str] = {}
    for service, spec in (compose.get("services") or {}).items():
        for mapping in spec.get("ports") or []:
            host_side = str(mapping).partition(":")[0].strip()
            if host_side.isdigit():
                published[service] = host_side

    problems: list[str] = []
    for module in module_schema_owners():
        base = ROOT.parent / module

        revisions: dict[str, str] = {}
        referenced: set[str] = set()
        for path in sorted((base / "alembic" / "versions").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            revision = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', text, re.M)
            down = re.search(r'^down_revision\s*=\s*["\']([^"\']+)["\']', text, re.M)
            if revision:
                revisions[revision.group(1)] = path.name
            if down:
                referenced.add(down.group(1))
        heads = sorted(set(revisions) - referenced)
        manifest = yaml.safe_load((base / "module.yaml").read_text(encoding="utf-8"))
        declared = manifest["database"]["migration_head"]
        if len(heads) != 1:
            problems.append(f"{module}: expected exactly one Alembic head, found {heads or 'none'}")
        elif declared != heads[0]:
            problems.append(
                f"{module}/module.yaml declares migration_head={declared} "
                f"but the Alembic head is {heads[0]} ({revisions[heads[0]]})"
            )

        for name in (".env.example", "alembic.ini"):
            path = base / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"@localhost:(\d+)/", text):
                if "postgres" in published and match.group(1) != published["postgres"]:
                    problems.append(
                        f"{module}/{name}: PostgreSQL host port {match.group(1)} "
                        f"does not match compose ({published['postgres']})"
                    )
            for match in re.finditer(r"nats://localhost:(\d+)", text):
                if "nats" in published and match.group(1) != published["nats"]:
                    problems.append(
                        f"{module}/{name}: NATS host port {match.group(1)} "
                        f"does not match compose ({published['nats']})"
                    )

    if problems:
        raise ValueError("local configuration drift:\n  " + "\n  ".join(problems))


def check_test_evidence() -> None:
    """The suite totals the docs quote must describe the contract the repository is on now.

    Phase and strategy documents quote suite totals ("1145 passed", "23 files / 133 tests"), and
    those numbers are the main evidence that anything works. They are also hand-copied prose, so
    nothing noticed the case that actually happened: the contract moved from 0.7.0 to 0.16.0, the
    suites were never re-recorded, and the quoted totals silently described a revision that no
    longer exists -- a wrong number that stays plausible, which is worse than an obviously stale
    one. `scripts/test_evidence.py` records each run bound to the contract version and digest it
    was measured against; this gate is what makes that record binding.

    The script was written *for* this gate and then no gate ran it, which is precisely the failure
    mode it exists to catch: a mechanism with no enforcement point is indistinguishable from no
    mechanism, and `--check` reported five stale/missing modules to nobody for the whole interval.
    """
    script = ROOT / "scripts" / "test_evidence.py"
    # No is_file() guard and no except, unlike the B2 script loaded above: this file is MAIN's
    # own and always present, so failing to load it is a gate failure rather than a reason to
    # silently skip the check.
    spec = importlib.util.spec_from_file_location("_main_test_evidence", script)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    problems = module.evidence_problems()
    if problems:
        raise ValueError(
            "test evidence is missing or stale (run MAIN/scripts/test_evidence.py --all):\n  "
            + "\n  ".join(problems)
        )


def main() -> None:
    check_contract_examples()
    check_contract_parses_strictly()
    check_shared_vocabularies()
    for path in sorted((ROOT / "contracts" / "schemas").glob("*.json")):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
    check_manifests()
    check_module_installations()
    check_module_descriptors()
    check_contract_digest_implementations()
    check_requirement_baseline()
    check_decision_references()
    check_change_records()
    check_route_declarations()
    check_module_governance()
    check_document_encoding_and_links()
    check_no_replacement_characters()
    check_local_configuration_consistency()
    check_test_evidence()
    print(f"contracts, manifests and requirements: ok ({contract_digest()})")
    print("module documentation governance and links: ok")
    print("migration heads and local ports agree with compose: ok")
    print("test evidence is current and green for every module at this contract: ok")


if __name__ == "__main__":
    main()
