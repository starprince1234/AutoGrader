"""参考解答验证端点（`CHG-0019` / `ADR-0015` 的 `POST /api/v1/reference-verifications`）。

契约权威：`MAIN/contracts/openapi/b2.yaml` 的 `verifyReferenceAnswer` 与 `ReferenceVerification`。
本模块只做四件事，其余全在 `autograder_b2.reference`：

1. **可用性**：参考解答验证没装配（缺 B3 通道/工作区根）即 `503`，而不是一个"看起来跑过"的空结果；
2. **认证与授权**：`bearerAuth`（`401`）→ 角色与课程作用域（`403`）。
   契约要的是"该题目的课程写权限"，B2 只能判到"staff + 至少一门课"——第三层不可判定，
   原因与记录见 `auth.authorization.require_reference_verification_authority` 与 `docs/api/contract.md`；
3. **请求体**：`{"question_version_id": ...}`，`additionalProperties: false`，形状不对即 `422`；
4. **调服务**，把它的四类结论映射成契约声明的状态码：`404`（版本不存在）、
   `422`（版本存在但状态让验证不可能：没有参考解答 / 还没有 evaluation package）、
   `503`（依赖不可用：沙箱、B3 通道、两侧声明不一致）。`503` 的两种来源在**计数**上不同：
   "本平台没有沙箱"是文档化的平台条件，不计入 `record_failure()`；其余原因计入。

**不得**在这里做任何"顺带"的事：不落库（服务层不碰引擎）、不返回受保护内容
（响应字段集合由 `ReferenceVerification` 锁定）、不因为沙箱不可用而返回伪成功。
内部一致性缺陷（`ReferenceVerificationDefect`）**不被捕获**：宿主记 500，
与 `evaluation_routes` 对缺陷的处置同一口径——缺陷不伪装成客户端错误或依赖故障。

**一处"看起来等价、实际决定生死"的选择**：阻塞的服务调用必须经
`run_in_threadpool` 下线，不能在事件循环里直接调。本端点是 B2 里**第一个在宿主请求路径内
同步回调宿主自身**的路径（`B2_B3_BASE_URL` 指向同一个进程），在循环里直接调就会自我等待到超时。
理由与取舍写在 handler 内的注释里；回归用例是
`tests/test_reference_verification.py::test_the_blocking_b3_call_runs_off_the_event_loop`。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from autograder_b2.api.errors import (
    FORBIDDEN,
    UNAUTHORIZED,
    Problem,
    problem_response,
)
from autograder_b2.auth.authorization import (
    AuthorizationError,
    require_reference_verification_authority,
)
from autograder_b2.auth.tokens import AuthenticationError, Principal
from autograder_b2.reference.verification import (
    ReferenceSandboxUnavailable,
    ReferenceVerificationNotFound,
    ReferenceVerificationNotPossible,
    ReferenceVerificationUnavailable,
)
from autograder_b2.runtime import B2Runtime

#: 请求体允许的键（契约 `additionalProperties: false`：多一个键即拒绝）。
_REQUEST_FIELDS = frozenset({"question_version_id"})


def build_reference_router(runtime: B2Runtime) -> APIRouter:
    """参考解答验证路径族（闭包持有 `runtime`，与其它路由族同一形态）。"""
    router = APIRouter()

    @router.post("/api/v1/reference-verifications")
    async def verify_reference_answer(request: Request) -> Response:
        """在沙箱里执行该版本的参考解答并逐用例比对；**不落库**，成功才产生存证。"""
        instance = request.url.path
        unavailable = runtime.references_unavailable()
        if unavailable is not None:
            return _not_available(instance, unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response
        try:
            require_reference_verification_authority(principal)
        except AuthorizationError:
            return problem_response(FORBIDDEN, instance)

        payload_or_response = await _read_body(request, instance)
        if isinstance(payload_or_response, Response):
            return payload_or_response

        assert runtime.references is not None  # references_unavailable() 通过即已装配
        try:
            # **必须把这次阻塞调用移出事件循环**——这不是风格问题，是会死锁的选择。
            #
            # `verify()` 会同步 HTTP 请求 `B2_B3_BASE_URL` 上的 B3 内部端点。在**组合宿主**里那个
            # 地址就是**正在服务本请求的同一个进程**（`127.0.0.1:18000`）：如果在事件循环里直接调它，
            # 宿主就没有机会去服务那个嵌套请求 ⇒ 自我等待到超时。真实端口实测（修复前）：
            # `503 B3 request failed: TimeoutError`，耗时 10.03s ≈ 包超时 10s，而同一个 B3 端点
            # 从外部调不到 1 秒。评测链的取包没暴露这件事，只因为它跑在**独立的 B2 worker 进程**里，
            # 不在宿主请求路径上——本端点是第一个在宿主请求路径内同步回调宿主的路径。
            #
            # 为什么保留 `async def` 而不是改成 `def`（FastAPI 会自动把 `def` 放进线程池）：
            # 本模块**自己读原始请求体**（`await request.json()`）来控制 422 的形状；改成 `def`
            # 就只能交给 FastAPI/Pydantic 解析请求体，`422` 的 `detail` 与未知字段的措辞随之改变。
            # 两者都能解掉死锁，这里选"保留请求体控制 + 显式下线"这一种：改动面只有这一行，
            # 可观测行为（除死锁本身）零变化。
            #
            # 这一行覆盖了整条链上的**三处**回调（取参考解答、取 evaluation package、记录存证）：
            # 它们都在 `verify()` 内部顺序执行，因此没有"修一处漏两处"的余地。
            result = await run_in_threadpool(runtime.references.verify, payload_or_response)
        except ReferenceVerificationNotFound as error:
            return problem_response(
                Problem(404, "not-found", "Not Found", str(error)), instance
            )
        except ReferenceVerificationNotPossible as error:
            return problem_response(
                Problem(422, "unprocessable-entity", "Unprocessable Entity", str(error)), instance
            )
        except ReferenceSandboxUnavailable as error:
            # 本平台没有沙箱（`ADR-0004`：不得降级为裸进程）——同样是 `503`、同样零存证，但**不计入**
            # `record_failure()`：那是**已被文档化的平台条件**，不是"未预期失败"。把它算进去会让
            # readiness 的那句诊断（"自启动以来累计 N 次未预期失败"）在这台机器上**永远**脏着，
            # 而运维需要的是"这台机器上参考解答验证跑不了"这个事实——它已经由本 503 的 detail 与
            # 服务层的 `runner.sandbox_unavailable` 日志如实反映。
            return problem_response(
                Problem(503, "service-unavailable", "Service Unavailable", str(error)), instance
            )
        except ReferenceVerificationUnavailable as error:
            # 未预期的依赖不可用（B3 通道不通、上游 5xx、两侧声明不一致）：既有状态也有日志，
            # 且**零存证**——服务层在抛之前没有写任何东西（它根本不碰存储）。状态侧额外记一次
            # `record_failure()`：**只如实累计、不翻转健康判定**（理由见
            # `B2Runtime.record_failure` 的 docstring）。不吞异常语义：仍按契约返回 503。
            runtime.record_failure()
            return problem_response(
                Problem(503, "service-unavailable", "Service Unavailable", str(error)), instance
            )
        return JSONResponse(status_code=200, content=result.as_contract())

    return router


def _authenticate(runtime: B2Runtime, request: Request, instance: str) -> Principal | Response:
    """验签 → `Principal`，或 `401` 响应。"""
    authenticator = runtime.intake.authenticator
    assert authenticator is not None  # ready 为假时路由已 503
    try:
        return authenticator.authenticate(request.headers.get("authorization"))
    except AuthenticationError:
        return problem_response(UNAUTHORIZED, instance)


async def _read_body(request: Request, instance: str) -> str | Response:
    """读并校验请求体 → `question_version_id`（规范化后的 UUID 文本），或 `422` 响应。

    取值**不回显**：非法取值可能是任意字符串（含源码片段），而 422 的 detail 会进日志与界面。
    """
    try:
        payload: Any = await request.json()
    except ValueError:
        return _unprocessable(instance, "request body must be a JSON object")
    if not isinstance(payload, dict):
        return _unprocessable(instance, "request body must be a JSON object")
    unknown = sorted(set(payload) - _REQUEST_FIELDS)
    if unknown:
        return _unprocessable(instance, "request body has unknown fields: " + ", ".join(unknown))
    if "question_version_id" not in payload:
        return _unprocessable(instance, "question_version_id is required")
    raw = payload["question_version_id"]
    if not isinstance(raw, str):
        return _unprocessable(instance, "question_version_id must be a UUID string")
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        return _unprocessable(instance, "question_version_id must be a UUID string")


def _not_available(instance: str, reason: str) -> JSONResponse:
    return problem_response(
        Problem(
            503,
            "service-unavailable",
            "Service Unavailable",
            f"reference verification is not available: {reason}",
        ),
        instance,
    )


def _unprocessable(instance: str, detail: str) -> JSONResponse:
    return problem_response(
        Problem(422, "unprocessable-entity", "Unprocessable Entity", detail), instance
    )


__all__ = ["build_reference_router"]
