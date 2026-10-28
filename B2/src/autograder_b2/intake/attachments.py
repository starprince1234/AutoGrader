"""邮件附件的 MIME 解析与安全闸门（结构判定 + 正向判定 + 坏签名扫描 + 上限）。

威胁模型（[docs/architecture/boundaries.md](../../docs/architecture/boundaries.md)）：邮件入口的
一切输入**都可伪造**——发件人、文件名、`Content-Type`、`Content-Transfer-Encoding` 都不作数。
判定顺序被有意固定成下面这样：

1. **先判大小、再解码**：编码形态（base64 / quoted-printable）的长度已经给出解码后大小的**上界**，
   先按上界拒绝，压缩炸弹就永远没有机会在内存里展开——"先解码再判大小"正是这类攻击的入口；
2. **先认已知的坏东西（为了原因码精确）**：可执行体与归档容器的签名扫描；
3. **再做正向判定**：允许列表内的附件必须能被"纯文本源码"这一性质**证明**——严格 UTF-8 解码成功、
   无 NUL 字节、每个字符可打印或属于空格类分隔符、控制字符只允许 `\\t\\n\\r\\x0c`（`\\x0c`
   换页符被 Python 当空白接受，`\\x0b` 不被接受）；
4. **文本还要过结构级容器判定**：一份"全长可打印"的 tar / zip 仍然是**能打开的归档**
   （数字字段写成"七位八进制 + 空格"、magic 区填空格、数据块用空格补齐即无 NUL），
   正向判定看不出来，因此用 stdlib 解析器（`tarfile` / `zipfile`）在**有界字节**上做结构判定，
   并且查**任意偏移**（预置前缀不算绕过）。炸弹证据可读即 `archive_bomb`，否则 `archive_attachment`；
5. **最后看扩展名**：只放行纯文本源码（`.py` / `.sh` / `.txt` / `.md`）；
6. **名称走 I2 白名单**：附件名会成为提交内的 `path`，因此与 API 路径受**同一套**白名单约束
   （`..`、绝对路径、空格、控制字符一律拒绝），且**不做静默改名**——替学生猜文件名等于伪造事实。

### 这个闸门承诺什么、不承诺什么

- **承诺**：落盘的内容不是**容器/可执行体**（归档、压缩流、可执行映像、嵌套邮件），
  并且是被证明过的纯文本。所有"未被枚举到"的容器/可执行形态由正向判定 + 结构判定接住，
  而不是靠签名表穷举。
- **不承诺**："不是别的解释器的程序"。可打印的解释器输入——PostScript（`%!PS`）、仅可打印 ASCII
  的 PDF、含 `<script>` 的 HTML/SVG、`.bat`/`.ps1` 文本——在命名为允许扩展名时**会**通过闸门。
  这是设计使然：接收层只回答"能不能安全地收下这份字节"，"这段源码会不会做危险的事"属于静态规则
  与沙箱（大类 3/4）的职责。**不得**把本闸门当成"不是程序"的证明。
- **也不承诺**："代码能通过语法检查"。`.py` 有语法错误是**学生结果**（由 RQ-B-2-40 静态检查出报告），
  不是接收期拒绝理由。因此这里**禁止**用 `ast.parse` 之类做闸门——那会把"语法错误"变成"邮件被拒"，
  破坏"两条入口同一路径"的语义。

### 容器识别：分四组，判据各不相同（本节与实现逐条对应）

1. **"任意偏移即定论"组**
   （`_ANY_OFFSET_ARCHIVE_SIGNATURES` / `_ANY_OFFSET_EXECUTABLE_SIGNATURES`）：
   签名里含**合法纯文本里不可能出现的字节**（NUL、控制字符、非 UTF-8 前导字节）——
   `PK\x03\x04`、`\x1f\x8b`、`\x1f\x9d`、`\x04\x22\x4d\x18`、`\x28\xb5\x2f\xfd`、
   `\xfd7zXZ\x00`、`7z\xbc\xaf\x27\x1c`、`Rar!\x1a\x07`、`\x7fELF`、Mach-O 四型、
   `\x00asm`、`ustar\0`。入组判据是**"不可能出现在合法文本里"**，不是"看起来像二进制"：
   第一处命中即定论（没有"候选不合法要往后找"的情形）。
   - **`arj`（`\x60\xea`）已移出本组**：0x60 是反引号（可打印），0xea 是 U+A000–U+AFFF 的合法
     UTF-8 首字节，`"…code point \u0060\uA000…"` 这样的正文会命中它（真实误报，已复现）。
     真 ARJ 的压缩体必然含 NUL/控制字节，由正向判定拒绝（原因码退化为 `not_plain_text`）。
2. **"可打印但可结构校验"组**（`tar` / `cpio`(newc,crc,odc) / `ar` / `CAB`）：
   这些 magic 全是可打印 ASCII，因此**遍历全部出现位置**并逐个结构校验，直到某个位置合法为止。
   只认第一处或只看偏移 0 会同时造成两类错误：**漏判**（前缀里放一个诱饵字面量就盖住后面的真归档）
   与**误报**（文本里恰好出现该字面量）。结构校验各自的判据：tar 头部校验和；
   cpio 按**变体自己的头形状**（newc/crc 110 字节十六进制、odc 76 字节八进制）外加
   "名字字段既要是可打印 ASCII、又不能是数字 token 的续写"；ar 的 60 字节成员头（末两字节是
   反引号与 LF）+ 声明数据落在文件内；CAB 的三个保留字段为 0 + 声明大小落在文件内。
3. **bzip2**（`BZh<1-9>1AY&SY`）：10 个**可打印**字节，没有廉价的结构判据（其后是随机 CRC 与压缩流），
   因此只**在偏移 0** 认定——真 `.bz2` 必然以它开头，而源码/文档里出现这串字面量
   （例如 `MAGIC = b"BZh91AY&SY"`）不该被拒。真 `.bz2` 仍然被拒（`archive_attachment`）。
4. **LZH 的签名已删除**（原 `-lh5-` 按偏移 2 匹配）：它可打印且没有廉价的结构判据，保留只会误拒
   合法文本；真 LZH 的压缩体必然含 NUL/控制字节，正向判定已足以拒绝（原因码 `not_plain_text`）。

另：无论签名是否命中，**结构级容器判定**（`zipfile.is_zipfile` + `tarfile` + 校验和扫描）
都会在通过正向判定的文本上再跑一遍（见上面第 4 条）。tar 的校验和扫描是
**逐 512 字节边界 + 校验和字段候选**双路、**无候选上限**——上限正是"用诱饵把真头推到上限之外"的入口。

### 成本上界（实测，本机）

结构判定与输入长度线性：1.5 MB 纯文本约 30–50 ms；最坏情况（1.5 MB 全是可打印诱饵）
约 1.3 s（`070701` 洪水 1.278 s、tar 八进制诱饵 1.121 s）。按生产默认（单文件 1.5 MB、
总预算 4 MB 一个提交）一封邮件最坏约 3–4 s CPU。**不加候选上限是有意的**：上限本身可以被
"诱饵洪水"利用来藏真头。

### 已知误报面（诚实列出，未自行放宽）

严格 UTF-8 判据 + 允许列表会拒绝一些**合法**提交：UTF-16/GBK/latin-1 编码的源码（非法 UTF-8）、
附件名含空格或中文（I2 白名单）、`main.py.` 这类尾点命名（见下）。这些都会进隔离区并带精确原因码，
运维可人工处置。

**`main.py.`（尾点）为什么必须拒绝**（不是"改名的近亲"，是硬事实）：**Windows 文件系统会剥掉路径
末尾的点与空格**，于是"声明的路径"与"磁盘上实际的文件名"会不一致（`main.py.` 落盘成 `main.py`）。
这既会让入口脚本解析/路径比对出现名字不匹配，也可能成为绕过路径判定的手段，因此按
`unsupported_attachment_type` 拒绝（扩展名解析为 `.`，不在允许列表）。

**零宽/排版字符：放行 5 个，拒绝 9 个**。`U+200B/200C/200D/FEFF/00AD`（ZWSP/ZWNJ/ZWJ/BOM/软连字符）
是 Cf 但**无法隐藏可执行内容**，从网页/Word 复制粘贴时极常见，因此放行；而
`U+202A–202E`（LRE/RLE/PDF/LRO/RLO，5 个）与 `U+2066–2069`（LRI/RLI/FSI/PDI，4 个）共 **9 个**
双向控制字符属于 Trojan Source 攻击面（RLO 能把 `os.system("rm -rf /")` 在编辑器里显示成无害样子），
因此**显式拒绝**，并给出独立的原因文案 `bidirectional control character (Trojan Source)`——
放行那 5 个的前提正是这 9 个不被放过。（集合就是 `_BIDI_CONTROL_CHARACTERS`，有测试逐字符钉住。）

把"预置前缀"当成绕过手段是无效的：`# -*- coding: utf-8 -*-\\n` + ZIP 仍然是 ZIP。

ZIP 的声明展开量按 `zipfile` 自己的算法做了前缀补偿（用 EOCD 记录的位置、中央目录长度与偏移反推
`concat`），因此"预置前缀的炸弹"拿到的是 `archive_bomb` 而不是笼统的"归档容器"。

结论：**归档容器一律拒绝**（允许列表里没有"可解包内容"这一类）；`archive_bomb` 与
`archive_attachment` 的区别只在于"是否已由归档自己的声明证明其展开量超限"。

本模块不写库、不打日志、不做 IO：只做纯判定。拒绝以 `AttachmentRejected`（精确原因码 + **有界、
无内容**的明细）抛出——明细里只允许出现**文件名、字节数、形态标签、偏移**这些有界事实，
**绝不**回显攻击者可控的头值，更不含正文或附件原文。

解析期资源受 `max_attachments` 界定：具名部件在**遍历过程中**计数，达到上限即停止物化
（`attachments_overflow`），因此"部件极多"的邮件不会先把整封物化再判断。
"""

from __future__ import annotations

import base64
import binascii
import io
import quopri
import re
import tarfile
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import Message
from email.utils import parseaddr
from typing import Literal

from autograder_b2.config import MAX_FILES
from autograder_b2.domain.clock import sha256_hex
from autograder_b2.domain.envelope import is_allowed_relative_path, redact_path_for_message
from autograder_b2.intake.service import IntakeLimits

#: 允许的附件类型：**纯文本源码**是全部。归档、可执行体、二进制一概不在列表内。
ALLOWED_ATTACHMENT_EXTENSIONS = frozenset({".py", ".sh", ".txt", ".md"})
#: 可执行体扩展名（内容判定之外的第二道：截断/伪装的二进制也让原因码精确）。
EXECUTABLE_EXTENSIONS = frozenset(
    {".exe", ".dll", ".so", ".dylib", ".bin", ".com", ".scr", ".msi", ".bat", ".cmd", ".ps1",
     ".elf", ".o", ".a", ".apk", ".dmg", ".deb", ".rpm"}
)
#: 归档容器扩展名。
ARCHIVE_EXTENSIONS = frozenset(
    {".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".tar", ".jar", ".whl", ".war"}
)

#: `Message-ID` 原样保留（不做任何改写）的长度上限：≤ 它的值可读、可人工核对。
MAX_MESSAGE_ID_LENGTH = 200
#: 超长值的落库形态：前缀**有意比上限更长**，因此"超长形态"与"原样形态"两个集合不相交——
#: 这是单射的必要条件（否则一个精心构造的短值就能等于某个长值的落库形态，两封不同的邮件会撞键）。
MESSAGE_ID_LONG_PREFIX_LENGTH = MAX_MESSAGE_ID_LENGTH + 8
#: 长标识附带的对**完整原值**的摘要长度：截断因此不构成碰撞。
MESSAGE_ID_DIGEST_LENGTH = 16
#: `parse_message` 在未显式给上限时使用的具名部件上限（= 契约的 `files.maxItems`）。
#: 正文摘录上限（与 `b2.yaml` 的 `IntakeMessageView.body_excerpt.maxLength` 一致）。
MAX_BODY_EXCERPT = 4000

DEFAULT_MAX_ATTACHMENTS = MAX_FILES
#: 邮件主题的落库上限（与 `IntakeQuarantineView.subject.maxLength` 一致）。
MAX_SUBJECT_LENGTH = 200
#: base64 的 4 字符一组对应 3 字节；去掉 padding 后余数字符再补 `余数 - 1` 个字节。
_BASE64_CHARACTERS_PER_GROUP = 4
_BASE64_BYTES_PER_GROUP = 3

#: 邮件入口的原因码全集。前 16 项由本模块判定，后 3 项由 `intake.imap`（匹配/解析阶段）判定。
EmailIntakeReason = Literal[
    "malformed_message",
    "missing_message_id",
    "no_attachments",
    "too_many_attachments",
    "attachment_name_not_allowed",
    "duplicate_attachment_name",
    "unsupported_attachment_encoding",
    "malformed_attachment",
    "attachment_too_large",
    "attachment_total_too_large",
    "executable_attachment",
    "archive_bomb",
    "archive_attachment",
    "unsupported_attachment_type",
    "not_plain_text",
    "nested_message_attachment",
    "message_too_large",
    "not_uniquely_matched",
    "ambiguous_match",
]

_BASE64_ENCODINGS = frozenset({"base64", "x-base64"})
_QUOTED_PRINTABLE_ENCODINGS = frozenset({"quoted-printable"})
_PLAIN_ENCODINGS = frozenset({"", "7bit", "8bit", "binary"})
_SUPPORTED_ENCODINGS = _BASE64_ENCODINGS | _QUOTED_PRINTABLE_ENCODINGS | _PLAIN_ENCODINGS
#: 报错文案里**固定**列出支持集（不是回显攻击者的值）。
SUPPORTED_ENCODING_TOKENS = "7bit, 8bit, binary, base64, quoted-printable"

_BASE64_WHITESPACE = re.compile(rb"\s+")
#: 纯文本允许的控制字符。`\x0c`（换页）被 Python 当空白接受（`compile("x = 1\x0c")` 通过），
#: 因此放行；`\x0b`（垂直制表）与 `\x1c`–`\x1f` 被 Python 拒绝，继续拒绝。
_ALLOWED_TEXT_CONTROL = frozenset("\t\n\r\x0c")
#: 允许的**排版类**格式字符（Unicode Cf，零宽/无操作、**没有重排能力**）：ZWSP、ZWNJ、ZWJ、
#: WORD JOINER、BOM/ZWNBSP、软连字符。它们从网页/Word 复制粘贴时极常见，且**无法隐藏可执行内容**；
#: 把它们当"非纯文本"只会让学生白丢一次提交，因此放行（共 6 个）。
_ALLOWED_TEXT_FORMAT = frozenset("\u200b\u200c\u200d\u2060\ufeff\u00ad")
#: **必须拒绝**的双向格式控制字符：这就是 Unicode 双向格式控制字符族的**完整 10 个**——
#: ALM `U+061C`（隐式方向标记）、LRE/RLE/PDF/LRO/RLO `U+202A–U+202E`、LRI/RLI/FSI/PDI `U+2066–U+2069`。
#: 它们是 Trojan Source 的攻击面：RLO 能把 `os.system("rm -rf /")` 在编辑器里显示成看似无害的样子，
#: ALM 同样能让文本按不同方向重排显示。它们同属 Cf，但**不属于**"排版无害"那一类，
#: 因此单独列出并显式拒绝（放行那 6 个排版字符的前提正是这 10 个不被放过）。
_BIDI_CONTROL_CHARACTERS = frozenset(
    "\u061c\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
#: ASCII 快速判定标记：允许的字节 → 0x00，其余 → 0x01（NUL 由 `plain_text_problem` 先判，不重复报）。
#: 表在模块级只算一次；判定走 `bytes.translate` + `find`，因此是 C 速度。
_ASCII_DISALLOWED = 1
_ASCII_TEXT_TABLE = bytes(
    0x00
    if (0x20 <= value < 0x7F or value in (0x00, 0x09, 0x0A, 0x0C, 0x0D))
    else _ASCII_DISALLOWED
    for value in range(256)
)

_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP_CENTRAL_SIGNATURE = b"PK\x01\x02"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP_EOCD_MIN_SIZE = 22
#: 22 字节 EOCD + 最多 65535 字节归档注释。
_ZIP_EOCD_SEARCH_WINDOW = 66_000
_ZIP_CENTRAL_HEADER_SIZE = 46
_MAX_CENTRAL_DIRECTORY_ENTRIES = 100_000
_ZIP64_EXTRA_ID = 0x0001

#: tar 的结构常量：512 字节块、校验和字段、数字字段、PAX 扩展头类型。
_TAR_BLOCK = 512
_TAR_CHECKSUM_FIELD = (148, 156)
_TAR_SIZE_FIELD = (124, 136)
_TAR_SPARSE_REALSIZE_FIELD = (483, 495)
_TAR_MAGIC_OFFSET = 257
_TAR_TYPE_FLAG = 156
_TAR_EXTENDED_TYPES = (b"x", b"g")
_TAR_SPARSE_TYPE = b"S"
#: 单次判定里最多读多少个 tar 成员（把工作量的上界写死在代码里）。
_TAR_MEMBER_LIMIT = 64
#: "校验和字段"的候选形态：6–7 位八进制 + NUL/空格（v7/ustar/GNU 都用这种写法）。
_TAR_CHECKSUM_CANDIDATE = re.compile(rb"[0-7]{6,7}[\x00 ]")
#: 有效头的最小校验和：504 个字节之和 + 8×空格(256)。小于它的声明值直接排除。
_TAR_MIN_CHECKSUM = 0x100
_TAR_FIELD_PADDING = b"\x00 "
_OCTAL_DIGITS = b"01234567"
#: 有符号校验和要减掉的字节（`_signed_sum` 用删除计数代替逐字节循环）。
_HIGH_BYTES = bytes(range(0x80, 0x100))

#: 含"合法纯文本里不可能出现的字节"的**可执行体**签名 ⇒ 任意偏移匹配。
_ANY_OFFSET_EXECUTABLE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x7fELF", "elf"),
    (b"\xca\xfe\xba\xbe", "fat-mach-o-or-java-class"),
    (b"\xbe\xba\xfe\xca", "fat-mach-o-or-java-class"),
    (b"\xfe\xed\xfa\xce", "mach-o"),
    (b"\xce\xfa\xed\xfe", "mach-o"),
    (b"\xfe\xed\xfa\xcf", "mach-o"),
    (b"\xcf\xfa\xed\xfe", "mach-o"),
    (b"\x00asm", "webassembly"),
)
#: 含不可打印字节的**归档/容器**签名 ⇒ 任意偏移匹配。
#: 入组判据是"这些字节**不可能**出现在合法纯文本里"，不是"看起来像二进制"：
#: `arj` 的两字节魔数 `\x60\xea` 已从这里**移除**——0x60 是反引号（可打印），0xea 是
#: U+A000–U+AFFF 的合法 UTF-8 首字节，因此 `"…code point \u0060\uA000…"` 这样的正文会命中它
#: （真实误报，已复现并修掉）。真 ARJ 压缩体必然含 NUL/控制字节，由正向判定拒绝
#: （原因码退化为 `not_plain_text`，拒绝结论不变）。
_ANY_OFFSET_ARCHIVE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),
    (b"PK\x07\x08", "zip"),
    (b"\x1f\x8b", "gzip"),
    (b"\x1f\x9d", "compress"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"\x04\x22\x4d\x18", "lz4"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"Rar!\x1a\x07", "rar"),
)
#: bzip2 的块魔数 `1AY&SY`：与 `BZh<级别>` 一起构成 10 个**可打印**字节，没有廉价的结构判据
#: （其后是随机 CRC 与压缩流）。因此只**在偏移 0** 认定：真 `.bz2` 必然以它开头，而源码/文档里
#: 出现这串字面量（例如 `MAGIC = b"BZh91AY&SY"`）不该被拒。
_BZIP2_FILE_PREFIX = b"BZh"
_BZIP2_BLOCK_MAGIC = b"\x31\x41\x59\x26\x53\x59"
_BZIP2_LEVELS = b"123456789"
#: 可打印签名的**结构校验**常量。这些 magic 全是可打印 ASCII，因此只按偏移 0 或"只认第一处"
#: 判定会同时造成两类错误：误报（普通文本里出现该字面量）与漏判（前缀里放一个诱饵字面量，
#: 让真归档永远不被检查）。因此这一组签名必须：**遍历全部出现位置** + 逐个做结构校验。
#: cpio 的两种**头形状**差别很大，不能互相套用：
#: - newc/crc（`070701`/`070702`）：110 字节头 = 6 magic + 13 个 8 宽**十六进制**字段；
#: - odc（`070707`）：76 字节头 = 6 magic + 7 个 6 宽八进制字段 + 11(mtime) + 6(namesize) + 11(filesize)。
_CPIO_MAGICS: tuple[tuple[bytes, str], ...] = (
    (b"070701", "newc"),
    (b"070702", "crc"),
    (b"070707", "odc"),
)
_CPIO_NEWC_HEADER = 110
_CPIO_NEWC_FIELD_WIDTH = 8
_CPIO_NEWC_NAMESIZE_INDEX = 11
_CPIO_ODC_HEADER = 76
_CPIO_ODC_WIDTHS: tuple[int, ...] = (6, 6, 6, 6, 6, 6, 6, 11, 6, 11)
_CPIO_ODC_NAMESIZE_INDEX = 8
_CPIO_HEX_CHARACTERS = frozenset(b"0123456789abcdefABCDEF")
_CPIO_OCTAL_CHARACTERS = frozenset(b"01234567")
#: ar：`!<arch>\n`（可打印）之后每个成员头 60 字节、以 ``\`\n`` 结尾、大小字段是十进制。
_AR_MAGIC = b"!<arch>\n"
_AR_HEADER = 60
_AR_TERMINATOR = b"`\n"
_AR_DECIMAL_CHARACTERS = frozenset(b"0123456789 ")
_AR_SIZE_FIELD = (48, 58)
#: CAB：头是二进制（三个保留字段必须为 0），因此"可打印的 CAB"不存在——校验保留字段与声明大小
#: 即可既认得出真 CAB、又不误判文本。
_CAB_MAGIC = b"MSCF"
_CAB_HEADER = 36
_CAB_RESERVED_FIELDS = ((4, 8), (12, 16), (20, 24))
_CAB_SIZE_FIELD = (8, 12)
#: tar 的魔数（**含 NUL** 的完整 8 字节形态：POSIX `ustar\0 00` / GNU `ustar  \0`）。
#: 每个出现位置都要过头部校验（`ustar` 字面量也可以被放进前缀当诱饵）。
_TAR_SIGNATURES: tuple[bytes, ...] = (b"ustar\x0000", b"ustar  \x00")


class AttachmentRejected(Exception):
    """附件（或整封邮件）在进入任何业务处理前被拒；`detail` 已脱敏且有界。"""

    def __init__(
        self,
        reason: EmailIntakeReason,
        detail: str,
        *,
        attachment_name: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason: EmailIntakeReason = reason
        self.detail = detail
        self.attachment_name = attachment_name
        self.size_bytes = size_bytes


@dataclass(frozen=True, slots=True)
class RawAttachment:
    """**尚未解码**的附件：保留编码形态，使"先判大小、再解码"在结构上成立。"""

    filename: str
    content_type: str
    transfer_encoding: str
    encoded: bytes
    #: 解码后大小的**上界**（base64 按去掉空白后的长度折算，quoted-printable 取编码长度）。
    estimated_size: int

    def decode(self) -> bytes:
        """真正解码（只在通过大小闸门之后调用）。"""
        encoding = self.transfer_encoding
        if encoding in _BASE64_ENCODINGS:
            payload = _BASE64_WHITESPACE.sub(b"", self.encoded)
            payload += b"=" * (-len(payload) % 4)  # 补齐截断的 padding，不猜测内容
            try:
                return base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError) as error:
                raise AttachmentRejected(
                    "malformed_attachment",
                    "base64 payload is not decodable",
                    attachment_name=redact_path_for_message(self.filename),
                    size_bytes=self.estimated_size,
                ) from error
        if encoding in _QUOTED_PRINTABLE_ENCODINGS:
            return quopri.decodestring(self.encoded)
        if encoding in _PLAIN_ENCODINGS:
            return self.encoded
        raise AttachmentRejected(
            "unsupported_attachment_encoding",
            _unsupported_encoding_detail(self),
            attachment_name=redact_path_for_message(self.filename),
            size_bytes=self.estimated_size,
        )


@dataclass(frozen=True, slots=True)
class ParsedAttachment:
    """已通过安全闸门、可进入提交的附件。"""

    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """从原始 MIME 报文里取出的、进入业务判定所需的字段（**不含正文**）。

    `nested_attachments` 是**具名的嵌套邮件**（`message/rfc822` 之类）的文件名：它们不是
    "没有附件"，因此必须单独记下来并明确拒绝，绝不能静默丢弃。
    `attachments_overflow` 表示具名部件数超过了解析上限（收集在达到上限时即停止）。
    """

    message_id: str | None
    sender: str
    attachments: tuple[RawAttachment, ...] = ()
    nested_attachments: tuple[str, ...] = ()
    attachments_overflow: bool = False
    #: 邮件主题头，已折叠空白、只保留可打印字符、最多 200 字；**不是正文**。
    #: 复核列表要老师"一眼认出这封邮件"，只靠邮箱/UID 认不出来；主题是唯一可用的有界元数据。
    subject: str | None = None
    #: 正文摘录：仅 `text/plain`、折叠空白、脱敏后最多 4000 字（`MAX_BODY_EXCERPT`）。
    #: 它不是"整封信"，`body_truncated` 为真时必须让读者知道这一点。
    body_excerpt: str | None = None
    body_truncated: bool = False


@dataclass(frozen=True, slots=True)
class ContentVerdict:
    """内容定性：**只看字节**，与文件名/声明的类型无关。

    - `executable` / `archive`：命中已知签名或结构判定（`evidence` 区分来源）；
    - `binary`：没有命中容器/可执行体，但**不能**被证明是纯文本（`label` 是有界、无内容的问题描述）；
    - `text`：严格 UTF-8、无 NUL、字符集合规，且**没有**通过结构判定的容器特征。
    """

    kind: Literal["executable", "archive", "binary", "text"]
    label: str
    offset: int = 0
    bomb: bool = False
    declared_size: int | None = None
    evidence: Literal["signature", "structure"] = "signature"


@dataclass(frozen=True, slots=True)
class _TarProbe:
    """tar 结构判定的结果：首个头的偏移与声明展开量（读不到就是 `None`）。"""

    offset: int
    declared_size: int | None


def message_id_for_storage(value: str | None) -> str | None:
    """把邮件头里的 `Message-ID` 转成**可安全落库/记日志、且单射**的存储形态。

    三条性质都必须成立：

    1. **单射（对完整原值）**：`(mailbox, message_id)` 是幂等的第二把钥匙，多对一的归一化会让两个
       **不同**的 `Message-ID` 撞成同一个键，于是第二封邮件被当成重复投递而静默跳过。因此
       控制字符走**转义**（`\\` → `\\\\`、控制字符 → `\\xNN`）而不是删除，内部空格也不再被删掉。
    2. **截断不构成碰撞**：超长值的形态是「比上限更长的前缀 + 对**完整原值**的摘要」，而原样形态
       长度 ≤ 上限——两个集合**不相交**，所以一个精心构造的短值不可能等于某个长值的落库形态，
       两个长值也不会因为前缀相同而撞键。
    3. **可安全打印**：控制字符被转义（而不是留在字符串里），日志不会被劈成两行（日志注入）；
       不可打印字符一律转义还避免了 `surrogateescape` 还原出的代理码位写进 PostgreSQL 的 text 列。

    输入必须是**未经解析的**头值原文（见 `_raw_message_id`），否则在到达这里之前就已经被
    `email` 的 `msg-id` 解析折成一个更短的值了。
    """
    if not value:
        return None
    escaped = _escape_header_text(value)
    if len(escaped) <= MAX_MESSAGE_ID_LENGTH:
        return escaped
    digest = sha256_hex(value.encode("utf-8", "surrogateescape"))[:MESSAGE_ID_DIGEST_LENGTH]
    head = escaped[:MESSAGE_ID_LONG_PREFIX_LENGTH]
    return f"{head}~{digest}"


def _raw_message_id(message: Message) -> str | None:
    """取**未解析**的 `Message-ID` 头值（同一头出现多次时取第一条，结果确定可复现）。

    为什么不直接用 `message.get("Message-ID")`：那个值经过 `email` 的 `msg-id` 解析，会把
    `<a b@example.edu>` 折成 `<a`——两个**不同**的报文于是归一到同一个标识，第二封会被当成
    重复投递而静默跳过。原始头值才是"这封邮件自称的身份"，用它做幂等键才谈得上单射。
    """
    for name, value in message.raw_items():
        if name.lower() == "message-id":
            return value
    return None


def _subject_for_storage(message: Message) -> str | None:
    """把主题头收敛成可落库的有界字符串（**只取主题，不碰正文**）。

    主题同样是可伪造的输入，因此只用于展示：折叠空白、去掉控制字符、截断到 200 字。
    截断不保证唯一，也不需要唯一——它不是幂等键，只是一个帮助老师认出邮件的标签。
    """
    try:
        raw = message.get("Subject")
    except Exception:  # noqa: BLE001 - 畸形头不应让整封邮件变成不可解析
        return None
    if raw is None:
        return None
    text = "".join(character for character in str(raw) if character.isprintable())
    text = " ".join(text.split())
    if not text:
        return None
    return text[:MAX_SUBJECT_LENGTH].rstrip() or None


def parse_message(raw: bytes, *, max_attachments: int = DEFAULT_MAX_ATTACHMENTS) -> EmailMessage:
    """解析原始 MIME 报文：只取 `Message-ID`、发件人、**有界主题头**与**具名**部件，正文原样丢弃。

    `max_attachments` 在**遍历过程中**生效：具名部件数达到上限即停止物化并置
    `attachments_overflow`，因此"部件极多"的邮件不会先把整封物化再判断。
    """
    if not raw.strip():
        raise AttachmentRejected("malformed_message", "empty message body")
    try:
        message: Message = message_from_bytes(raw, policy=policy.default)
    except Exception as error:  # noqa: BLE001 - 解析器对畸形输入可能抛任意异常，这里必须全接住
        # 只带异常**类型名**：异常消息里可能嵌着原始字节（那就是内容）。
        raise AttachmentRejected("malformed_message", f"parser raised {type(error).__name__}") from None

    sender = parseaddr(str(message.get("From") or ""))[1].strip().lower()
    attachments, nested, overflow = _collect_parts(message, max_attachments=max_attachments)
    body_excerpt, body_truncated = _body_excerpt(message)
    return EmailMessage(
        message_id=message_id_for_storage(_raw_message_id(message)),
        sender=sender,
        attachments=attachments,
        nested_attachments=nested,
        attachments_overflow=overflow,
        subject=_subject_for_storage(message),
        body_excerpt=body_excerpt,
        body_truncated=body_truncated,
    )


def screen_attachments(
    message: EmailMessage, *, limits: IntakeLimits
) -> tuple[ParsedAttachment, ...]:
    """安全闸门：嵌套邮件 → 数量 → 名称 → 编码 → 大小 → 解码 → 内容定性 → 扩展名 → 累计大小。

    任一件不通过即整封邮件被拒（不产生"部分归一化"的提交）。
    """
    if message.nested_attachments:
        # 嵌套邮件（message/rfc822）是容器，且**不能**静默丢弃：一件都不丢，直接拒绝并说清是哪一件。
        first = redact_path_for_message(message.nested_attachments[0])
        raise AttachmentRejected(
            "nested_message_attachment",
            f"nested message attachment {first} is not accepted"
            f" ({len(message.nested_attachments)} nested message part(s))",
            attachment_name=first,
        )

    attachments = message.attachments
    if message.attachments_overflow or len(attachments) > limits.max_files:
        detail = (
            f"more than {limits.max_files} attachments; collection stopped at the limit"
            if message.attachments_overflow
            else f"{len(attachments)} attachments, above the {limits.max_files} limit"
        )
        raise AttachmentRejected("too_many_attachments", detail)
    if not attachments:
        raise AttachmentRejected("no_attachments", "the message carries no named attachment")

    accepted: list[ParsedAttachment] = []
    seen: set[str] = set()
    total = 0
    for attachment in attachments:
        shown = redact_path_for_message(attachment.filename)
        if not is_allowed_relative_path(attachment.filename):
            raise AttachmentRejected(
                "attachment_name_not_allowed",
                f"attachment name {shown} is not a permitted relative path",
                attachment_name=shown,
            )
        # 大小写不敏感判重：`main.py` 与 `MAIN.PY` 在大小写不敏感文件系统上会互相覆盖。
        folded = attachment.filename.casefold()
        if folded in seen:
            raise AttachmentRejected(
                "duplicate_attachment_name",
                f"duplicate attachment name {shown} (names are compared case-insensitively)",
                attachment_name=shown,
            )
        seen.add(folded)
        if attachment.transfer_encoding not in _SUPPORTED_ENCODINGS:
            # **不回显**头值：那是攻击者可控文本（可以塞进正文），只报有界事实。
            raise AttachmentRejected(
                "unsupported_attachment_encoding",
                _unsupported_encoding_detail(attachment),
                attachment_name=shown,
            )
        if attachment.estimated_size > limits.max_file_bytes:
            # 解码**之前**就拒：这一步是压缩炸弹的内存边界。
            raise AttachmentRejected(
                "attachment_too_large",
                f"attachment {shown} declares {attachment.estimated_size} bytes,"
                f" above the {limits.max_file_bytes} byte limit",
                attachment_name=shown,
                size_bytes=attachment.estimated_size,
            )

        content = attachment.decode()
        if len(content) > limits.max_file_bytes:
            raise AttachmentRejected(
                "attachment_too_large",
                f"attachment {shown} is {len(content)} bytes,"
                f" above the {limits.max_file_bytes} byte limit",
                attachment_name=shown,
                size_bytes=len(content),
            )

        verdict = sniff_content(content, declared_size_limit=limits.max_total_bytes)
        if verdict.kind == "executable":
            raise AttachmentRejected(
                "executable_attachment",
                f"attachment {shown} contains an executable ({verdict.label})"
                f" at payload offset {verdict.offset}",
                attachment_name=shown,
                size_bytes=len(content),
            )
        if verdict.kind == "archive":
            raise AttachmentRejected(
                "archive_bomb" if verdict.bomb else "archive_attachment",
                _archive_detail(verdict, limits),
                attachment_name=shown,
                size_bytes=len(content),
            )
        if verdict.kind == "binary":
            # 正向判定失败：没有任何容器/可执行体特征，但**证明了**它不是纯文本源码。
            raise AttachmentRejected(
                "not_plain_text",
                f"attachment {shown} is not plain text: {verdict.label}",
                attachment_name=shown,
                size_bytes=len(content),
            )

        extension = _extension_of(attachment.filename)
        if extension in EXECUTABLE_EXTENSIONS:
            raise AttachmentRejected(
                "executable_attachment",
                f"attachment {shown} carries the executable extension {extension}",
                attachment_name=shown,
                size_bytes=len(content),
            )
        if extension in ARCHIVE_EXTENSIONS:
            raise AttachmentRejected(
                "archive_attachment",
                f"attachment {shown} carries the archive extension {extension}",
                attachment_name=shown,
                size_bytes=len(content),
            )
        if extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
            raise AttachmentRejected(
                "unsupported_attachment_type",
                f"attachment {shown} has extension {extension or '<none>'};"
                " only plain text sources are accepted",
                attachment_name=shown,
                size_bytes=len(content),
            )

        total += len(content)
        if total > limits.max_total_bytes:
            raise AttachmentRejected(
                "attachment_total_too_large",
                f"attachments total {total} bytes, above the {limits.max_total_bytes} byte limit",
                attachment_name=shown,
                size_bytes=len(content),
            )
        accepted.append(
            ParsedAttachment(
                filename=attachment.filename,
                content_type=attachment.content_type,
                content=content,
            )
        )
    return tuple(accepted)


def sniff_content(content: bytes, *, declared_size_limit: int) -> ContentVerdict:
    """按**字节**定性内容：可执行体 → 归档容器 → 非纯文本 → 文本（再过结构判定）。

    `declared_size_limit` 是"宣称的展开量"的判定阈值（取整份提交的字节预算）：超过它即判定为
    压缩炸弹。判定读的是归档自己声明的长度，因此不需要、也**绝不**真的解压。

    `MZ`（DOS/PE 的 2 字节签名）是唯一需要"文本判定"配合的签名：偏移 0 处有 `MZ` **且**整体不是
    纯文本 ⇒ 可执行附件（原因码保持 `executable_attachment`，安全事件里看得见）；偏移 0 处有 `MZ`
    但整体**是**合法纯文本 ⇒ 按文本处理（否则一个恰好以 `MZ` 开头的 `.py` 会被误拒）。
    真 PE 看 0x3c 处的 PE 头，命中即 `pe`，与文本判定无关。
    """
    problem = plain_text_problem(content)
    executable = _match_executable(content)
    if executable is not None:
        label, offset = executable
        if label != "mz" or problem is not None:
            return ContentVerdict(kind="executable", label=label, offset=offset)
    archive = _match_archive(content)
    if archive is not None:
        label, offset = archive
        declared = _declared_archive_size(label, content, offset, stop_at=declared_size_limit)
        return ContentVerdict(
            kind="archive",
            label=label,
            offset=offset,
            bomb=declared is not None and declared > declared_size_limit,
            declared_size=declared,
        )
    if problem is not None:
        return ContentVerdict(kind="binary", label=problem)
    # 文本也可能是**可打印的容器**（例如数字字段写成"七位八进制 + 空格"、magic 区填空格、
    # 数据块用空格补齐的 tar，全长无 NUL）。签名表看不出来，只有结构判定能看出来。
    container = probe_container(content, declared_size_limit=declared_size_limit)
    if container is not None:
        return container
    return ContentVerdict(kind="text", label="utf-8 text")


def probe_container(content: bytes, *, declared_size_limit: int) -> ContentVerdict | None:
    """结构级容器判定：用 stdlib 解析器（`zipfile` / `tarfile`）判定"这是不是一个归档容器"。

    只在**有界字节**上做判定：不抽取、不解压（`tarfile` 固定用无压缩模式 `r:`，绝不 `r:*`）。
    签名表穷举不到的可打印容器由这里接住；命中即按容器拒绝。
    """
    zip_verdict = _probe_zip_container(content, declared_size_limit=declared_size_limit)
    if zip_verdict is not None:
        return zip_verdict
    tar_probe = _probe_tar_container(content, declared_size_limit=declared_size_limit)
    if tar_probe is None:
        return None
    declared = tar_probe.declared_size
    return ContentVerdict(
        kind="archive",
        label="tar",
        offset=tar_probe.offset,
        bomb=declared is not None and declared > declared_size_limit,
        declared_size=declared,
        evidence="structure",
    )


def plain_text_problem(content: bytes) -> str | None:
    """正向判定"是不是纯文本源码"；返回**有界、无内容**的问题描述，`None` 表示确实是纯文本。

    判据：严格 UTF-8 解码成功、无 NUL 字节、且每个字符要么可打印、要么属于**空格类分隔符**
    （NBSP、全角空格等——源码里很常见，且它们不是控制字符）、要么是 `\\t\\n\\r\\x0c` 或 BOM。
    其余一律判定为非纯文本（`\\x0b`、`\\x1c`–`\\x1f`、其它格式字符、行/段分隔符、代理码位……）。

    描述只含**类别与偏移**，不含任何原始字节或字节值。纯 ASCII（绝大多数源码）走 `translate` +
    `find` 的 C 速度路径：对每个字节跑一次 Python 循环在上限 1.5 MB × 100 个附件的场景下太慢。
    """
    nul = content.find(b"\x00")
    if nul >= 0:
        return f"NUL byte at byte offset {nul}"
    if content.isascii():
        index = content.translate(_ASCII_TEXT_TABLE).find(_ASCII_DISALLOWED)
        return None if index < 0 else f"control character at character offset {index}"
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        return f"invalid UTF-8 sequence at byte offset {error.start}"
    for index, character in enumerate(text):
        if _is_text_character(character):
            continue
        if character in _BIDI_CONTROL_CHARACTERS:
            # 单独给这一类精确的原因：它是最需要被看见的拒绝（Trojan Source），
            # 不能被埋在"某个 Cf 字符"里。
            return f"bidirectional control character (Trojan Source) at character offset {index}"
        category = unicodedata.category(character)
        if category == "Cc":
            return f"control character at character offset {index}"
        return f"non-printable character (Unicode category {category}) at character offset {index}"
    return None


def _is_text_character(character: str) -> bool:
    """单个字符是否属于"纯文本源码"允许的字符集（判据见 `plain_text_problem`）。

    允许集 = 可打印字符 ∪ 空格类分隔符（Zs）∪ `\\t\\n\\r\\x0c` ∪ 5 个排版类格式字符（Cf）。
    **双向控制字符显式排除**（它们是 Cf，但属 Trojan Source 攻击面，见 `_BIDI_CONTROL_CHARACTERS`）。
    """
    if character in _BIDI_CONTROL_CHARACTERS:
        return False
    if character in _ALLOWED_TEXT_CONTROL or character in _ALLOWED_TEXT_FORMAT:
        return True
    if character.isprintable():
        return True
    return unicodedata.category(character) == "Zs"


def _collect_parts(
    message: Message, *, max_attachments: int
) -> tuple[tuple[RawAttachment, ...], tuple[str, ...], bool]:
    """只收**具名**部件，正文部件（无名字）直接丢弃；具名部件在遍历中计数、超限即停。

    **不过滤 `Content-Disposition`**：处置方式与文件名一样是攻击者可控的元数据，让它决定
    "这个部件要不要过安全闸门"是错的。带文件名的部件一律收进来走闸门；反过来，任何带文件名的
    部件都不会被静默丢掉（否则一封邮件的某个附件可以无声消失，学生被按更少的文件评分）。

    **具名的嵌套邮件**（`message/rfc822` 之类，载荷是子 `Message`）同样计名收下：它既不是正文，
    也不是可以静默忽略的东西——由 `screen_attachments` 明确拒绝。
    """
    collected: list[RawAttachment] = []
    nested: list[str] = []
    overflow = False
    for part in message.walk():
        filename = part.get_filename()
        if part.is_multipart():
            if not filename:
                continue
            if len(collected) + len(nested) >= max_attachments:
                overflow = True
                break
            nested.append(filename)
            continue
        if not filename:
            continue
        if len(collected) >= max_attachments:
            # 达到上限即停：不再物化后续部件（解析期的内存与时间由 max_attachments 界定）。
            overflow = True
            break
        payload = part.get_payload(decode=False)
        if isinstance(payload, list):
            # 极少数畸形形态：载荷是列表却没有被判定为 multipart。按嵌套容器收名，不物化内容。
            nested.append(filename)
            continue
        # `email` 的解析器把报文体按 ascii+surrogateescape 存成 str：这样编码能**逐字节**还原，
        # 因而"附件原文"从来没有被解释过，只是被搬运。
        encoded = (
            payload.encode("ascii", "surrogateescape") if isinstance(payload, str) else bytes(payload)
        )
        encoding = str(part.get("Content-Transfer-Encoding") or "").strip().lower()
        collected.append(
            RawAttachment(
                filename=filename,
                content_type=(part.get_content_type() or "").lower(),
                transfer_encoding=encoding,
                encoded=encoded,
                estimated_size=_estimated_decoded_size(encoding, encoded),
            )
        )
    return tuple(collected), tuple(nested), overflow


def _estimated_decoded_size(encoding: str, encoded: bytes) -> int:
    """解码后大小的**精确**值（不是上界）。base64 按长度与 padding 折算，其余编码原样。

    为什么必须精确：上限判定用 `>` 比较，而 `(len // 4) * 3` 对 `n ≡ 1 (mod 3)` 会高估 2 个字节
    ——**恰好等于上限**的合法文件会被当成"超限 2 字节"拒掉（真实误报，已复现）。
    公式：去掉 padding 后 `每 4 个字符 3 字节`，余数字符再补 `余数 - 1` 个字节。
    """
    if encoding in _BASE64_ENCODINGS:
        stripped = _BASE64_WHITESPACE.sub(b"", encoded).rstrip(b"=")
        groups, remainder = divmod(len(stripped), _BASE64_CHARACTERS_PER_GROUP)
        return groups * _BASE64_BYTES_PER_GROUP + max(remainder - 1, 0)
    return len(encoded)


def _escape_header_text(value: str) -> str:
    """把邮件头文本转成**单射**的可打印形式：`\\` → `\\\\`，不可打印字符 → `\\xNN` / `\\uXXXX`。

    "删掉控制字符"是多对一的（两个不同的头值会归一到同一个键），因此这里选择转义而不是删除。
    """
    escaped: list[str] = []
    for character in value:
        if character == "\\":
            escaped.append("\\\\")
        elif character.isprintable():
            escaped.append(character)
        elif ord(character) <= 0xFF:
            escaped.append(f"\\x{ord(character):02x}")
        else:
            escaped.append(f"\\u{ord(character):04x}")
    return "".join(escaped)


def _unsupported_encoding_detail(attachment: RawAttachment) -> str:
    """不支持的传输编码：只报**有界事实**（字段名、值长度、期望的支持集），不回显头值。"""
    return (
        "Content-Transfer-Encoding is not supported"
        f" (value length {len(attachment.transfer_encoding)};"
        f" expected one of {SUPPORTED_ENCODING_TOKENS})"
    )


def _extension_of(filename: str) -> str:
    _, dot, extension = filename.rpartition(".")
    return f".{extension.lower()}" if dot else ""


def _archive_detail(verdict: ContentVerdict, limits: IntakeLimits) -> str:
    """归档拒绝的脱敏明细：只说得出证据的那一句话（不确定就不说）。"""
    how = "structure" if verdict.evidence == "structure" else "magic bytes"
    where = "" if verdict.offset == 0 else f" at payload offset {verdict.offset}"
    if verdict.declared_size is None:
        return f"{verdict.label} container detected by {how}{where}"
    if verdict.bomb:
        return (
            f"{verdict.label} container detected by {how}{where} declares"
            f" {verdict.declared_size} uncompressed bytes,"
            f" above the {limits.max_total_bytes} byte budget"
        )
    return (
        f"{verdict.label} container detected by {how}{where}"
        f" (declares {verdict.declared_size} uncompressed bytes)"
    )


def _match_executable(content: bytes) -> tuple[str, int] | None:
    """可执行体识别：PE/DOS `MZ` 看偏移 0，其余签名含不可打印字节、因此可在任意偏移命中。

    `MZ` 的判定分两层（见 `sniff_content`）：0x3c 处的 PE 头偏移有效 ⇒ 真 PE；
    否则记录为 `mz`，**由文本判定决定去留**——整体不是纯文本才是可执行附件，
    整体是合法纯文本则按文本处理（`MZ` 只有两个可打印字节，一个以它开头的 `.py` 不该被误拒，
    但真 DOS 可执行体必然含 NUL/控制字节，因此仍然会以 `executable_attachment` 被拒并记安全事件）。

    刻意**不**枚举 `.pyc` 魔数：那里的前两字节随版本变化，而"第 2–4 字节是 `\\r\\r\\n`"这个判据
    对合法文本也可能成立（误拒风险），而真字节码的 flags/长度字段必然含 NUL，正向判定已经接住。
    """
    if content.startswith(b"MZ"):
        if len(content) >= 0x40:
            offset = int.from_bytes(content[0x3C:0x40], "little")
            if 0 < offset <= len(content) - 4 and content[offset : offset + 4] == b"PE\x00\x00":
                return "pe", 0
        return "mz", 0
    for signature, label in _ANY_OFFSET_EXECUTABLE_SIGNATURES:
        offset = content.find(signature)
        if offset >= 0:
            return label, offset
    return None


def _match_archive(content: bytes) -> tuple[str, int] | None:
    """归档容器识别（只看魔数/结构，绝不尝试解压）。

    分两类，判据不同（这是本轮修掉 HIGH 漏判的核心）：

    1. **含"合法文本里不可能出现的字节"的签名**：第一处命中即定论——不需要校验，也就不存在
       "这个候选不合法、要往后找下一个"的情形，因此不可能因为"只认第一处"而漏判；
    2. **可打印的签名（tar / cpio / ar / CAB）**：必须**遍历全部出现位置**并逐个结构校验，直到
       某个位置合法为止。只认第一处会造成两类错误：前缀里放一个诱饵字面量就能让真归档永远不被
       检查（漏判，第三轮复核的 HIGH），而一个恰好含该字面量的普通文本会被误判成容器（误报）。
    """
    for signature, label in _ANY_OFFSET_ARCHIVE_SIGNATURES:
        offset = content.find(signature)
        if offset >= 0:
            return label, offset
    bzip2 = _match_bzip2(content)
    if bzip2 is not None:
        return bzip2
    for signature in _TAR_SIGNATURES:
        for offset in _occurrences(content, signature):
            start = offset - _TAR_MAGIC_OFFSET
            if start >= 0 and _tar_header_is_valid(content[start : start + _TAR_BLOCK]):
                return "tar", start
    for matcher in (_match_cpio, _match_ar, _match_cab):
        found = matcher(content)
        if found is not None:
            return found
    return None


def _occurrences(content: bytes, signature: bytes) -> Iterator[int]:
    """按出现顺序产出 signature 的**所有**位置（而不是只认第一处）。"""
    start = content.find(signature)
    while start >= 0:
        yield start
        start = content.find(signature, start + 1)


def _match_bzip2(content: bytes) -> tuple[str, int] | None:
    """bzip2：只认**偏移 0** 的 `BZh<级别>1AY&SY`（见 `_BZIP2_FILE_PREFIX` 的说明）。

    这 10 个字节全是可打印 ASCII，放在"任意偏移即定论"组里会把 `MAGIC = b"BZh91AY&SY"` 这样的
    源码判成容器（真实误报，已复现）；放到偏移 0 只保留"文件确实以 bzip2 魔数开头"这一条强信号。
    """
    if content[:3] != _BZIP2_FILE_PREFIX or content[3:4] not in _BZIP2_LEVELS:
        return None
    if content[4:10] != _BZIP2_BLOCK_MAGIC:
        return None
    return "bzip2", 0


def _match_cpio(content: bytes) -> tuple[str, int] | None:
    """cpio：遍历每个 magic 的**所有**出现位置，按该变体**自己的**头形状校验。

    newc/crc 与 odc 的头形状完全不同（110 字节十六进制 vs 76 字节八进制），拿 newc 的形状去套
    odc 会**永远命中不了**——这种"形状不匹配"比漏掉一个签名字节更隐蔽。
    """
    for magic, variant in _CPIO_MAGICS:
        for offset in _occurrences(content, magic):
            if _cpio_header_is_valid(content, offset, magic, variant):
                return "cpio", offset
    return None


def _cpio_header_is_valid(content: bytes, offset: int, magic: bytes, variant: str) -> bool:
    """按变体校验 cpio 头：定宽字段的字符集 + 名字必须落在文件内。"""
    if variant == "odc":
        header = content[offset : offset + _CPIO_ODC_HEADER]
        if len(header) < _CPIO_ODC_HEADER:
            return False
        cursor = len(magic)
        fields: list[bytes] = []
        for width in _CPIO_ODC_WIDTHS:
            field = header[cursor : cursor + width]
            if len(field) < width or not set(field) <= _CPIO_OCTAL_CHARACTERS:
                return False
            fields.append(field)
            cursor += width
        namesize = int(fields[_CPIO_ODC_NAMESIZE_INDEX], 8)
        header_size = _CPIO_ODC_HEADER
    else:
        header = content[offset : offset + _CPIO_NEWC_HEADER]
        if len(header) < _CPIO_NEWC_HEADER:
            return False
        body = header[len(magic) :]
        if len(body) % _CPIO_NEWC_FIELD_WIDTH:
            return False
        fields = [
            body[index : index + _CPIO_NEWC_FIELD_WIDTH]
            for index in range(0, len(body), _CPIO_NEWC_FIELD_WIDTH)
        ]
        if not all(set(field) <= _CPIO_HEX_CHARACTERS for field in fields):
            return False
        namesize = int(fields[_CPIO_NEWC_NAMESIZE_INDEX], 16)
        header_size = _CPIO_NEWC_HEADER
    # 名字字段是这条判定的关键：**必须落在文件内、必须是可打印 ASCII、且不能是数字 token 的续写**。
    # 最后一条挡掉"长数字块恰好满足定宽字段布局"的误报（例如 `070707` + 一串八进制数字，
    # 其"名字"位置正好是更多数字）。真 cpio 的名字是文件名（`a.py`、`TRAILER!!!`），
    # 允许结尾的 NUL（odc/newc 的惯例）。
    if offset + header_size + namesize > len(content):
        return False
    name = content[offset + header_size : offset + header_size + namesize]
    stripped = name[:-1] if name.endswith(b"\x00") else name
    if not stripped:
        return False
    if not all(0x20 <= byte < 0x7F for byte in stripped):
        return False
    alphabet = _CPIO_OCTAL_CHARACTERS if variant == "odc" else _CPIO_HEX_CHARACTERS
    return not all(byte in alphabet for byte in stripped)


def _match_ar(content: bytes) -> tuple[str, int] | None:
    """ar/deb：遍历 `!<arch>\\n` 的**所有**出现位置，直到某个位置的成员头合法。

    只取第一处会让"前缀里先出现一个 `!<arch>\\n` 字面量、真归档在后"直接绕过闸门——第三轮复核的
    HIGH 漏判就是这条（真实 `ar.exe` 产物 + 真实 PostgreSQL/CAS 端到端复现）。
    """
    for offset in _occurrences(content, _AR_MAGIC):
        if _ar_header_is_valid(content, offset):
            return "ar", offset
    return None


def _ar_header_is_valid(content: bytes, offset: int) -> bool:
    """成员头 60 字节：以 ``\\`\\n`` 结尾、大小字段是十进制、且声明的数据落在文件内。"""
    cursor = offset + len(_AR_MAGIC)
    header = content[cursor : cursor + _AR_HEADER]
    if len(header) < _AR_HEADER or header[58:60] != _AR_TERMINATOR:
        return False
    size_field = header[_AR_SIZE_FIELD[0] : _AR_SIZE_FIELD[1]]
    if not size_field.strip() or not set(size_field) <= _AR_DECIMAL_CHARACTERS:
        return False
    # 成员数据（奇数长度还要补一个 `\n`）必须落在文件内：这条把"看起来像 ar 的文本"挡掉。
    size = int(size_field)
    return cursor + _AR_HEADER + size + size % 2 <= len(content)


def _match_cab(content: bytes) -> tuple[str, int] | None:
    """CAB：遍历 `MSCF` 的所有出现位置，校验三个保留字段为 0 且声明大小落在文件内。"""
    for offset in _occurrences(content, _CAB_MAGIC):
        header = content[offset : offset + _CAB_HEADER]
        if len(header) < _CAB_HEADER:
            continue
        if any(
            header[start:end] != b"\x00" * (end - start) for start, end in _CAB_RESERVED_FIELDS
        ):
            continue
        declared = int.from_bytes(header[_CAB_SIZE_FIELD[0] : _CAB_SIZE_FIELD[1]], "little")
        if declared < _CAB_HEADER or offset + declared > len(content):
            continue
        return "cab", offset
    return None


def _declared_archive_size(label: str, content: bytes, offset: int, *, stop_at: int) -> int | None:
    """归档**自己声明**的未压缩总字节数；结构不自洽时返回 `None`（按普通归档处理）。"""
    if label == "zip":
        return _zip_declared_uncompressed_size(content, stop_at=stop_at)
    if label == "gzip" and len(content) >= 18:
        # gzip 的 ISIZE 是最后 4 字节（对 2^32 取模）。取模只会让声明值**偏小**，
        # 因此"大于阈值"这个结论永远成立，不会把炸弹误判成普通归档。
        return int.from_bytes(content[-4:], "little")
    if label == "tar":
        # 真实 tar 的声明量同样可读（PAX/GNU sparse 的巨额声明就在这里暴露）。
        return _tar_declared_size(content, max(offset, 0), stop_at=stop_at)
    return None


# --- 结构级容器判定 -----------------------------------------------------------


def _probe_zip_container(content: bytes, *, declared_size_limit: int) -> ContentVerdict | None:
    """`zipfile` 的结构判定（含自解压/预置前缀形态：`is_zipfile` 自己会找 EOCD）。"""
    try:
        is_zip = zipfile.is_zipfile(io.BytesIO(content))
    except (OSError, ValueError):
        return None
    if not is_zip:
        return None
    declared = _zip_declared_uncompressed_size(content, stop_at=declared_size_limit)
    eocd = content.rfind(
        _ZIP_EOCD_SIGNATURE, max(0, len(content) - _ZIP_EOCD_SEARCH_WINDOW)
    )
    return ContentVerdict(
        kind="archive",
        label="zip",
        offset=max(eocd, 0),
        bomb=declared is not None and declared > declared_size_limit,
        declared_size=declared,
        evidence="structure",
    )


def _probe_tar_container(content: bytes, *, declared_size_limit: int) -> _TarProbe | None:
    """tar 的结构判定：stdlib 解析器（偏移 0）+ 头部校验和扫描（任意偏移，覆盖预置前缀）。

    校验和扫描**不设任意的候选上限**：工作量与输入大小成线性（每个候选只做 512 字节的求和，
    候选间距 ≥8 字节 ⇒ ≤64 × len 字节比较），而输入本身已被 `max_file_bytes` 界定；
    设上限反而会给"用大量八进制诱饵把真头推到上限之外"留出空间。
    """
    probe = _probe_tar_with_stdlib(content, declared_size_limit=declared_size_limit)
    if probe is not None:
        return probe
    for offset in _tar_header_offsets(content):
        if _tar_header_is_valid(content[offset : offset + _TAR_BLOCK]):
            return _TarProbe(
                offset=offset,
                declared_size=_tar_declared_size(content, offset, stop_at=declared_size_limit),
            )
    return None


def _probe_tar_with_stdlib(content: bytes, *, declared_size_limit: int) -> _TarProbe | None:
    """用 `tarfile` 打开（**无压缩**模式 `r:`，绝不 `r:*`）：能打开即为容器。

    读成员头只是为了拿"声明的展开量"（PAX/GNU sparse 的真实大小就在 `size` 与 sparse 表里），
    因此成员数有上限、累计量一超阈值即停；读取过程中的任何解析错误都只终止统计，不影响
    "它是容器"这个结论（方向是 fail-closed）。
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
            total = 0
            for _ in range(_TAR_MEMBER_LIMIT):
                try:
                    member = archive.next()
                except (tarfile.TarError, OSError, ValueError, EOFError):
                    break
                if member is None:
                    break
                total += _tar_member_declared_size(member)
                if total > declared_size_limit:
                    break
    except (tarfile.TarError, OSError, ValueError, EOFError):
        return None
    return _TarProbe(offset=0, declared_size=total if total else None)


def _tar_member_declared_size(member: tarfile.TarInfo) -> int:
    """一个成员**声明的**展开量：常规成员是 `size`，sparse 成员取 sparse 表的最大者。"""
    sizes = [int(member.size or 0)]
    sparse = getattr(member, "sparse", None)
    if sparse:
        sizes.extend(int(size) for _offset, size in sparse)
    return max(sizes)


def _tar_header_offsets(content: bytes) -> Iterator[int]:
    """候选 tar 头偏移：**逐 512 字节边界**（对齐前缀）+ **校验和字段候选**（任意前缀，无上限）。"""
    for offset in range(0, max(len(content) - _TAR_BLOCK + 1, 0), _TAR_BLOCK):
        yield offset
    for match in _TAR_CHECKSUM_CANDIDATE.finditer(content):
        offset = match.start() - _TAR_CHECKSUM_FIELD[0]
        if offset < 0 or offset + _TAR_BLOCK > len(content) or offset % _TAR_BLOCK == 0:
            continue
        yield offset


def _tar_header_is_valid(header: bytes) -> bool:
    """tar 头的结构校验：非全零块 + 校验和字段可解析 + 校验和相符（有符号/无符号都认）。

    这正是 `tarfile.TarInfo.frombuf` 的判据，因此"结构有效的头"与"`tarfile` 认它是 tar"一致；
    区别只在于这里可以**从任意偏移**开始看，预置前缀不影响。

    实现上只做**定长字节运算**（`sum` 与 `translate` 都是 C 速度）：这条判定会被每个候选偏移调用，
    逐字节的 Python 循环会让"诱饵洪水"变成实打实的 CPU 消耗（实测 187k 候选从 15.4 s 降到 0.6 s）。
    """
    if len(header) < _TAR_BLOCK or header == b"\x00" * _TAR_BLOCK:
        return False
    declared = _parse_tar_number(header[_TAR_CHECKSUM_FIELD[0] : _TAR_CHECKSUM_FIELD[1]])
    if declared is None or declared < _TAR_MIN_CHECKSUM:
        # 校验和 = 504 个字节之和 + 8×空格(=256)，因此小于 256 的声明值不可能是有效头。
        # 这一条把"用 `0000000 ` 铺满文件"的诱饵洪水变成一次整数比较（实测把最坏情况砍掉一半）。
        return False
    head = header[: _TAR_CHECKSUM_FIELD[0]]
    tail = header[_TAR_CHECKSUM_FIELD[1] :]
    spacer = (_TAR_CHECKSUM_FIELD[1] - _TAR_CHECKSUM_FIELD[0]) * 0x20
    if declared == sum(head) + spacer + sum(tail):
        return True
    return declared == _signed_sum(head) + spacer + _signed_sum(tail)


def _signed_sum(block: bytes) -> int:
    """按**有符号**字节求和（GNU tar 的另一种校验和口径），用 C 速度的删除计数实现。"""
    if not block:
        return 0
    high = len(block) - len(block.translate(None, _HIGH_BYTES))
    return sum(block) - 0x100 * high


def _tar_declared_size(content: bytes, offset: int, *, stop_at: int) -> int | None:
    """从某个头开始按块走一遍，累计成员声明的展开量（含 PAX 记录里的 size / sparse 大小）。

    有界：最多读 `_TAR_MEMBER_LIMIT` 个成员，累计量一超阈值立即返回。
    """
    cursor = offset
    total = 0
    seen = False
    for _ in range(_TAR_MEMBER_LIMIT):
        header = content[cursor : cursor + _TAR_BLOCK]
        if not _tar_header_is_valid(header):
            break
        seen = True
        declared = _parse_tar_number(header[_TAR_SIZE_FIELD[0] : _TAR_SIZE_FIELD[1]])
        type_flag = header[_TAR_TYPE_FLAG : _TAR_TYPE_FLAG + 1]
        if type_flag in _TAR_EXTENDED_TYPES and declared:
            body = content[cursor + _TAR_BLOCK : cursor + _TAR_BLOCK + declared]
            total = max(total, _pax_declared_size(body))
        elif type_flag == _TAR_SPARSE_TYPE:
            real = _parse_tar_number(
                header[_TAR_SPARSE_REALSIZE_FIELD[0] : _TAR_SPARSE_REALSIZE_FIELD[1]]
            )
            total = max(total, real if real is not None else (declared or 0))
        elif declared is not None:
            total += declared
        if total > stop_at:
            return total
        step = declared or 0
        cursor += _TAR_BLOCK + ((step + _TAR_BLOCK - 1) // _TAR_BLOCK) * _TAR_BLOCK
    return total if seen else None


def _pax_declared_size(body: bytes) -> int:
    """PAX 扩展头正文里的 `size` / `GNU.sparse.size` / `GNU.sparse.realsize`（取最大值）。"""
    total = 0
    for line in body.split(b"\n"):
        if b"=" not in line:
            continue
        entry = line.split(b" ", 1)[-1]
        key, _, value = entry.partition(b"=")
        if key.strip() not in {b"size", b"GNU.sparse.size", b"GNU.sparse.realsize"}:
            continue
        try:
            total = max(total, int(value.strip()))
        except ValueError:
            continue
    return total


def _parse_tar_number(field: bytes) -> int | None:
    """读 tar 的数值字段：八进制（NUL/空格终止）或 GNU base-256（最高位置 1）。

    只用 `split` / `strip` / `translate` 这类 C 速度操作：这是每个候选偏移都会走的热路径。
    """
    if not field:
        return None
    if field[0] & 0x80:
        value = int.from_bytes(field, "big")
        return value & ((1 << (len(field) * 8 - 1)) - 1)
    text = field.split(b"\x00", 1)[0].strip(_TAR_FIELD_PADDING)
    if not text or text.translate(None, _OCTAL_DIGITS):
        return None
    return int(text, 8)


# --- ZIP 的声明展开量 ---------------------------------------------------------


def _zip_declared_uncompressed_size(content: bytes, *, stop_at: int) -> int | None:
    """读 ZIP 中央目录，累加各条目声明的未压缩大小（不解压、不落盘）。

    中央目录的真实位置按 `zipfile` 自己的算法做前缀补偿：预置了任意前缀的归档（例如
    `# -*- coding: utf-8 -*-\\n` + ZIP）依然是可打开的 ZIP，其 EOCD 里记录的偏移是相对归档自身
    起点的，需要用 `concat` 校正，否则"预置前缀的炸弹"只能被笼统地当成普通归档。

    累计值一旦超过 `stop_at` 立即返回：扫描量因此有界，"小文件 + 巨额声明"当场暴露。
    """
    window_start = max(0, len(content) - _ZIP_EOCD_SEARCH_WINDOW)
    eocd = content.rfind(_ZIP_EOCD_SIGNATURE, window_start)
    if eocd < 0 or eocd + _ZIP_EOCD_MIN_SIZE > len(content):
        return None
    entries = int.from_bytes(content[eocd + 10 : eocd + 12], "little")
    central_size = int.from_bytes(content[eocd + 12 : eocd + 16], "little")
    central_offset = int.from_bytes(content[eocd + 16 : eocd + 20], "little")
    if entries == 0xFFFF or central_offset == 0xFFFFFFFF:
        located = _zip64_central_directory(content, eocd)
        if located is None:
            return None
        entries, central_size, central_offset = located
    start = _zip_central_directory_start(content, eocd, central_offset, central_size)
    if start is None:
        return None

    cursor = start
    total = 0
    for _ in range(min(entries, _MAX_CENTRAL_DIRECTORY_ENTRIES)):
        header = content[cursor : cursor + _ZIP_CENTRAL_HEADER_SIZE]
        if len(header) < _ZIP_CENTRAL_HEADER_SIZE or not header.startswith(_ZIP_CENTRAL_SIGNATURE):
            return total
        uncompressed = int.from_bytes(header[24:28], "little")
        name_length = int.from_bytes(header[28:30], "little")
        extra_length = int.from_bytes(header[30:32], "little")
        comment_length = int.from_bytes(header[32:34], "little")
        if uncompressed == 0xFFFFFFFF:
            extra_start = cursor + _ZIP_CENTRAL_HEADER_SIZE + name_length
            zip64_size = _zip64_uncompressed_size(
                content[extra_start : extra_start + extra_length], extra_length
            )
            if zip64_size is None:
                return None
            uncompressed = zip64_size
        total += uncompressed
        if total > stop_at:
            return total
        cursor += _ZIP_CENTRAL_HEADER_SIZE + name_length + extra_length + comment_length
    return total


def _zip_central_directory_start(
    content: bytes, eocd: int, central_offset: int, central_size: int
) -> int | None:
    """定位真实的中央目录起点：先按记录值，再按 `zipfile` 的前缀补偿，最后回退到签名搜索。"""
    if _at_central_directory(content, central_offset):
        return central_offset
    adjusted = central_offset + (eocd - central_size - central_offset)
    if _at_central_directory(content, adjusted):
        return adjusted
    fallback = content.find(_ZIP_CENTRAL_SIGNATURE)
    if fallback >= 0:
        return fallback
    return None


def _at_central_directory(content: bytes, offset: int) -> bool:
    if offset < 0 or offset + 4 > len(content):
        return False
    return content[offset : offset + 4] == _ZIP_CENTRAL_SIGNATURE


def _zip64_central_directory(content: bytes, eocd: int) -> tuple[int, int, int] | None:
    """ZIP64 情形下从 locator + 记录里取（条目数, 中央目录长度, 中央目录偏移）。"""
    locator = content.rfind(_ZIP64_LOCATOR_SIGNATURE, max(0, eocd - 64), eocd)
    if locator < 0 or locator + 20 > len(content):
        return None
    record = int.from_bytes(content[locator + 8 : locator + 16], "little")
    if record + 56 > len(content) or content[record : record + 4] != _ZIP64_EOCD_SIGNATURE:
        return None
    entries = int.from_bytes(content[record + 32 : record + 40], "little")
    central_size = int.from_bytes(content[record + 40 : record + 48], "little")
    central_offset = int.from_bytes(content[record + 48 : record + 56], "little")
    return entries, central_size, central_offset


def _zip64_uncompressed_size(extra: bytes, extra_length: int) -> int | None:
    """在中央目录条目的 extra field 里找 0x0001（ZIP64）并取未压缩大小。"""
    window = extra[:extra_length]
    position = 0
    while position + 4 <= len(window):
        header_id = int.from_bytes(window[position : position + 2], "little")
        size = int.from_bytes(window[position + 2 : position + 4], "little")
        body = window[position + 4 : position + 4 + size]
        if header_id == _ZIP64_EXTRA_ID and len(body) >= 8:
            return int.from_bytes(body[0:8], "little")
        position += 4 + size
    return None


_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")


def _redact(text: str) -> str:
    """去掉邮箱与手机号：老师需要的是内容，不是学生的联系方式副本。"""
    return _PHONE_PATTERN.sub("[手机号]", _EMAIL_PATTERN.sub("[邮箱]", text))


def _body_excerpt(message: Message) -> tuple[str | None, bool]:
    """取 `text/plain` 正文的有界摘录。

    只取**没有文件名**的 `text/plain` 部件：带文件名的是附件（正文位置不该混入附件内容），
    `text/html` 也不取——把标签剥掉得到的"正文"既不可靠又容易被构造。
    """
    collected: list[str] = []
    for part in message.walk():
        if part.get_content_type() != "text/plain":
            continue
        if part.get_filename():
            continue
        if part.is_multipart():
            continue
        # 编码损坏的正文不应让整封邮件不可处理；也不记日志——日志是白名单事件，
        # 异常消息里可能夹带原文片段。
        payload: object | None = None
        try:
            payload = part.get_content()
        except Exception:  # noqa: BLE001 - 见上
            payload = None
        if isinstance(payload, str) and payload.strip():
            collected.append(payload)
    if not collected:
        return None, False

    # 引用历史（"On ... wrote:"之后）保留：老师判断学生意图时它常常就是上下文。
    collapsed = " ".join(" ".join(collected).split())
    if not collapsed:
        return None, False
    redacted = _redact(collapsed)
    if len(redacted) <= MAX_BODY_EXCERPT:
        return redacted, False
    return redacted[:MAX_BODY_EXCERPT], True
