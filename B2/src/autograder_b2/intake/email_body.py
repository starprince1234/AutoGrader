"""邮件正文的机器可读五行：核验码 / 作业编号 / 课序 / 姓名 / 学号（设计 §6.4 第 5 步）。

为什么要有这个模块：附件名只回答"这是第几题的答案文件"，而"这封邮件属于**哪个作业**、
由**哪个学生**发出"必须由正文声明并由服务端独立核对。正文是**完全可伪造的输入**，因此这里
只做三件事：

1. **解析**（纯函数、无 IO、无 DB）：从有界正文摘录里按标签取值，中/英文冒号都接受；
2. **缺项分类**：缺哪个标签就报哪个标签的**英文键名**，绝不回显取值（取值可能含 PII/内容）；
3. **规范化**：`姓名`/`学号`先去掉首尾空白、把内部连续空白折叠成一个空格，再做精确比较——
   不猜测"看起来像"。`作业编号`规范化为 UUID 文本。核验码/作业编号/学号只取标签后的第一个
   token：正文摘录被折叠成单行后，学生写在五行之下的附言会"紧跟"最后一个值，不能把附言并进
   学号。

判定本身（是否等于核验码挑战的 assignment、是否等于 B4 身份目录里的姓名/学号）不在这里：
那是 `intake.imap` 的职责，本模块只提供可复用的纯函数，避免解析与判定各写一套。

课序只要求非空：B2 无法验证"课序展示名"（那是 B4 的课程序号/名称），身份由"核验码 + 发件
账号 + 姓名/学号与 B4 目录一致"证明；课序在这里是给老师人工核对的人读信息（设计 §6.1）。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

#: 正文标签 → 内部字段键。标签是固定的中文字面量（设计 §6.1 冻结的模板）。
FIELD_LABELS: dict[str, str] = {
    "核验码": "verification_code",
    "作业编号": "assignment_id",
    "课序": "course_sequence",
    "姓名": "name",
    "学号": "student_number",
}
#: 标签定位正则：`核验码：`/`作业编号:` 等。**不假设正文有换行**——`attachments._body_excerpt`
#: 会把正文所有空白折叠成单个空格（有界摘录的既有行为），因此这里在**整段文本**上按标签切分：
#: 每个字段的值 = 该标签的冒号之后到**下一个标签**开始之前的子串。标签词本身若作为普通文字
#: 出现在某个值里（例如姓名恰好包含"姓名"且后跟冒号），会被误当成分隔——这是模板固定五行格式的
#: 已知边界，服务端只按"可复现的确定性规则"解释，不做模糊猜测。
_LABEL_PATTERN = re.compile(
    r"(?P<label>"
    + "|".join(re.escape(label) for label in FIELD_LABELS)
    + r")\s*[:：]\s*"
)
#: 字段内部键的读取顺序（也是"缺项"报告的顺序，便于客户端稳定展示）。
FIELD_ORDER: tuple[str, ...] = tuple(FIELD_LABELS.values())
#: 取值是**不含空白的单个 token** 的字段：只取标签后的第一个 token，避免把学生附言并进取值
#: （正文摘录折叠成单行后，最后一个标签之后的文字会"紧跟"在值后面）。
_SINGLE_TOKEN_FIELDS = frozenset({"verification_code", "assignment_id", "student_number"})
#: 单个取值的长度上限：正文摘录本身有 4000 字上限，但一个"值"不该跨整段正文。
MAX_FIELD_LENGTH = 200
#: 姓名/学号规范化后的空白（含 Unicode 空白）；连续空白折叠为单个半角空格。
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class EmailBody:
    """解析结果：五个字段按 `FIELD_ORDER` 排列；`missing` 列出缺失的字段键。

    `values` 里保留**规范化前**的原始取值（已 strip），判定方按需再规范化；缺失字段的值为
    `None`。字段数固定为 5，`as_mapping()` 给判定方一个只读视图。
    """

    verification_code: str | None = None
    assignment_id: str | None = None
    course_sequence: str | None = None
    name: str | None = None
    student_number: str | None = None
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        """五个字段是否全部存在（非空）。"""
        return not self.missing

    def as_mapping(self) -> dict[str, str | None]:
        return {key: getattr(self, key) for key in FIELD_ORDER}


def parse_email_body(text: str | None) -> EmailBody:
    """从正文摘录里解析五行；未知文字忽略，重复标签取**第一次**出现（确定性）。

    **不假设正文有换行**：`attachments._body_excerpt` 把正文空白折叠成单个空格，因此这里在整段
    文本上按标签正则定位，值 = 冒号后到下一个标签之前（或文本末尾）的子串。带换行的原始正文与
    折叠后的单行摘录因此解析出同样的五个字段。

    - 空正文 → 五个字段全缺（调用方据此报"缺核验码/缺字段"）；
    - 值可以带前导/尾随空白（会被 strip）；
    - 同一个标签出现多次时取第一次：不猜测哪一次"更真"；
    - 值长度截断到 `MAX_FIELD_LENGTH`（截断不改变"存在性"，但避免一个值吃掉整篇正文）。

    本函数**不抛异常**、不产生日志：任何输入都映射成"缺哪些字段"的数据。
    """
    values: dict[str, str] = {}
    if isinstance(text, str) and text:
        matches = list(_LABEL_PATTERN.finditer(text))
        for index, match in enumerate(matches):
            key = FIELD_LABELS[match.group("label")]
            if key in values:
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            value = text[match.end() : end].strip()
            if key in _SINGLE_TOKEN_FIELDS:
                # 正文摘录被折叠成单行，最后一个标签之后的学生附言会并进"最后一个字段"。
                # 核验码/作业编号/学号都是**不含空白的单个 token**，取第一个 token 即可，
                # 既不把附言当成取值，也不改变"存在性"的判定。
                value = value.split(" ", 1)[0] if value else ""
            if value:
                values[key] = value[:MAX_FIELD_LENGTH]
    resolved: dict[str, str | None] = {key: values.get(key) for key in FIELD_ORDER}
    missing = tuple(key for key in FIELD_ORDER if not resolved[key])
    return EmailBody(
        verification_code=resolved["verification_code"],
        assignment_id=resolved["assignment_id"],
        course_sequence=resolved["course_sequence"],
        name=resolved["name"],
        student_number=resolved["student_number"],
        missing=missing,
    )


def normalize_person_text(value: str | None) -> str | None:
    """姓名/学号的比较形态：去首尾空白、内部连续空白折叠成单个半角空格（精确比较，不改大小写）。"""
    if value is None:
        return None
    collapsed = _WHITESPACE.sub(" ", value).strip()
    return collapsed or None


def normalize_assignment_id(value: str | None) -> str | None:
    """把正文里声明的作业编号规范化为 UUID 文本；不是合法 UUID 返回 `None`（不猜）。

    契约把 `assignment_id` 定义为 `format: uuid`，学生从页面复制时可能出现大写/花括号写法；
    规范化让"同一个 UUID 的不同合法写法"不被误判成不同作业。
    """
    if value is None:
        return None
    try:
        return str(uuid.UUID(value.strip()))
    except (AttributeError, TypeError, ValueError):
        return None


def missing_fields_detail(body: EmailBody) -> str:
    """缺项的可读明细：只出现**字段键名**（英文稳定标识），不回显任何取值。

    文案同时出现中文标签，方便学生对照自己邮件里缺了哪一行。
    """
    labels_by_key = {key: label for label, key in FIELD_LABELS.items()}
    shown = ", ".join(f"{key}（{labels_by_key[key]}）" for key in body.missing)
    return f"the message body is missing required field(s): {shown}"


__all__ = [
    "FIELD_LABELS",
    "FIELD_ORDER",
    "MAX_FIELD_LENGTH",
    "EmailBody",
    "missing_fields_detail",
    "normalize_assignment_id",
    "normalize_person_text",
    "parse_email_body",
]
