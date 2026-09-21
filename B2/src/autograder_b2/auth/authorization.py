"""授权判定：认证（已验签）、角色、课程作用域三层独立成立，且**服务端独立判定**。

`MAIN/docs/architecture/security.md` §3.6 要求三层各自成立；前端可见性不构成任何一层。
错误码语义（`MAIN/docs/api/identifiers.md` §3）：未认证 `401`；已认证但无权限 `403`，**不用** `404`
掩盖。

本模块只回答"能不能做"，不含任何存储访问。
"""

from __future__ import annotations

from autograder_b2.auth.tokens import AuthenticationError, Principal, _canonical_uuid


class AuthorizationError(Exception):
    """已认证但权限不足 → 对外 403。"""


def _canonical_request_uuid(value: str, field: str) -> str:
    """把**请求侧**的标识规范化后再参与比较。

    为什么必须做：契约的 `Uuid` 是 `format: uuid`，大写、花括号、`urn:uuid:`、无连字符的 32 位
    十六进制**都是合法写法**，而授权判定是字符串比较。令牌侧已经规范化（`tokens._canonical_uuid`），
    请求侧若不规范化，一份**完全合法**的请求会因为大小写差异被判 403（实测：`student_id` 写成大写
    的本人提交被拒 403）。这里复用同一份规范化规则，不重写规则。

    失败按 403 而不是 401：请求侧的取值不合法属于"这次请求不可授权"，不是"认证失败"。
    """
    try:
        return _canonical_uuid(value, field)
    except AuthenticationError as error:
        raise AuthorizationError(f"{field} is not a UUID") from error


def require_submission_authority(
    principal: Principal, *, course_id: str, section_id: str, student_id: str
) -> None:
    """提交写入的授权判定。

    四条规则，任一不满足即 403：
    1. 令牌必须持有**至少一个已登记角色**（认证不等于授权）；
    2. 令牌必须覆盖请求的 `course_id`（作用域相符）；
    3. 学生只能提交**自己**的作业（`student_id` 必须等于令牌主体）——否则就是冒名提交；
    4. 教师/助教可在其课程作用域内代学生提交（邮件入口与人工补交场景），管理员不受课程限制。

    比较前先把请求侧三个标识规范化（见 `_canonical_request_uuid`）：否则同一份合规请求会因写法差异
    被误拒，而"误拒"与"冒名放行"在日志里都表现为 403/202，很难事后分辨。
    """
    if principal.is_admin:
        return
    if not principal.has_recognized_role:
        raise AuthorizationError("the token carries no recognized platform role")
    canonical_course = _canonical_request_uuid(course_id, "course_id")
    canonical_section = _canonical_request_uuid(section_id, "section_id")
    canonical_student = _canonical_request_uuid(student_id, "student_id")
    if not principal.covers_course(canonical_course, canonical_section):
        raise AuthorizationError("course scope does not cover this submission")
    if canonical_student != principal.user_id and not principal.is_course_staff:
        raise AuthorizationError("a student may only submit their own work")


#: 隔离区复核台的角色（`CHG-0014`：teacher/assistant/admin）。只读复核**不含**学生：
#: 一条隔离记录连的是"某封邮件没能变成提交"，学生既不拥有它、也不该凭它推断他人邮件是否存在。
_QUARANTINE_STAFF_ROLES = ("teacher", "assistant")


def require_intake_quarantine_authority(principal: Principal) -> None:
    """隔离区复核（列表与人工归属）的授权判定；不满足即 `AuthorizationError`（对外 403）。

    契约 `CHG-0014` 把这组端点的授权写成"课程作用域内的 teacher/assistant/admin"。三层里有两层
    在当前数据模型下能真正判定，第三层**判定不了**，这里如实说明而不是假装判过：

    1. **角色**：学生一律 403（`has_recognized_role` 不成立时同样 403——认证不等于授权）；
    2. **课程作用域的存在性**：教师/助教的令牌必须携带**至少一个**课程作用域条目。一个
       `scope: []` 的 staff 令牌不属于任何课程作用域，因此不满足"课程作用域内"；
    3. **课程作用域的相符性**：**无法判定**——`b2.intake_quarantines` 没有 `course_id`，匹配失败时
       也**不存在**课程（自动匹配正是没得出身份才隔离的）。因此任何 staff 令牌都能看到本部署的
       全部隔离记录，而不是"只看到自己课程的"。这不是收窄，也没被写成收窄：硬造一个课程关联
       等于伪造数据，见 [docs/requirements/traceability.md](../../../docs/requirements/traceability.md)
       的契约缺口一节与 `CHG-0014` 的实现报告。

    管理员不受课程限制（与其余端点同口径），因此第 2 条对它不适用。
    """
    if not principal.has_recognized_role:
        raise AuthorizationError("the token carries no recognized platform role")
    if principal.is_admin:
        return
    if not set(principal.roles) & set(_QUARANTINE_STAFF_ROLES):
        raise AuthorizationError("quarantine review is limited to teacher, assistant or admin")
    if not principal.scopes:
        raise AuthorizationError("the token carries no course scope")


def require_question_quality_authority(principal: Principal) -> None:
    """题目质量汇总（`CHG-0015`）的授权判定；不满足即 `AuthorizationError`（对外 403）。

    这是**面向教师的聚合**（"这道题值不值得留"），故限教师/助教/管理员。两处如实说明：

    1. 响应只有计数，因此本条不是为了遮掩内容，而是为了不把一个教师视图暴露成学生可见面；
    2. **按课程判定同样做不到**：请求里只有 `question_version_id`，而"题目属于哪门课"是 B3 的
       事实，B2 不读 B3 的 schema、也不在此复制一份映射。因此本判定只到角色一层，
       课程维度的越权面属契约缺口（见 `docs/api/contract.md`）。
    """
    if not principal.has_recognized_role:
        raise AuthorizationError("the token carries no recognized platform role")
    if principal.is_admin or set(principal.roles) & set(_QUARANTINE_STAFF_ROLES):
        return
    raise AuthorizationError("question quality is limited to teacher, assistant or admin")


def require_reference_verification_authority(principal: Principal) -> None:
    """参考解答验证（`CHG-0019` / `ADR-0015` 决策 1）的授权判定；不满足即 403。

    契约要求"`bearerAuth` + 该题目的**课程写权限**（与 `publish` 同一套判定）"。三层里有两层
    在这里能真正判定，第三层**判定不了**，这里如实说明而不是假装判过：

    1. **角色**：教师/助教/管理员之外一律 403（含学生，含没有任何已登记角色的令牌——
       认证不等于授权）；
    2. **课程作用域的存在性**：教师/助教的令牌必须携带**至少一个**课程作用域条目：
       一个 `scope: []` 的 staff 令牌不属于任何课程，因此不满足"某门课的写权限"；
    3. **课程作用域的相符性**：**无法判定**——请求里只有 `question_version_id`，
       而"题目属于哪门课"是 B3 的事实（`b3.question` 的 `course_id`）。B2 不读 B3 的 schema、
       也不在此复制一份映射，而本端点调 B3 用的是**服务令牌**（B3 因此也不知道是哪位教师）。
       硬造一个课程关联等于伪造数据；因此本判定只到"staff + 至少一门课"这一层，
       课程维度的越权面属契约缺口（见 `docs/api/contract.md`，与隔离区复核、
       `require_question_quality_authority` 同一处缺口，各有记录用例）。

    管理员不受课程限制（与其余端点同口径），因此第 2 条对它不适用。
    """
    if not principal.has_recognized_role:
        raise AuthorizationError("the token carries no recognized platform role")
    if principal.is_admin:
        return
    if not set(principal.roles) & set(_QUARANTINE_STAFF_ROLES):
        raise AuthorizationError(
            "reference verification is limited to teacher, assistant or admin"
        )
    if not principal.scopes:
        raise AuthorizationError("the token carries no course scope")
