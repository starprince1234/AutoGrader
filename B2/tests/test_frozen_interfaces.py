"""冻结接口的文档↔代码一致性测试（T-IFACE-*）。

以 `docs/architecture/interfaces.md` 为**唯一基准**：解析其中的 python 代码块，断言运行期类型的
字段名序列、**字段注解**（经 `ast.unparse` 规范化后逐字比较）、字面量别名取值与方法参数一致。
任何一方漂移都会让本测试失败——这正是"冻结"的机器含义。

`CaseRunner` 是文档第 5.1 节顶层 `run_case(package, submission) -> CaseEvidence` 的协议形态，
参数名必须等于文档函数参数（去掉 `self`），由 `test_run_case_protocol_matches_doc_function` 断言。
"""

from __future__ import annotations

import ast
import pathlib
import re
from dataclasses import fields, is_dataclass
from typing import get_args

import pytest

from autograder_b2 import domain

INTERFACES_DOC = pathlib.Path(__file__).resolve().parents[1] / "docs" / "architecture" / "interfaces.md"
_PY_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)

#: 文档中声明了形态的类型（数据类 + Protocol）→ 运行期类型。
FROZEN_TYPES: dict[str, object] = {
    "EnvelopeFile": domain.EnvelopeFile,
    "ApiProvenance": domain.ApiProvenance,
    "EmailProvenance": domain.EmailProvenance,
    "SubmissionEnvelope": domain.SubmissionEnvelope,
    "StaticFinding": domain.StaticFinding,
    "CheckOutcome": domain.CheckOutcome,
    "StaticAnalysisResult": domain.StaticAnalysisResult,
    "SecurityFinding": domain.SecurityFinding,
    "SecurityDecision": domain.SecurityDecision,
    "SubmissionRunInput": domain.SubmissionRunInput,
    "CaseEvidence": domain.CaseEvidence,
    "DomainEvent": domain.DomainEvent,
    "DispatchReport": domain.DispatchReport,
    "OutboxWriter": domain.OutboxWriter,
    "OutboxDispatcher": domain.OutboxDispatcher,
}

#: 文档中显式声明的 `Literal` 别名 → 运行期别名。
FROZEN_LITERAL_ALIASES: dict[str, object] = {
    "Severity": domain.Severity,
    "CheckStatus": domain.CheckStatus,
    "SecurityStatus": domain.SecurityStatus,
    "SecurityCategory": domain.SecurityCategory,
    "SecuritySeverity": domain.SecuritySeverity,
    "DetectedIn": domain.DetectedIn,
    "FailureCategory": domain.FailureCategory,
}


def _normalize(annotation: str) -> str:
    """把一个注解源码字符串规范化（解析后重新 unparse），消除引号/空格差异。"""
    return ast.unparse(ast.parse(annotation, mode="eval").body)


def _doc_nodes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def _doc_class_annotations(tree: ast.Module) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name, node in _doc_nodes(tree).items():
        result[name] = {
            statement.target.id: ast.unparse(statement.annotation)
            for statement in node.body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        }
    return result


def _doc_class_methods(tree: ast.Module) -> dict[str, dict[str, list[str]]]:
    return {
        name: {method.name: [arg.arg for arg in method.args.args] for method in node.body if isinstance(method, ast.FunctionDef)}
        for name, node in _doc_nodes(tree).items()
    }


def _doc_functions(tree: ast.Module) -> dict[str, list[str]]:
    return {
        node.name: [arg.arg for arg in node.args.args] for node in tree.body if isinstance(node, ast.FunctionDef)
    }


def _doc_literal_aliases(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target, value = node.targets[0], node.value
        if not (isinstance(target, ast.Name) and isinstance(value, ast.Subscript)):
            continue
        if not (isinstance(value.value, ast.Name) and value.value.id == "Literal"):
            continue
        elements = value.slice.elts if isinstance(value.slice, ast.Tuple) else [value.slice]
        result[target.id] = tuple(el.value for el in elements if isinstance(el, ast.Constant))
    return result


@pytest.fixture(scope="module")
def doc_source() -> str:
    assert INTERFACES_DOC.is_file(), "interfaces.md 缺失"
    return INTERFACES_DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def doc_tree(doc_source: str) -> ast.Module:
    blocks = _PY_BLOCK.findall(doc_source)
    assert blocks, "interfaces.md 未包含 python 代码块"
    return ast.parse("\n".join(blocks))


def test_doc_declares_all_five_frozen_interfaces(doc_source: str) -> None:
    for heading in (
        "## 2. `SubmissionEnvelope`（归一化后）",
        "## 3. `StaticAnalysisResult`",
        "## 4. `SecurityDecision`",
        "## 5. `run_case(package, submission) -> CaseEvidence`",
        "## 6. outbox 发布接口",
    ):
        assert heading in doc_source, f"interfaces.md 缺少冻结接口章节: {heading}"


def test_five_interfaces_importable_from_single_entry() -> None:
    """五项接口（含 runner/outbox 协议）可从 `autograder_b2.domain` 单点 import。"""
    for name in ("SubmissionEnvelope", "StaticAnalysisResult", "SecurityDecision", "CaseEvidence", "DomainEvent"):
        assert hasattr(domain, name), f"{name} 未从 autograder_b2.domain 导出"
    for name in ("CaseRunner", "OutboxWriter", "OutboxDispatcher"):
        assert hasattr(domain, name), f"{name} 未从 autograder_b2.domain 导出"


@pytest.mark.parametrize("name", sorted(FROZEN_TYPES))
def test_field_names_match_doc_verbatim(name: str, doc_tree: ast.Module) -> None:
    doc_fields = _doc_class_annotations(doc_tree)[name]
    runtime = FROZEN_TYPES[name]
    if is_dataclass(runtime):
        runtime_fields = [field.name for field in fields(runtime)]
    else:
        runtime_fields = list(getattr(runtime, "__annotations__", {}))
    assert runtime_fields == list(doc_fields), f"{name} 字段名/顺序与文档不一致"


@pytest.mark.parametrize("name", sorted(FROZEN_TYPES))
def test_field_annotations_match_doc_verbatim(name: str, doc_tree: ast.Module) -> None:
    """字段注解逐字一致（经 ast.unparse 规范化后比较）。"""
    doc_annotations = _doc_class_annotations(doc_tree)[name]
    runtime = FROZEN_TYPES[name]
    if not is_dataclass(runtime):
        pytest.skip("Protocol 无字段")
    runtime_annotations = runtime.__annotations__
    assert set(runtime_annotations) == set(doc_annotations), f"{name} 注解字段集合不一致"
    for field_name, doc_annotation in doc_annotations.items():
        assert _normalize(runtime_annotations[field_name]) == _normalize(doc_annotation), (
            f"{name}.{field_name} 注解与文档不一致"
        )


@pytest.mark.parametrize("name", sorted(FROZEN_TYPES))
def test_declared_shape_matches_doc(name: str, doc_tree: ast.Module) -> None:
    """数据类必须 frozen+slots；Protocol 方法参数必须与文档一致。"""
    node = _doc_nodes(doc_tree)[name]
    runtime = FROZEN_TYPES[name]
    decorators = {ast.unparse(dec) for dec in node.decorator_list}
    if is_dataclass(runtime):
        assert "dataclass(frozen=True, slots=True)" in decorators
        assert runtime.__dataclass_params__.frozen is True, f"{name} 必须 frozen"
        assert hasattr(runtime, "__slots__"), f"{name} 必须 slots"
    else:
        assert "Protocol" in {ast.unparse(base) for base in node.bases}
        for method_name, doc_args in _doc_class_methods(doc_tree)[name].items():
            method = getattr(runtime, method_name, None)
            assert method is not None, f"{name}.{method_name} 未实现"
            code = method.__code__
            assert list(code.co_varnames[: code.co_argcount]) == doc_args, f"{name}.{method_name} 参数不一致"


@pytest.mark.parametrize("name", sorted(FROZEN_LITERAL_ALIASES))
def test_literal_alias_values_match_doc(name: str, doc_tree: ast.Module) -> None:
    doc_values = _doc_literal_aliases(doc_tree).get(name)
    if doc_values is None:
        pytest.skip(f"{name} 在文档中以 inline Literal 出现（由字段注解测试覆盖）")
    assert get_args(FROZEN_LITERAL_ALIASES[name]) == doc_values, f"{name} 取值与文档不一致"


def test_run_case_protocol_matches_doc_function(doc_tree: ast.Module) -> None:
    """`CaseRunner.run_case` 的参数必须等于文档顶层 `run_case` 的参数（去掉 self）。"""
    doc_args = _doc_functions(doc_tree)["run_case"]
    assert doc_args == ["package", "submission"]
    code = domain.CaseRunner.run_case.__code__
    assert list(code.co_varnames[: code.co_argcount]) == ["self", *doc_args]


def test_rejection_reasons_match_doc_table(doc_source: str) -> None:
    """`EnvelopeRejected.reason` 取值集合必须逐条等于 interfaces.md 第 2.4 节表格。

    文档允许一行并列两个原因码（如 `` `empty_fileset` / `duplicate_path` ``），此处按行内出现顺序展开。
    """
    section = doc_source.split("### 2.4 失败语义", 1)[1].split("\n## ", 1)[0]
    codes: list[str] = []
    in_table = False
    for line in section.splitlines():
        if line.startswith("|---"):
            in_table = True
            continue
        if in_table and line.startswith("|") and "`" in line:
            first_cell = line.strip("|").split("|")[0]
            codes.extend(re.findall(r"`([a-z_]+)`", first_cell))
    assert len(codes) == 9, f"第 2.4 节原因码数量异常: {codes}"
    assert get_args(domain.RejectionReason) == tuple(codes)


def test_failure_semantics_error_types_landed(doc_source: str) -> None:
    """第 5.4 节点名的失败类型必须在代码中存在（失败语义不能只写在纸上）。"""
    section = doc_source.split("### 5.4 失败语义", 1)[1].split("\n## ", 1)[0]
    for error_name in ("SandboxUnavailableError", "PackageUnavailableError", "InternalEvaluationError"):
        assert error_name in section, f"文档第 5.4 节未提及 {error_name}"
        assert hasattr(domain, error_name), f"{error_name} 未落地"
        assert issubclass(getattr(domain, error_name), RuntimeError)


def test_three_state_security_status_exact_values() -> None:
    assert get_args(domain.SecurityStatus) == ("found", "not_found", "not_checked")
    assert domain.B2_PRODUCER == "B2"


def test_no_field_alias_or_dual_write_in_frozen_types(doc_tree: ast.Module) -> None:
    """禁止别名/双写：同一类型内不得出现同一语义的两个字段名。"""
    for name in FROZEN_TYPES:
        doc_fields = list(_doc_class_annotations(doc_tree)[name])
        assert len(doc_fields) == len(set(doc_fields)), f"{name} 存在重复字段"
    assert not hasattr(domain.SubmissionEnvelope, "sha256")
    assert not hasattr(domain.SubmissionEnvelope, "files_base64")
    envelope_fields = {field.name for field in fields(domain.SubmissionEnvelope)}
    assert "submission_digest" in envelope_fields
    assert "digest" not in envelope_fields
