"""组合根：把**已交付**的实现接到编排层声明的四个窄协议上。

`evaluation.ports` 只声明"编排需要什么形态的输入"，并明确写着四个端口的**生产装配由集成方注入**
（见该模块 docstring）。本模块就是那层装配，职责边界如下：

| 端口 | 生产实现 | 接的是什么 |
|---|---|---|
| `PackageSource` | `HttpPackageSource` | B3 的 `GET /api/v1/question-versions/{id}/evaluation-package`（服务间、冻结契约） |
| `WorkspaceProvider` | `CasWorkspaceProvider` | 内容寻址存储 → 服务端生成的独立工作区 |
| `SecurityDecider` | `StaticSecurityDecider` | `security.decide_static`（不重写规则） |
| `CaseRunner` | `RunnerCaseRunner` | `runner.run_case`（冻结签名，原样转发） |

**本层不做**：不改判分、不重写规则、不放宽策略、不做授权判定（授权在路由层）、不解释 package 语义
（三个不透明句柄原样透传，runner 侧做严格相等校验）。

## 两处必须说清的接线约定

1. **解释器/环境身份只在取包时确定**：`PackageView.environment_digest` 与 `interpreter_version` 由
   `bindings` 按冻结公式计算，素材是 package 声明的沙箱策略与部署声明的环境标签——因为取包阶段
   **不执行**任何沙箱进程，此处拿不到运行时观测值（边界见 `bindings` 模块的"诚实边界"）。
2. **每个用例一份工作区**：`CasWorkspaceProvider.prepare(case_id=...)` 每次都建**新**目录，绝不复用；
   `release()` 只删除位于本 provider 根目录之下的路径（避免把任意路径当工作区删掉）。
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from autograder_b2.domain.envelope import EnvelopeFile, is_allowed_relative_path
from autograder_b2.domain.evidence import (
    CaseEvidence,
    PackageUnavailableError,
    SubmissionRunInput,
)
from autograder_b2.domain.security import SecurityDecision
from autograder_b2.evaluation.ports import (
    CaseDescriptor,
    ComparisonPolicy,
    PackageView,
)
from autograder_b2.integration.bindings import (
    UNSET_ENVIRONMENT_TAG,
    environment_digest,
    question_version_binding,
)
from autograder_b2.runner.package import EvaluationPackage, parse_evaluation_package
from autograder_b2.runner.policy import policy_digest
from autograder_b2.runner.run_case import run_case
from autograder_b2.security.decision import decide_static
from autograder_b2.storage.cas import ContentAddressedStore

#: 取包响应的硬上限。契约没有声明大小上界，而 package 含隐藏用例与期望输出：不设上限等于让一个
#: 异常/恶意的上游把 worker 的内存吃光。8 MiB 对题库规模足够，超出按"不可获取"处理（技术失败）。
DEFAULT_MAX_PACKAGE_BYTES = 8 * 1024 * 1024
#: 取包超时（秒）。B3 是同步 HTTP，挂死会占住评测 worker。
DEFAULT_PACKAGE_TIMEOUT_SECONDS = 10.0

#: 转述上游 `detail` 的长度上限（超过即丢弃，不截断）：`detail` 是给人读的一句话，
#: 一条长得离谱的"原因"多半不是原因（而是被回显的请求体），宁可不说。
PROBLEM_DETAIL_LIMIT = 500


@dataclass(frozen=True, slots=True)
class B3Endpoint:
    """B3 取包端点的定位与凭据（由宿主/部署注入）。

    `authorization` 用 `repr=False`：凭据**不得**出现在任何 repr/日志/事件里。
    B3 该端点要求服务间鉴权（契约 `401/403`），而 B4 目前**没有**机器令牌签发端点，
    因此这不是"B2 自己签发"的令牌，而是宿主注入的既有凭据——详见
    `docs/collaboration/interfaces.md` 记录的跨模块缺口。
    """

    base_url: str
    authorization: str = field(repr=False)
    timeout_seconds: float = DEFAULT_PACKAGE_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_PACKAGE_BYTES

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url 必须是 http(s) 绝对地址")
        if not self.authorization.strip():
            raise ValueError("authorization 不得为空：该端点要求服务间鉴权")


def _as_uuid_text(value: object, name: str) -> str:
    """规范化为标准 UUID 文本；不合法即技术性不可获取（而不是把它拼进 URL）。"""
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PackageUnavailableError(f"{name} is not a UUID") from exc


class HttpPackageSource:
    """`PackageSource` 的生产实现：从 B3 拉取 evaluation package 并解析为 `PackageView`。

    失败一律抛 `PackageUnavailableError`（其子类 `PackageRejected` 在解析不合法时抛出），
    由编排层落 `failed + infrastructure_error`——**绝不**伪造一份 package 或降级成"无用例通过"。
    """

    def __init__(self, endpoint: B3Endpoint, *, environment_tag: str = UNSET_ENVIRONMENT_TAG) -> None:
        self._endpoint = endpoint
        self._environment_tag = environment_tag

    @property
    def environment_tag(self) -> str:
        return self._environment_tag

    def fetch(self, *, evaluation_run_id: str, question_version_id: str) -> PackageView:
        """按题目版本取包。

        `evaluation_run_id` 只用于保证调用方给的是 UUID（不参与 URL，避免任何拼接面）；
        URL 里只出现**规范化后**的 UUID 文本。
        """
        _as_uuid_text(evaluation_run_id, "evaluation_run_id")
        version = _as_uuid_text(question_version_id, "question_version_id")
        url = (
            f"{self._endpoint.base_url.rstrip('/')}"
            f"/api/v1/question-versions/{version}/evaluation-package"
        )
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"authorization": self._endpoint.authorization, "accept": "application/json"},
        )
        payload = self._read_json(request)
        parsed = parse_evaluation_package(payload)
        if parsed.question_version_id != version:
            # 上游返回了另一个版本的包：绑定摘要必须绑定"实际拿到的那份内容"，但版本号不符意味着
            # 该响应不可信，宁可不评测（技术失败），也不能把它当成本次题目版本的包。
            raise PackageUnavailableError(
                "evaluation package does not match the requested question version"
            )
        return self._view(parsed)

    def _read_json(self, request: urllib.request.Request) -> object:
        """读取并解析响应体；错误信息**不含**主机名、端口、URL 与响应体内容。"""
        limit = self._endpoint.max_response_bytes
        try:
            with urllib.request.urlopen(request, timeout=self._endpoint.timeout_seconds) as response:
                body = response.read(limit + 1)
        except urllib.error.HTTPError as error:
            # 只报状态码：URL 含内部主机与端口，不写进消息（日志/事件都禁止内部地址）。
            # 状态码**同时**挂在异常上：`CHG-0019` 的参考解答验证端点要按它区分 `404`（版本不存在）
            # 与 `409`（版本存在但没有包——草稿即如此），这两者必须给出不同的对外语义。
            # `409` 的**原因**同样推不出来（草稿未发布 / 摘要与发布时记录的不符 / 内容被改过），
            # 因此把 Problem Details 的 `detail` 也带上——它只作为**属性**存在，不进消息、不进日志，
            # 由需要给调用者一个可行动原因的那条路径（验证端点的 422）转述。
            raise PackageUnavailableError(
                f"evaluation package endpoint returned status {error.code}",
                status_code=int(error.code),
                upstream_detail=_problem_detail(error, limit),
            ) from error
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise PackageUnavailableError(
                "evaluation package endpoint is not reachable"
            ) from error
        if len(body) > limit:
            raise PackageUnavailableError("evaluation package exceeds the accepted size limit")
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError) as error:
            raise PackageUnavailableError("evaluation package response is not valid JSON") from error

    def _view(self, parsed: EvaluationPackage) -> PackageView:
        """契约 package → 编排视图；三个不透明句柄按 `ports` 的接线约定原样提供。"""
        interpreter = parsed.security_policy.interpreter_for(parsed.language)
        comparison = parsed.adapter.comparison
        return PackageView(
            package_digest=parsed.package_digest,
            language=parsed.language,
            cases=tuple(
                CaseDescriptor(
                    case_id=case.case_id,
                    ordinal=case.ordinal,
                    weight=case.weight,
                    visible_to_student=case.visible_to_student,
                    handle=case,
                )
                for case in parsed.tests
            ),
            runner_policy_digest=policy_digest(parsed.security_policy),
            environment_digest=environment_digest(
                language=parsed.language,
                interpreter=interpreter,
                environment_tag=self._environment_tag,
            ),
            comparison=ComparisonPolicy(
                stdout=comparison.stdout,
                stderr=comparison.stderr,
                numeric_tolerance=comparison.numeric_tolerance,
            ),
            question_version_binding=question_version_binding(
                question_version_id=parsed.question_version_id,
                package_digest=parsed.package_digest,
            ),
            interpreter_version=interpreter,
            handle=parsed,
            adapter_handle=parsed.adapter,
            policy_handle=parsed.security_policy,
        )


def _problem_detail(error: urllib.error.HTTPError, limit: int) -> str | None:
    """从 Problem Details 响应体里取 `detail`（取不到即 `None`，由调用方决定怎么措辞）。

    只接受**契约固定的形状**（`common.yaml` 的 Problem Details：JSON 对象里的非空字符串 `detail`），
    并有长度上限：超过即丢弃而不是截断——截断会把一句半截的话当成上游的原因，而它可能正好丢掉
    关键的那半句。读体**有界**（与正常响应同一个上限），失败一律吞掉：这里已经在处理一条失败的
    路径，绝不能再抛一个新异常把原状态码盖掉。
    """
    try:
        body = error.read(limit + 1)
    except (OSError, ValueError):  # pragma: no cover - 上游已经关掉连接等
        return None
    if len(body) > limit:
        return None
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


class CasWorkspaceProvider:
    """`WorkspaceProvider` 的生产实现：内容寻址存储 → 服务端生成的独立工作区。

    目录名**只由服务端 UUID 派生**（run / case），学生提供的路径片段只用于工作区**内部**的相对
    位置，且再次过一遍冻结的路径白名单——即使库里的行被绕过入口写坏，也不会写出工作区之外。
    """

    def __init__(self, *, store: ContentAddressedStore, workspace_root: Path | str) -> None:
        root = Path(workspace_root)
        self._store = store
        self._root = root
        # 工作区根**不得**位于 CAS 根之内：CAS 的孤儿 GC 会把没有元数据的内容文件当孤儿清理，
        # 而工作区里的文件正是没有元数据的——放进去会让 GC 去删正在执行的工作区。
        cas_root = Path(store.root)
        if root == cas_root or cas_root in root.parents or root in cas_root.parents:
            raise ValueError("workspace_root must be a separate tree from the artifact root")

    @property
    def root(self) -> Path:
        return self._root

    def prepare(
        self,
        *,
        submission_id: str,
        evaluation_run_id: str,
        files: Sequence[EnvelopeFile],
        case_id: str | None = None,
    ) -> Path:
        """物化一份**新的**工作区（同 run 的每个用例各一份；绝不复用上一个用例的目录）。"""
        _as_uuid_text(submission_id, "submission_id")
        run_dir = self._root / _as_uuid_text(evaluation_run_id, "evaluation_run_id")
        target = run_dir / ("run" if case_id is None else f"case-{_as_uuid_text(case_id, 'case_id')}")
        target.mkdir(parents=True, exist_ok=True)
        for item in files:
            if not is_allowed_relative_path(item.path):
                # 二次防线：入口已校验过，但工作区写入是"学生可控字符串 → 文件系统路径"的最后一跳。
                raise ValueError("submission file path is not allowed")
            content = self._store.read(item.content_sha256)
            if len(content) != item.size_bytes:
                raise ValueError("stored artifact size does not match the submission metadata")
            destination = target / item.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        return target

    def release(self, workspace: Path) -> None:
        """删除本 provider 根之下的工作区；根之外的路径一律拒绝（不删任意路径）。"""
        resolved = Path(workspace).resolve()
        root = self._root.resolve()
        if resolved == root or root not in resolved.parents:
            raise ValueError("refusing to release a path outside the workspace root")
        shutil.rmtree(resolved, ignore_errors=True)


class StaticSecurityDecider:
    """`SecurityDecider` 的生产实现：转调 `security.decide_static`。

    **不**传 `static_findings`：编排层已把静态合法性层作为**独立阶段**（`_run_static`）执行并自行判定
    阻断，两层各自阻断、互不替代（安全判定层不得依赖上游结果才能定论，否则"上游没跑"会让本层沉默）。
    """

    def decide(
        self, *, evaluation_run_id: str, files: Mapping[str, str], language: str
    ) -> SecurityDecision:
        _as_uuid_text(evaluation_run_id, "evaluation_run_id")
        return decide_static(dict(files), language=language)


class RunnerCaseRunner:
    """`CaseRunner` 的生产实现：原样转发 `runner.run_case`（冻结签名，仅两个位置参数）。

    不传 `probe`/`launcher`：它们只是测试接缝，生产默认值即真实沙箱探测与 nsjail 启动器。
    """

    def run_case(self, package: object, submission: SubmissionRunInput) -> CaseEvidence:
        return run_case(package, submission)  # type: ignore[arg-type]
