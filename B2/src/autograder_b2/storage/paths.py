"""服务端存储路径派生（唯一的路径来源）。

**客户端永远不能指定路径**：内容寻址的存储路径只由内容摘要派生（ADR-0003 第 2 条），因此
目录穿越在结构上不可达——不存在"客户端给的路径被采用"这条代码路径。

派生规则：`ab/cd/<完整摘要>`（前 2 位 / 次 2 位 / 全摘要）。相对路径，不含绝对前缀，
不含 `.`/`..` 段，满足 `artifacts.storage_path` 的 CHECK（非空、非绝对、无 `..`）。
"""

from __future__ import annotations

import os
import pathlib

from autograder_b2.domain.clock import is_digest

STORAGE_PATH_SEGMENT_LENGTH = 2


class InvalidDigestError(ValueError):
    """摘要形态非法（不是 64 位小写十六进制）——调用方必须先算摘要，再派生路径。"""


def storage_path_for(content_sha256: str) -> str:
    """由摘要派生**相对**存储路径（服务端唯一来源，纯函数、确定性）。"""
    if not is_digest(content_sha256):
        raise InvalidDigestError("content_sha256 must be 64 lowercase hex characters")
    head = STORAGE_PATH_SEGMENT_LENGTH
    return f"{content_sha256[:head]}/{content_sha256[head : head * 2]}/{content_sha256}"


def absolute_path_under(root: pathlib.Path, content_sha256: str) -> pathlib.Path:
    """在给定根目录下拉出绝对路径；并断言结果确实位于根目录之内。

    第二重保险：即使派生规则将来被改坏，越出根目录也会当场抛错，而不是写到别处。

    **判定是纯词法的**（`normcase` + `abspath`），**不**调用 `resolve()`：`resolve()` 会做文件系统
    解析，而在并发下（目标目录刚被另一个写入者建出来时）Windows 上它可能给候选路径加上扩展长度
    前缀（问号前缀）、给根目录却不加，于是同一个根目录之内的路径看起来"逃出了根目录"——实测 12 路
    并发写同一摘要时有 5 路被这道守卫**误判**为越界（`InvalidDigestError` 是 `ValueError` 子类，
    会一路冒泡成 500）。词法判定不碰磁盘，因此不存在这个竞态；它要防的"派生规则被改坏"也照样能防住。
    """
    root_path = pathlib.Path(root)
    relative = pathlib.PurePosixPath(storage_path_for(content_sha256))
    if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
        raise InvalidDigestError("derived storage path is not a safe relative path")
    candidate = root_path.joinpath(*relative.parts)
    root_normalised = os.path.normcase(os.path.abspath(str(root_path)))
    candidate_normalised = os.path.normcase(os.path.abspath(str(candidate)))
    try:
        inside = os.path.commonpath([root_normalised, candidate_normalised]) == root_normalised
    except ValueError:
        # 不同盘符（Windows）⇒ 绝不可能在根目录之内。
        inside = False
    if not inside or candidate_normalised == root_normalised:
        raise InvalidDigestError("derived storage path escaped the artifact root")
    return pathlib.Path(os.path.abspath(str(candidate)))
