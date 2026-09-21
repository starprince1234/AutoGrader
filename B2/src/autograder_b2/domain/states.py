"""提交状态机的**单一常量源**（取值集合由 MAIN 锁定，见 `docs/architecture/models.md` 第 1 节）。

```text
received → validated → queued → running → completed | rejected | failed
```

`rejected` 有两条到达路径：静态阶段（`validated → rejected`，不进入 `running`）与执行阶段
（`running → rejected`，安全违规）。`failed` 只从 `running` 到达——技术失败以"尝试执行过"为前提。
把这张表写成代码而不是散落的字符串字面量，是为了让非法迁移在写入前就被拒绝。
"""

from __future__ import annotations

from collections.abc import Mapping

RECEIVED = "received"
VALIDATED = "validated"
QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
REJECTED = "rejected"
FAILED = "failed"

#: 顺序即文档顺序，供 CHECK 约束与枚举校验复用。
ALL_STATES = (RECEIVED, VALIDATED, QUEUED, RUNNING, COMPLETED, REJECTED, FAILED)
TERMINAL_STATES = (COMPLETED, REJECTED, FAILED)

SUBMISSION_TRANSITIONS: Mapping[str, tuple[str, ...]] = {
    RECEIVED: (VALIDATED, REJECTED),
    VALIDATED: (QUEUED, REJECTED),
    QUEUED: (RUNNING,),
    RUNNING: (COMPLETED, REJECTED, FAILED),
    COMPLETED: (),
    REJECTED: (),
    FAILED: (),
}


class IllegalTransition(ValueError):
    """不在锁定状态机内的迁移（含终态出发的任何迁移）。"""


def can_transition(from_state: str | None, to_state: str) -> bool:
    """`from_state is None` 表示实体创建（首次进入状态机）。"""
    if from_state is None:
        return to_state == RECEIVED
    return to_state in SUBMISSION_TRANSITIONS.get(from_state, ())


def require_transition(from_state: str | None, to_state: str) -> None:
    if not can_transition(from_state, to_state):
        raise IllegalTransition(f"{from_state!r} -> {to_state!r} is not an allowed transition")
