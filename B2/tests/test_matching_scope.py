"""匹配候选共识 → 隔离记录 scope（P13 的行级授权依据）。

这份用例**不需要数据库**：`MappingEmailMatcher` 是纯函数，而"scope 只从规则候选共识产生"
恰好是安全链的第一环——如果这里把跨课候选折成其中任意一个，后面所有行级过滤都建立在错
的事实上。数据库侧的持久化与 SQL 过滤由 `tests/test_intake_api.py` / `test_email_intake.py`
在真实 PostgreSQL 上覆盖。
"""

from __future__ import annotations

from autograder_b2.intake.matching import MappingEmailMatcher

COURSE_A = "11111111-1111-4111-8111-111111111111"
COURSE_B = "22222222-2222-4222-8222-222222222222"
SECTION_1 = "33333333-3333-4333-8333-333333333333"
SECTION_2 = "44444444-4444-4444-8444-444444444444"
STUDENT_1 = "55555555-5555-4555-8555-555555555555"
STUDENT_2 = "66666666-6666-4666-8666-666666666666"
VERSION = "77777777-7777-4777-8777-777777777777"


def _target(*, student: str, course: str, section: str) -> dict[str, str]:
    return {
        "student_id": student,
        "course_id": course,
        "section_id": section,
        "question_version_id": VERSION,
    }


def _match(rules: dict[str, dict[str, str]]):
    matcher = MappingEmailMatcher(rules)
    return matcher.match(
        mailbox="submissions", sender="alice@example.edu", attachment_names=("main.py",)
    )


def test_ambiguous_candidates_sharing_one_scope_record_that_pair() -> None:
    """候选是不同学生、但课程/课序完全一致：scope 可审计且可持久化。"""
    result = _match(
        {
            "*": _target(student=STUDENT_1, course=COURSE_A, section=SECTION_1),
            "alice@example.edu|main.py": _target(
                student=STUDENT_2, course=COURSE_A, section=SECTION_1
            ),
        }
    )

    assert result.outcome == "ambiguous"
    assert result.target is None, "歧义时不猜学生身份"
    assert result.scope_context is not None
    assert (result.scope_context.course_id, result.scope_context.section_id) == (
        COURSE_A,
        SECTION_1,
    )


def test_ambiguous_candidates_spanning_sections_have_no_scope() -> None:
    result = _match(
        {
            "*": _target(student=STUDENT_1, course=COURSE_A, section=SECTION_1),
            "alice@example.edu|main.py": _target(
                student=STUDENT_2, course=COURSE_A, section=SECTION_2
            ),
        }
    )

    assert result.outcome == "ambiguous"
    assert result.scope_context is None, "跨课序的候选不得被折成其中任意一个"


def test_ambiguous_candidates_spanning_courses_have_no_scope() -> None:
    result = _match(
        {
            "*": _target(student=STUDENT_1, course=COURSE_A, section=SECTION_1),
            "alice@example.edu|main.py": _target(
                student=STUDENT_2, course=COURSE_B, section=SECTION_1
            ),
        }
    )

    assert result.outcome == "ambiguous"
    assert result.scope_context is None


def test_not_matched_has_no_scope() -> None:
    result = MappingEmailMatcher(
        {"bob@example.edu": _target(student=STUDENT_1, course=COURSE_A, section=SECTION_1)}
    ).match(mailbox="submissions", sender="alice@example.edu", attachment_names=("main.py",))

    assert result.outcome == "not_matched"
    assert result.scope_context is None


def test_unique_match_carries_the_scope_for_auditability() -> None:
    """唯一命中不产生隔离记录；scope 仍如实带出，供审计与测试核对。"""
    result = _match(
        {"alice@example.edu": _target(student=STUDENT_1, course=COURSE_A, section=SECTION_1)}
    )

    assert result.outcome == "matched"
    assert result.target is not None and result.target.student_id == STUDENT_1
    assert result.scope_context is not None
    assert result.scope_context.section_id == SECTION_1


def test_duplicate_rules_for_the_same_identity_are_not_ambiguous() -> None:
    same = _target(student=STUDENT_1, course=COURSE_A, section=SECTION_1)
    result = _match({"*": same, "alice@example.edu": same})

    assert result.outcome == "matched"
    assert result.scope_context is not None
