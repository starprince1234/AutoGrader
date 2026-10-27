"""B3 公开题目上下文的安全读取（B2 侧消费者；`learning-context` 安全视图由成员三提供）。

## 为什么 B2 自己去读，而不是让 B1 把题面塞进请求体

`TC-HINT-089` 的边界是"学生只看到自己的生成结果，不展示 service token 或内部调用信息"，而
`POST /learning-hints` **没有模型参数、也没有题目上下文字段**：客户端不能自报题面（否则题面就成了
一条可注入的指令通道，见 `TC-HINT-088`）。因此 B2 用 `run.question_version_id` 自己去取公开上下文。

## 只接受白名单字段

响应被当作**不可信数据**处理：只取 `title` / `stem_markdown` / `language` / `tags` / `adapter_kind`，
其余字段（包括任何 `reference_answer`、`cases`、`fixture`）**根本不读**。字段类型不对或超长即当作
"没有上下文"，而不是把畸形内容拼进 prompt；题干长度被截断到部署上限（截断是**输入预算**，不是
安全边界——安全边界是"这些字段本来就不含答案"）。

## 读不到时怎么办

B3 基址/凭据未配置或 B3 返回错误时，提示仍然可以生成，只是 `context_scope` 里如实少一节；
默认路径来自契约 `DEFAULT_LEARNING_CONTEXT_PATH`，`B2_LEARNING_CONTEXT_PATH` 只用于覆盖。少读是严格更安全的：没有题面，模型就无法复述题干里的敏感内容。
失败不会重试、不影响权威评测，也不会被写成"已读取"。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

#: 题干进入 prompt 的最大字符数（输入预算；超出部分丢弃并记录 fact，不改变安全判断）。
MAX_STEM_CHARS = 6000
#: 标签最多取多少个（B3 契约 `QuestionPublic.tags` 的 maxItems 是 20）。
MAX_TAGS = 20
#: 响应体读取上限。
RESPONSE_LIMIT_BYTES = 1 << 20
DEFAULT_TIMEOUT_SECONDS = 10.0

#: 契约 `getQuestionVersionLearningContext` 的路径（`b3.yaml` `0.18.0` 起）。B2 默认就用它，
#: 只有部署显式设置 `B2_LEARNING_CONTEXT_PATH` 才覆盖。B3 基址/凭据未配置时**不构造客户端**，
#: 因此"没有联调过 B3"不会变成一次出网调用。
DEFAULT_LEARNING_CONTEXT_PATH = (
    "/api/v1/question-versions/{question_version_id}/learning-context"
)

#: 允许进入 prompt 的公开字段（顺序即 `context_scope` 的命名顺序）。
CONTEXT_FIELDS: tuple[str, ...] = (
    "question.title",
    "question.stem_markdown",
    "question.language",
    "question.tags",
    "question.adapter_kind",
)

#: 语言枚举（与 `common.yaml` 的 `Language` 一致）；未知取值不猜，直接丢弃该字段。
LANGUAGES: tuple[str, ...] = ("python", "bash")
ADAPTER_KINDS: tuple[str, ...] = ("stdin", "file", "callable")


class ContextTransport(Protocol):
    def get_json(
        self, url: str, headers: Mapping[str, str], timeout: float
    ) -> tuple[int, str]: ...


class UrllibContextTransport:
    """标准库实现（没有新依赖；与评语 provider 的传输分层同一形态）。"""

    def get_json(
        self, url: str, headers: Mapping[str, str], timeout: float
    ) -> tuple[int, str]:
        request = urllib.request.Request(url, method="GET")
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
            raise ContextUnavailable(type(error).__name__) from error


class ContextUnavailable(RuntimeError):
    """B3 上下文不可用（网络/HTTP/形状/超长）。提示生成照常继续，只是少读一节。"""


@dataclass(frozen=True, slots=True)
class QuestionContext:
    """公开题目上下文（只含白名单字段；缺失的字段为 `None`）。"""

    title: str | None
    stem_markdown: str | None
    language: str | None
    tags: tuple[str, ...]
    adapter_kind: str | None
    truncated: bool = False

    def scope_paths(self) -> tuple[str, ...]:
        """实际读到的字段路径（写进 `context_scope`，让"读了什么"可审计）。"""
        present: list[str] = []
        if self.title is not None:
            present.append("question.title")
        if self.stem_markdown is not None:
            present.append("question.stem_markdown")
        if self.language is not None:
            present.append("question.language")
        if self.tags:
            present.append("question.tags")
        if self.adapter_kind is not None:
            present.append("question.adapter_kind")
        return tuple(present)

    def as_prompt_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.title is not None:
            payload["title"] = self.title
        if self.stem_markdown is not None:
            payload["stem_markdown"] = self.stem_markdown
        if self.language is not None:
            payload["language"] = self.language
        if self.tags:
            payload["tags"] = list(self.tags)
        if self.adapter_kind is not None:
            payload["adapter_kind"] = self.adapter_kind
        return payload


class B3LearningContextClient:
    """按 `question_version_id` 读取公开上下文（服务身份；只用部署既有 B3 通道）。"""

    def __init__(
        self,
        base_url: str,
        *,
        path_template: str,
        authorization: str,
        transport: ContextTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("B3 base URL must be an absolute http(s) URL")
        if "{question_version_id}" not in path_template:
            raise ValueError("path template must contain {question_version_id}")
        if not authorization:
            raise ValueError("B3 learning-context needs the service authorization header")
        self._base_url = base_url.rstrip("/")
        self._path_template = path_template
        self._authorization = authorization
        self._transport = transport if transport is not None else UrllibContextTransport()
        self._timeout = timeout_seconds

    def fetch(self, question_version_id: str) -> QuestionContext | None:
        """读取并白名单化；不可用返回 `None`（调用方据此少读一节，不重试）。"""
        url = f"{self._base_url}{self._path_template.format(question_version_id=question_version_id)}"
        status, body = self._transport.get_json(
            url,
            {"accept": "application/json", "authorization": self._authorization},
            self._timeout,
        )
        if status < 200 or status >= 300:
            return None
        try:
            document = json.loads(body)
        except json.JSONDecodeError:
            return None
        if not isinstance(document, Mapping):
            return None
        return _context(document)


def _context(document: Mapping[str, Any]) -> QuestionContext:
    """白名单化：只读五个公开字段，类型不符即丢弃该字段（不猜、不转换）。"""
    stem = document.get("stem_markdown")
    truncated = False
    if isinstance(stem, str):
        if len(stem) > MAX_STEM_CHARS:
            stem = stem[:MAX_STEM_CHARS]
            truncated = True
    else:
        stem = None
    title = document.get("title") if isinstance(document.get("title"), str) else None
    language = document.get("language")
    if language not in LANGUAGES:
        language = None
    adapter_kind = document.get("adapter_kind")
    if adapter_kind not in ADAPTER_KINDS:
        adapter_kind = None
    raw_tags = document.get("tags")
    tags: tuple[str, ...] = ()
    if isinstance(raw_tags, list):
        tags = tuple(
            tag for tag in raw_tags[:MAX_TAGS] if isinstance(tag, str) and 0 < len(tag) <= 40
        )
    return QuestionContext(
        title=title,
        stem_markdown=stem,
        language=language,
        tags=tags,
        adapter_kind=adapter_kind,
        truncated=truncated,
    )


__all__ = [
    "ADAPTER_KINDS",
    "CONTEXT_FIELDS",
    "DEFAULT_LEARNING_CONTEXT_PATH",
    "DEFAULT_TIMEOUT_SECONDS",
    "LANGUAGES",
    "MAX_STEM_CHARS",
    "B3LearningContextClient",
    "ContextTransport",
    "ContextUnavailable",
    "QuestionContext",
    "UrllibContextTransport",
]
