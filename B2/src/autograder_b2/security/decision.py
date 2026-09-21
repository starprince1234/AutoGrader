"""安全结论三态判定与**阻断门**（`RQ-B-2-70`；签名冻结见 interfaces.md 第 4 节）。

本模块只做**判定**：把检查器的执行结果折算成 `SecurityDecision`，并回答"能否进入 runner"。
检查项标识、必需集合、`covered`/`unchecked` 的语义全在这里，是"未检查 ≠ 安全"的**唯一**实现点。

**两层并存（不收敛）**：`src/autograder_b2/static/` 的静态合法性层与本模块的高危模式层是
纵深防御的两层（`MAIN/docs/architecture/security.md` §4：「任何'因为第 3 层拦住了所以第 4 层
可以简化'的推理都是错误的」）。本模块**独立扫描**，绝不退化为"只消费静态明细"；当调用方把静态
明细传进来（`static_findings=...`）时，本模块把它当作**补充证据**并入同一结论：

- 上游明细折算为 `detected_in="static"` 的 finding，`rule_id` 保留上游规则 ID（来源可追溯）；
- 上游任一明细 `blocking=True` ⇒ 折算为 `high`，于是本模块 `blocking` 也为真（I17 的唯一来源是
  `high`）；上游**非**阻断明细只降档为 medium/low，绝不把上游的"不阻断"升级成阻断；
- 阻断判定是**或**：上游 blocking 或本模块 blocking 任一为真 ⇒ 阻断门关闭；
- 同一危险不重复呈现：合并按 `(category, rule_id, file)` 去重（可选用 `static_rule_aliases`
  把上游规则 ID 映射到本模块规则 ID，使跨层同源判定真正折叠）；被合并掉的条数写进 `rationale`。

三态（I15）的落地读法（三态互斥且完备）：

- `found` ⟺ 至少一条 finding，且只来自已执行的检查项；
- `not_found` ⟺ `unchecked` 为空（所有必需检查都真的跑过）且无 finding；
- `not_checked` ⟺ `unchecked` 非空且无 finding。

`findings` 非空时以 `found` 为准（正发现优先）：一条高危发现不会因为"另一项检查没跑"而消失——
此时 `unchecked` 仍非空，阻断门照样关闭。`assert_decision_invariants` 把这条读法写成可执行断言。

阻断门（I17 + ADR-0002 的"禁止降级"）：

- `allows_dynamic_execution(decision)` 只在【必需检查全部跑过】且【无 `high` 发现】时为真；
- `blocking == True` ⇒ 编排层置 `rejected`（`terminal_reason = "static_blocked"`），**不得**产生 `running`；
- `unchecked` 非空（不是因发现而阻断）⇒ 置 `failed`（`terminal_reason = "infrastructure_error"`），
  **绝不**退化为"没检查也照跑"——这是 I18 要求的"安全拒绝"与"技术失败"的区分点，由 `terminal_reason` 给出。

**已决策项（fail-closed；经主控裁定后固化于此，阶段文档登记为已决策）**：`not_checked ⇒ 阻断门关闭`，
且该情形的终态是 `failed` + `infrastructure_error`，**严格区别于**安全拒绝的 `rejected` +
`static_blocked`。理由有二：

1. **误判代价不对称**：漏放（把"没检查"当"没问题"而执行了恶意代码）不可恢复；误停（把本可运行的提交
   停成一次技术性失败）可见、可重试、可定位。安全门禁因此取保守方向。
2. **ADR-0002 与 interfaces.md §8.3 禁止降级**：新增隐藏开关、"检查缺失时照跑"都是把安全层变成可绕过的
   装饰；`MAIN/docs/architecture/security.md` §4 的"任何'因为第 3 层拦住了所以第 4 层可以简化'的推理
   都是错误的"同样适用于"某一项检查没跑也先跑起来"。

`static_findings=None` 表示"本门禁独立成判"（上游明细未并入，也不进 `covered`/`unchecked`）；要让
"上游静态层没跑"显式进入 `unchecked`，用 `mark_check_unchecked(..., check_id=STATIC_LAYER_CHECK_ID)`。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, get_args

from autograder_b2.domain.security import (
    DetectedIn,
    SecurityCategory,
    SecurityDecision,
    SecurityFinding,
    SecuritySeverity,
    SecurityStatus,
)
from autograder_b2.domain.static_result import StaticFinding
from autograder_b2.security.patterns import (
    BASH_TEXT_CHECK_ID,
    PYTHON_AST_CHECK_ID,
    PYTHON_TEXT_CHECK_ID,
    RULES_BY_ID,
    CheckUnavailableError,
    analyze_bash_text,
    analyze_python_ast,
    analyze_python_text,
    redact_text,
    safe_location,
)

#: 检查项标识 → 该检查项对每个源码的分析函数（绝不执行待测代码）。
Analyzer = Callable[[str, str], tuple[SecurityFinding, ...]]

#: 运行期沙箱违规检查项（由 runner 在沙箱内观测；静态阶段尚未执行，故不进静态必需集合）。
RUNTIME_SANDBOX_CHECK_ID = "runtime:sandbox_violations"

#: 上游静态合法性层的并入标识：调用方传了 `static_findings` 时进 `covered`。
STATIC_LAYER_CHECK_ID = "static:upstream_findings"

#: 各语言的**必需检查项**（单一来源：必需集合由语言派生，不接受调用方"少要几项"）。
REQUIRED_CHECKS: Mapping[str, tuple[str, ...]] = {
    "python": (PYTHON_AST_CHECK_ID, PYTHON_TEXT_CHECK_ID),
    "bash": (BASH_TEXT_CHECK_ID,),
}

#: 上游明细的严重度折算：**非**阻断明细一律不得升为 `high`（否则会把"不阻断"变成阻断）。
_STATIC_NON_BLOCKING_SEVERITY: Mapping[str, SecuritySeverity] = {
    "error": "medium",
    "warning": "low",
    "info": "low",
}

#: 上游明细的类别归类关键词（**仅**影响标签，不影响阻断判定；顺序即优先级）。
_STATIC_CATEGORY_KEYWORDS: tuple[tuple[SecurityCategory, tuple[str, ...]], ...] = (
    ("destructive_command", ("破坏性", "删除根", "rm -rf", "rmtree", "格式化", "块设备")),
    ("network_attempt", ("网络", "联网", "外连", "反弹", "下载执行", "socket", "curl", "wget")),
    ("privilege_escalation", ("提权", "sudo", "setuid", "chmod +s", "root 权限")),
    ("sensitive_path_read", ("敏感路径", "影子", "shadow", "凭据", "私钥", "id_rsa")),
    ("fork_bomb", ("fork", "进程爆炸", "自我复制进程")),
    ("resource_limit", ("资源", "内存", "无限循环", "超限")),
    ("archive_bomb", ("压缩", "解压", "归档", "zip", "tar")),
)

#: 归类失败时的兜底类别（最保守的通用标签）。
_STATIC_CATEGORY_FALLBACK: SecurityCategory = "sandbox_violation"

#: 上游折算 finding 的确定性 ID 命名空间（同输入 ⇒ 同 ID）。
_UPSTREAM_ID_NAMESPACE = uuid.UUID("b7d0e3a1-4c62-4f18-9a3d-21c5f0a8e442")


@dataclass(frozen=True, slots=True)
class StaticCheck:
    """一个静态检查器：标识 + 分析函数（`(文件名, 源码) -> findings`）。

    分析函数抛 `CheckUnavailableError` 表示该检查项**未完成**（`not_checked`）。
    """

    check_id: str
    analyze: Analyzer


@dataclass(frozen=True, slots=True)
class CheckReport:
    """单个检查项的执行结果（`ran` / `not_checked` + 脱敏原因）。

    `origins` 记录 finding ID → 逻辑文件名，供跨层去重 `(category, rule_id, file)` 使用。
    """

    check_id: str
    status: Literal["ran", "not_checked"]
    reason: str | None
    findings: tuple[SecurityFinding, ...]
    origins: Mapping[str, str] = field(default_factory=dict)


#: 默认检查器集合（注入子集即表达"某必需检查没跑"）。
DEFAULT_CHECKS: tuple[StaticCheck, ...] = (
    StaticCheck(check_id=PYTHON_AST_CHECK_ID, analyze=analyze_python_ast),
    StaticCheck(check_id=PYTHON_TEXT_CHECK_ID, analyze=analyze_python_text),
    StaticCheck(check_id=BASH_TEXT_CHECK_ID, analyze=analyze_bash_text),
)

#: `unchecked` 项在缺少原因时的占位说明（跨阶段合并时旧原因不随结论保留）。
_REASON_NOT_RETAINED = "未执行（原因未随结论保留）"

#: I16：`not_checked` 的结论文本里**禁止**出现的字样。
_FORBIDDEN_WORDS_WHEN_NOT_CHECKED = ("安全", "通过")


def check_language(check_id: str) -> str | None:
    """检查项标识的语言前缀（`static:python_ast` → `python`；无法判定返回 `None`）。"""
    _, separator, tail = check_id.partition(":")
    if not separator or not tail:
        return None
    return tail.split("_", 1)[0] or None


def required_checks(language: str) -> tuple[str, ...]:
    """某语言的必需检查项；语言不受支持即抛 `ValueError`（不猜、不降级）。"""
    try:
        return REQUIRED_CHECKS[language]
    except KeyError:
        raise ValueError(f"不支持的待测语言：{language!r}（只接受 python / bash）") from None


def classify_static_message(message: str) -> SecurityCategory:
    """按关键词给上游静态明细归类（启发式；归类失败用兜底类别）。

    **仅影响标签**：阻断与否只由 `StaticFinding.blocking` 决定，与归类结果无关。
    """
    lowered = message.lower()
    for category, keywords in _STATIC_CATEGORY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return category
    return _STATIC_CATEGORY_FALLBACK


def static_layer_finding(
    finding: StaticFinding,
    *,
    rule_aliases: Mapping[str, str] | None = None,
) -> SecurityFinding:
    """把上游静态合法性层的明细折算成本模块的 finding。

    - `rule_id` **保留上游规则 ID**（来源可追溯；该字段可空，但静态来源必须填）；
    - 严重度：`blocking=True` → `high`；否则 error→medium / warning→low / info→low（不升档）；
    - 类别：优先取别名映射到的本模块规则的类别（精确），否则按关键词归类（启发式）；
    - 文本：上游 `message` + 脱敏位置（白名单外的文件名替换为占位符），整体 ≤1000 字符。
    """
    alias = (rule_aliases or {}).get(finding.rule_id, finding.rule_id)
    rule = RULES_BY_ID.get(alias)
    category = rule.category if rule is not None else classify_static_message(finding.message)
    if finding.blocking:
        severity: SecuritySeverity = "high"
    else:
        severity = _STATIC_NON_BLOCKING_SEVERITY.get(finding.severity, "medium")
    location = safe_location(finding.file)
    where = f"{location}:{finding.line}" if finding.line else location
    message = redact_text(f"{finding.message}（上游静态规则 {finding.rule_id}，位置 {where}）")
    return SecurityFinding(
        finding_id=str(
            uuid.uuid5(
                _UPSTREAM_ID_NAMESPACE,
                f"{finding.rule_id}|{finding.file}|{finding.line}|{finding.message}",
            )
        ),
        category=category,
        severity=severity,
        detected_in="static",
        message=message,
        rule_id=finding.rule_id,
    )


def _merge_upstream_findings(
    own: tuple[tuple[SecurityFinding, str], ...],
    supplied: Sequence[StaticFinding],
    aliases: Mapping[str, str],
) -> tuple[tuple[SecurityFinding, ...], int]:
    """按 `(category, rule_id, file)` 合并上游明细；返回（并入的 finding, 同源被合并掉的条数）。

    本模块自己扫出来的 finding 优先保留（它们是独立扫描的证据），上游同源条目被折叠。
    """
    seen = {
        (finding.category, finding.rule_id or "", origin)
        for finding, origin in own
    }
    merged: list[SecurityFinding] = []
    collapsed = 0
    for item in supplied:
        converted = static_layer_finding(item, rule_aliases=aliases)
        key = (converted.category, aliases.get(item.rule_id, item.rule_id), item.file)
        if key in seen:
            collapsed += 1
            continue
        seen.add(key)
        merged.append(converted)
    return tuple(merged), collapsed


def _run_one(check: StaticCheck, sources: Mapping[str, str]) -> CheckReport:
    """跑一个检查器；不可用/内部异常一律转 `not_checked` + 脱敏原因，且**丢弃**其部分结果。"""
    collected: list[SecurityFinding] = []
    origins: dict[str, str] = {}
    try:
        for name in sorted(sources):
            for finding in check.analyze(name, sources[name]):
                collected.append(finding)
                origins[finding.finding_id] = name
    except CheckUnavailableError as exc:
        return CheckReport(check.check_id, "not_checked", redact_text(exc.reason), ())
    except Exception as exc:  # noqa: BLE001 —— 检查器缺陷不得中断整条评测，但必须降级为"未检查"
        return CheckReport(
            check.check_id,
            "not_checked",
            f"检查器内部错误（{type(exc).__name__}），检查未完成",
            (),
        )
    return CheckReport(check.check_id, "ran", None, tuple(collected), origins)


def run_checks(
    sources: Mapping[str, str],
    *,
    language: str,
    checks: Sequence[StaticCheck] | None = None,
) -> tuple[CheckReport, ...]:
    """执行适用于该语言的检查器，并为"未提供/不适用"的必需检查项补 `not_checked` 报告。

    无源码文本时全部必需检查项均为 `not_checked`（"没有代码可查"不等于"查过且没问题"）。
    """
    required = required_checks(language)
    effective = tuple(DEFAULT_CHECKS if checks is None else checks)
    applicable = [check for check in effective if check_language(check.check_id) == language]
    reports: list[CheckReport] = []
    if not sources:
        return tuple(
            CheckReport(check_id, "not_checked", "未提供任何源码文本，检查未执行", ())
            for check_id in required
        )
    for check in applicable:
        reports.append(_run_one(check, sources))
    provided = {check.check_id for check in applicable}
    for check_id in required:
        if check_id not in provided:
            reports.append(
                CheckReport(check_id, "not_checked", "未提供该检查器（检查项不可用）", ())
            )
    return tuple(sorted(reports, key=lambda report: report.check_id))


def _describe(check_id: str, reasons: Mapping[str, str]) -> str:
    return f"{check_id}（{reasons.get(check_id, _REASON_NOT_RETAINED)}）"


def _rationale(
    status: SecurityStatus,
    findings: tuple[SecurityFinding, ...],
    unchecked: tuple[str, ...],
    reasons: Mapping[str, str],
    blocking: bool,
    extra_note: str | None,
) -> str:
    """脱敏结论文本；`not_checked` 不得出现"安全/通过"字样（I16）。"""
    if status == "found":
        high = sum(1 for finding in findings if finding.severity == "high")
        text = f"检查发现 {len(findings)} 项违规模式（其中 high {high} 项）。"
        if blocking:
            text += "已阻断动态执行。"
        if unchecked:
            text += f"另有 {len(unchecked)} 项必需检查未完成："
            text += "；".join(_describe(check_id, reasons) for check_id in unchecked) + "。"
    elif status == "not_found":
        text = "必需检查项全部执行完毕，未发现违规模式。"
    else:
        text = f"检查未完成（{len(unchecked)} 项必需检查未执行）："
        text += "；".join(_describe(check_id, reasons) for check_id in unchecked)
        text += "。结论为「未检查」，不得表述为未发现违规，也不允许进入动态执行。"
    if extra_note:
        text += extra_note
    return redact_text(text)


def conclude(
    findings: Iterable[SecurityFinding],
    *,
    covered: Iterable[str],
    unchecked: Iterable[str],
    reasons: Mapping[str, str] | None = None,
    extra_note: str | None = None,
) -> SecurityDecision:
    """三态推导的唯一入口：`findings`/`covered`/`unchecked` → `SecurityDecision`。

    `covered` 与 `unchecked` 不允许重叠（同一检查项不可能"跑过"又"没跑"）：重叠即 `ValueError`，
    不做静默归一（安全结论不接受"差不多对"）。
    """
    ordered = tuple(sorted(set(findings), key=lambda item: (item.rule_id or "", item.finding_id)))
    covered_ids = tuple(sorted(set(covered)))
    unchecked_ids = tuple(sorted(set(unchecked)))
    overlap = set(covered_ids) & set(unchecked_ids)
    if overlap:
        raise ValueError(f"检查项不得同时出现在 covered 与 unchecked：{sorted(overlap)}")
    if ordered:
        status: SecurityStatus = "found"
    elif unchecked_ids:
        status = "not_checked"
    else:
        status = "not_found"
    blocking = status == "found" and any(finding.severity == "high" for finding in ordered)
    decision = SecurityDecision(
        status=status,
        findings=ordered,
        blocking=blocking,
        covered=covered_ids,
        unchecked=unchecked_ids,
        rationale=_rationale(
            status, ordered, unchecked_ids, dict(reasons or {}), blocking, extra_note
        ),
    )
    assert_decision_invariants(decision)
    return decision


def decide_static(
    sources: Mapping[str, str],
    *,
    language: str,
    checks: Sequence[StaticCheck] | None = None,
    static_findings: Sequence[StaticFinding] | None = None,
    static_rule_aliases: Mapping[str, str] | None = None,
) -> SecurityDecision:
    """静态安全判定（进入 runner **之前**的唯一判定入口）。

    - `sources`：逻辑文件名 → 源码文本（文件名不是内部绝对路径；非白名单名会被脱敏成占位符）；
    - `language`：`python` / `bash`，决定必需检查项集合；
    - `checks`：注入的检查器集合（缺省 `DEFAULT_CHECKS`）；注入子集即表达"某必需检查没跑"；
    - `static_findings`：**可选**的上游静态合法性层明细（纵深防御的相邻层）。传了就并入结论：
      上游 `blocking` 会让本结论同样阻断，并在 `covered` 记入 `STATIC_LAYER_CHECK_ID`；
      `None` 表示本门禁独立成判（该层既不算跑过也不算没跑，不削弱本模块自己的扫描）；
    - `static_rule_aliases`：上游规则 ID → 本模块规则 ID，用于跨层同源折叠（可选）。

    结论只由 `status == "ran"` 的检查项贡献；任何未完成的检查项都进 `unchecked`。
    """
    reports = run_checks(sources, language=language, checks=checks)
    own = tuple(
        (finding, report.origins.get(finding.finding_id, ""))
        for report in reports
        if report.status == "ran"
        for finding in report.findings
    )
    findings = tuple(finding for finding, _origin in own)
    covered = tuple(report.check_id for report in reports if report.status == "ran")
    unchecked = tuple(report.check_id for report in reports if report.status == "not_checked")
    reasons = {
        report.check_id: report.reason or _REASON_NOT_RETAINED
        for report in reports
        if report.status == "not_checked"
    }
    note: str | None = None
    if static_findings is not None:
        aliases = dict(static_rule_aliases or {})
        upstream, collapsed = _merge_upstream_findings(own, static_findings, aliases)
        findings = findings + upstream
        covered = covered + (STATIC_LAYER_CHECK_ID,)
        note = (
            f"已并入上游静态明细 {len(static_findings)} 条"
            f"（同源重复 {collapsed} 条按 (category, rule_id, file) 合并）。"
        )
    return conclude(findings, covered=covered, unchecked=unchecked, reasons=reasons, extra_note=note)


def assert_decision_invariants(decision: SecurityDecision) -> None:
    """校验 I15–I19；违反即抛 `ValueError`（本函数不修改、不修正结论）。"""
    if decision.status not in get_args(SecurityStatus):
        raise ValueError(f"status 越界：{decision.status!r}")
    categories = get_args(SecurityCategory)
    severities = get_args(SecuritySeverity)
    detected_in = get_args(DetectedIn)
    for finding in decision.findings:
        if finding.category not in categories:
            raise ValueError(f"category 越界：{finding.category!r}")
        if finding.severity not in severities:
            raise ValueError(f"severity 越界：{finding.severity!r}")
        if finding.detected_in not in detected_in:
            raise ValueError(f"detected_in 越界：{finding.detected_in!r}")
        if finding.detected_in == "static" and not finding.rule_id:
            raise ValueError("I19：静态发现必须携带 rule_id")
        if not finding.message:
            raise ValueError("finding.message 不得为空")
        if len(finding.message) > 1000:
            raise ValueError("finding.message 超过 1000 字符上限")
    high = any(finding.severity == "high" for finding in decision.findings)
    if decision.blocking != (decision.status == "found" and high):
        raise ValueError("I17：blocking 必须等于 status==found 且存在 high 发现")
    if decision.status == "found" and not decision.findings:
        raise ValueError("I15：found 必须至少有一条 finding")
    if decision.status == "not_found":
        if decision.findings:
            raise ValueError("I15：not_found 不得携带 finding")
        if decision.unchecked:
            raise ValueError("I15：not_found 要求 unchecked 为空（所有必需检查都真的跑过）")
    if decision.status == "not_checked":
        if decision.findings:
            raise ValueError("I15：not_checked 不得携带 finding（有发现时应为 found）")
        if not decision.unchecked:
            raise ValueError("I15：not_checked 要求 unchecked 非空")
    overlap = set(decision.covered) & set(decision.unchecked)
    if overlap:
        raise ValueError(f"检查项不得同时出现在 covered 与 unchecked：{sorted(overlap)}")
    if decision.status == "not_checked":
        for word in _FORBIDDEN_WORDS_WHEN_NOT_CHECKED:
            if word in decision.rationale:
                raise ValueError(f"I16：not_checked 的结论文本不得出现「{word}」字样")


def allows_dynamic_execution(decision: SecurityDecision) -> bool:
    """阻断门：**只有必需检查全部跑过、且没有 high 发现**时才允许进入 runner。

    - `found` + high（`blocking`）⇒ 假；编排层置 `rejected`（`static_blocked`），不得产生 `running`。
      上游静态层判 blocking 时，经 `static_layer_finding` 折算为 high，因此这里同样为假（阻断是"或"）。
    - `not_checked` ⇒ **假**（fail-closed）：检查缺失时拒绝执行而不是"没检查也照跑"；按 I18 这属于
      技术性失败（`failed` + `infrastructure_error`），**不是**安全拒绝，由 `terminal_reason` 区分。
    - `found` 但只有 medium/low ⇒ 真（发现照常记录；I17 规定阻断只由 high 决定）。
    """
    assert_decision_invariants(decision)
    return not decision.blocking and not decision.unchecked


def terminal_reason(decision: SecurityDecision) -> str | None:
    """把结论折算成编排层的终态依据：`static_blocked` / `infrastructure_error` / `None`（放行）。

    这里把 I18 的区分**固化成一个函数**，避免编排层把"安全拒绝"与"技术失败"混起来。
    """
    assert_decision_invariants(decision)
    if decision.blocking:
        return "static_blocked"
    if decision.unchecked:
        return "infrastructure_error"
    return None


def record_runtime_findings(
    decision: SecurityDecision,
    findings: Iterable[SecurityFinding],
) -> SecurityDecision:
    """合并运行期沙箱发现：`runtime:sandbox_violations` 记入 `covered`，三态重新推导。

    只接受 `detected_in == "runtime"` 的发现（静态发现必须由 `decide_static` 产生）。
    """
    merged = tuple(findings)
    for finding in merged:
        if finding.detected_in != "runtime":
            raise ValueError("运行期合并只接受 detected_in == 'runtime' 的发现")
    covered = tuple(set(decision.covered) | {RUNTIME_SANDBOX_CHECK_ID})
    unchecked = tuple(
        check_id for check_id in decision.unchecked if check_id != RUNTIME_SANDBOX_CHECK_ID
    )
    return conclude(decision.findings + merged, covered=covered, unchecked=unchecked, reasons={})


def mark_check_unchecked(
    decision: SecurityDecision,
    *,
    check_id: str,
    reason: str,
) -> SecurityDecision:
    """把某检查项标为未执行（如沙箱不可用 ⇒ 运行期检查没跑）并重新推导三态。

    §4.4 的落地：沙箱缺失时动态检查进 `unchecked`，阻断门随之关闭，运行终态 `failed`
    （`infrastructure_error`），**绝不**退化为无沙箱执行后再声称干净。
    """
    return conclude(
        decision.findings,
        covered=decision.covered,
        unchecked=decision.unchecked + (check_id,),
        reasons={check_id: redact_text(reason)},
    )
