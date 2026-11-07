"""组合根：把**已交付**的实现接到编排层声明的窄协议上。

`evaluation.ports` 只声明"编排需要什么形态的输入"，并明确写着端口的**生产装配由集成方注入**
（见该模块 docstring）。本模块就是那层装配，职责边界如下：

| 端口 | 生产实现 | 接的是什么 |
|---|---|---|
| `PackageSource` | `HttpPackageSource` | B3 的 `GET /api/v1/question-versions/{id}/evaluation-package`（服务间、冻结契约） |
| `WorkspaceProvider` | `CasWorkspaceProvider` | 内容寻址存储 → 服务端生成的独立工作区（逐用例 / 批量） |
| `SecurityDecider` | `StaticSecurityDecider` | `security.decide_static`（不重写规则） |
| `CaseRunner` | `RunnerCaseRunner` | `runner.run_case` / `runner.run_cases`（冻结签名，原样转发） |
| `DeliveryVerifier` | `EmailDeliveryVerifier` | 真实 IMAP 收件链路落下的验证记录 + 共享邮件规则引擎（不执行代码） |
| `FixtureProvider` | `B3FixtureSource` | B3 的 `GET .../fixture-contents?mount=input`（受保护 fixture 字节，有界） |

**本层不做**：不改判分、不重写规则、不放宽策略、不做授权判定（授权在路由层）、不解释 package 语义
（三个不透明句柄原样透传，runner 侧做严格相等校验），也不把受保护内容（fixture 字节、SSH 口令、
邮件正文）写进日志/事件/异常消息。

## 三处必须说清的接线约定

1. **解释器/环境身份只在取包时确定**：`PackageView.environment_digest` 与 `interpreter_version` 由
   `bindings` 按冻结公式计算，素材是 package 声明的沙箱策略与部署声明的环境标签——因为取包阶段
   **不执行**任何沙箱进程，此处拿不到运行时观测值（边界见 `bindings` 模块的"诚实边界"）。
2. **工作区粒度**：`CasWorkspaceProvider.prepare(case_id=...)` 每次都建**新**目录，绝不复用；
   `batch=True` 是 `single_run`/`ssh_session` 一次批量执行唯一的工作区（同样每次新建、同样可被
   沙箱用户读写、父目录保持私有）。`release()` 只删除位于本 provider 根目录之下的路径。
3. **`execution` 声明**：`HttpPackageSource` 把 package 里的 `execution` 映射成 `ExecutionView`
   （`handle` = runner 解析出的声明对象，含 SSH 口令等受保护配置；本视图的公开字段只有模式、
   分隔行与内部诊断摘要）。`execution_digest` 用**规范化 JSON 摘要**计算，且 SSH 口令在投影里
   被固定标记替换——摘要绑定声明的结构，不是口令本身。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import shutil
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from autograder_b2.domain.clock import sha256_hex, utc_now
from autograder_b2.domain.envelope import EnvelopeFile, SubmissionEnvelope, is_allowed_relative_path
from autograder_b2.domain.evidence import (
    BatchRunInput,
    CaseEvidence,
    PackageUnavailableError,
    SubmissionRunInput,
)
from autograder_b2.domain.security import SecurityDecision
from autograder_b2.evaluation.ports import (
    CaseDescriptor,
    ComparisonPolicy,
    DeliveryUnavailableError,
    DeliveryVerificationDefect,
    ExecutionView,
    PackageView,
)
from autograder_b2.intake.email_rules import (
    EmailReceipt,
    EmailRequirements,
    evaluate_email_receipt,
)
from autograder_b2.intake.email_verification import intake_health, load_email_receipt
from autograder_b2.integration.bindings import (
    UNSET_ENVIRONMENT_TAG,
    environment_digest,
    question_version_binding,
)
from autograder_b2.runner.fixtures import FixtureContent, FixtureProvider
from autograder_b2.runner.package import EvaluationPackage, parse_evaluation_package
from autograder_b2.runner.policy import policy_digest
from autograder_b2.runner.run_case import run_case, run_cases
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
        raw_execution = payload.get("execution") if isinstance(payload, Mapping) else None
        return self._view(parsed, raw_execution)

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

    def _view(self, parsed: EvaluationPackage, raw_execution: object = None) -> PackageView:
        """契约 package → 编排视图；三个不透明句柄按 `ports` 的接线约定原样提供。

        `execution` 视图的模式/分隔行直接取自 runner 的解析结果（单一解析点）；受保护配置（SSH 口令）
        只随 `handle` 传递，本视图不复制、不打印。`execution_digest` 是对**声明结构的规范化摘要**
        （口令在投影里被固定标记替换），只作 B2 内部诊断值，不进报告/事件/日志。
        """
        interpreter = parsed.security_policy.interpreter_for(parsed.language)
        comparison = parsed.adapter.comparison
        execution = parsed.execution
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
            execution=ExecutionView(
                mode=execution.mode,
                execution_digest=execution_digest_of(raw_execution),
                segment_delimiter=execution.segment_delimiter,
                handle=execution,
            ),
            handle=parsed,
            adapter_handle=parsed.adapter,
            policy_handle=parsed.security_policy,
        )


#: `execution.ssh.password` 在摘要投影里的固定替身：摘要绑定声明的结构，**不**绑定口令本身
#: （对口令做可暴力枚举的摘要等于给出一份可离线校验的凭据指纹）。
PROTECTED_PASSWORD_MARKER = "<protected>"


def execution_digest_of(declaration: object) -> str | None:
    """`execution` 声明的规范化 JSON 摘要（缺省声明 ⇒ `None`）。

    只作 B2 内部诊断/日志无关值：execution 是 package 内容的一部分，已由 `package_digest` 绑定，
    因此它**不**参与五项绑定摘要，也不进报告。投影规则：排序键 + 紧凑分隔符的 JSON；
    `ssh.password` 换成 `PROTECTED_PASSWORD_MARKER`（结构可校验、口令不可反推）。
    """
    if declaration is None:
        return None
    if not isinstance(declaration, Mapping):
        raise PackageUnavailableError("evaluation package execution declaration is not an object")
    projection: dict[str, object] = {}
    for key, value in declaration.items():
        if not isinstance(key, str):
            raise PackageUnavailableError("evaluation package execution declaration has a non-string key")
        if key == "ssh" and isinstance(value, Mapping):
            ssh = {str(name): item for name, item in value.items()}
            if "password" in ssh:
                ssh["password"] = PROTECTED_PASSWORD_MARKER
            projection[key] = ssh
        else:
            projection[key] = value
    canonical = json.dumps(
        projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        batch: bool = False,
    ) -> Path:
        """物化一份**新的**工作区（逐用例模式下每个用例各一份；批量模式每次执行一份）。

        `batch=True` 是 `single_run`/`ssh_session` 的一次批量执行：目录名为 `batch`，权限口径与用例
        工作区一致（沙箱用户可读写、父目录私有），且每次 `prepare` 都新建。`batch` 与 `case_id`
        互斥——同时给出说明调用方把两种粒度搞混了，直接拒绝而不是猜一个。
        """
        if batch and case_id is not None:
            raise ValueError("batch workspace and case workspace are different scopes; pass only one")
        _as_uuid_text(submission_id, "submission_id")
        run_dir = self._root / _as_uuid_text(evaluation_run_id, "evaluation_run_id")
        if batch:
            target = run_dir / "batch"
        elif case_id is None:
            target = run_dir / "run"
        else:
            target = run_dir / f"case-{_as_uuid_text(case_id, 'case_id')}"
        accessible = batch or case_id is not None
        target.mkdir(parents=True, exist_ok=True)
        # The per-run parent must stay private even when the process umask is permissive: the
        # jailed UID gets access to the case/batch tree only, and "parent not listable" is part
        # of that boundary. Relying on the ambient umask made the guarantee environment
        # dependent (the same provider produced 0755 parents under umask 022).
        run_dir.chmod(0o700)
        if accessible:
            # The jailed UID is a delegated subordinate ID, not the API UID.
            # Only the mounted case/batch tree is made accessible; its parent stays private.
            target.chmod(0o777)
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
            if accessible:
                parent = destination.parent
                while parent != target:
                    parent.chmod(0o777)
                    parent = parent.parent
                destination.chmod(0o644)
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
    """`CaseRunner` 的生产实现：原样转发 `runner.run_case` / `runner.run_cases`。

    不传 `probe`/`launcher`：它们只是测试接缝，生产默认值即真实沙箱探测与 nsjail 启动器。
    `fixture_provider` 是**生产接缝**：受保护 fixture 字节由 `B3FixtureSource` 提供，
    由 runner 在沙箱启动前物化进工作区并逐字节校验摘要（本层不做物化、也不缓存字节）。
    """

    def __init__(self, *, fixture_provider: FixtureProvider | None = None) -> None:
        self._fixture_provider = fixture_provider

    def run_case(self, package: object, submission: SubmissionRunInput) -> CaseEvidence:
        # Only forward the keyword when a provider was injected: some callers/tests patch the
        # module-level `run_case` with a two-argument callable, and an unconditional
        # `fixture_provider=None` would turn that into a TypeError instead of a run.
        if self._fixture_provider is None:
            return run_case(package, submission)  # type: ignore[arg-type]
        return run_case(  # type: ignore[arg-type]
            package, submission, fixture_provider=self._fixture_provider
        )

    def run_cases(self, package: object, batch: BatchRunInput) -> tuple[CaseEvidence, ...]:
        return run_cases(  # type: ignore[arg-type]
            package, batch, fixture_provider=self._fixture_provider
        )


#: B3 受保护 fixture 字节端点的路径（`b3.yaml`；`base_url` 由部署给出）。
FIXTURE_CONTENTS_PATH = "/api/v1/question-versions/{version}/fixture-contents"
#: 契约 `FixtureContentBundle` 的边界（与设计基线 §5 同源）。
MAX_FIXTURE_ITEMS = 50
MAX_FIXTURE_ITEM_BYTES = 1 * 1024 * 1024
MAX_FIXTURE_TOTAL_BYTES = 4 * 1024 * 1024
#: 单个 fixture 路径的契约上限。
MAX_FIXTURE_PATH_LENGTH = 200

_REQUIRED_BUNDLE_FIELDS = frozenset({"question_version_id", "items"})
_REQUIRED_ITEM_FIELDS = frozenset(
    {"path", "mount", "content_sha256", "size_bytes", "content_base64"}
)
_DIGEST_HEX = set("0123456789abcdef")


class B3FixtureSource:
    """`FixtureProvider` 的生产实现：从 B3 取回 `mount=input` 的受保护 fixture 字节。

    安全与诚实边界：

    - **只读 `mount=input`**：`expected_output` 的字节没有读取端点（比较只需摘要），本类也不会请求它；
    - **有界**：单文件 ≤1 MiB、总量 ≤4 MiB、条目 ≤50；超限按**基础设施故障**（`PackageUnavailableError`）
      处理，绝不截断——截断会静默改变学生代码读到的东西；
    - **摘要以本地计算为准**：解码后自己算 SHA-256 并与响应声明交叉核验；与 package 声明的交叉核验由
      runner 完成（它把 provider 返回的摘要与 `adapter.files[].content_sha256` 逐项比对）；
    - **不泄露**：任何异常消息都不含字节、路径清单或内部主机/端口/URL。

    它**不**复用/改写参考解答工作区：参考解答走 `reference.workspace` 的一次性目录，两者没有任何共享状态。
    """

    def __init__(self, endpoint: B3Endpoint) -> None:
        self._endpoint = endpoint

    @property
    def endpoint(self) -> B3Endpoint:
        return self._endpoint

    def fetch_inputs(self, *, question_version_id: str) -> tuple[FixtureContent, ...]:
        """取该版本的全部 input fixture 字节（严格解析契约 `FixtureContentBundle`）。"""
        version = _as_uuid_text(question_version_id, "question_version_id")
        url = (
            f"{self._endpoint.base_url.rstrip('/')}"
            f"{FIXTURE_CONTENTS_PATH.format(version=version)}?mount=input"
        )
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"authorization": self._endpoint.authorization, "accept": "application/json"},
        )
        document = self._read_json(request)
        return _parse_fixture_bundle(document, requested_version=version)

    def _read_json(self, request: urllib.request.Request) -> object:
        """读取并解析响应体（有界；错误信息不含主机名、端口、URL 与响应体内容）。"""
        limit = self._endpoint.max_response_bytes
        try:
            with urllib.request.urlopen(request, timeout=self._endpoint.timeout_seconds) as response:
                body = response.read(limit + 1)
        except urllib.error.HTTPError as error:
            raise PackageUnavailableError(
                f"fixture contents endpoint returned status {error.code}",
                status_code=int(error.code),
                upstream_detail=_problem_detail(error, limit),
            ) from error
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise PackageUnavailableError("fixture contents endpoint is not reachable") from error
        if len(body) > limit:
            raise PackageUnavailableError("fixture contents response exceeds the accepted size limit")
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError) as error:
            raise PackageUnavailableError("fixture contents response is not valid JSON") from error


def _parse_fixture_bundle(document: object, *, requested_version: str) -> tuple[FixtureContent, ...]:
    """严格解析 `FixtureContentBundle`；形状不合规 ⇒ `PackageUnavailableError`（技术故障）。"""
    if not isinstance(document, Mapping):
        raise PackageUnavailableError("fixture contents response is not a JSON object")
    unknown = sorted(set(document) - _REQUIRED_BUNDLE_FIELDS)
    missing = sorted(_REQUIRED_BUNDLE_FIELDS - set(document))
    if unknown or missing:
        raise PackageUnavailableError("fixture contents response does not match the contract shape")
    version = document["question_version_id"]
    if not isinstance(version, str):
        raise PackageUnavailableError("fixture contents question_version_id is not a string")
    try:
        canonical_version = str(uuid.UUID(version))
    except (AttributeError, TypeError, ValueError) as error:
        raise PackageUnavailableError("fixture contents question_version_id is not a UUID") from error
    if canonical_version != requested_version:
        raise PackageUnavailableError("fixture contents do not match the requested question version")
    items = document["items"]
    if not isinstance(items, list):
        raise PackageUnavailableError("fixture contents items is not an array")
    if len(items) > MAX_FIXTURE_ITEMS:
        raise PackageUnavailableError("fixture contents exceed the accepted item count")
    contents: list[FixtureContent] = []
    seen_paths: set[str] = set()
    total_bytes = 0
    for item in items:
        content = _parse_fixture_item(item, seen_paths=seen_paths)
        total_bytes += content.size_bytes
        if total_bytes > MAX_FIXTURE_TOTAL_BYTES:
            raise PackageUnavailableError("fixture contents exceed the accepted total size")
        contents.append(content)
    return tuple(contents)


def _parse_fixture_item(item: object, *, seen_paths: set[str]) -> FixtureContent:
    """解析并**本地重算**一个 fixture 的摘要（服务端声明的摘要只用于交叉核验）。"""
    if not isinstance(item, Mapping):
        raise PackageUnavailableError("fixture contents item is not a JSON object")
    if set(item) != _REQUIRED_ITEM_FIELDS:
        raise PackageUnavailableError("fixture contents item does not match the contract shape")
    path = item["path"]
    if not isinstance(path, str) or len(path) > MAX_FIXTURE_PATH_LENGTH:
        raise PackageUnavailableError("fixture contents item path is invalid")
    if not is_allowed_relative_path(path):
        raise PackageUnavailableError("fixture contents item path is not allowed")
    if path in seen_paths:
        raise PackageUnavailableError("fixture contents contain a duplicate path")
    seen_paths.add(path)
    if item["mount"] != "input":
        raise PackageUnavailableError("fixture contents item is not an input fixture")
    declared_digest = item["content_sha256"]
    if (
        not isinstance(declared_digest, str)
        or len(declared_digest) != 64
        or not set(declared_digest) <= _DIGEST_HEX
    ):
        raise PackageUnavailableError("fixture contents item digest is not a sha256 digest")
    size_bytes = item["size_bytes"]
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
        raise PackageUnavailableError("fixture contents item size is not a non-negative integer")
    if size_bytes > MAX_FIXTURE_ITEM_BYTES:
        raise PackageUnavailableError("fixture contents item exceeds the accepted size")
    encoded = item["content_base64"]
    if not isinstance(encoded, str) or not encoded:
        raise PackageUnavailableError("fixture contents item content is not a non-empty string")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise PackageUnavailableError("fixture contents item content is not valid base64") from error
    if len(payload) != size_bytes:
        raise PackageUnavailableError("fixture contents item size does not match its content")
    if hashlib.sha256(payload).hexdigest() != declared_digest:
        raise PackageUnavailableError("fixture contents item digest does not match its content")
    return FixtureContent(
        path=path,
        content_sha256=declared_digest,
        size_bytes=size_bytes,
        content=payload,
    )


#: 邮件投递核验证据的 schema 标签（参与证据摘要，公式变更时必须改它）。
EMAIL_EVIDENCE_SCHEMA = "b2.email-evidence.v1"
EMAIL_CHECK_SCHEMA = "b2.email-check.v1"


class EmailDeliveryVerifier:
    """`DeliveryVerifier` 的生产实现：真实收件事实 + **共享**邮件规则引擎（不执行代码）。

    事实来源与所有权证明（设计基线 §3.5/§4）：`b2.email_verifications` 的行只可能由真实 IMAP 拉取链路
    在"核验码 → 发送者账号核验 → 首次发件地址绑定"全部通过后写入；本类**只读**该记录，不重新解析邮件、
    不保存正文、不记录地址/主题/附件名。

    失败语义：
    - 没有验证记录 **且** 收件链路心跳新鲜 ⇒ 学生侧 `missing_output`（邮件没到，不是依赖故障）；
    - 心跳过期/未配置、验证记录不可读 ⇒ `DeliveryUnavailableError`（运行落 `failed+infrastructure_error`）；
    - package/规则装配非法、证据与用例对不上 ⇒ `DeliveryVerificationDefect`（内部缺陷）。

    与参考答案验证的关系：同一套规则由 `reference/verification.py` 对**合成回执 JSON** 调用；
    因此"学生判定"与"参考解自检"不会漂移成两套规则。
    """

    def __init__(
        self,
        *,
        engine: Engine,
        now: Callable[[], datetime] = utc_now,
        receipt_loader: Callable[[object, str], EmailReceipt | None] = load_email_receipt,
        health_probe: Callable[..., str] = intake_health,
    ) -> None:
        self._engine = engine
        self._now = now
        self._load_receipt = receipt_loader
        self._health = health_probe

    def verify(
        self, package: object, submission: SubmissionEnvelope
    ) -> tuple[CaseEvidence, ...]:
        execution = getattr(package, "execution", None)
        if execution is None or getattr(execution, "mode", None) != "email_delivery":
            raise DeliveryVerificationDefect(
                "the delivery verifier was handed a package that does not declare email_delivery"
            )
        config = getattr(execution, "email", None)
        if config is None:
            raise DeliveryVerificationDefect("email_delivery package carries no email requirements")
        try:
            requirements = EmailRequirements(
                attempt=int(config.attempt),
                subject_template=str(config.subject_template),
                required_attachment_template=str(config.required_attachment_template),
            )
        except (TypeError, ValueError) as error:
            raise DeliveryVerificationDefect("email requirements are not valid") from error

        receipt, health = self._receipt_for(submission)
        if receipt is None:
            if health != "fresh":
                raise DeliveryUnavailableError(
                    "the mail intake dependency is not healthy, so a missing message is not the"
                    " student's fault"
                )
            receipt = EmailReceipt.not_received()
        try:
            outcomes = evaluate_email_receipt(receipt, requirements)
        except ValueError as error:
            # 模板/attempt 非法是 package/装配缺陷，不是学生失败。
            raise DeliveryVerificationDefect("email rules rejected the declared requirements") from error
        return self._evidence(package, submission, outcomes)

    def _receipt_for(self, submission: SubmissionEnvelope) -> tuple[EmailReceipt | None, str]:
        """读取真实收件事实与依赖健康（同一连接内读取，避免两份快照互相矛盾）。"""
        try:
            with self._engine.connect() as connection:
                receipt = self._load_receipt(connection, submission.submission_id)
                if receipt is not None:
                    return receipt, "fresh"
                return None, self._health(connection, now=self._now())
        except (DeliveryUnavailableError, DeliveryVerificationDefect):
            raise
        except SQLAlchemyError as error:
            raise DeliveryUnavailableError("the email verification state is unreadable") from error
        except OSError as error:
            raise DeliveryUnavailableError("the email verification state is unreadable") from error

    def _evidence(
        self,
        package: object,
        submission: SubmissionEnvelope,
        outcomes: Sequence[object],
    ) -> tuple[CaseEvidence, ...]:
        """把四个（或 package 声明的）邮件检查映射到 package 用例，逐条产出证据。"""
        by_check: dict[str, object] = {}
        for outcome in outcomes:
            check = getattr(outcome, "check", None)
            if not isinstance(check, str) or check in by_check:
                raise DeliveryVerificationDefect("email rules returned an invalid check list")
            by_check[check] = outcome
        tests = getattr(package, "tests", None)
        if not isinstance(tests, Sequence) or not tests:
            raise DeliveryVerificationDefect("email_delivery package declares no cases")
        evidences: list[CaseEvidence] = []
        seen_checks: set[str] = set()
        for case in sorted(tests, key=lambda item: int(item.ordinal)):
            check = getattr(case, "email_check", None)
            if not isinstance(check, str):
                raise DeliveryVerificationDefect(
                    "an email_delivery case declares no email_check"
                )
            if check in seen_checks:
                raise DeliveryVerificationDefect("email_delivery declares the same check twice")
            seen_checks.add(check)
            outcome = by_check.get(check)
            if outcome is None:
                raise DeliveryVerificationDefect(
                    "email rules returned no outcome for a declared check"
                )
            evidences.append(_email_evidence(submission, case, check, outcome))
        return tuple(evidences)


def _email_evidence(
    submission: SubmissionEnvelope, case: object, check: str, outcome: object
) -> CaseEvidence:
    """构造一条 `email_delivery` 证据（摘要全部确定性；不含地址/主题/附件名/正文）。"""
    case_id = str(case.case_id)
    ordinal = int(case.ordinal)
    weight = float(getattr(case, "weight", 1))
    passed = bool(outcome.passed)
    category = getattr(outcome, "category", None)
    message = getattr(outcome, "message", None)
    if passed and category is not None:
        raise DeliveryVerificationDefect("email rules returned a passed check with a failure category")
    if not passed and not isinstance(category, str):
        raise DeliveryVerificationDefect("email rules returned a failed check without a category")
    input_digest = sha256_hex(
        _canonical_json(
            {
                "schema": EMAIL_EVIDENCE_SCHEMA,
                "submission_id": submission.submission_id,
                "case_id": case_id,
                "check": check,
            }
        ).encode("utf-8")
    )
    stdout_digest = sha256_hex(
        _canonical_json(
            {"schema": EMAIL_CHECK_SCHEMA, "check": check, "passed": passed, "category": category}
        ).encode("utf-8")
    )
    evidence_digest = sha256_hex(
        _canonical_json(
            {
                "schema": EMAIL_EVIDENCE_SCHEMA,
                "submission_id": submission.submission_id,
                "case_id": case_id,
                "ordinal": ordinal,
                "weight": weight,
                "check": check,
                "passed": passed,
                "failure_category": category,
                "message": message,
            }
        ).encode("utf-8")
    )
    return CaseEvidence(
        case_id=case_id,
        ordinal=ordinal,
        weight=weight,
        passed=passed,
        failure_category=category,  # type: ignore[arg-type] - 取值域由邮件规则引擎保证
        input_digest=input_digest,
        stdout_digest=stdout_digest,
        stderr_digest=sha256_hex(b""),
        actual_output_excerpt=None,
        expected_output_excerpt=None,
        exit_code=None,
        duration_ms=None,
        resource_limit_hit=None,
        sandbox_violations=(),
        message=message if isinstance(message, str) else None,
        evidence_digest=evidence_digest,
    )


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "FIXTURE_CONTENTS_PATH",
    "MAX_FIXTURE_ITEMS",
    "MAX_FIXTURE_ITEM_BYTES",
    "MAX_FIXTURE_TOTAL_BYTES",
    "PROTECTED_PASSWORD_MARKER",
    "B3FixtureSource",
    "CasWorkspaceProvider",
    "EmailDeliveryVerifier",
    "HttpPackageSource",
    "RunnerCaseRunner",
    "StaticSecurityDecider",
    "execution_digest_of",
]
