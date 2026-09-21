"""核验 B2 `contracts.lock` 与 `MAIN/contracts/**` 实时摘要是否一致（只读，不修改任何文件）。

摘要算法与 MAIN 官方实现逐字节一致（`MAIN/host/src/autograder_host/checks.py: contract_digest()`）：
对 `contracts/` 下所有文件按相对路径 POSIX 字符串排序，依次 update(相对路径) → update(b"\\0") → update(文件字节)，
取 SHA-256 十六进制。本工具不复制契约字段，只复算摘要。

用法：
    uv run python verify/contract_lock_check.py

退出码：0 = 一致；1 = 漂移（契约被改动或锁未同步）；2 = 环境缺失。
"""

from __future__ import annotations

import hashlib
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = (REPO_ROOT / ".." / "MAIN" / "contracts").resolve()
LOCK_PATH = REPO_ROOT / "contracts.lock"
_LOCK_LINE = re.compile(r"^([a-z_]+):\s*(.*)$")


def contract_digest(contracts_root: pathlib.Path) -> str:
    """MAIN 官方摘要算法；参数为 contracts 目录。"""
    digest = hashlib.sha256()
    for relative in sorted(p.relative_to(contracts_root).as_posix() for p in contracts_root.rglob("*") if p.is_file()):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((contracts_root / relative).read_bytes())
    return digest.hexdigest()


def read_lock(lock_path: pathlib.Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        match = _LOCK_LINE.match(line.strip())
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


def main() -> int:
    if not CONTRACTS_ROOT.is_dir():
        print(f"contracts root not found: {CONTRACTS_ROOT.name}")
        return 2
    if not LOCK_PATH.is_file():
        print("contracts.lock not found")
        return 2
    lock = read_lock(LOCK_PATH)
    live = contract_digest(CONTRACTS_ROOT)
    locked = lock.get("digest", "")
    print(f"contract_version : {lock.get('contract_version', '')}")
    print(f"source           : {lock.get('source', '')}")
    print(f"digest_policy    : {lock.get('digest_policy', '')}")
    print(f"lock_digest      : {locked}")
    print(f"live_digest      : {live}")
    print(f"file_count       : {sum(1 for p in CONTRACTS_ROOT.rglob('*') if p.is_file())}")
    print(f"MATCH            : {live == locked}")
    return 0 if live == locked else 1


if __name__ == "__main__":
    raise SystemExit(main())
