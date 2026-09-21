"""逐用例的**确定性**解释文本（`CHG-0012` 的 `case_evidence[].explanation`）。

这不是模型输出，也永远不能是：`explain_case` 是**纯函数**——同一份证据对象必然得到同一段文字，
因此它可以进报告、可以进 `result_digest`、可以被第三方复核。模型生成的教学评语走的是**另一条轨**
（`ADR-0009` / `RQ-T-20`，见 `autograder_b2.commentary`），那边的东西进不了这里；
而 `case_evidence` 的 schema 是 `additionalProperties: false`，"模型输出进不了报告"因此是
**结构性事实**而不是一句承诺。

## 允许说什么、禁止说什么

只允许**陈述证据里已有的事实**，不允许建议、不允许评价代码风格、不允许给分：

- 允许：行号、路径、计数、契约枚举对应的中文类别、退出码、命中的限额名称；
- 禁止：期望输出的内容（受保护——尤其隐藏用例）、源码片段、任何"你应该…"的建议、
  任何"大概是多少分"的说法。

行号是**行号**：它说明"第几行不同"，但不复述那一行是什么。这是"可解释"与"受保护内容不外泄"
之间唯一的平衡点，也是隐藏用例的学生视图仍然能拿到解释的原因。
"""

from __future__ import annotations

from autograder_b2.domain.evidence import CaseEvidence

#: B2 的限制项 → 面向人的名称（`resource_limit_hit` 的取值域见 `domain.evidence`）。
LIMIT_ITEM_LABELS: dict[str, str] = {
    "memory": "内存上限",
    "cpu": "CPU 时间上限",
    "wall": "墙钟上限",
    "processes": "进程数上限",
    "output": "输出上限",
    "files": "产出文件数上限",
}

#: 安全违规类别 → 面向人的名称（契约 `security_conclusion.findings[].category` 的取值域）。
SECURITY_CATEGORY_LABELS: dict[str, str] = {
    "destructive_command": "破坏性命令",
    "network_attempt": "联网尝试",
    "privilege_escalation": "提权尝试",
    "sensitive_path_read": "读取敏感路径",
    "fork_bomb": "进程炸弹",
    "resource_limit": "资源超限",
    "archive_bomb": "压缩炸弹",
    "sandbox_violation": "沙箱违规",
}

#: 解释文本上限（契约 `case_evidence[].explanation.maxLength`）。
EXPLANATION_LIMIT = 2000


def explain_case(evidence: CaseEvidence) -> str:
    """按**固定规则**给出本用例结局的一句话说明（同输入同输出）。"""
    text = _sentence(evidence)
    suffix = _evidence_suffix(evidence)
    combined = f"{text}{suffix}"
    return combined if len(combined) <= EXPLANATION_LIMIT else combined[:EXPLANATION_LIMIT]


def _sentence(evidence: CaseEvidence) -> str:
    category = evidence.failure_category
    if evidence.passed and category is None:
        return "本用例通过：比较策略判定相符，未触发资源上限，也没有安全违规。"
    if category is None:
        return "本用例未通过，但证据里没有记录失败类别（请按证据缺失处理，不要当成代码问题）。"
    if category == "timeout":
        return "本用例超时：达到墙钟上限后进程树被清理，未产出可判定的结果。"
    if category == "resource_limit":
        label = LIMIT_ITEM_LABELS.get(evidence.resource_limit_hit or "")
        if label is None:
            return "本用例因资源上限被终止（具体限制项未能判定）。"
        return f"本用例触发沙箱的{label}：进程被沙箱终止，未跑完。"
    if category == "security_blocked":
        return f"本用例被沙箱安全策略阻断（{_security_labels(evidence)}）：这不是技术故障，而是安全判定。"
    if category == "missing_output":
        paths = _joined(evidence.missing_outputs)
        return f"本用例未产出声明的文件：{paths}（期望文件缺失，因此无法比较内容）。"
    if category == "output_mismatch":
        return _mismatch_sentence(evidence)
    if category == "import_error":
        return "本用例的 callable 入口无法在工作区内解析：模块导入失败，或声明的属性不可调用。"
    if category == "crash":
        return _crash_sentence(evidence)
    if category == "infrastructure_error":
        return "本用例的输入资产缺失或摘要不符，属**技术故障**，不是学生代码的问题。"
    return f"本用例未通过（失败类别：{category}）。"


def _mismatch_sentence(evidence: CaseEvidence) -> str:
    parts: list[str] = []
    if evidence.mismatch_line is not None:
        parts.append(f"输出与期望在第 {evidence.mismatch_line} 行首次不一致")
    else:
        parts.append("输出与期望不一致（未能定位到具体行）")
    if evidence.mismatched_outputs:
        paths = _joined(evidence.mismatched_outputs)
        parts.append(f"产出文件的内容摘要与声明不符：{paths}")
    return "本用例未通过：" + "；".join(parts) + "。"


def _crash_sentence(evidence: CaseEvidence) -> str:
    exit_code = evidence.exit_code
    if exit_code is None:
        return "本用例未通过：进程没有产生退出码（异常终止），也没有可判定的输出。"
    if exit_code < 0:
        return f"本用例未通过：进程被信号终止（退出码 {exit_code}）。"
    return f"本用例未通过：进程以非零退出码 {exit_code} 结束。"


def _evidence_suffix(evidence: CaseEvidence) -> str:
    """补一句**只由摘要稳定字段推出**的补充事实（没有可说的就不说）。

    **只准用稳定字段**：`explanation` 会进报告、进而进 `result_digest`，而 `duration_ms` 与
    `resource` 是墙钟/环境读数（两者都被排除在摘要之外，见 `run_case.evidence_digest_of`）。
    把它们的取值写进这段文字，等于让"同绑定摘要 ⇒ 同 `result_digest`"从后门失效。
    """
    parts: list[str] = []
    if evidence.resource_limit_hit == "files":
        parts.append("产出文件数超过声明的上限（这是执行后的计数判定，不是沙箱在运行中拦截）")
    if evidence.artifacts is not None:
        parts.append(
            f"工作区变化：新建 {len(evidence.artifacts.created)}、"
            f"修改 {len(evidence.artifacts.modified)}、删除 {len(evidence.artifacts.deleted)}"
        )
        if evidence.artifacts.truncated:
            parts.append("文件清单超过契约上限，已截断（真实数量更多）")
    if evidence.sandbox_violations:
        parts.append(f"沙箱违规明细 {len(evidence.sandbox_violations)} 条已记入安全发现")
    if not parts:
        return ""
    return "（" + "；".join(parts) + "）"


def _security_labels(evidence: CaseEvidence) -> str:
    categories = sorted({item.category for item in evidence.sandbox_violations})
    if not categories:
        return "类别未记录"
    return "、".join(SECURITY_CATEGORY_LABELS.get(item, item) for item in categories)


def _joined(paths: tuple[str, ...]) -> str:
    return "、".join(paths) if paths else "（清单为空）"


__all__ = [
    "EXPLANATION_LIMIT",
    "LIMIT_ITEM_LABELS",
    "SECURITY_CATEGORY_LABELS",
    "explain_case",
]
