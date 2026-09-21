"""参考解答验证在 B3 服务通道上的两个调用：取参考解答（`CHG-0023`）与记录存证（`CHG-0008`）。

**为什么是一个模块而不是两个各写一份**：`ADR-0015` 的全部要点是"复用 B2 **已有的** B3 服务通道"。
两处调用因此共用同一个 `B3Endpoint`（`base_url` + `authorization` + 超时 + 读取上限），
**没有新增凭据、没有新增配置项、没有新增信任关系**。装配在 `runtime._wire_evaluation_chain`：
取包与这两处调用拿的是同一个 `B3Endpoint` 对象（有测试断言这一条，而不是靠"读代码看出来"）。

## 两个调用的失败语义**刻意不同**

| 调用 | 上游失败时 | 理由 |
|---|---|---|
| `fetch_reference_answer` | 抛异常，由路由映射成 `404`/`422`/`503` | 它是**输入**：拿不到参考解答就没有可执行的东西，不能返回一个"看起来验证过"的结果 |
| `record_attestation` | 返回 `None` | 契约明说 `attestation_id: null` = B3 没有记录，而**观察到的 `cases` 仍要返回**：那是真实发生的事。这里不是"吞异常"，是契约规定的状态表达 |

`ReferenceAnswerView.reference_answer` 用 `repr=False`：它是**受保护内容**（参考解答正文），
与 `B3Endpoint.authorization`、`CommentaryProviderConfig.api_key` 是同一条规则——
一次 `print`/日志/异常链就足以把它带出去。它也不进 `__str__`（`dataclass` 的默认实现只用 `repr`）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from autograder_b2.integration.adapters import B3Endpoint

#: 参考解答的语言与适配器类型取值域（`common.yaml` 的 `Language` 与 `QuestionVersionPublic.adapter_kind`）。
LANGUAGES = ("python", "bash")
ADAPTER_KINDS = ("stdin", "file", "callable")

#: `/api/v1/question-versions/{id}` 下的两条路径（`b3.yaml`）。路径由本模块定死，base_url 由部署给出。
REFERENCE_ANSWER_PATH = "/api/v1/question-versions/{version}/reference-answer"
ATTESTATIONS_PATH = "/api/v1/question-versions/{version}/attestations"

#: 存证里的 `verifier` 标签：与 CHG-0008 的既有取值（`MAIN/scripts/...`）同一形态，
#: 让 B3 那一侧读到的是一句"谁跑过"，而不是一个不透明的 id。
VERIFIER_LABEL = "autograder-b2/reference-verification"

#: 转述上游 `detail` 的长度上限（超过即丢弃，不截断——理由见 `_problem_detail`）。
PROBLEM_DETAIL_LIMIT = 500

#: 从 OpenAI 式响应里取 JSON 之外的一切都不做：B3 是同一个平台的模块，响应形状是契约。
_REQUIRED_ANSWER_FIELDS = (
    "question_version_id",
    "content_digest",
    "language",
    "adapter_kind",
    "reference_answer",
)
_DIGEST_HEX = set("0123456789abcdef")


class ReferenceAnswerNotFound(LookupError):
    """该题目版本不存在（上游 `404`）——对外 `404`。"""


class ReferenceAnswerMissing(RuntimeError):
    """版本存在但**没有**参考解答（上游 `409`）——对外 `422`。

    与"不存在"必须分开：资源在，是它的状态让验证不可能（`CHG-0023` §3.1 把这一点写进了契约）。
    """


class ReferenceAnswerUnavailable(RuntimeError):
    """参考解答取不到（网络/超时/上游 `401`/`403`/`5xx`/响应形状不符）——对外 `503`。

    消息**只含归类事实**（状态码、异常类型、字段名），不回显响应体：它含参考解答正文。
    """


class B3Transport(Protocol):
    """HTTP 传输的窄协议（可注入 ⇒ "上游返回 404/409/500 时各走哪条路"可被断言）。"""

    def get_json(
        self, url: str, headers: Mapping[str, str], timeout: float, limit: int
    ) -> tuple[int, str]: ...

    def post_json(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
        limit: int,
    ) -> tuple[int, str]: ...


class UrllibB3Transport:
    """标准库 `urllib.request` 实现（唯一的默认传输，与评语 provider 同形态：不加依赖）。"""

    def get_json(
        self, url: str, headers: Mapping[str, str], timeout: float, limit: int
    ) -> tuple[int, str]:
        request = urllib.request.Request(url, method="GET")
        for name, value in headers.items():
            request.add_header(name, value)
        return self._send(request, timeout, limit)

    def post_json(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
        limit: int,
    ) -> tuple[int, str]:
        body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        for name, value in headers.items():
            request.add_header(name, value)
        return self._send(request, timeout, limit)

    def _send(
        self, request: urllib.request.Request, timeout: float, limit: int
    ) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read(limit + 1).decode(
                    "utf-8", errors="replace"
                )
        except urllib.error.HTTPError as error:
            # 上游的**状态码**是路由层要用的分类依据（404/409 与 5xx 语义不同）；
            # 响应体不回显（它可能含参考解答正文）。
            return int(error.code), error.read(limit + 1).decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise ReferenceAnswerUnavailable(
                f"B3 request failed: {type(error).__name__}"
            ) from error


@dataclass(frozen=True, slots=True)
class ReferenceAnswerView:
    """契约 `ReferenceAnswer` 的只读视图。

    `reference_answer` 是**受保护正文**：`repr=False`，且本模块的任何日志/异常都不携带它。
    """

    question_version_id: str
    content_digest: str
    language: str
    adapter_kind: str
    reference_answer: str = field(repr=False)


class B3VerificationClient:
    """取参考解答 + 记录存证（共用注入的 `B3Endpoint`，因此与取包是同一个通道）。"""

    def __init__(self, endpoint: B3Endpoint, *, transport: B3Transport | None = None) -> None:
        self._endpoint = endpoint
        self._transport = transport if transport is not None else UrllibB3Transport()

    @property
    def endpoint(self) -> B3Endpoint:
        return self._endpoint

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "authorization": self._endpoint.authorization,
            "accept": "application/json",
        }

    def fetch_reference_answer(self, question_version_id: str) -> ReferenceAnswerView:
        """`GET .../reference-answer`（internal + `serviceToken`）。"""
        version = _canonical_uuid(question_version_id)
        status, body = self._transport.get_json(
            self._url(REFERENCE_ANSWER_PATH, version),
            self._headers,
            self._endpoint.timeout_seconds,
            self._endpoint.max_response_bytes,
        )
        if status == 404:
            raise ReferenceAnswerNotFound("the question version does not exist")
        if status == 409:
            # **转述 B3 的原因，绝不自己造一句**：`409` 的含义是"版本的当前状态让它不能验证"，
            # 但**为什么**推不出来——真实 B3 已经至少有两种（实测）：
            #   `a draft version has no evaluation package; publish it first`
            #   `this version has no content digest yet, so an answer executed against it could not
            #    be attested: a version needs at least one test …`
            # 再加上"该语言确实没有参考解答"。三者要教师做的事完全不同，而一句自造的
            # "carries no reference answer" 在前两种情形下**是假的**（版本里明明有参考解答）。
            raise ReferenceAnswerMissing(_refusal("the reference answer", status, body))
        if status < 200 or status >= 300:
            # 含 401/403：那是**本部署的服务凭据**不成立（配置问题），不是调用者的错。
            raise ReferenceAnswerUnavailable(f"B3 returned HTTP {status} for the reference answer")
        return _parse_answer(body, expected_version=version)

    def record_attestation(
        self,
        *,
        question_version_id: str,
        content_digest: str,
        cases_total: int,
        cases_passed: int,
        verified_at: str,
    ) -> str | None:
        """`POST .../attestations`（internal + `serviceToken`）→ `attestation_id` 或 `None`。

        契约把"B3 没有记录"表达为 `attestation_id: null`，**不是**错误：调用方仍会拿到逐用例
        观察结果。因此这里对**任何**上游失败都返回 `None`（并让调用方记一条依赖故障日志），
        但内部缺陷（例如上游返回 201 却没有 `attestation_id`）照旧抛出——那不是"没记录"，
        是"记了但读不回来"，把它写成 `None` 会谎报成"没记录"。
        """
        version = _canonical_uuid(question_version_id)
        payload: dict[str, Any] = {
            "content_digest": content_digest,
            "verifier": VERIFIER_LABEL,
            "cases_total": cases_total,
            "cases_passed": cases_passed,
            "verified_at": verified_at,
        }
        headers = {**self._headers, "content-type": "application/json"}
        try:
            status, body = self._transport.post_json(
                self._url(ATTESTATIONS_PATH, version),
                payload,
                headers,
                self._endpoint.timeout_seconds,
                self._endpoint.max_response_bytes,
            )
        except ReferenceAnswerUnavailable:
            return None
        if status != 201:
            # 409（版本不是草稿 / 摘要与 B3 重算的不符）与 5xx 都归"没有记录"：
            # 契约只让 `attestation_id` 表达这一件事，理由由日志记，不塞进响应。
            return None
        try:
            document = json.loads(body)
        except json.JSONDecodeError as error:
            raise ReferenceAnswerUnavailable(
                "B3 recorded the attestation but its response is not valid JSON"
            ) from error
        if not isinstance(document, Mapping):
            raise ReferenceAnswerUnavailable("B3 attestation response is not a JSON object")
        identifier = document.get("attestation_id")
        if not isinstance(identifier, str):
            raise ReferenceAnswerUnavailable("B3 attestation response has no attestation_id")
        return _canonical_uuid(identifier)

    def _url(self, template: str, version: str) -> str:
        return f"{self._endpoint.base_url.rstrip('/')}{template.format(version=version)}"


def _refusal(what: str, status: int, body: str) -> str:
    """上游**拒了**（`409`）时的对外原因：转述它的 `detail`，取不到就只陈述确定性的事实。

    两条纪律：

    1. **只转述，不替换**。上游的 `detail` 是它自己的原因；本层**不得**说出一件上游没说过的事实
       （曾经这里自造过一句 "carries no reference answer"，而真实原因可能是"还没有 content_digest"
       —— 对那种版本，那句话是假的，而且会让教师去做一件错的事）。
    2. 取不到 `detail` 时**也不猜**：只说 `409` 本身成立的事实（"当前的版本状态下不可验证"），
       把"不知道具体原因"如实留在措辞里。
    """
    upstream = _problem_detail(body)
    if upstream is None:
        return (
            f"B3 refused to serve {what} (HTTP {status}) without a reason; the version cannot be "
            "verified in its current state"
        )
    return f"B3 refused to serve {what} (HTTP {status}): {upstream}"


def _problem_detail(body: str) -> str | None:
    """从 Problem Details 响应体里取 `detail`（取不到即 `None`）。

    与取包那侧（`integration.adapters._problem_detail`）同一条纪律：只接受契约固定的形状
    （JSON 对象里的非空字符串 `detail`），并有长度上限——超过即丢弃而不是截断，因为一句被人为截断的
    "原因"会把关键的那半句删掉。这里失败一律返回 `None`：调用方已经有状态码可用。
    """
    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(document, Mapping):
        return None
    detail = document.get("detail")
    if not isinstance(detail, str) or not detail.strip():
        return None
    if len(detail) > PROBLEM_DETAIL_LIMIT:
        return None
    return detail.strip()


def _canonical_uuid(value: object) -> str:
    """规范化为标准 UUID 文本；不合法即拒绝（绝不把任意字符串拼进 URL）。"""
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as error:
        raise ReferenceAnswerUnavailable("value is not a UUID") from error


def _parse_answer(body: str, *, expected_version: str) -> ReferenceAnswerView:
    """按契约 `ReferenceAnswer` 解析：多一个键、少一个键、类型不对都算**不可用**。

    严格解析的理由与 `runner.package.parse_evaluation_package` 相同：上游形状不合规意味着
    "这份输入不可信"，此时唯一的诚实结论是"取不到"，而不是挑出认识的字段继续跑。
    """
    try:
        document = json.loads(body)
    except json.JSONDecodeError as error:
        raise ReferenceAnswerUnavailable("reference answer response is not valid JSON") from error
    if not isinstance(document, Mapping):
        raise ReferenceAnswerUnavailable("reference answer response is not a JSON object")
    unknown = sorted(set(document) - set(_REQUIRED_ANSWER_FIELDS))
    if unknown:
        raise ReferenceAnswerUnavailable(
            "reference answer response has unknown fields: " + ", ".join(unknown)
        )
    missing = [name for name in _REQUIRED_ANSWER_FIELDS if name not in document]
    if missing:
        raise ReferenceAnswerUnavailable(
            "reference answer response is missing fields: " + ", ".join(missing)
        )

    version = _text_field(document, "question_version_id")
    if version != expected_version:
        # 上游给了另一个版本的参考解答：绑定摘要会绑到"实际执行的那份内容"，
        # 而版本号不符意味着这份响应不可信，宁可不验证。
        raise ReferenceAnswerUnavailable("reference answer does not match the requested version")
    digest = _text_field(document, "content_digest")
    if len(digest) != 64 or not set(digest) <= _DIGEST_HEX:
        raise ReferenceAnswerUnavailable("reference answer content_digest is not a sha256 digest")
    language = _text_field(document, "language")
    if language not in LANGUAGES:
        raise ReferenceAnswerUnavailable(f"reference answer language is unsupported: {language!r}")
    adapter_kind = _text_field(document, "adapter_kind")
    if adapter_kind not in ADAPTER_KINDS:
        raise ReferenceAnswerUnavailable(
            f"reference answer adapter_kind is unsupported: {adapter_kind!r}"
        )
    answer = document["reference_answer"]
    if not isinstance(answer, str) or not answer:
        raise ReferenceAnswerUnavailable("reference answer body is not a non-empty string")
    return ReferenceAnswerView(
        question_version_id=version,
        content_digest=digest,
        language=language,
        adapter_kind=adapter_kind,
        reference_answer=answer,
    )


def _text_field(document: Mapping[str, Any], name: str) -> str:
    value = document[name]
    if not isinstance(value, str) or not value:
        raise ReferenceAnswerUnavailable(f"reference answer field {name} is not a non-empty string")
    return value


__all__ = [
    "ADAPTER_KINDS",
    "ATTESTATIONS_PATH",
    "LANGUAGES",
    "REFERENCE_ANSWER_PATH",
    "VERIFIER_LABEL",
    "B3Transport",
    "B3VerificationClient",
    "ReferenceAnswerMissing",
    "ReferenceAnswerNotFound",
    "ReferenceAnswerUnavailable",
    "ReferenceAnswerView",
    "UrllibB3Transport",
]
