"""静态规则目录：规则对象 + **确定性注册表**（`RQ-B-2-40` 验收 #2）。

规则对象 = 规则 ID + `severity` + 可读中文说明 + 是否阻断类（`blocking`）。本模块是规则说明文本的
**唯一来源**：检查器只按规则 ID 引用，`make_finding` 是产出明细的**唯一出口**，因此 I10（位置与说明
齐备）与 I12（不内联源码行）由结构保证，而不是靠各检查器自觉。

注册表是确定性的：冻结为按 `rule_id` 字典序排序的元组，不依赖 `dict`/`set` 的迭代顺序，也不读环境
变量；同一输入的两次运行得到完全相同的明细序列与摘要（I11）。工具选型与阻断语义见
[ADR-0001](../../docs/adr/ADR-0001-static-analysis-tooling.md)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

from autograder_b2.domain.static_result import Severity, StaticFinding

#: 被评测语言只有 Python 与 Bash（`RQ-B-0-10`）。
Language = Literal["python", "bash"]

#: 与 `CheckOutcome.checker` 同一取值集合：每条规则必须能归到某个已声明的检查。
Checker = Literal["python_ast", "bash_syntax", "structural_rules"]

#: `StaticFinding.message` 上限（契约 `static_findings[].message` ≤1000 字符）。
MESSAGE_LIMIT = 1000

#: 参与"是否内联源码"判定的源码行最短长度：太短的行（`fi`、`}`）会与普通说明文本撞车
#: （例如 "profile" 里含 "fi"），因此只对足够长的行做整行比对。
_TAINT_MIN_LENGTH = 8


@dataclass(frozen=True, slots=True)
class Rule:
    """一条静态规则（规则目录的原子）。"""

    rule_id: str
    severity: Severity
    message: str        # 可读中文说明；绝不含源码行
    blocking: bool      # 阻断类：severity=error 且命中 ⇒ 阻断动态运行（ADR-0001 第 4 条）
    language: Language
    checker: Checker    # 该规则由哪个检查产出（明细归因到 CheckOutcome）


#: 规则目录（未排序的内部字面量；对外一律经 `RULES` 访问）。
_CATALOG: tuple[Rule, ...] = (
    # ---- Python：语法与编码 ----
    Rule(
        rule_id="STAT-PY-001",
        severity="error",
        message="Python 源码无法完成 ast 解析：存在语法错误",
        blocking=False,
        language="python",
        checker="python_ast",
    ),
    # ---- Python：结构化规则 ----
    Rule(
        rule_id="STAT-PY-010",
        severity="warning",
        message="裸 except 会捕获全部异常（含 KeyboardInterrupt/SystemExit），失败被静默吞掉",
        blocking=False,
        language="python",
        checker="structural_rules",
    ),
    Rule(
        rule_id="STAT-PY-011",
        severity="warning",
        message="使用 eval/exec 动态执行代码，行为不可静态判定",
        blocking=False,
        language="python",
        checker="structural_rules",
    ),
    Rule(
        rule_id="STAT-PY-012",
        severity="warning",
        message="while True 循环体内没有 break，存在不终止风险",
        blocking=False,
        language="python",
        checker="structural_rules",
    ),
    Rule(
        rule_id="STAT-PY-013",
        severity="info",
        message="模块顶层存在定义/导入/__main__ 守卫之外的可执行语句，导入即有副作用风险",
        blocking=False,
        language="python",
        checker="structural_rules",
    ),
    Rule(
        rule_id="STAT-PY-030",
        severity="error",
        message="把命令交给 shell 解释执行（subprocess shell 选项或 os.system/os.popen）",
        blocking=True,
        language="python",
        checker="structural_rules",
    ),
    Rule(
        rule_id="STAT-PY-031",
        severity="error",
        message="代码中出现敏感凭据文件路径字面量（系统账号口令文件一类）",
        blocking=True,
        language="python",
        checker="structural_rules",
    ),
    # ---- Bash：语法 ----
    Rule(
        rule_id="STAT-SH-001",
        severity="error",
        message="bash -n 报告语法错误：脚本无法被 Bash 解析",
        blocking=False,
        language="bash",
        checker="bash_syntax",
    ),
    # ---- Bash：结构化规则 ----
    Rule(
        rule_id="STAT-SH-010",
        severity="error",
        message="破坏性删除结构：递归强制删除根目录或家目录",
        blocking=True,
        language="bash",
        checker="structural_rules",
    ),
    Rule(
        rule_id="STAT-SH-011",
        severity="error",
        message="下载内容直接交给 shell 执行（curl/wget 管道到 bash/sh）",
        blocking=True,
        language="bash",
        checker="structural_rules",
    ),
    Rule(
        rule_id="STAT-SH-012",
        severity="error",
        message="fork bomb 结构：递归自我复制并后台展开",
        blocking=True,
        language="bash",
        checker="structural_rules",
    ),
    Rule(
        rule_id="STAT-SH-013",
        severity="error",
        message="脚本引用敏感凭据文件路径（系统账号口令文件一类）",
        blocking=True,
        language="bash",
        checker="structural_rules",
    ),
    Rule(
        rule_id="STAT-SH-014",
        severity="error",
        message="反向 shell 结构：/dev/tcp 重定向、交互式 shell 重定向或 nc 执行模式",
        blocking=True,
        language="bash",
        checker="structural_rules",
    ),
    Rule(
        rule_id="STAT-SH-020",
        severity="warning",
        message="使用 eval 动态执行字符串，行为不可静态判定",
        blocking=False,
        language="bash",
        checker="structural_rules",
    ),
    Rule(
        rule_id="STAT-SH-021",
        severity="info",
        message="脚本未声明 set -e（命令失败不会中断脚本，错误可能被掩盖）",
        blocking=False,
        language="bash",
        checker="structural_rules",
    ),
)


def _freeze(catalog: tuple[Rule, ...]) -> tuple[Rule, ...]:
    """校验注册表并冻结为 rule_id 字典序元组（重复 ID 即启动期失败，不留静默覆盖）。"""
    seen: set[str] = set()
    for rule in catalog:
        if rule.rule_id in seen:
            raise RuntimeError(f"duplicate static rule id: {rule.rule_id}")
        seen.add(rule.rule_id)
        if not rule.message or len(rule.message) > MESSAGE_LIMIT:
            raise RuntimeError(f"static rule message out of range: {rule.rule_id}")
        if rule.severity not in ("error", "warning", "info"):
            raise RuntimeError(f"static rule severity invalid: {rule.rule_id}")
    return tuple(sorted(catalog, key=lambda item: item.rule_id))


#: 对外可见的规则目录：按 `rule_id` 字典序，确定性且只读。
RULES: tuple[Rule, ...] = _freeze(_CATALOG)

#: 规则 ID → 规则对象（由 `RULES` 派生，只读使用）。
_BY_ID: dict[str, Rule] = {rule.rule_id: rule for rule in RULES}

#: 按语言分组的规则序列（同样按 `rule_id` 排序）。
_BY_LANGUAGE: dict[str, tuple[Rule, ...]] = {
    language: tuple(rule for rule in RULES if rule.language == language)
    for language in ("python", "bash")
}


def get_rule(rule_id: str) -> Rule:
    """按 ID 取规则；未知 ID 立即失败（明细不得引用目录外的规则）。"""
    try:
        return _BY_ID[rule_id]
    except KeyError:
        raise KeyError(f"unknown static rule id: {rule_id}") from None


def rules_for(language: Language) -> tuple[Rule, ...]:
    """该语言的规则序列（`rule_id` 字典序，确定性）。"""
    if language not in _BY_LANGUAGE:
        raise ValueError(f"unsupported language: {language!r}")
    return _BY_LANGUAGE[language]


def rule_catalog_digest() -> str:
    """规则目录的规范化摘要（同一注册表 ⇒ 同一摘要；供报告侧标注所用规则集版本）。"""
    payload = json.dumps(
        [asdict(rule) for rule in RULES],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sanitize_detail(
    text: str,
    *,
    source: str | None = None,
    redact: tuple[str, ...] = (),
    limit: int = MESSAGE_LIMIT,
) -> str:
    """I12/I19：把结构化细节净化成**不含源码行、不含内部绝对路径**的单行文本。

    顺序：替换脱敏对象（工作区/文件绝对路径 → `<路径>`）→ 去控制字符 → 折叠空白 →
    若文本仍包含任一源码行（去空白后长度 ≥8）则**整段丢弃**（返回空串）→ 截断到上限。
    宁可少给细节，也不内联源码：调用方在拿到空串时只保留规则说明文本。
    """
    cleaned = text
    for secret in redact:
        if secret:
            cleaned = cleaned.replace(secret, "<路径>")
    printable = "".join(ch if ch.isprintable() else " " for ch in cleaned)
    collapsed = " ".join(printable.split())
    if source is not None and _mentions_source(printable, collapsed, source):
        return ""
    return collapsed[:limit]


def _mentions_source(raw: str, collapsed: str, source: str) -> bool:
    """文本里是否出现了源码的某一行（长行才判定，避免短行误伤说明文本）。"""
    for line in source.splitlines():
        stripped = line.strip()
        if len(stripped) < _TAINT_MIN_LENGTH:
            continue
        if stripped in raw or " ".join(stripped.split()) in collapsed:
            return True
    return False


def make_finding(
    rule_id: str,
    *,
    file: str,
    line: int | None,
    column: int | None,
    detail: str = "",
    source: str | None = None,
    redact: tuple[str, ...] = (),
) -> StaticFinding:
    """产出**唯一**形态的明细：规则目录取 severity/blocking/说明，细节经净化后追加。

    - `detail` 只能是结构化信息（异常类名、`ast` 给出的错误描述、语句类型名），**不得**是源码片段；
      即便传入源码片段，`sanitize_detail` 也会整段丢弃，最终 `message` 保证不含源码行（I12）。
    - `file` 只接受白名单校验后的工作区**相对**路径（`redact` 用于兜底清除绝对路径）。
    """
    rule = get_rule(rule_id)
    composed = f"{rule.message}：{detail}" if detail else rule.message
    sanitized = sanitize_detail(composed, source=source, redact=redact)
    if sanitized:
        message = sanitized
    else:
        # 组合文本被源码行污染：退回规则说明；说明本身也被污染时只保留规则 ID。
        fallback = sanitize_detail(rule.message, source=source, redact=redact)
        message = fallback or f"命中静态规则 {rule.rule_id}（说明文本因避免内联源码而省略）"
    return StaticFinding(
        rule_id=rule.rule_id,
        severity=rule.severity,
        file=file,
        line=line,
        column=column,
        message=message,
        blocking=rule.blocking,
    )
