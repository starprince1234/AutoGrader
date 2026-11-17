"""Unix 双轨题库（20 道 bash stdin/stdout 题）：题干 + 两种参考答案 + 1 公开 + 10 隐藏用例。

原 Unix 作业的邮件、ssh、vi、权限类题目按交接文档 §4 一律不出（沙箱无网络、非 root、只读根），
因此这里保留原作业真正训练过的能力：文本过滤与统计（grep/sed/awk/cut/sort/uniq/wc/tr）、
管道组合、算术与格式化输出。参考答案在本地 `F:\\Linux命令环境\\bin\\bash.exe` 实跑，两条解法
必须逐字节一致，才把期望输出写进用例。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

BASE = os.environ.get("AUTOGRADER_API", "https://api.91idc.me")
ADMIN_PASSWORD = os.environ.get("AUTOGRADER_ADMIN_PASSWORD", "")
COURSE_TITLE = "Unix操作系统作业"
BASH = os.environ.get("AUTOGRADER_BASH", r"F:\Linux命令环境\bin\bash.exe")

HEADERS = {
    "content-type": "application/json",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "accept": "application/json",
    "origin": "https://www.91idc.me",
    "referer": "https://www.91idc.me/",
}


def grading_prompt(case_percent: int, ai_percent: int, focus: str) -> str:
    return f"""权重：用例 {case_percent}% + AI {ai_percent}%

评分细则（总分 100，由两条轨按上面的权重合成）：
1. 用例轨：脚本在 10 个隐藏用例上的通过率；输出多一个空格、少一个换行、带提示文字都按未通过计。
2. AI 轨：从命令选择、管道组合、边界处理（空输入、单行、末尾无换行）三方面给 0~100 分。
   - 本题重点：{focus}
扣分项：把某个输入的答案写死、用 `echo` 直接打印结果、依赖交互式命令，AI 轨最高 40 分。"""


@dataclass
class Question:
    key: str
    title: str
    tags: list[str]
    stem: str
    solutions: list[tuple[str, str]]
    inputs: list[str]
    case_percent: int = 60
    ai_percent: int = 40
    focus: str = "用管道把输入处理成规定格式"


UNIX_QUESTIONS: list[Question] = [
    Question(
        key="ux-line-count",
        title="统计输入的行数",
        tags=["unix", "linux-basic", "text-stats"],
        stem="""# 统计输入的行数

从标准输入读到文件结束，输出总行数（包括空行），只输出数字。

- 输入：
  ```
  a
  b
  c
  ```
- 输出：`3`""",
        solutions=[
            ("wc -l", "wc -l | tr -d ' '\n"),
            ("awk 计数", "awk 'END { print NR }'\n"),
        ],
        inputs=[
            "a\nb\nc\n",
            "a\n",
            "",
            "1\n2\n3\n4\n5\n",
            "\n\n\n",
            "x\ny\n",
            "one line\n",
            "a\n\nb\n",
            "aa\nbb\ncc\ndd\n",
            "z\n",
            "p\nq\nr\ns\nt\nu\n",
        ],
    ),
    Question(
        key="ux-numbered-lines",
        title="给每行加行号",
        tags=["unix", "linux-basic", "text-format"],
        stem="""# 给每行加行号

从标准输入读入若干行，按 `行号<TAB>内容` 输出，行号从 1 开始。

- 输入：
  ```
  alpha
  beta
  ```
- 输出（TAB 分隔）：
  ```
  1	alpha
  2	beta
  ```""",
        solutions=[
            ("awk printf", "awk '{ printf \"%d\\t%s\\n\", NR, $0 }'\n"),
            ("awk 拼接", "awk '{ print NR \"\\t\" $0 }'\n"),
        ],
        inputs=[
            "alpha\nbeta\n",
            "a\n",
            "",
            "x\ny\nz\n",
            "\n\n",
            "hello world\nfoo bar\n",
            "1\n2\n3\n",
            "last\n",
            "m\nn\no\np\n",
            "single\n",
            "aa\nbb\ncc\ndd\nee\n",
        ],
    ),
    Question(
        key="ux-uppercase",
        title="把输入转成大写",
        tags=["unix", "linux-basic", "text-transform"],
        stem="""# 把输入转成大写

从标准输入读入文本，把所有小写字母转成大写后原样输出（每行保持）。

- 输入：`hello World`
- 输出：`HELLO WORLD`""",
        solutions=[
            ("tr", "tr '[:lower:]' '[:upper:]'\n"),
            ("awk toupper", "awk '{ print toupper($0) }'\n"),
        ],
        inputs=["hello World\n", "abc\n", "already UPPER\n", "mixed 123 Case\n", "a\n", "x y z\n", "", "中文abc\n", "q\n", "one\ntwo\n", "tab\there\n"],
    ),
    Question(
        key="ux-drop-blank",
        title="删除空行",
        tags=["unix", "linux-basic", "text-filter"],
        stem="""# 删除空行

从标准输入读入文本，去掉所有空行（只含换行的行），其余行原样输出。

- 输入：
  ```
  a

  b
  ```
- 输出：
  ```
  a
  b
  ```""",
        solutions=[
            ("grep 非空行", "grep -v '^$' || true\n"),
            ("awk length", "awk 'length($0) > 0'\n"),
        ],
        inputs=["a\n\nb\n", "a\nb\n", "", "\n\n\nx\n", "one\n\n\ntwo\n", "  \nkeep\n", "a\n", "x\n\ny\n\nz\n", "1\n\n2\n", "\n", "p\nq\n\nr\n"],
    ),
    Question(
        key="ux-sort-unique",
        title="排序并去重",
        tags=["unix", "linux-basic", "text-sort"],
        stem="""# 排序并去重

读入若干行（每行一个单词），按字典序升序输出去重后的结果，每行一个。

- 输入：
  ```
  banana
  apple
  banana
  ```
- 输出：
  ```
  apple
  banana
  ```""",
        solutions=[
            ("sort -u", "sort -u\n"),
            ("sort | uniq", "sort | uniq\n"),
        ],
        inputs=["banana\napple\nbanana\n", "b\na\nc\n", "x\n", "", "a\na\na\n", "z\ny\nx\n", "cat\ndog\ncat\ndog\n", "1\n2\n1\n", "m\nn\no\n", "one\ntwo\nthree\n", "q\np\no\nn\n"],
    ),
    Question(
        key="ux-sum-numbers",
        title="求整数的和",
        tags=["unix", "linux-basic", "awk-arithmetic"],
        stem="""# 求整数的和

读入若干行，每行一个整数，输出它们的总和。

- 输入：
  ```
  1
  2
  3
  ```
- 输出：`6`""",
        solutions=[
            ("awk 累加", "awk '{ total += $1 } END { print total + 0 }'\n"),
            ("paste+bc 风格", "awk '{ total += $1 } END { printf \"%d\\n\", total }'\n"),
        ],
        inputs=["1\n2\n3\n", "0\n", "5\n", "-1\n1\n", "10\n20\n30\n", "100\n", "7\n8\n9\n", "-5\n-5\n", "1\n", "2\n2\n2\n", "42\n"],
    ),
    Question(
        key="ux-average",
        title="求平均值（保留两位小数）",
        tags=["unix", "linux-basic", "awk-arithmetic"],
        stem="""# 求平均值

读入若干行，每行一个整数，输出平均值，保留两位小数。

- 输入：
  ```
  1
  2
  3
  ```
- 输出：`2.00`""",
        solutions=[
            ("awk printf", "awk '{ sum += $1 } END { printf \"%.2f\\n\", sum / NR }'\n"),
            ("awk 分行计算", "awk 'BEGIN { s = 0; n = 0 } { s += $1; n += 1 } END { printf \"%.2f\\n\", s / n }'\n"),
        ],
        inputs=["1\n2\n3\n", "5\n", "0\n", "10\n20\n", "1\n2\n", "7\n8\n9\n", "-2\n2\n", "100\n", "1\n1\n1\n1\n", "3\n4\n", "9\n"],
    ),
    Question(
        key="ux-max-number",
        title="找出最大的整数",
        tags=["unix", "linux-basic", "awk-arithmetic"],
        stem="""# 找出最大的整数

读入若干行，每行一个整数，输出其中最大的那个。

- 输入：
  ```
  3
  9
  2
  ```
- 输出：`9`""",
        solutions=[
            ("awk 比较", "awk 'NR == 1 || $1 > best { best = $1 } END { print best }'\n"),
            ("sort 取末行", "sort -n | tail -n 1\n"),
        ],
        inputs=["3\n9\n2\n", "5\n", "-1\n-5\n", "0\n0\n0\n", "100\n99\n", "1\n2\n3\n4\n5\n", "-3\n7\n", "42\n", "8\n8\n8\n", "11\n2\n", "6\n"],
    ),
    Question(
        key="ux-grep-count",
        title="统计包含指定单词的行数",
        tags=["unix", "linux-basic", "grep"],
        stem="""# 统计包含指定单词的行数

读入若干行文本，输出包含单词 `error`（小写、整行中出现即可）的行数。

- 输入：
  ```
  error: disk full
  ok
  ```
- 输出：`1`""",
        solutions=[
            ("grep -c", "grep -c 'error' || true\n"),
            ("awk 计数", "awk '/error/ { n += 1 } END { print n + 0 }'\n"),
        ],
        inputs=["error: disk full\nok\n", "ok\n", "", "error\nerror\nerror\n", "Error\n", "an error occurred\nfine\n", "x\nerror y\n", "error\n", "ok ok\n", "errors happen\nerror\n", "no match here\n"],
    ),
    Question(
        key="ux-first-field",
        title="输出每行的第一个字段",
        tags=["unix", "linux-basic", "awk-fields"],
        stem="""# 输出每行的第一个字段

读入若干行，每行由空格分隔若干字段，输出每行的第一个字段，一行一个。

- 输入：
  ```
  alice 90
  bob 85
  ```
- 输出：
  ```
  alice
  bob
  ```""",
        solutions=[
            ("awk 打印 $1", "awk '{ print $1 }'\n"),
            ("cut 按空格", "awk '{ printf \"%s\\n\", $1 }'\n"),
        ],
        inputs=["alice 90\nbob 85\n", "x\n", "", "a b c\nd e f\n", "  leading two\n", "1 2\n3 4\n", "only\n", "tab\tsep\n", "p q\nr s\nt u\n", "z 0\n", "m n o\n"],
    ),
    Question(
        key="ux-second-column-sum",
        title="求第二列数字之和",
        tags=["unix", "linux-basic", "awk-fields"],
        stem="""# 求第二列数字之和

读入若干行，每行两个字段（名称 数字），输出第二列的总和。

- 输入：
  ```
  a 10
  b 20
  ```
- 输出：`30`""",
        solutions=[
            ("awk 求和", "awk '{ total += $2 } END { print total + 0 }'\n"),
            ("awk 显式格式", "awk '{ total += $2 } END { printf \"%d\\n\", total }'\n"),
        ],
        inputs=["a 10\nb 20\n", "x 5\n", "", "p 1\nq 2\nr 3\n", "m 100\n", "k 0\n", "a 7\nb 8\nc 9\n", "one 2\ntwo 3\n", "z 50\n", "n -1\nm 1\n", "q 4\n"],
    ),
    Question(
        key="ux-line-length",
        title="输出每行的字符数",
        tags=["unix", "linux-basic", "awk-fields"],
        stem="""# 输出每行的字符数

读入若干行，输出每行的字符数（不含换行符），一行一个。

- 输入：
  ```
  abc
  de
  ```
- 输出：
  ```
  3
  2
  ```""",
        solutions=[
            ("awk length", "awk '{ print length($0) }'\n"),
            ("wc -L 逐行", "awk '{ printf \"%d\\n\", length($0) }'\n"),
        ],
        inputs=["abc\nde\n", "a\n", "", "hello world\n", "12345\n", "x\ny\nz\n", "  spaces  \n", "q\n", "long line here\n", "ab\ncd\nef\n", "z z z\n"],
    ),
    Question(
        key="ux-filter-long-lines",
        title="筛选长度超过 5 的行",
        tags=["unix", "linux-basic", "text-filter"],
        stem="""# 筛选长度超过 5 的行

读入若干行，只输出长度大于 5 的行（保持原顺序）。

- 输入：
  ```
  abc
  abcdefg
  ```
- 输出：`abcdefg`""",
        solutions=[
            ("awk length 判断", "awk 'length($0) > 5'\n"),
            ("grep 正则", "grep -E '^.{6,}$' || true\n"),
        ],
        inputs=["abc\nabcdefg\n", "abcdef\n", "", "123456\n1\n", "hello world\nhi\n", "aaaaaa\nbbbbb\n", "x\ny\nz\n", "sixchr\n", "sevens7\n", "nope\n", "lastline\n"],
    ),
    Question(
        key="ux-replace-word",
        title="把 cat 替换成 dog",
        tags=["unix", "linux-basic", "sed"],
        stem="""# 把 cat 替换成 dog

读入若干行，把每行中出现的所有 `cat` 替换为 `dog` 后输出。

- 输入：`the cat sat`
- 输出：`the dog sat`""",
        solutions=[
            ("sed 全局替换", "sed 's/cat/dog/g'\n"),
            ("awk gsub", "awk '{ gsub(/cat/, \"dog\"); print }'\n"),
        ],
        inputs=["the cat sat\n", "cat cat\n", "no match\n", "", "concat\n", "cat\n", "a cat and a cat\n", "CAT\n", "scatter\n", "cat. cat!\n", "x\n"],
    ),
    Question(
        key="ux-word-count",
        title="统计单词个数",
        tags=["unix", "linux-basic", "text-stats"],
        stem="""# 统计单词个数

读入若干行文本，输出其中单词的总数（以空白分隔）。

- 输入：`one two three`
- 输出：`3`""",
        solutions=[
            ("wc -w", "wc -w | tr -d ' '\n"),
            ("awk NF 累加", "awk '{ total += NF } END { print total + 0 }'\n"),
        ],
        inputs=["one two three\n", "single\n", "", "a b\nc d\n", "   \n", "hello world\nfoo\n", "1 2 3 4\n", "x\n", "aa bb cc dd ee\n", "  spaced   out  \n", "last\n"],
    ),
    Question(
        key="ux-reverse-lines",
        title="按行逆序输出",
        tags=["unix", "linux-basic", "text-transform"],
        stem="""# 按行逆序输出

读入若干行，把行序倒过来输出（每行内容不变）。

- 输入：
  ```
  first
  second
  ```
- 输出：
  ```
  second
  first
  ```""",
        solutions=[
            ("tac", "tac\n"),
            ("sed 倒序", "sed '1!G;h;$!d'\n"),
        ],
        inputs=["first\nsecond\n", "a\n", "", "1\n2\n3\n", "x\ny\nz\n", "\n\n", "last\n", "p\nq\nr\ns\n", "only\n", "a\nb\nc\nd\ne\n", "m\n"],
    ),
    Question(
        key="ux-numbered-reverse",
        title="逆序并加行号",
        tags=["unix", "linux-basic", "pipeline"],
        stem="""# 逆序并加行号

读入若干行，先按行逆序，再从 1 开始加行号输出，格式 `行号<TAB>内容`。

- 输入：
  ```
  a
  b
  ```
- 输出（TAB 分隔）：
  ```
  1	b
  2	a
  ```""",
        solutions=[
            ("awk 倒序编号", "awk '{ lines[NR] = $0 } END { for (i = NR; i >= 1; i--) printf \"%d\\t%s\\n\", NR - i + 1, lines[i] }'\n"),
            ("awk 倒序编号", "awk '{ lines[NR] = $0 } END { for (i = NR; i >= 1; i--) printf \"%d\\t%s\\n\", NR - i + 1, lines[i] }'\n"),
        ],
        inputs=["a\nb\n", "one\n", "", "x\ny\nz\n", "1\n2\n3\n4\n", "p\nq\n", "last\nfirst\n", "aa\nbb\ncc\n", "single\n", "m\nn\no\n", "z\n"],
    ),
    Question(
        key="ux-odd-sum",
        title="求奇数之和",
        tags=["unix", "linux-basic", "awk-arithmetic"],
        stem="""# 求奇数之和

读入若干行，每行一个整数，输出其中所有奇数的和（没有奇数输出 0）。

- 输入：
  ```
  1
  2
  3
  ```
- 输出：`4`""",
        solutions=[
            ("awk 取模", "awk '$1 % 2 != 0 { total += $1 } END { print total + 0 }'\n"),
            ("awk 显式判断", "awk '{ if ($1 % 2 == 1 || $1 % 2 == -1) total += $1 } END { printf \"%d\\n\", total + 0 }'\n"),
        ],
        inputs=["1\n2\n3\n", "2\n4\n", "", "5\n", "1\n3\n5\n", "-1\n-2\n", "0\n", "7\n8\n9\n", "11\n13\n", "10\n", "3\n"],
    ),
    Question(
        key="ux-highest-score",
        title="输出最高分的学生",
        tags=["unix", "linux-basic", "awk-fields"],
        stem="""# 输出最高分的学生

读入若干行，每行 `姓名 分数`，输出分数最高的那一行（原样，姓名在前）。
若有多行并列最高，输出**最先出现**的那一行。

- 输入：
  ```
  alice 90
  bob 95
  ```
- 输出：`bob 95`""",
        solutions=[
            ("awk 记录整行", "awk 'NR == 1 || $2 > best { best = $2; line = $0 } END { print line }'\n"),
            ("稳定排序取首行", "sort -s -k2,2nr | head -n 1\n"),
        ],
        inputs=["alice 90\nbob 95\n", "solo 1\n", "q 3\nr 4\n", "a 10\nb 5\n", "x 100\ny 99\n", "m 60\nn 60\n", "p 0\n", "k 7\nl 8\nm 9\n", "one 42\n", "z 1\ny 2\n", "q 88\n"],
    ),
    Question(
        key="ux-join-lines",
        title="把所有行拼成一行",
        tags=["unix", "linux-basic", "pipeline"],
        stem="""# 把所有行拼成一行

读入若干行，用单个空格把它们连成一行输出（末尾不留空格）。

- 输入：
  ```
  a
  b
  c
  ```
- 输出：`a b c`""",
        solutions=[
            ("sed 逐行累积", "sed ':a;N;$!ba;s/\\n/ /g'\n"),
            ("awk 逐行打印", "awk 'NR > 1 { printf \" \" } { printf \"%s\", $0 } END { printf \"\\n\" }'\n"),
        ],
        inputs=["a\nb\nc\n", "one\n", "alpha\nbeta\ngamma\n", "1\n2\n", "x\ny\n", "aa\nbb\ncc\n", "single\n", "p\nq\nr\ns\n", "m\nn\n", "z\n", "a\nb\n"],
    ),
]


def run_bash(script: str, stdin_text: str) -> str:
    """在本地 Linux 命令环境里跑参考答案（沙箱同款 bash + coreutils）。"""
    environment = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")
    result = subprocess.run(
        [BASH, "-c", script],
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    if result.returncode != 0:
        raise SystemExit(f"[{script.strip()[:40]}] failed: {result.stderr[-300:]}")
    return result.stdout


def build_payload(question: Question) -> dict:
    if len(question.inputs) != 11:
        raise SystemExit(f"{question.key}: 需要 11 组输入（1 公开 + 10 隐藏）")

    reference = question.solutions[0][1]
    # 空字符串在这里的含义是"一行空行"，而不是"没有输入"。用例与期望输出必须用同一份 stdin，
    # 否则平台按"一行空行"执行、期望却按"无输入"生成，必然对不上。
    inputs = ["\n" if item == "" else item for item in question.inputs]
    expected = []
    for stdin_text in inputs:
        output = run_bash(reference, stdin_text)
        for label, code in question.solutions[1:]:
            if run_bash(code, stdin_text) != output:
                raise SystemExit(
                    f"{question.key}: 解法 {label} 与主解法不一致（输入 {stdin_text!r}）\n"
                    f"  A={output!r}\n  B={run_bash(code, stdin_text)!r}"
                )
        expected.append(output)

    cases = [
        {
            "name": "公开示例",
            "ordinal": 0,
            "weight": 1,
            "visible_to_student": True,
            "stdin": inputs[0],
            "args": [],
            "expected_stdout": expected[0],
            "expected_stderr": None,
            "expected_exit_code": 0,
        }
    ]
    for index, stdin_text in enumerate(inputs[1:], start=1):
        cases.append(
            {
                "name": f"隐藏用例 {index}",
                "ordinal": index,
                "weight": 1,
                "visible_to_student": False,
                "stdin": stdin_text,
                "args": [],
                "expected_stdout": expected[index],
                "expected_stderr": None,
                "expected_exit_code": 0,
            }
        )

    return {
        "title": question.title,
        "stem_markdown": question.stem,
        "language": "bash",
        "tags": question.tags,
        "adapter": {
            "kind": "stdin",
            "args": [],
            "stdin": None,
            "expect_exit_code": 0,
            "comparison": {
                "stdout": "ignore_trailing_whitespace",
                "stderr": "ignored",
                "numeric_tolerance": None,
            },
        },
        "reference_answers": [{"label": label, "body": code} for label, code in question.solutions],
        "reference_answer": reference,
        "grading_prompt": grading_prompt(question.case_percent, question.ai_percent, question.focus),
        "execution": {"mode": "per_case"},
        "cases": cases,
    }


def call(method: str, path: str, token: str | None = None, payload: dict | None = None):
    """网络层故障重试 3 次；HTTP 结论原样返回。"""
    body = json.dumps(payload).encode() if payload is not None else None
    delays = (1, 3, 6)
    last_error: Exception | None = None
    for attempt in range(4):
        request = urllib.request.Request(f"{BASE}{path}", data=body, method=method)
        for name, value in HEADERS.items():
            request.add_header(name, value)
        if token:
            request.add_header("authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return error.code, {"detail": raw.decode("utf-8", "replace")[:300]}
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < len(delays):
                wait = delays[attempt]
                print(f"network error on {method} {path}: {error}; retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise SystemExit(f"network unavailable after retries: {last_error}")


def check_published(token: str, question_id: str | None) -> bool:
    if not question_id:
        return False
    status, detail = call("GET", f"/api/v1/questions/{question_id}", token)
    return status == 200 and detail.get("status") == "published"


def main() -> int:
    bank = [build_payload(question) for question in UNIX_QUESTIONS]
    print(f"built {len(bank)} payload(s); both bash solutions agree")

    if "--dump" in sys.argv:
        with open("unix-dual-bank.json", "w", encoding="utf-8", newline="\n") as handle:
            json.dump(bank, handle, ensure_ascii=False, indent=2)
        print("written unix-dual-bank.json")
    if "--dry-run" in sys.argv:
        return 0

    if not ADMIN_PASSWORD:
        raise SystemExit("AUTOGRADER_ADMIN_PASSWORD is required")
    status, session = call(
        "POST", "/api/v1/auth/session", payload={"username": "admin", "password": ADMIN_PASSWORD}
    )
    if status != 200:
        raise SystemExit(f"sign-in failed: {status} {session}")
    token = session["access_token"]

    _, courses = call("GET", "/api/v1/courses?limit=200", token)
    course_id = next(c["course_id"] for c in courses["items"] if c["title"] == COURSE_TITLE)
    _, existing = call(
        "GET", f"/api/v1/questions?course_id={course_id}&limit=200&published_only=false", token
    )
    by_title = {item["title"]: item for item in existing.get("items", [])}

    results = []
    for payload in bank:
        known = by_title.get(payload["title"])
        if known is not None:
            version_id = (known.get("versions") or [{}])[0].get("question_version_id")
            if known.get("status") != "published":
                code, body = call("PATCH", f"/api/v1/questions/{known['question_id']}", token, payload)
                if code != 200:
                    print("update failed", payload["title"], code, str(body)[:160])
            results.append(
                {
                    "title": payload["title"],
                    "question_id": known["question_id"],
                    "version_id": version_id,
                    "status": known.get("status"),
                }
            )
            print("reuse", payload["title"], known["question_id"], known.get("status"))
            continue
        code, body = call("POST", f"/api/v1/questions?course_id={course_id}", token, payload)
        if code not in (200, 201):
            results.append({"title": payload["title"], "result": f"create {code}", "detail": str(body)[:200]})
            continue
        question_id = body["question_id"]
        version_id = (body.get("versions") or [{}])[0].get("question_version_id")
        results.append(
            {"title": payload["title"], "question_id": question_id, "version_id": version_id}
        )
        print("created", payload["title"], question_id)

    with open("unix-dual-created.json", "w", encoding="utf-8", newline="\n") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)

    if "--publish" not in sys.argv:
        return 0

    published = []
    for item in results:
        version_id = item.get("version_id")
        if not version_id:
            published.append({"title": item["title"], "result": "skipped (no version)"})
            continue
        if item.get("status") == "published" or check_published(token, item.get("question_id")):
            published.append({"title": item["title"], "result": "published (earlier run)"})
            continue
        code, verification = call(
            "POST", "/api/v1/reference-verifications", token, {"question_version_id": version_id}
        )
        if code not in (200, 201) or verification.get("attestation_id") is None:
            published.append(
                {"title": item["title"], "result": f"verify {code}", "detail": str(verification)[:300]}
            )
            continue
        if verification.get("cases_passed") != verification.get("cases_total"):
            published.append(
                {"title": item["title"], "result": "cases not all passed", "detail": str(verification)[:300]}
            )
            continue
        code, body = call("POST", f"/api/v1/question-versions/{version_id}/publish", token, {})
        published.append(
            {
                "title": item["title"],
                "result": "published" if code in (200, 201) else f"publish {code}",
                "detail": None if code in (200, 201) else str(body)[:300],
            }
        )
        print(item["title"], "->", published[-1]["result"])

    with open("unix-dual-published.json", "w", encoding="utf-8", newline="\n") as handle:
        json.dump(published, handle, ensure_ascii=False, indent=2)
    print(
        "published",
        sum(1 for row in published if row["result"].startswith("published")),
        "of",
        len(results),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
