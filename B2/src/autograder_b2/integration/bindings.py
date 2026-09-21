"""五项绑定摘要里由**组合层**负责的两项：`question_version_binding` 与 `environment_digest`。

## 为什么这两项由组合层算

`ADR-0005` 规定"同绑定摘要 ⇒ 同分数同报告摘要"。五项摘要里：

| 摘要 | 来源 | 谁算 |
|---|---|---|
| `submission_digest` | 提交文件（路径 + 各文件 SHA-256） | 接收层（`domain.envelope` 不变量的落地处） |
| `package_digest` | evaluation package 自身 | B3（package 契约字段），B2 只读取 |
| `runner_policy_digest` | package 声明的沙箱策略 | runner（`runner.policy.policy_digest`，逐字段规范化） |
| `question_version_binding` | 题目版本 × package 内容 | **组合层（本模块）** |
| `environment_digest` | 执行环境身份 | **组合层（本模块）** |

后两项没有任何单一实现方拥有全部素材：题目版本标识来自 B3 取包请求、package 摘要来自 package 本身、
解释器身份来自沙箱策略、环境标签来自部署配置。因此它们只能在把 B3/runner/部署配置接在一起的地方
计算——即组合层。把它们散进 `ports`/`report`/`store` 会让同一事实产生多个计算点（本仓库明令禁止的双写）。

## 公式（规范化 JSON → 紧凑分隔符 → SHA-256，与 `policy_digest` 同构）

```text
question_version_binding = sha256(json({"schema": "b2.question-version-binding.v1",
                                        "question_version_id": <uuid>,
                                        "package_digest": <64 位小写 hex>}))

environment_digest      = sha256(json({"schema": "b2.environment.v1",
                                       "language": <python|bash>,
                                       "interpreter": <策略里该语言的解释器绝对路径>,
                                       "environment_tag": <部署声明的环境标签>}))
```

规范化的两点理由：JSON 排序键 + 紧凑分隔符给出**单射**编码（换行拼接在取值可含换行时不单射），
且与 `runner.policy.policy_digest` 用同一套规范化方式，避免"同一件事两套编码"。

## 诚实边界（必须与文档一致的已知限制）

- `environment_tag` 由部署显式声明（配置项 `B2_ENVIRONMENT_TAG`），**不是**运行时观测值：B2 在取包
  阶段不执行沙箱，无法读取沙箱镜像/rootfs 的真实内容摘要。未配置时取 `unset`，此时**仅 rootfs 内容
  不同、而解释器路径与语言相同的两个环境不会被区分**——这是本公式的已知限制，不声称它覆盖 rootfs。
  要让环境身份真正可信，部署必须把镜像摘要（例如容器镜像 digest）写进该配置。
- `interpreter` 取策略里该语言的解释器**绝对路径**（`RunnerPolicy.interpreter_for`），它是 B2 钉住的
  解释器身份，不是观测到的版本号字符串。
"""

from __future__ import annotations

import hashlib
import json
import re

#: 规范化负载的 schema 标签：公式或字段集变更时必须改标签（否则旧摘要会被误认为同义）。
ENVIRONMENT_SCHEMA = "b2.environment.v1"
QUESTION_VERSION_BINDING_SCHEMA = "b2.question-version-binding.v1"

#: 部署未声明环境标签时的取值。**不是**"任何环境"的同义词：它是一个诚实的"未声明"标记。
UNSET_ENVIRONMENT_TAG = "unset"

#: 环境标签的字符集上限：允许镜像摘要（`sha256:...`）、版本号、短标识；拒绝空白与控制字符。
_ENVIRONMENT_TAG_RE = re.compile(r"^[A-Za-z0-9_.:+@/-]{1,200}$")

_LANGUAGES = ("python", "bash")


def _canonical_digest(payload: dict[str, str]) -> str:
    """排序键 + 紧凑分隔符的 JSON → UTF-8 → SHA-256（小写 hex）。"""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_uuid_text(value: object, name: str) -> str:
    import uuid as _uuid

    try:
        return str(_uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是 UUID 形态的字符串") from exc


def _require_digest_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{name} 必须是 64 位小写十六进制摘要")
    return value


def question_version_binding(*, question_version_id: str, package_digest: str) -> str:
    """题目版本 × package 内容 的绑定摘要。

    两者一起绑定，是因为"同一个题目版本号"在不同时刻可能对应不同 package 内容（发布是不可变的，
    但取包端点可能因配置错误返回别的版本）——只绑版本号会让"版本号相同 ⇒ 同分"变成谎话。
    """
    return _canonical_digest(
        {
            "schema": QUESTION_VERSION_BINDING_SCHEMA,
            "question_version_id": _require_uuid_text(question_version_id, "question_version_id"),
            "package_digest": _require_digest_text(package_digest, "package_digest"),
        }
    )


def environment_digest(*, language: str, interpreter: str, environment_tag: str) -> str:
    """执行环境身份摘要（语言 + 解释器身份 + 部署声明的环境标签）。"""
    if language not in _LANGUAGES:
        raise ValueError(f"未知语言：{language!r}")
    if not isinstance(interpreter, str) or not interpreter.startswith("/") or "\\" in interpreter:
        # 与 runner 的 `build_argv` 同一条要求：解释器必须是 POSIX 绝对路径（相对路径会被沙箱解析成别的东西）。
        raise ValueError("interpreter 必须是 POSIX 绝对路径")
    if not isinstance(environment_tag, str) or not _ENVIRONMENT_TAG_RE.match(environment_tag):
        raise ValueError("environment_tag 必须是 1-200 位受限字符集（镜像摘要/版本号/短标识）")
    return _canonical_digest(
        {
            "schema": ENVIRONMENT_SCHEMA,
            "language": language,
            "interpreter": interpreter,
            "environment_tag": environment_tag,
        }
    )
