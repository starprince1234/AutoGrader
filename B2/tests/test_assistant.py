"""Unit & route tests for real-time AI assistant (`POST /api/v1/submissions/ai-assistant`)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autograder_b2.api.assistant_routes import build_assistant_router
from autograder_b2.assistant.service import (
    AssistantAccessDenied,
    AssistantServiceUnavailable,
    AssistantValidationError,
    generate_draft_ai_assistance,
)
from autograder_b2.auth.tokens import CourseScope, Principal
from autograder_b2.commentary.provider import CommentaryProviderConfig
from autograder_b2.integration.adapters import B3Endpoint
from autograder_b2.integration.assignments import AssignmentAudience
from autograder_b2.integration.b4_authorization import (
    Enrollment,
    UserAuthorization,
)
from autograder_b2.reference.b3 import ReferenceAnswerView

COURSE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "course-1"))
SECTION_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "section-1"))
ASSIGNMENT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "assignment-1"))
QUESTION_VERSION_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "qv-1"))
OTHER_QUESTION_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "qv-other"))
STUDENT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "student-1"))
TEACHER_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "teacher-1"))


@dataclass
class _StubAssignmentDir:
    audience: AssignmentAudience | None

    def get(self, assignment_id: str) -> AssignmentAudience | None:
        if self.audience is not None and assignment_id == self.audience.assignment_id:
            return self.audience
        return None


@dataclass
class _StubAuthDir:
    records: dict[str, UserAuthorization]

    def get(self, user_id: str) -> UserAuthorization:
        return self.records[user_id]


class _StubReferenceClient:
    def __init__(self, view: ReferenceAnswerView) -> None:
        self.view = view

    def fetch_reference_answer(self, question_version_id: str) -> ReferenceAnswerView:
        assert question_version_id == QUESTION_VERSION_ID
        return self.view


class _StubProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []
        self.config = CommentaryProviderConfig(
            provider="openai_compatible",
            base_url="https://api.example.invalid/v1",
            model="test-tutor-model",
            api_key="test-key",
            timeout_seconds=10.0,
        )

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def _make_runtime(*, enrolled: bool = True, with_provider: bool = True):
    audience = AssignmentAudience(
        assignment_id=ASSIGNMENT_ID,
        course_id=COURSE_ID,
        title="循环练习",
        status="open",
        question_version_ids=(QUESTION_VERSION_ID,),
    )
    enrollments = (
        (
            Enrollment(
                course_id=COURSE_ID,
                section_id=SECTION_ID,
                class_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "class-1")),
            ),
        )
        if enrolled
        else ()
    )
    auth_dir = _StubAuthDir(
        {
            STUDENT_ID: UserAuthorization(
                user_id=STUDENT_ID,
                enrollments=enrollments,
                course_grants=(),
            ),
            TEACHER_ID: UserAuthorization(
                user_id=TEACHER_ID,
                enrollments=(),
                course_grants=(COURSE_ID,),
            ),
        }
    )
    provider = (
        _StubProvider(
            json.dumps(
                {
                    "stage_diagnosis": "你已经写出了外层变量初始化，但还没有编写循环体。",
                    "next_steps": [
                        "使用 `for i in range(1, 101):` 遍历 1 到 100。",
                        "在循环体内逐行 `print(i)` 输出结果。",
                    ],
                    "key_concepts": ["range(1, 101) 左闭右开区间"],
                    "check_points": ["确认输出从 1 开始到 100 结束，共 100 行"],
                },
                ensure_ascii=False,
            )
        )
        if with_provider
        else None
    )
    commentary = SimpleNamespace(provider=provider) if with_provider else None
    runtime = SimpleNamespace(
        intake=SimpleNamespace(
            authenticator=SimpleNamespace(
                authenticate=lambda auth: Principal(
                    user_id=STUDENT_ID,
                    roles=("student",),
                    scopes=(CourseScope(course_id=COURSE_ID, section_id=SECTION_ID),),
                    token_id="tok-1",
                )
            ),
            assignment_directory=_StubAssignmentDir(audience),
            authorization_directory=auth_dir,
            unavailable_reason=None,
            settings=SimpleNamespace(
                b3_base_url="http://b3.invalid",
                b3_authorization="Bearer b3-secret",
            ),
        ),
        commentary=commentary,
        commentary_reason=None if with_provider else "AI 模型服务尚未配置",
        b3_endpoint=B3Endpoint(base_url="http://b3.invalid", authorization="Bearer b3-secret"),
    )
    return runtime, provider


def test_generate_draft_ai_assistance_happy_path() -> None:
    runtime, provider = _make_runtime()
    principal = Principal(
        user_id=STUDENT_ID,
        roles=("student",),
        scopes=(CourseScope(course_id=COURSE_ID, section_id=SECTION_ID),),
        token_id="tok-1",
    )
    ref_client = _StubReferenceClient(
        ReferenceAnswerView(
            question_version_id=QUESTION_VERSION_ID,
            content_digest="a" * 64,
            language="python",
            adapter_kind="stdin",
            reference_answer="for i in range(1, 101):\n    print(i)\n",
            stem_markdown="编写 Python 程序输出 1 到 100。",
        )
    )

    result = generate_draft_ai_assistance(
        runtime,
        principal=principal,
        assignment_id=ASSIGNMENT_ID,
        question_version_id=QUESTION_VERSION_ID,
        draft_code="x = 1\n",
        student_question="range 的结束值应该写多少？",
        reference_client=ref_client,
    )

    assert result.question_version_id == QUESTION_VERSION_ID
    assert result.model_name == "test-tutor-model"
    assert "循环体" in result.stage_diagnosis
    assert len(result.next_steps) == 2
    assert provider is not None and len(provider.prompts) == 1
    assert "x = 1" in provider.prompts[0]
    assert "range 的结束值应该写多少" in provider.prompts[0]
    assert "for i in range(1, 101):" in provider.prompts[0]


def test_generate_draft_ai_assistance_rejects_unenrolled_and_wrong_question() -> None:
    runtime, _ = _make_runtime(enrolled=False)
    principal = Principal(
        user_id=STUDENT_ID,
        roles=("student",),
        scopes=(CourseScope(course_id=COURSE_ID, section_id=SECTION_ID),),
        token_id="tok-1",
    )
    with pytest.raises(AssistantAccessDenied):
        generate_draft_ai_assistance(
            runtime,
            principal=principal,
            assignment_id=ASSIGNMENT_ID,
            question_version_id=QUESTION_VERSION_ID,
            draft_code="",
        )

    runtime_ok, _ = _make_runtime(enrolled=True)
    with pytest.raises(AssistantValidationError):
        generate_draft_ai_assistance(
            runtime_ok,
            principal=principal,
            assignment_id=ASSIGNMENT_ID,
            question_version_id=OTHER_QUESTION_ID,
            draft_code="",
        )

    runtime_no_llm, _ = _make_runtime(enrolled=True, with_provider=False)
    with pytest.raises(AssistantServiceUnavailable):
        generate_draft_ai_assistance(
            runtime_no_llm,
            principal=principal,
            assignment_id=ASSIGNMENT_ID,
            question_version_id=QUESTION_VERSION_ID,
            draft_code="",
        )


def test_assistant_route_http_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, _ = _make_runtime()

    monkeypatch.setattr(
        "autograder_b2.assistant.service.B3VerificationClient",
        lambda endpoint: _StubReferenceClient(
            ReferenceAnswerView(
                question_version_id=QUESTION_VERSION_ID,
                content_digest="a" * 64,
                language="python",
                adapter_kind="stdin",
                reference_answer="print(42)",
                stem_markdown="输出 42",
            )
        ),
    )

    app = FastAPI()
    app.include_router(build_assistant_router(runtime))
    client = TestClient(app)

    ok_resp = client.post(
        "/api/v1/submissions/ai-assistant",
        headers={"Authorization": "Bearer test-token"},
        json={
            "assignment_id": ASSIGNMENT_ID,
            "question_version_id": QUESTION_VERSION_ID,
            "draft_code": "",
        },
    )
    assert ok_resp.status_code == 200
    payload = ok_resp.json()
    assert payload["question_version_id"] == QUESTION_VERSION_ID
    assert payload["next_steps"]

    bad_resp = client.post(
        "/api/v1/submissions/ai-assistant",
        headers={"Authorization": "Bearer test-token"},
        json={
            "assignment_id": ASSIGNMENT_ID,
            "question_version_id": QUESTION_VERSION_ID,
            "draft_code": "",
            "unexpected": 1,
        },
    )
    assert bad_resp.status_code == 422
