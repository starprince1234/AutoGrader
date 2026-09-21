"""契约一致性测试（T-CONTRACT-*）：B2 入口校验与 MAIN 契约的关系，以及契约锁只读核验。

关键实测发现（写进断言，防止被"想当然"覆盖）：MAIN `submission-envelope` 的 `path` 模式
`^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$` **接受 `..` 段**（`../a.py`、`a/../../b.py` 均通过 schema）。
因此契约模式是路径安全的**下限**，B2 的 `is_allowed_relative_path` 白名单才是穿越的唯一有效防线——
"契约已校验"绝不等于"路径安全"。
"""

from __future__ import annotations

import uuid

import pytest
import yaml

from autograder_b2 import contracts
from autograder_b2.domain import (
    MAX_PATH_SEGMENTS,
    EnvelopeRejected,
    assert_allowed_relative_path,
    is_allowed_relative_path,
)
from tests.conftest import B2_ROOT

SCHEMA = contracts.SUBMISSION_ENVELOPE_SCHEMA


def _uuid() -> str:
    return str(uuid.uuid4())


def _file(path: str, digest: str = "a" * 64, content_base64: str = "aGk=") -> dict[str, object]:
    return {"path": path, "content_sha256": digest, "content_base64": content_base64}


def _envelope(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "submission_id": _uuid(),
        "student_id": _uuid(),
        "course_id": _uuid(),
        "section_id": _uuid(),
        "question_version_id": _uuid(),
        "files": [_file("main.py")],
        "idempotency_key": "k-1",
    }
    payload.update(overrides)
    return payload


# --- 契约锁只读核验（本小类验收第 3 条：MAIN/contracts 与 contracts.lock 未被改动） ---------


def test_contracts_lock_matches_live_digest() -> None:
    """锁与 `module.yaml` 的契约版本必须相同，且摘要与 `MAIN/contracts` 实时一致。

    **版本号不写死在断言里**（2026-09-18 更正）：它由 MAIN 的 relock 流程推进，本文件曾钉死
    `0.1.0`——MAIN 把契约升到 `0.4.0` 并同步重锁 `contracts.lock` 与 `module.yaml` 之后，这条断言
    立刻变成假失败。它真正要保证的不变量是"锁与声明一致"（宿主 registry 也按这个读），
    不是"版本停在某一刻"。
    """
    manifest = yaml.safe_load((B2_ROOT / "module.yaml").read_text(encoding="utf-8"))
    lock = contracts.read_contract_lock()
    assert lock["contract_version"] == manifest["contract_version"]
    assert lock["digest_policy"] == "sha256"
    assert contracts.contract_digest() == lock["digest"], "MAIN/contracts 与 contracts.lock 摘要漂移"
    assert contracts.contract_digest_matches_lock() is True


def test_contract_digest_is_stable_and_reproducible() -> None:
    """摘要算法必须可复算（同输入同输出），否则"契约未变"无法证明。"""
    first = contracts.contract_digest()
    second = contracts.contract_digest()
    assert first == second
    assert len(first) == 64 and first == first.lower()


def test_contract_files_are_read_only_from_b2() -> None:
    """契约目录只读：B2 的访问不产生写入（比对读前读后摘要）。"""
    before = contracts.contract_digest()
    contracts.load_contract_schema(SCHEMA)
    contracts.contract_schema_errors(SCHEMA, _envelope())
    assert contracts.contract_digest() == before


# --- 契约一致性（本小类验收第 2 条） -------------------------------------------------------


def test_compliant_envelope_passes_contract_schema() -> None:
    assert contracts.contract_schema_errors(SCHEMA, _envelope()) == []


def test_unknown_field_rejected() -> None:
    errors = contracts.contract_schema_errors(SCHEMA, _envelope(source_code="print(1)"))
    assert errors, "未知字段必须被拒（additionalProperties: false）"
    assert any("additional" in e.lower() or "not allowed" in e.lower() or "source_code" in e for e in errors)


@pytest.mark.parametrize(
    "missing",
    ["submission_id", "student_id", "course_id", "section_id", "question_version_id", "files", "idempotency_key"],
)
def test_missing_required_field_rejected(missing: str) -> None:
    payload = _envelope()
    payload.pop(missing)
    assert contracts.contract_schema_errors(SCHEMA, payload), f"缺必填 {missing} 必须被拒"


def test_files_must_not_be_empty() -> None:
    assert contracts.contract_schema_errors(SCHEMA, _envelope(files=[])), "空文件集必须被拒"


@pytest.mark.parametrize("bad_digest", ["A" * 64, "a" * 63, "a" * 65, "z" * 64, ""])
def test_malformed_file_digest_rejected(bad_digest: str) -> None:
    payload = _envelope(files=[_file("main.py", digest=bad_digest)])
    assert contracts.contract_schema_errors(SCHEMA, payload), f"非法摘要 {bad_digest[:8]!r} 必须被拒"


def test_malformed_identifiers_rejected() -> None:
    assert contracts.contract_schema_errors(SCHEMA, _envelope(submission_id="not-a-uuid")), "非法 UUID 必须被拒"
    assert contracts.contract_schema_errors(SCHEMA, _envelope(idempotency_key="")), "空幂等键必须被拒"
    assert contracts.contract_schema_errors(SCHEMA, _envelope(idempotency_key="k" * 201)), "超长幂等键必须被拒"


def test_file_extra_field_rejected() -> None:
    payload = _envelope(files=[{**_file("main.py"), "mode": "0755"}])
    assert contracts.contract_schema_errors(SCHEMA, payload), "文件对象的多余字段必须被拒"


# --- 契约模式的已知边界：`..` 不被 schema 拦住，必须由 B2 白名单拦住 -------------------------


@pytest.mark.parametrize("traversal", ["../a.py", "../../etc/passwd", "a/../../b.py", "./../a.py"])
def test_contract_schema_accepts_dotdot_but_b2_allowlist_rejects(traversal: str) -> None:
    """实测：契约模式接受 `..` 段；因此 B2 白名单是该攻击面唯一有效防线。"""
    assert contracts.contract_schema_errors(SCHEMA, _envelope(files=[_file(traversal)])) == [], (
        "契约模式行为已变化：若 schema 现在会拒绝 `..`，请更新本断言与 interfaces.md I2"
    )
    assert is_allowed_relative_path(traversal) is False
    with pytest.raises(EnvelopeRejected) as excinfo:
        assert_allowed_relative_path(traversal)
    assert excinfo.value.reason == "path_not_allowed"


@pytest.mark.parametrize("bad_path", ["/etc/passwd", "..\\a.py", "a\\b.py", "", "a//b.py", "x" * 201, "a/b/c/d/e/f/g/h/i.py", "a\x00b"])
def test_contract_schema_or_allowlist_rejects_bad_path(bad_path: str) -> None:
    """绝对路径/反斜杠由契约拦住；空串/空段/超长/超深/控制字符由 B2 白名单拦住。两层合计必须全拒。"""
    schema_errors = contracts.contract_schema_errors(SCHEMA, _envelope(files=[_file(bad_path)]))
    allowlist_ok = is_allowed_relative_path(bad_path)
    assert schema_errors or not allowlist_ok, f"{bad_path!r} 两层都没拦住"


@pytest.mark.parametrize("good_path", ["main.py", "src/main.py", "a-b_c.d.py", "solution/part_1.py"])
def test_allowlist_accepts_legitimate_paths(good_path: str) -> None:
    assert is_allowed_relative_path(good_path) is True
    assert contracts.contract_schema_errors(SCHEMA, _envelope(files=[_file(good_path)])) == []


def test_allowlist_boundaries() -> None:
    assert is_allowed_relative_path("/".join(["a"] * MAX_PATH_SEGMENTS)) is True
    assert is_allowed_relative_path("/".join(["a"] * (MAX_PATH_SEGMENTS + 1))) is False
    assert is_allowed_relative_path("a" * 200) is True
    assert is_allowed_relative_path("a" * 201) is False
    assert is_allowed_relative_path(None) is False
    assert is_allowed_relative_path(123) is False
    assert is_allowed_relative_path("..") is False
    assert is_allowed_relative_path(".") is False


# --- 其余 schema 也可读（后续小类复用同一只读入口） ---------------------------------------


@pytest.mark.parametrize(
    "schema",
    [
        contracts.EVALUATION_PACKAGE_SCHEMA,
        contracts.EVALUATION_REPORT_SCHEMA,
        contracts.EVENT_ENVELOPE_SCHEMA,
    ],
)
def test_other_contract_schemas_load(schema: str) -> None:
    loaded = contracts.load_contract_schema(schema)
    assert loaded["$schema"].startswith("https://json-schema.org/draft/2020-12")
