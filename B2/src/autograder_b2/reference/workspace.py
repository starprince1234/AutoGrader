"""参考解答的一次性工作区：把**受保护正文**物化成沙箱输入，用完即删。

## 为什么不复用 `CasWorkspaceProvider`

`CasWorkspaceProvider` 的输入是内容寻址存储里的 `content_sha256`：它按摘要去 CAS 取字节。
参考解答没有、**也不该有** CAS 条目——本端点"**不落库**"（`ADR-0015` 决策 1、契约描述），
把参考解答正文写进 CAS 会同时产生两样东西：一份持久化的受保护内容，以及一行
`b2.artifacts` 元数据。那既是"落库"，也让"每一次评测取包里不必携带的东西"变成了一份
**长期驻留**的副本。因此这里是"内存里的正文 → 一次性目录"，与 CAS 无关。

## 三条安全性质（与 `CasWorkspaceProvider` 同口径）

1. **目录名只由服务端 UUID 派生**（本次验证的标识 + 用例 `case_id`）：调用者提供不了任何路径片段，
   因此不存在"学生可控字符串 → 文件系统路径"这一跳；
2. **文件名过同一份路径白名单**（`domain.envelope.is_allowed_relative_path`）：入口脚本名由 runner
   的 `entry_name_for(language)` 给出，但白名单仍要过一遍——"上游给的字符串"永远是外部输入；
3. **`release` 只删本 provider 根之下的路径**：越界即拒绝，绝不把任意路径当工作区删掉。

## 行尾与字节：**不替作者改写**

写盘用 `write_text(..., encoding="utf-8", newline="")`，即**不翻译行尾**：落到盘上的字节就是
B3 存的那份正文。写 CRLF 还是 LF 是作者在写题时决定的事，替它归一化会让"我们执行了什么"
与"B3 里存着什么"出现无人可见的差异；若作者存了 CRLF 而 `bash` 因此失败，那是**真实观测**。
（对照：`MAIN/scripts/verify_question_bank.py` 用 `newline="\\n"` 翻译，那是另一个工具的取值。）
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from autograder_b2.domain.envelope import is_allowed_relative_path


class ReferenceAnswerWorkspace:
    """一次性工作区的生产者/回收者（每个用例一份，绝不复用）。"""

    def __init__(self, *, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def prepare(
        self, *, verification_id: str, case_id: str, filename: str, content: str
    ) -> Path:
        """建一个**新的**工作区并把参考解答写成入口脚本，返回其路径。"""
        verification = _canonical_uuid(verification_id, "verification_id")
        case = _canonical_uuid(case_id, "case_id")
        if not is_allowed_relative_path(filename):
            raise ValueError("reference answer filename is not an allowed relative path")
        base = self._select_base()
        target = base / verification / f"case-{case}"
        target.mkdir(parents=True, exist_ok=True)
        # The sandboxed process runs as `nobody` (uid 65534); this process runs under
        # systemd's `UMask=0077`, so every directory `mkdir(parents=True)` creates is 0700.
        #
        # Two levels matter, not one. Giving `case-<id>` 0777 is not enough: `nobody` still has
        # to *traverse* `<root>/<verification>` and `<root>` itself. Those parents stay 0700,
        # so the sandbox cannot even reach the entry script -- the process then exits without
        # producing output, which reads as `crash`/`output_mismatch` for **every** case and
        # therefore blocks every publication (`0/1 passed`, no attestation).
        # 0711 = traverse without listing: the sandbox can enter a directory it already knows
        # the name of, but cannot enumerate what else is in there.
        os.chmod(target.parent, 0o711)
        os.chmod(target, 0o777)
        entry = target / filename
        entry.write_text(content, encoding="utf-8", newline="")
        os.chmod(entry, 0o644)
        return target

    def _select_base(self) -> Path:
        """Always build the one-shot workspace under the system temp dir.

        Why not ``B2_WORKSPACE_ROOT``: that root lives under a systemd-created directory
        (``/var/lib/<service>``, mode ``0750``) and the jailed process is mapped to uid 65534.
        In production this configuration produced ``exit_code=255`` with **empty stdout** for
        every case -- the sandbox never got to execute the entry script -- while the sandbox
        canary, whose workspace sits under ``/tmp`` (``1777``), runs the very same interpreter
        successfully.

        The canary is the reference implementation of "a workspace the sandbox can use", so the
        reference-answer workspace follows it: a fresh ``0700`` directory (chmod ``0711`` so the
        jailed user can traverse it), whose parent ``/tmp`` is world-traversable. Each case gets
        its own directory and it is deleted as soon as the case finishes -- the protected answer
        never outlives the verification that needed it.
        """
        fallback = Path(tempfile.mkdtemp(prefix="b2-reference-"))
        os.chmod(fallback, 0o711)
        return fallback

    def release(self, workspace: Path) -> None:
        """删除本 provider 根之下的工作区；根之外的路径一律拒绝（不删任意路径）。

        用例目录删掉之后，若本次验证的目录已经空了就一并删掉：**受保护正文不留在盘上**，
        连"这里发生过一次验证"的目录名也不留。非空时保留（那是别的用例还在用）。
        """
        resolved = Path(workspace).resolve()
        allowed_roots = {self._root.resolve(), Path(tempfile.gettempdir()).resolve()}
        parent_root = next((root for root in allowed_roots if root in resolved.parents), None)
        if parent_root is None:
            raise ValueError("refusing to release a path outside the workspace root")
        shutil.rmtree(resolved, ignore_errors=True)
        if resolved.parent != parent_root:
            try:
                resolved.parent.rmdir()
            except OSError:
                pass  # 还有别的用例目录：保留，不是错误


def _canonical_uuid(value: object, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a UUID") from error


__all__ = ["ReferenceAnswerWorkspace"]
