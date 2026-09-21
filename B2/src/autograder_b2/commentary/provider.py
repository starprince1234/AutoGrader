"""评语 provider：**可配置、默认关闭、只用标准库**（`ADR-0009` 决策 4）。

三条不可协商项：

1. **默认关闭**：没有配置就是没有 provider 对象——`GET` 返回 `unavailable`，`POST` 返回 `409`
   并指名缺哪些变量，**且没有任何出网调用**（不是"调用了但忽略结果"，是根本不存在调用点）。
2. **不引入生产依赖**：HTTP 走标准库 `urllib.request`（项目规则：不为一个可选功能加依赖）。
   接口按 OpenAI 兼容的 `POST {base_url}/chat/completions` 形态实现，因此**自建端点与外部服务
   走同一个接口**（`ADR-0009` 方案 E：强制 self-hosted 被否，但它是允许的部署选项）。
3. **失败不得影响分数轨**：provider 的任何失败都在这里被归一成 `CommentaryProviderError`，
   由调用方标记该条评语 `failed`，评测状态与分数**一律不动**。

**本模块只负责把响应信封里的 content 取出来**，不解释它的内容：`CHG-0018` 起 content 是一个
JSON 对象的文本，长度与形状的判定在 `commentary/parsing.py`（那里的失败同样只影响这一条评语）。
把解析放在这里会让"传输失败"与"协议失败"混成一种错误，而它们的排查方向完全不同。

`transport` 是**仅测试可见**的注入点（与 `runner.run_case` 的 `probe`/`launcher` 同一形态）：
测试用它证明"未配置时不会出网"，也让生成路径可以在无网环境里被测。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

#: 请求超时（秒）。provider 故障不该把一次 HTTP 请求挂死——它是可选轨，超时即失败。
DEFAULT_TIMEOUT_SECONDS = 30.0

#: 端点路径（OpenAI 兼容形态）。base_url 由部署方给出，路径由本模块定死。
CHAT_COMPLETIONS_PATH = "/chat/completions"

#: 响应体的读取上限（防御性：provider 返回巨大响应时不把无界内容读进内存）。
RESPONSE_LIMIT_BYTES = 1 << 20


class CommentaryProviderError(RuntimeError):
    """provider 调用失败（网络/HTTP/响应形态/超长）。

    消息**只含归类事实**（HTTP 状态码、异常类型、长度），不回显 prompt、证据或响应正文：
    那些内容里可能有部署方不愿外传的东西，而失败原因本来也不需要它们。
    """


@dataclass(frozen=True, slots=True)
class CommentaryProviderConfig:
    """一份可用的 provider 配置（`config.load_commentary_settings` 负责从环境读出来）。"""

    provider: str
    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}{CHAT_COMPLETIONS_PATH}"


class CommentaryTransport(Protocol):
    """HTTP 传输的窄协议（可注入 ⇒ "未配置时不出网"可被断言）。"""

    def post_json(
        self, url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
    ) -> tuple[int, str]: ...


class UrllibTransport:
    """标准库 `urllib.request` 实现（唯一的默认传输）。"""

    def post_json(
        self, url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
    ) -> tuple[int, str]:
        body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read(RESPONSE_LIMIT_BYTES).decode(
                    "utf-8", errors="replace"
                )
        except urllib.error.HTTPError as error:
            return int(error.code), error.read(RESPONSE_LIMIT_BYTES).decode(
                "utf-8", errors="replace"
            )
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise CommentaryProviderError(
                f"provider request failed: {type(error).__name__}"
            ) from error


class CommentaryProvider(Protocol):
    """生成一次模型响应的最小接口（返回模型 content 原文；形状由 `parsing` 判定）。"""

    config: CommentaryProviderConfig

    def complete(self, prompt: str) -> str: ...


class HttpCommentaryProvider:
    """OpenAI 兼容的 chat completions 客户端（`provider`/`model` 都是配置项）。"""

    def __init__(
        self, config: CommentaryProviderConfig, *, transport: CommentaryTransport | None = None
    ) -> None:
        self.config = config
        self._transport = transport if transport is not None else UrllibTransport()

    def complete(self, prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        status, body = self._transport.post_json(
            self.config.endpoint, payload, headers, self.config.timeout_seconds
        )
        if status < 200 or status >= 300:
            # 不回显响应正文：它可能包含 provider 侧的诊断细节（甚至回显请求）。
            raise CommentaryProviderError(f"provider returned HTTP {status}")
        try:
            document = json.loads(body)
        except json.JSONDecodeError as error:
            raise CommentaryProviderError("provider response is not valid JSON") from error
        content = _extract_content(document)
        if content is None or not content.strip():
            # 只判"信封里有没有内容"。内容**是否符合约定形状**由 `parsing.parse_generation`
            # 判定：那是协议失败，不是传输失败，两者的失败原因必须能分开读。
            raise CommentaryProviderError("provider response has no message content")
        return content


def _extract_content(document: Any) -> str | None:
    """从 OpenAI 兼容响应里取 content（取不到即 `None`，由调用方归类为失败）。"""
    if not isinstance(document, Mapping):
        return None
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, Mapping):
        return None
    message = first.get("message")
    if not isinstance(message, Mapping):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


def build_provider(
    config: CommentaryProviderConfig, *, transport: CommentaryTransport | None = None
) -> HttpCommentaryProvider:
    """生产装配：唯一的 provider 实现（没有环境变量可选后端）。"""
    return HttpCommentaryProvider(config, transport=transport)


__all__ = [
    "CHAT_COMPLETIONS_PATH",
    "DEFAULT_TIMEOUT_SECONDS",
    "RESPONSE_LIMIT_BYTES",
    "CommentaryProvider",
    "CommentaryProviderConfig",
    "CommentaryProviderError",
    "CommentaryTransport",
    "HttpCommentaryProvider",
    "UrllibTransport",
    "build_provider",
]
