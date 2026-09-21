"""内容寻址存储（CAS）：以 SHA-256 为去重键，先写内容后写元数据。

不变量（ADR-0003 + `docs/architecture/interfaces.md` I2/I3）：

1. **路径不可注入**：存储路径只由摘要派生（`storage.paths.storage_path_for`），不接受任何
   客户端路径；写入前还会断言派生结果落在根目录内。
2. **内容寻址去重**：同一内容只写一次文件、只留一行 `artifacts`（`uq_artifacts_content_sha256`）。
3. **先内容、后元数据**：文件先落盘（临时文件 + `os.replace` 原子替换）；元数据写入失败只留下
   **可回收的孤儿内容**，不会产生"有元数据无内容"的坏状态。
4. **提交文件路径白名单**：写 `submission_files` 之前对每个 `path` 施加 I2 白名单；数据库层的
   CHECK 是第三道防线。
"""

from __future__ import annotations

import os
import pathlib
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.postgresql import insert as pg_insert

from autograder_b2.db.schema import artifacts, submission_files
from autograder_b2.domain.clock import is_digest, sha256_hex, utc_now
from autograder_b2.domain.envelope import (
    EnvelopeFile,
    EnvelopeRejected,
    assert_allowed_relative_path,
)
from autograder_b2.storage.paths import absolute_path_under, storage_path_for


class ArtifactNotFoundError(LookupError):
    """元数据与内容都不存在。"""


class ArtifactContentMissingError(RuntimeError):
    """元数据存在但内容文件缺失——存储与数据库不一致，必须显式暴露而不是返回空内容。"""


class ArtifactIntegrityError(RuntimeError):
    """读回的内容摘要与元数据不符（内容被篡改或写坏）。"""


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """一次内容写入的结果；`deduplicated` 表示命中了既有内容（不改变语义）。"""

    artifact_id: str
    content_sha256: str
    size_bytes: int
    storage_path: str
    deduplicated: bool


def _holds_expected_content(target: pathlib.Path, digest: str) -> bool:
    """目标文件是否**已经就是这份摘要对应的内容**（并发写入的判定依据）。

    摘要相同 ⇒ 字节必然相同，因此"另一个写入者已经写好了同一份内容"与"我写成功"在语义上等价。
    读取失败（文件刚好被删、权限问题等）按"不是"处理，让上层抛原始错误。
    """
    try:
        return target.is_file() and sha256_hex(target.read_bytes()) == digest
    except OSError:
        return False


#: 并发写入同一摘要时的**有界重试**：Windows 上 `os.replace` 撞上并发句柄会抛 WinError 5，
#: 而重试窗口不只"目标已存在"这一刻——另一个写入者可能正在 rename 途中（彼时 `is_file()` 仍为假）。
#: 内容由摘要决定，所以"再等一会儿，内容就位即成功"永远是对的；上限保证不会无限等待。
_CONCURRENT_WRITE_ATTEMPTS = 8
_CONCURRENT_WRITE_BACKOFF_SECONDS = 0.01


class ContentAddressedStore:
    """内容寻址存储。`root` 由部署配置给出（构造参数，非隐式环境读取）。"""

    def __init__(self, root: pathlib.Path | str, engine: Engine) -> None:
        self._root = pathlib.Path(root)
        self._engine = engine

    @property
    def root(self) -> pathlib.Path:
        return self._root

    @property
    def engine(self) -> Engine:
        """本存储使用的引擎（同层协作方——如孤儿回收——与 CAS 共用同一实例与根目录）。"""
        return self._engine

    # --- 内容 ------------------------------------------------------------------

    def put(self, content: bytes, *, now: datetime | None = None) -> StoredArtifact:
        """写入内容并返回存储引用。内容先落盘，元数据后写（失败即留可回收孤儿）。"""
        digest = sha256_hex(content)
        relative = storage_path_for(digest)
        target = absolute_path_under(self._root, digest)
        if not target.is_file():
            self._write_accepting_concurrent_writer(target, content, digest)

        timestamp = utc_now() if now is None else now
        with self._engine.begin() as connection:
            inserted = connection.execute(
                pg_insert(artifacts)
                .values(
                    artifact_id=uuid.uuid4(),
                    content_sha256=digest,
                    size_bytes=len(content),
                    storage_path=relative,
                    created_at=timestamp,
                )
                .on_conflict_do_nothing(index_elements=[artifacts.c.content_sha256])
                .returning(artifacts.c.artifact_id)
            ).fetchone()
            if inserted is not None:
                artifact_id = str(inserted[0])
            else:
                artifact_id = str(
                    connection.execute(
                        sa.select(artifacts.c.artifact_id).where(artifacts.c.content_sha256 == digest)
                    ).scalar_one()
                )
        return StoredArtifact(
            artifact_id=artifact_id,
            content_sha256=digest,
            size_bytes=len(content),
            storage_path=relative,
            deduplicated=inserted is None,
        )

    def _write_accepting_concurrent_writer(
        self, target: pathlib.Path, content: bytes, digest: str
    ) -> None:
        """写入内容；若并发写入者正在写**同一份内容**，接受其结果而不是报错。

        并发窗口有两段，只处理一段不够（实测）：① 目标已存在——另一个写入者已完成；② **另一个写入者
        正在 rename 途中**——此时 `os.replace` 会因目标被并发句柄占用而抛 WinError 5，而 `is_file()`
        仍为假。因此这里做**有界重试**：每次失败后短暂让出，再判"内容是否已就位"；超出次数仍不就位才
        把最后一次错误抛出（那才是真失败：磁盘满、权限错等）。
        """
        last_error: OSError | None = None
        for attempt in range(_CONCURRENT_WRITE_ATTEMPTS):
            try:
                self._write_atomic(target, content)
                return
            except OSError as error:
                last_error = error
                if _holds_expected_content(target, digest):
                    return
                time.sleep(_CONCURRENT_WRITE_BACKOFF_SECONDS * (attempt + 1))
        if _holds_expected_content(target, digest):
            return
        assert last_error is not None
        raise last_error

    def read(self, content_sha256: str, *, verify: bool = True) -> bytes:
        """读回内容。`verify=True` 时校验摘要（默认开启：宁可慢一点也不返回坏内容）。"""
        if not is_digest(content_sha256):
            raise ArtifactNotFoundError("malformed digest")
        with self._engine.connect() as connection:
            known = connection.execute(
                sa.select(artifacts.c.storage_path).where(artifacts.c.content_sha256 == content_sha256)
            ).fetchone()
        if known is None:
            raise ArtifactNotFoundError(content_sha256)
        absolute = absolute_path_under(self._root, content_sha256)
        if not absolute.is_file():
            raise ArtifactContentMissingError(content_sha256)
        content = absolute.read_bytes()
        if verify and sha256_hex(content) != content_sha256:
            raise ArtifactIntegrityError(content_sha256)
        return content

    def exists(self, content_sha256: str) -> bool:
        """内容与元数据都存在才为真（只看其一都会给出误导性答案）。"""
        with self._engine.connect() as connection:
            known = connection.execute(
                sa.select(artifacts.c.artifact_id).where(artifacts.c.content_sha256 == content_sha256)
            ).fetchone()
        return known is not None and absolute_path_under(self._root, content_sha256).is_file()

    # --- 提交文件元数据 ---------------------------------------------------------

    def put_submission_files(
        self,
        submission_id: str,
        files: Sequence[tuple[str, bytes]],
        *,
        now: datetime | None = None,
    ) -> tuple[EnvelopeFile, ...]:
        """写入一个提交的全部文件：内容进 CAS，元数据进 `submission_files`（独立事务）。

        前置条件：`submissions` 中已存在 `submission_id`（外键保证引用完整性）。
        路径白名单在此**真的被调用**——这是"客户端路径不可达"的执行点。
        需要"提交行 + 文件行 + outbox 事件同一事务"时，用 `stage_contents` + `attach_files`。
        """
        timestamp = utc_now() if now is None else now
        staged, envelope_files = self.stage_contents(files, now=timestamp)
        with self._engine.begin() as connection:
            self.attach_files(connection, submission_id, staged, now=timestamp)
        return envelope_files

    def stage_contents(
        self, files: Sequence[tuple[str, bytes]], *, now: datetime | None = None
    ) -> tuple[tuple[tuple[str, StoredArtifact], ...], tuple[EnvelopeFile, ...]]:
        """**只写内容**（"先内容后元数据"的第一步）：校验路径与文件集，把字节落盘到 CAS。

        返回 `(staged, envelope_files)`；`staged` 供调用方在**自己的事务**里写元数据。
        路径白名单与文件集校验在此完成，因此非法输入不会留下任何内容。
        """
        paths = [path for path, _ in files]
        if not paths:
            raise EnvelopeRejected("empty_fileset", "submission must contain at least one file")
        if len(set(paths)) != len(paths):
            raise EnvelopeRejected("duplicate_path", "duplicate file path in submission")
        for path in paths:
            assert_allowed_relative_path(path)

        timestamp = utc_now() if now is None else now
        staged: list[tuple[str, StoredArtifact]] = []
        envelope_files: list[EnvelopeFile] = []
        for path, content in files:
            artifact = self.put(content, now=timestamp)
            staged.append((path, artifact))
            envelope_files.append(
                EnvelopeFile(
                    path=path,
                    content_sha256=artifact.content_sha256,
                    size_bytes=artifact.size_bytes,
                    storage_ref=artifact.storage_path,
                )
            )
        return tuple(staged), tuple(envelope_files)

    def attach_files(
        self,
        connection: Connection,
        submission_id: str,
        staged: Sequence[tuple[str, StoredArtifact]],
        *,
        now: datetime | None = None,
    ) -> None:
        """在调用方的事务内写入 `submission_files` 元数据行。"""
        timestamp = utc_now() if now is None else now
        for path, artifact in staged:
            connection.execute(
                sa.insert(submission_files).values(
                    submission_id=submission_id,
                    path=path,
                    artifact_id=artifact.artifact_id,
                    created_at=timestamp,
                )
            )

    # --- 内部 ------------------------------------------------------------------

    def _write_atomic(self, target: pathlib.Path, content: bytes) -> None:
        """临时文件 + fsync + 原子替换：读者永远看不到半截内容。"""
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".tmp-{uuid.uuid4().hex}"
        try:
            with open(temporary, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
