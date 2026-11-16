"""实时 AI 助教服务（`POST /api/v1/submissions/ai-assistant`）。

当学生在网页工作台编辑代码时，随时点击“AI 助教”按钮触发：
1. 校验调用者身份、作业存在性、题目归属关系以及学生当前课程在籍权限；
2. 通过平台内部受保护通道（`B3VerificationClient`）读取题干与参考答案（参考答案仅用于助教内部分析，严禁原文泄露给学生）；
3. 复用 `CommentaryProvider` 调用大模型，结合学生当前草稿代码（允许为空）与可选追问，输出结构化的启发式下一步建议。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from autograder_b2.auth.tokens import Principal
from autograder_b2.commentary.parsing import normalize_text
from autograder_b2.commentary.provider import CommentaryProvider, CommentaryProviderError
from autograder_b2.domain.clock import utc_now
from autograder_b2.integration.adapters import B3Endpoint
from autograder_b2.integration.assignments import AssignmentAudience, AssignmentUnavailable
from autograder_b2.integration.b4_authorization import AuthorizationUnavailable
from autograder_b2.reference.b3 import (
    B3VerificationClient,
    ReferenceAnswerMissing,
    ReferenceAnswerNotFound,
    ReferenceAnswerUnavailable,
    ReferenceAnswerView,
)

if TYPE_CHECKING:  # pragma: no cover
    from autograder_b2.runtime import B2Runtime

MAX_DRAFT_CHARS = 16_000
MAX_QUESTION_CHARS = 1_000

DEFAULT_DISCLAIMERS: tuple[str, ...] = (
    "AI 助教建议旨在启发思路，仅供参考，不代表提交后的最终评测得分。",
    "为保证教学训练效果，助教不会直接提供完整参考答案源码。",
)


class AssistantValidationError(ValueError):
    """请求参数或题目状态不合法（映射为 HTTP 422）。"""


class AssistantNotFound(LookupError):
    """作业或题目版本不存在（映射为 HTTP 404）。"""


class AssistantAccessDenied(PermissionError):
    """调用者未在所属课程在籍或无权访问（映射为 HTTP 403）。"""


class AssistantServiceUnavailable(RuntimeError):
    """大模型或上游 B3/B4 服务不可用（映射为 HTTP 503）。"""


@dataclass(frozen=True, slots=True)
class DraftAiAssistantResult:
    """实时 AI 助教返回结果（与 OpenAPI `DraftAiAssistantResponse` 对应）。"""

    question_version_id: str
    provider_name: str
    model_name: str
    generated_at: datetime
    stage_diagnosis: str
    next_steps: tuple[str, ...]
    key_concepts: tuple[str, ...]
    check_points: tuple[str, ...]
    disclaimers: tuple[str, ...] = DEFAULT_DISCLAIMERS


def _canonical_uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssistantValidationError(f"{field} 必须是有效的 UUID。")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError as error:
        raise AssistantValidationError(f"{field} 格式不是有效的 UUID。") from error


def _resolve_b3_endpoint(runtime: B2Runtime) -> B3Endpoint:
    if runtime.b3_endpoint is not None:
        return runtime.b3_endpoint
    settings = runtime.intake.settings
    if settings is not None and settings.b3_base_url and settings.b3_authorization:
        return B3Endpoint(
            base_url=settings.b3_base_url,
            authorization=settings.b3_authorization,
        )
    raise AssistantServiceUnavailable("题目服务通道尚未配置，无法读取题干与参考思路。")


def _resolve_provider(runtime: B2Runtime) -> CommentaryProvider:
    commentary = runtime.commentary
    if commentary is None or commentary.provider is None:
        reason = runtime.commentary_reason or "AI 模型服务尚未配置。"
        raise AssistantServiceUnavailable(reason)
    return commentary.provider


def _require_audience(
    runtime: B2Runtime,
    *,
    assignment_id: str,
    question_version_id: str,
) -> AssignmentAudience:
    directory = runtime.intake.assignment_directory
    if directory is None:
        raise AssistantServiceUnavailable("作业目录尚未配置，无法校验题目归属。")
    try:
        audience = directory.get(assignment_id)
    except AssignmentUnavailable as error:
        raise AssistantServiceUnavailable("作业目录暂时不可达，请稍后重试。") from error
    if audience is None:
        raise AssistantNotFound("指定的作业不存在。")
    if not audience.contains_question(question_version_id):
        raise AssistantValidationError("该题目不属于当前作业。")
    return audience


def _require_course_access(
    runtime: B2Runtime,
    *,
    principal: Principal,
    course_id: str,
) -> None:
    if not principal.has_recognized_role:
        raise AssistantAccessDenied("当前账号角色无权使用 AI 助教。")
    if principal.is_admin:
        return
    directory = runtime.intake.authorization_directory
    if directory is None:
        if principal.is_course_staff and principal.covers_course(course_id, None):
            return
        raise AssistantServiceUnavailable("课程成员权限目录尚未配置。")
    try:
        authorization = directory.get(principal.user_id)
    except AuthorizationUnavailable as error:
        raise AssistantServiceUnavailable("课程成员权限服务暂时不可达，请稍后重试。") from error
    if authorization.is_course_staff_for(course_id):
        return
    if authorization.enrollment_for_course(course_id) is None:
        raise AssistantAccessDenied("您当前未选修该作业所属课程，无法使用 AI 助教。")


def build_assistant_prompt(
    *,
    reference: ReferenceAnswerView,
    draft_code: str,
    student_question: str | None,
) -> str:
    """构造实时 AI 助教 Prompt（严格要求启发式提示且不泄露完整答案）。"""
    stem = (reference.stem_markdown or "").strip() or "（未提供独立题干文本，请结合参考答案理解题意。）"
    tiers = reference.reference_answers or (
        {"label": "参考答案", "body": reference.reference_answer},
    )
    tier_blocks = [
        f"### 参考思路 {index}：{tier.get('label', f'解法 {index}')}\n"
        f"```{reference.language}\n{tier['body'].rstrip()}\n```"
        for index, tier in enumerate(tiers, start=1)
    ]

    normalized_draft = draft_code.strip()
    if not normalized_draft:
        draft_block = "（学生尚未开始编写代码，编辑器当前为空。）"
    else:
        snippet = draft_code[:MAX_DRAFT_CHARS]
        truncated_note = "\n... [草稿过长已截断]" if len(draft_code) > MAX_DRAFT_CHARS else ""
        draft_block = f"```{reference.language}\n{snippet.rstrip()}{truncated_note}\n```"

    question_block = (
        f"\n\n## 学生当前补充疑问\n{student_question.strip()[:MAX_QUESTION_CHARS]}"
        if student_question and student_question.strip()
        else ""
    )

    return (
        "你是一位耐心、专业、善于启发学生的计算机课程 AI 助教。\n"
        "学生正在网页代码编辑器中实时作答，尚未（或准备修改后再次）提交，需要你根据题目要求、参考解法（仅供内部比对）以及学生当前写出的草稿代码，给出清晰的下一步作答指导。\n\n"
        "【核心教学原则与安全红线】\n"
        "1. **严禁直接给出完整可提交的参考答案代码**，也不要逐字复述参考答案大段源码；要引导思考和具体修改方向。\n"
        "2. 若学生尚未写代码（草稿为空），请帮学生拆解题目需求、明确输入输出格式与第一步可以搭好的代码骨架思路。\n"
        "3. 若学生已有部分代码，请先肯定已有进展，再精准指出卡点/逻辑偏差/边界遗漏，并给出 2~4 条可落地的下一步修改步骤。\n"
        "4. 若学生附带了具体疑问，请优先针对该疑问进行清晰解答。\n\n"
        f"## 编程语言\n{reference.language}\n\n"
        f"## 题目题干\n{stem}\n\n"
        "## 内部参考答案（仅供助教比对思路，禁止原文抄给学生）\n"
        + "\n\n".join(tier_blocks)
        + "\n\n## 学生当前编辑器草稿\n"
        + draft_block
        + question_block
        + "\n\n## 输出格式要求\n"
        "请严格只输出一个合法的 JSON 对象（不要输出额外前缀文字），格式如下：\n"
        "{\n"
        '  "stage_diagnosis": "1~2句话总结学生当前处于什么阶段、做对了什么或卡在哪里",\n'
        '  "next_steps": ["下一步具体建议1（启发式）", "下一步具体建议2", "下一步具体建议3"],\n'
        '  "key_concepts": ["本题核心知识点或关键语法1", "关键语法2"],\n'
        '  "check_points": ["容易忽略的边界条件/输出格式自检项1", "自检项2"]\n'
        "}\n"
    )


def _extract_json_mapping(raw: str) -> Mapping[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {"stage_diagnosis": text[:600]}
        else:
            return {"stage_diagnosis": text[:600]}
    return parsed if isinstance(parsed, Mapping) else {"stage_diagnosis": text[:600]}


def _clean_list(value: Any, *, fallback: tuple[str, ...], limit: int = 5) -> tuple[str, ...]:
    if not isinstance(value, list):
        return fallback
    items: list[str] = []
    for entry in value[:limit]:
        if isinstance(entry, str):
            text = normalize_text(entry)
            if text:
                items.append(text[:400])
    return tuple(items) if items else fallback


def parse_assistant_response(
    raw: str,
    *,
    question_version_id: str,
    provider_name: str,
    model_name: str,
    generated_at: datetime,
) -> DraftAiAssistantResult:
    doc = _extract_json_mapping(raw)
    raw_diagnosis = doc.get("stage_diagnosis")
    diagnosis = (
        normalize_text(raw_diagnosis)[:600]
        if isinstance(raw_diagnosis, str) and normalize_text(raw_diagnosis)
        else "已结合题干与当前草稿完成分析，请参考下方步骤继续完善实现。"
    )
    next_steps = _clean_list(
        doc.get("next_steps"),
        fallback=("先对照题干检查输入读取方式与输出格式要求，再逐步补全核心逻辑。",),
    )
    key_concepts = _clean_list(
        doc.get("key_concepts"),
        fallback=("题干拆解", "输入输出规范"),
    )
    check_points = _clean_list(
        doc.get("check_points"),
        fallback=("检查空值/边界输入与输出末尾多余空格或换行。",),
    )
    return DraftAiAssistantResult(
        question_version_id=question_version_id,
        provider_name=provider_name,
        model_name=model_name,
        generated_at=generated_at,
        stage_diagnosis=diagnosis,
        next_steps=next_steps,
        key_concepts=key_concepts,
        check_points=check_points,
        disclaimers=DEFAULT_DISCLAIMERS,
    )


def generate_draft_ai_assistance(
    runtime: B2Runtime,
    *,
    principal: Principal,
    assignment_id: object,
    question_version_id: object,
    draft_code: object,
    student_question: object = None,
    reference_client: B3VerificationClient | None = None,
) -> DraftAiAssistantResult:
    """执行实时 AI 助教请求校验、取题干/参考答案并生成启发式建议。"""
    canonical_assignment_id = _canonical_uuid(assignment_id, "assignment_id")
    canonical_question_version_id = _canonical_uuid(question_version_id, "question_version_id")
    if not isinstance(draft_code, str):
        raise AssistantValidationError("draft_code 必须是字符串。")
    if len(draft_code) > 65536:
        raise AssistantValidationError("draft_code 长度超出 65536 字符限制。")
    if student_question is not None and not isinstance(student_question, str):
        raise AssistantValidationError("student_question 必须是字符串或 null。")
    if isinstance(student_question, str) and len(student_question) > MAX_QUESTION_CHARS:
        raise AssistantValidationError("student_question 长度超出 1000 字符限制。")

    audience = _require_audience(
        runtime,
        assignment_id=canonical_assignment_id,
        question_version_id=canonical_question_version_id,
    )
    _require_course_access(runtime, principal=principal, course_id=audience.course_id)

    provider = _resolve_provider(runtime)
    client = (
        reference_client
        if reference_client is not None
        else B3VerificationClient(_resolve_b3_endpoint(runtime))
    )

    try:
        reference = client.fetch_reference_answer(canonical_question_version_id)
    except ReferenceAnswerNotFound as error:
        raise AssistantNotFound("题目版本在题库中不存在。") from error
    except ReferenceAnswerMissing as error:
        raise AssistantValidationError("该题目尚未配置题干或参考思路，暂无法提供 AI 助教分析。") from error
    except ReferenceAnswerUnavailable as error:
        raise AssistantServiceUnavailable("题目上下文服务暂时不可达，请稍后重试。") from error

    prompt = build_assistant_prompt(
        reference=reference,
        draft_code=draft_code,
        student_question=student_question,
    )
    try:
        raw_reply = provider.complete(prompt)
    except CommentaryProviderError as error:
        raise AssistantServiceUnavailable(f"AI 助教生成失败：{error}") from error

    return parse_assistant_response(
        raw_reply,
        question_version_id=canonical_question_version_id,
        provider_name=provider.config.provider,
        model_name=provider.config.model,
        generated_at=utc_now(),
    )
