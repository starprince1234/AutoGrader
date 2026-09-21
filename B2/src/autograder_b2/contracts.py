"""只读访问 MAIN 契约（`../MAIN/contracts/**`）与 B2 契约锁。

契约权威在 MAIN：本模块**只读**契约文件，绝不复制、绝不修改、绝不写回。B2 内部形态见
[docs/architecture/interfaces.md](../../docs/architecture/interfaces.md)；本模块只提供
"按契约校验"与"复算契约摘要"两件工具，不在此定义任何契约字段。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from functools import cache
from typing import Any

from autograder_platform.contracts import deployment_root

#: Where the deployment keeps `B2/` and `MAIN/contracts`. Resolved through the SDK so the
#: answer is the same one B4's registry and B5's conformance endpoint use -- and so it stays
#: correct when this package is imported from an installed copy rather than the source tree.
#:
#: Deriving it here from `__file__` (`parents[2] / ".."`) looked equivalent and was not: under
#: `site-packages` that resolves to `<venv>/MAIN/contracts`, which does not exist, so every
#: `contract_schema_errors` call -- submission intake, report building, event publication --
#: raised `ContractUnavailableError` in the integrated host while the unit suite (running from
#: the working tree) stayed green.
_DEPLOYMENT_ROOT = deployment_root()
B2_ROOT = (
    _DEPLOYMENT_ROOT / "B2"
    if _DEPLOYMENT_ROOT is not None
    else pathlib.Path(__file__).resolve().parents[2]
)
MAIN_CONTRACTS_ROOT = (B2_ROOT / ".." / "MAIN" / "contracts").resolve()
CONTRACTS_LOCK_PATH = B2_ROOT / "contracts.lock"

SUBMISSION_ENVELOPE_SCHEMA = "schemas/submission-envelope.schema.json"
EVALUATION_PACKAGE_SCHEMA = "schemas/evaluation-package.schema.json"
EVALUATION_REPORT_SCHEMA = "schemas/evaluation-report.schema.json"
EVENT_ENVELOPE_SCHEMA = "schemas/event-envelope.schema.json"

_LOCK_LINE = re.compile(r"^([a-z_]+):\s*(.*)$")


class ContractUnavailableError(RuntimeError):
    """契约文件或契约锁不可读（环境缺失），不是业务失败。"""


def contract_digest(contracts_root: pathlib.Path | None = None) -> str:
    """MAIN 官方契约摘要算法（与 `MAIN/host/src/autograder_host/checks.py` 逐字节一致）。

    相对路径 POSIX 排序 → update(相对路径) → update(b"\\0") → update(文件字节) → SHA-256。
    """
    root = MAIN_CONTRACTS_ROOT if contracts_root is None else pathlib.Path(contracts_root)
    if not root.is_dir():
        raise ContractUnavailableError(f"contracts root missing: {root.name}")
    digest = hashlib.sha256()
    relatives = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    for relative in relatives:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((root / relative).read_bytes())
    return digest.hexdigest()


def read_contract_lock(lock_path: pathlib.Path | None = None) -> dict[str, str]:
    """读取 B2 `contracts.lock` 的键值（只读，不解析嵌套结构）。"""
    path = CONTRACTS_LOCK_PATH if lock_path is None else pathlib.Path(lock_path)
    if not path.is_file():
        raise ContractUnavailableError("contracts.lock missing")
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LOCK_LINE.match(line.strip())
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


@cache
def load_contract_schema(relative_path: str) -> dict[str, Any]:
    """按相对路径读取 MAIN 契约 JSON Schema（只读；缓存以免重复解析）。

    返回值是**共享的缓存对象**：调用方只可读，不得原地修改（契约只读是硬规则）。
    """
    path = MAIN_CONTRACTS_ROOT / relative_path
    if not path.is_file():
        raise ContractUnavailableError(f"contract schema missing: {relative_path}")
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def _validator(relative_path: str) -> Any:
    from jsonschema import Draft202012Validator, FormatChecker

    return Draft202012Validator(load_contract_schema(relative_path), format_checker=FormatChecker())


def contract_schema_errors(relative_path: str, instance: object) -> list[str]:
    """返回契约校验错误（按路径排序的稳定字符串）；空列表表示通过契约校验。"""
    errors = sorted(_validator(relative_path).iter_errors(instance), key=lambda e: list(e.path))
    return [f"{'/'.join(str(part) for part in error.path)}: {error.message}" for error in errors]


def contract_digest_matches_lock(lock_path: pathlib.Path | None = None) -> bool:
    """B2 契约锁摘要是否等于 MAIN 契约实时摘要（契约未被偷改的机器判据）。"""
    return contract_digest() == read_contract_lock(lock_path).get("digest", "")
