"""实时 AI 助教模块：读取学生编辑器当前草稿、题干与参考答案，生成启发式下一步提示。"""

from autograder_b2.assistant.service import (
    AssistantAccessDenied,
    AssistantNotFound,
    AssistantServiceUnavailable,
    AssistantValidationError,
    DraftAiAssistantResult,
    generate_draft_ai_assistance,
)

__all__ = [
    "AssistantAccessDenied",
    "AssistantNotFound",
    "AssistantServiceUnavailable",
    "AssistantValidationError",
    "DraftAiAssistantResult",
    "generate_draft_ai_assistance",
]
