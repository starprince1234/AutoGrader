"""组合层（`integration/`）的验收：四个端口的生产实现真的按契约与冻结约定工作。

覆盖的不变量：

- `HttpPackageSource`：URL 只由**规范化 UUID** 构成、凭据只进请求头、错误信息**不含**内部主机/端口/URL、
  响应体有硬上限、返回的 `PackageView` 三个不透明句柄与解析结果**同一对象**（`ports` 的接线约定）、
  两项绑定摘要按 `bindings` 的冻结公式计算。
- `CasWorkspaceProvider`：每个用例一份**新**工作区、内容与 CAS 逐字节一致、`release` 只删根目录之下、
  与 CAS 根互相包含时在构造期就拒绝（否则孤儿 GC 会去删正在执行的工作区）。
- `StaticSecurityDecider` / `RunnerCaseRunner`：只转发、不重判；接线约定被违反时**响亮**失败。

package 夹具复用 `tests.test_runner_core.package_payload`（不新建第二份契约形状定义）。
"""

from __future__ import annotations

import contextlib
import http.server
import json
import pathlib
import socket
import threading
import uuid
from collections.abc import Iterator

import pytest

from autograder_b2.domain.envelope import EnvelopeFile
from autograder_b2.domain.evidence import (
    InternalEvaluationError,
    PackageUnavailableError,
    SandboxUnavailableError,
    SubmissionRunInput,
)
from autograder_b2.integration.adapters import (
    B3Endpoint,
    CasWorkspaceProvider,
    HttpPackageSource,
    RunnerCaseRunner,
    StaticSecurityDecider,
)
from autograder_b2.integration.bindings import (
    UNSET_ENVIRONMENT_TAG,
    environment_digest,
    question_version_binding,
)
from autograder_b2.runner.package import PackageRejected, parse_evaluation_package
from autograder_b2.runner.policy import policy_digest, probe_sandbox
from autograder_b2.security.decision import allows_dynamic_execution
from autograder_b2.storage.cas import ContentAddressedStore
from tests.conftest import requires_postgres
from tests.test_runner_core import PACKAGE_DIGEST, QUESTION_ID, package_payload

#: 凭据只用于断言"确实进了 Authorization 头"；测试里不得把它打印出来。
TEST_CREDENTIAL = "Bearer test-credential-not-a-real-token"


@contextlib.contextmanager
def package_endpoint(*, status: int = 200, payload: bytes = b"{}") -> Iterator[tuple[str, list[dict[str, str]]]]:
    """最小真实 HTTP 服务：返回配置好的响应，并记录收到的请求（路径 + Authorization 头）。"""
    seen: list[dict[str, str]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            seen.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("authorization", ""),
                    "accept": self.headers.get("accept", ""),
                }
            )
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            """静默：测试服务的访问日志没有价值，且会污染输出。"""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _source(base_url: str, *, environment_tag: str = UNSET_ENVIRONMENT_TAG, limit: int = 1 << 20) -> HttpPackageSource:
    return HttpPackageSource(
        B3Endpoint(
            base_url=base_url,
            authorization=TEST_CREDENTIAL,
            timeout_seconds=5.0,
            max_response_bytes=limit,
        ),
        environment_tag=environment_tag,
    )


def _closed_port_url() -> str:
    """占一个端口再关掉：得到确定性的"连不上"地址。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


# --------------------------------------------------------------------------------------
# B3Endpoint：构造期校验
# --------------------------------------------------------------------------------------


class TestEndpointConfiguration:
    def test_base_url_must_be_absolute_http(self) -> None:
        with pytest.raises(ValueError):
            B3Endpoint(base_url="b3.internal:8000", authorization=TEST_CREDENTIAL)

    def test_authorization_must_be_present(self) -> None:
        # 该端点在契约里要求服务间鉴权：空凭据不是"匿名可用"，而是配置错误。
        with pytest.raises(ValueError):
            B3Endpoint(base_url="http://127.0.0.1:9999", authorization="   ")

    def test_credential_is_not_exposed_by_repr(self) -> None:
        """凭据不得出现在 repr 里（repr 会被日志/断言消息/事件带走）。"""
        endpoint = B3Endpoint(base_url="http://127.0.0.1:9999", authorization=TEST_CREDENTIAL)
        assert TEST_CREDENTIAL not in repr(endpoint)


# --------------------------------------------------------------------------------------
# HttpPackageSource：成功路径
# --------------------------------------------------------------------------------------


class TestHttpPackageSourceSuccess:
    def test_fetch_uses_canonical_uuid_path_and_returns_bound_view(self) -> None:
        payload = json.dumps(package_payload()).encode("utf-8")
        with package_endpoint(payload=payload) as (base_url, seen):
            view = _source(base_url).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )

        assert len(seen) == 1
        assert seen[0]["path"] == f"/api/v1/question-versions/{QUESTION_ID}/evaluation-package"
        assert seen[0]["authorization"] == TEST_CREDENTIAL
        assert seen[0]["accept"] == "application/json"

        parsed = parse_evaluation_package(package_payload())
        assert view.package_digest == PACKAGE_DIGEST
        assert view.language == "python"
        assert view.cases_total == len(parsed.tests)
        assert view.runner_policy_digest == policy_digest(parsed.security_policy)
        assert view.question_version_binding == question_version_binding(
            question_version_id=QUESTION_ID, package_digest=PACKAGE_DIGEST
        )
        assert view.environment_digest == environment_digest(
            language="python",
            interpreter=parsed.security_policy.interpreter_for("python"),
            environment_tag=UNSET_ENVIRONMENT_TAG,
        )
        # 比较策略取自 package（提交侧无法影响）。
        assert view.comparison.stdout == parsed.adapter.comparison.stdout
        assert view.comparison.stderr == parsed.adapter.comparison.stderr
        # case 的 ordinal/weight/可见性逐项来自 package。
        assert [case.ordinal for case in view.cases] == [case.ordinal for case in parsed.tests]
        assert [case.weight for case in view.cases] == [case.weight for case in parsed.tests]

    def test_opaque_handles_are_the_parsed_objects_themselves(self) -> None:
        """`ports` 的接线约定：handle=解析后 package、adapter=package.adapter、policy=package.security_policy。

        runner 对 `submission.policy`/`case`/`adapter` 做**严格相等**校验，所以这里必须是同一对象，
        不能是"等值的副本"——副本会让 runner 拒绝执行（或更糟：被误认为更严的策略）。
        """
        payload = json.dumps(package_payload()).encode("utf-8")
        with package_endpoint(payload=payload) as (base_url, _seen):
            view = _source(base_url).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )

        parsed = parse_evaluation_package(package_payload())
        assert view.handle == parsed
        assert view.adapter_handle is view.handle.adapter
        assert view.policy_handle is view.handle.security_policy
        assert [case.handle for case in view.cases] == list(view.handle.tests)

    def test_environment_tag_participates_in_the_environment_digest(self) -> None:
        payload = json.dumps(package_payload()).encode("utf-8")
        with package_endpoint(payload=payload) as (base_url, _seen):
            plain = _source(base_url).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )
            tagged = _source(base_url, environment_tag="img@sha256:abc123").fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )
        # 同一 package、同一解释器，只有部署声明的环境标签不同 => 环境摘要必须不同。
        assert plain.environment_digest != tagged.environment_digest
        # 但 package 摘要与题目版本绑定不受环境影响。
        assert plain.package_digest == tagged.package_digest
        assert plain.question_version_binding == tagged.question_version_binding


# --------------------------------------------------------------------------------------
# HttpPackageSource：失败路径（一律 PackageUnavailableError，且不泄露内部地址）
# --------------------------------------------------------------------------------------


class TestHttpPackageSourceFailures:
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 500, 503])
    def test_error_status_is_unavailable_and_does_not_leak_the_endpoint(self, status: int) -> None:
        with (
            package_endpoint(status=status, payload=b"{}") as (base_url, _seen),
            pytest.raises(PackageUnavailableError) as caught,
        ):
            _source(base_url).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )
        text = str(caught.value)
        # 只允许报状态码：主机名/端口/URL 都属于禁止泄露的内部信息。
        assert str(status) in text
        assert "127.0.0.1" not in text
        assert "http" not in text
        assert base_url.split(":")[-1] not in text

    @pytest.mark.parametrize(
        "payload",
        [
            (
                b'{"type": "https://autograder.local/problems/http-409", "title": "Conflict",'
                b' "status": 409, "detail": "a draft version has no evaluation package;'
                b' publish it first"}'
            ),
            b'{"detail": "short reason"}',
        ],
    )
    def test_problem_details_detail_is_carried_but_not_echoed_in_the_message(
        self, payload: bytes
    ) -> None:
        """`409` 的**原因**随异常带出（属性），而异常**消息**照旧只报状态码。

        两个方向都要钉：① `upstream_detail` 拿到 Problem Details 的 `detail`——参考解答验证端点要
        转述它（不同原因要教师做的事不同）；② 消息里**仍然**没有响应正文（日志/事件只记状态码，
        这条是既有的"不回显上游正文"纪律，不能被这次改动弄丢）。
        """
        with (
            package_endpoint(status=409, payload=payload) as (base_url, _seen),
            pytest.raises(PackageUnavailableError) as caught,
        ):
            _source(base_url).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )
        assert caught.value.status_code == 409
        assert caught.value.upstream_detail == json.loads(payload)["detail"]
        assert caught.value.upstream_detail not in str(caught.value)

    @pytest.mark.parametrize(
        "payload",
        [
            b"{}",
            b"not json",
            b'{"detail": ""}',
            b'{"detail": 42}',
            b'["a list, not an object"]',
            b'{"detail": "' + b"x" * 600 + b'"}',
        ],
    )
    def test_non_problem_details_bodies_yield_no_upstream_detail(self, payload: bytes) -> None:
        """形状不对就不带出任何"原因"：`detail` 要么是契约形状里的那句话，要么没有。

        过长的那一条**丢弃而不是截断**：半句话会被当成上游的原因，而它可能正好丢掉了关键的那半句。
        """
        with (
            package_endpoint(status=409, payload=payload) as (base_url, _seen),
            pytest.raises(PackageUnavailableError) as caught,
        ):
            _source(base_url).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )
        assert caught.value.status_code == 409
        assert caught.value.upstream_detail is None

    def test_unreachable_endpoint_is_unavailable(self) -> None:
        with pytest.raises(PackageUnavailableError) as caught:
            _source(_closed_port_url()).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )
        assert "127.0.0.1" not in str(caught.value)

    def test_non_uuid_question_version_never_reaches_the_wire(self) -> None:
        """题目版本不是 UUID 时不该把它拼进 URL（那正是注入面）。"""
        with package_endpoint() as (base_url, seen), pytest.raises(PackageUnavailableError):
            _source(base_url).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id="../../etc/passwd"
            )
        assert seen == []

    def test_non_uuid_run_id_is_rejected(self) -> None:
        with package_endpoint() as (base_url, seen), pytest.raises(PackageUnavailableError):
            _source(base_url).fetch(
                evaluation_run_id="not-a-uuid", question_version_id=QUESTION_ID
            )
        assert seen == []

    def test_oversized_response_is_rejected(self) -> None:
        payload = b"[" + b"0," * 4096 + b"0]"
        with package_endpoint(payload=payload) as (base_url, _seen), pytest.raises(
            PackageUnavailableError
        ):
            _source(base_url, limit=1024).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )

    def test_non_json_response_is_rejected(self) -> None:
        with package_endpoint(payload=b"<html>not json</html>") as (base_url, _seen), pytest.raises(
            PackageUnavailableError
        ):
            _source(base_url).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )

    def test_schema_invalid_package_surfaces_as_package_rejected(self) -> None:
        with package_endpoint(payload=b"{}") as (base_url, _seen), pytest.raises(PackageRejected):
            _source(base_url).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )

    def test_package_for_another_question_version_is_rejected(self) -> None:
        """上游返回别的版本 => 该响应不可信：宁可不评测，也不能当成本次版本的包。"""
        other_version = str(uuid.uuid4())
        payload = json.dumps(package_payload(question_version_id=other_version)).encode("utf-8")
        with package_endpoint(payload=payload) as (base_url, _seen), pytest.raises(
            PackageUnavailableError
        ):
            _source(base_url).fetch(
                evaluation_run_id=str(uuid.uuid4()), question_version_id=QUESTION_ID
            )


# --------------------------------------------------------------------------------------
# bindings：两项组合层摘要
# --------------------------------------------------------------------------------------


class TestBindingDigests:
    def test_question_version_binding_is_deterministic_and_injective(self) -> None:
        base = question_version_binding(question_version_id=QUESTION_ID, package_digest="a" * 64)
        assert base == question_version_binding(
            question_version_id=QUESTION_ID, package_digest="a" * 64
        )
        assert base != question_version_binding(
            question_version_id=QUESTION_ID, package_digest="b" * 64
        )
        assert base != question_version_binding(
            question_version_id=str(uuid.uuid4()), package_digest="a" * 64
        )
        assert len(base) == 64 and base == base.lower()

    def test_question_version_binding_accepts_uuid_spellings_but_normalises_them(self) -> None:
        upper = QUESTION_ID.upper()
        assert question_version_binding(
            question_version_id=upper, package_digest="a" * 64
        ) == question_version_binding(question_version_id=QUESTION_ID, package_digest="a" * 64)

    def test_environment_digest_is_injective_over_every_field(self) -> None:
        base = environment_digest(
            language="python", interpreter="/usr/bin/python3", environment_tag="tag"
        )
        assert base == environment_digest(
            language="python", interpreter="/usr/bin/python3", environment_tag="tag"
        )
        assert base != environment_digest(
            language="bash", interpreter="/usr/bin/python3", environment_tag="tag"
        )
        assert base != environment_digest(
            language="python", interpreter="/usr/local/bin/python3", environment_tag="tag"
        )
        assert base != environment_digest(
            language="python", interpreter="/usr/bin/python3", environment_tag="other"
        )

    @pytest.mark.parametrize(
        ("kwargs", "why"),
        [
            ({"language": "ruby", "interpreter": "/usr/bin/ruby", "environment_tag": "x"}, "未知语言"),
            ({"language": "python", "interpreter": "python3", "environment_tag": "x"}, "解释器非绝对路径"),
            ({"language": "python", "interpreter": "C:\\python.exe", "environment_tag": "x"}, "Windows 路径"),
            ({"language": "python", "interpreter": "/usr/bin/python3", "environment_tag": ""}, "空标签"),
            ({"language": "python", "interpreter": "/usr/bin/python3", "environment_tag": "有空格 的"}, "标签含空格"),
        ],
    )
    def test_environment_digest_rejects_inputs_that_cannot_be_bound(self, kwargs: dict[str, str], why: str) -> None:
        with pytest.raises(ValueError):
            environment_digest(**kwargs)

    def test_question_version_binding_rejects_bad_inputs(self) -> None:
        with pytest.raises(ValueError):
            question_version_binding(question_version_id="nope", package_digest="a" * 64)
        with pytest.raises(ValueError):
            question_version_binding(question_version_id=QUESTION_ID, package_digest="A" * 64)


# --------------------------------------------------------------------------------------
# 安全判定 / 逐用例执行的转发端口
# --------------------------------------------------------------------------------------


class TestForwardingPorts:
    def test_security_decider_blocks_a_high_risk_sample(self) -> None:
        decider = StaticSecurityDecider()
        decision = decider.decide(
            evaluation_run_id=str(uuid.uuid4()),
            files={"solution.py": "import os\nos.system('rm -rf /')\n"},
            language="python",
        )
        assert decision.status == "found"
        assert decision.blocking is True
        assert allows_dynamic_execution(decision) is False

    def test_security_decider_opens_the_gate_for_a_clean_sample(self) -> None:
        decider = StaticSecurityDecider()
        decision = decider.decide(
            evaluation_run_id=str(uuid.uuid4()),
            files={"solution.py": "print(sum([1, 2, 3]))\n"},
            language="python",
        )
        assert decision.status == "not_found"
        assert decision.unchecked == ()
        assert allows_dynamic_execution(decision) is True

    def test_security_decider_rejects_a_non_uuid_run_id(self) -> None:
        with pytest.raises(PackageUnavailableError):
            StaticSecurityDecider().decide(
                evaluation_run_id="not-a-uuid", files={"solution.py": "print(1)\n"}, language="python"
            )

    def test_runner_adapter_refuses_a_handle_that_is_not_a_parsed_package(self) -> None:
        """接线约定被违反时必须响亮失败：绝不把"看起来像 package 的东西"喂给沙箱执行。"""
        with pytest.raises(InternalEvaluationError):
            RunnerCaseRunner().run_case("not-a-parsed-package", None)  # type: ignore[arg-type]

    def test_runner_adapter_never_degrades_to_a_bare_process(self, tmp_path: pathlib.Path) -> None:
        """沙箱不可用 ⇒ `SandboxUnavailableError`；可用时则必须真的产出证据（不二义）。"""
        package = parse_evaluation_package(package_payload())
        availability = probe_sandbox(package.security_policy)
        workspace = tmp_path / "case-0"
        workspace.mkdir()
        case = package.tests[0]
        submission = SubmissionRunInput(
            submission_id=str(uuid.uuid4()),
            evaluation_run_id=str(uuid.uuid4()),
            correlation_id=str(uuid.uuid4()),
            workspace=workspace,
            files=(),
            case=case,
            adapter=package.adapter,
            policy=package.security_policy,
        )
        runner = RunnerCaseRunner()
        if availability.available:
            evidence = runner.run_case(package, submission)
            assert evidence.case_id == case.case_id
        else:
            with pytest.raises(SandboxUnavailableError):
                runner.run_case(package, submission)


# --------------------------------------------------------------------------------------
# 工作区端口（真库 + 真 CAS）
# --------------------------------------------------------------------------------------


class TestWorkspaceProvider:
    @pytest.fixture()
    def store(self, engine, artifact_root: pathlib.Path) -> ContentAddressedStore:
        return ContentAddressedStore(artifact_root, engine)

    def _file(self, store: ContentAddressedStore, path: str, content: bytes) -> EnvelopeFile:
        stored = store.put(content)
        return EnvelopeFile(
            path=path,
            content_sha256=stored.content_sha256,
            size_bytes=stored.size_bytes,
            storage_ref=stored.storage_path,
        )

    @requires_postgres
    def test_prepare_materialises_exact_bytes(
        self, store: ContentAddressedStore, tmp_path: pathlib.Path
    ) -> None:
        provider = CasWorkspaceProvider(store=store, workspace_root=tmp_path / "ws")
        files = (
            self._file(store, "solution.py", b"print(1)\n"),
            self._file(store, "lib/helper.py", b"VALUE = 2\n"),
        )
        workspace = provider.prepare(
            submission_id=str(uuid.uuid4()),
            evaluation_run_id=str(uuid.uuid4()),
            files=files,
        )
        assert (workspace / "solution.py").read_bytes() == b"print(1)\n"
        # 子目录相对路径也要被正确建出来（白名单允许一层以上目录）。
        assert (workspace / "lib" / "helper.py").read_bytes() == b"VALUE = 2\n"

    @requires_postgres
    def test_each_case_gets_its_own_workspace(
        self, store: ContentAddressedStore, tmp_path: pathlib.Path
    ) -> None:
        provider = CasWorkspaceProvider(store=store, workspace_root=tmp_path / "ws")
        run_id = str(uuid.uuid4())
        files = (self._file(store, "solution.py", b"print(1)\n"),)
        run_level = provider.prepare(
            submission_id=str(uuid.uuid4()), evaluation_run_id=run_id, files=files
        )
        first = provider.prepare(
            submission_id=str(uuid.uuid4()), evaluation_run_id=run_id, files=files, case_id=str(uuid.uuid4())
        )
        second = provider.prepare(
            submission_id=str(uuid.uuid4()), evaluation_run_id=run_id, files=files, case_id=str(uuid.uuid4())
        )
        assert len({run_level, first, second}) == 3
        for workspace in (run_level, first, second):
            assert (workspace / "solution.py").exists()

    @requires_postgres
    def test_release_removes_only_inside_the_root(
        self, store: ContentAddressedStore, tmp_path: pathlib.Path
    ) -> None:
        root = tmp_path / "ws"
        provider = CasWorkspaceProvider(store=store, workspace_root=root)
        workspace = provider.prepare(
            submission_id=str(uuid.uuid4()),
            evaluation_run_id=str(uuid.uuid4()),
            files=(self._file(store, "solution.py", b"print(1)\n"),),
        )
        provider.release(workspace)
        assert not workspace.exists()
        # 根本身与根之外的路径都不能被当工作区删掉。
        with pytest.raises(ValueError):
            provider.release(root)
        outside = tmp_path / "outside"
        outside.mkdir()
        with pytest.raises(ValueError):
            provider.release(outside)
        assert outside.exists()

    @requires_postgres
    def test_workspace_root_must_not_overlap_the_cas_root(
        self, store: ContentAddressedStore, artifact_root: pathlib.Path
    ) -> None:
        """CAS 的孤儿 GC 会清理没有元数据的内容文件，而工作区文件正是无元数据的。"""
        with pytest.raises(ValueError):
            CasWorkspaceProvider(store=store, workspace_root=artifact_root)
        with pytest.raises(ValueError):
            CasWorkspaceProvider(store=store, workspace_root=artifact_root / "ws")
        with pytest.raises(ValueError):
            CasWorkspaceProvider(store=store, workspace_root=artifact_root.parent)

    @requires_postgres
    def test_missing_content_is_reported_not_silently_skipped(
        self, store: ContentAddressedStore, tmp_path: pathlib.Path
    ) -> None:
        provider = CasWorkspaceProvider(store=store, workspace_root=tmp_path / "ws")
        ghost = EnvelopeFile(
            path="solution.py", content_sha256="c" * 64, size_bytes=3, storage_ref="cc/cc/" + "c" * 64
        )
        with pytest.raises(Exception):  # noqa: B017 - 具体异常类型由 CAS 定义（ArtifactNotFound/ContentMissing）
            provider.prepare(
                submission_id=str(uuid.uuid4()),
                evaluation_run_id=str(uuid.uuid4()),
                files=(ghost,),
            )

    @requires_postgres
    def test_path_outside_the_whitelist_is_refused(
        self, store: ContentAddressedStore, tmp_path: pathlib.Path
    ) -> None:
        """二次防线：即使库里的行被绕过入口写坏，也不许写出工作区之外。"""
        provider = CasWorkspaceProvider(store=store, workspace_root=tmp_path / "ws")
        stored = store.put(b"print(1)\n")
        for bad_path in ("../escape.py", "/abs.py", "a/../../b.py", "a\\b.py", ""):
            bad = EnvelopeFile(
                path=bad_path,
                content_sha256=stored.content_sha256,
                size_bytes=stored.size_bytes,
                storage_ref=stored.storage_path,
            )
            with pytest.raises(ValueError):
                provider.prepare(
                    submission_id=str(uuid.uuid4()),
                    evaluation_run_id=str(uuid.uuid4()),
                    files=(bad,),
                )
        assert not (tmp_path / "escape.py").exists()
