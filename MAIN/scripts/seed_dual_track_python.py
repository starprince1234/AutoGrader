"""Python 双轨题库（20 道）：题干 + 两种参考解法 + 1 个公开示例 + 10 个隐藏用例。

设计约束来自 `MAIN/docs/` 的交接文档 §4：
* 只出 stdin/stdout 型纯函数题（沙箱无网络、非 root、只读根）；
* 只用标准库；确定性、单例 <1s；
* 覆盖原作业训练过的能力：算术表达式、字符串处理、列表、字典/集合、控制流、循环、
  异常处理、正则、函数与递归、排序、计数统计。

每种解法都要在本地真跑一遍：期望输出由解法 A 生成，解法 B 必须逐字节复现；两者不一致直接报错，
不允许把"没验证过的期望输出"发到线上。权重按题目难度逐题声明在评分提示词里。
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
ADMIN = ("admin", os.environ.get("AUTOGRADER_ADMIN_PASSWORD", ""))
COURSE_TITLE = "Python编程与实践作业"

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

#: 权重声明行：`dual_track.parse_declared_weights` 只认这一行的两个百分数。
def weights_line(case_percent: int, ai_percent: int) -> str:
    return f"权重：用例 {case_percent}% + AI {ai_percent}%"


def grading_prompt(case_percent: int, ai_percent: int, focus: str) -> str:
    return f"""{weights_line(case_percent, ai_percent)}

评分细则（总分 100，由两条轨按上面的权重合成）：
1. 用例轨：程序在 10 个隐藏用例上的通过率。输出格式不符（多空格、多行、提示文字）按未通过计。
2. AI 轨：从算法思路、边界处理、代码可读性三方面给出 0~100 的连续分。
   - 本题重点：{focus}
扣分项：硬编码某个输入对应的输出、用异常吞掉错误、把答案写死，AI 轨最高 40 分。
若 AI 判断与用例通过数明显冲突，必须在总结里说明原因。"""


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
    focus: str = "把输入解析正确、按题目要求的格式逐行输出"


PY_QUESTIONS: list[Question] = [
    Question(
        key="py-sum-two",
        title="两数之和",
        tags=["python", "python-basic", "arithmetic"],
        stem="""# 两数之和

从标准输入读入一行，包含两个整数 `a b`（用空格分隔），输出它们的和。

- 输入示例：`3 5`
- 输出示例：`8`

只输出结果，不要任何提示文字。""",
        solutions=[
            ("直接相加", "a, b = map(int, input().split())\nprint(a + b)\n"),
            ("逐项转换", "parts = input().split()\nprint(int(parts[0]) + int(parts[1]))\n"),
        ],
        inputs=["3 5", "0 0", "-4 9", "100 -100", "7 8", "-1 -1", "12345 67890", "2 2", "-50 25", "999999 1", "42 0"],
    ),
    Question(
        key="py-average",
        title="求一组整数的平均值",
        tags=["python", "python-basic", "loop"],
        stem="""# 求一组整数的平均值

第一行是整数 `n`，第二行是 `n` 个整数。输出它们的平均值，保留两位小数（四舍五入）。

- 输入：
  ```
  3
  1 2 3
  ```
- 输出：`2.00`""",
        solutions=[
            ("sum 与 len", "n = int(input())\nvalues = list(map(int, input().split()))\nprint(f\"{sum(values) / n:.2f}\")\n"),
            ("循环累加", "n = int(input())\nvalues = input().split()\ntotal = 0\nfor item in values:\n    total += int(item)\nprint(f\"{total / n:.2f}\")\n"),
        ],
        inputs=["3\n1 2 3", "1\n5", "4\n10 20 30 40", "2\n-1 1", "5\n1 1 1 1 1", "2\n0 1", "6\n2 4 6 8 10 12", "3\n-5 -5 -5", "4\n7 8 9 10", "2\n100 1", "3\n1 2 4"],
    ),
    Question(
        key="py-max-min",
        title="最大值与最小值",
        tags=["python", "python-basic", "list"],
        stem="""# 最大值与最小值

第一行整数 `n`，第二行 `n` 个整数。先输出最大值，再输出最小值，各占一行。

- 输入：
  ```
  3
  5 1 9
  ```
- 输出：
  ```
  9
  1
  ```""",
        solutions=[
            ("内置函数", "n = int(input())\nvalues = list(map(int, input().split()))\nprint(max(values))\nprint(min(values))\n"),
            ("单次遍历", "n = int(input())\nvalues = list(map(int, input().split()))\nlargest = values[0]\nsmallest = values[0]\nfor v in values[1:]:\n    if v > largest:\n        largest = v\n    if v < smallest:\n        smallest = v\nprint(largest)\nprint(smallest)\n"),
        ],
        inputs=["3\n5 1 9", "1\n7", "4\n-1 -2 -3 -4", "5\n0 0 0 0 0", "2\n10 20", "6\n3 3 2 2 1 1", "3\n100 -100 0", "5\n9 8 7 6 5", "4\n2 4 6 8", "2\n-7 7", "3\n11 22 33"],
    ),
    Question(
        key="py-count-even",
        title="统计偶数个数",
        tags=["python", "python-basic", "loop"],
        stem="""# 统计偶数个数

第一行整数 `n`，第二行 `n` 个整数。输出其中偶数的个数。

- 输入：
  ```
  5
  1 2 3 4 6
  ```
- 输出：`3`""",
        solutions=[
            ("生成器求和", "n = int(input())\nvalues = list(map(int, input().split()))\nprint(sum(1 for v in values if v % 2 == 0))\n"),
            ("循环计数", "n = int(input())\nvalues = list(map(int, input().split()))\ncount = 0\nfor v in values:\n    if v % 2 == 0:\n        count += 1\nprint(count)\n"),
        ],
        inputs=["5\n1 2 3 4 6", "3\n1 3 5", "1\n0", "4\n2 4 6 8", "2\n-2 -3", "6\n7 8 9 10 11 12", "3\n-1 -2 -3", "5\n0 0 0 0 0", "4\n101 102 103 104", "2\n9 10", "7\n1 2 3 4 5 6 7"],
    ),
    Question(
        key="py-reverse-string",
        title="字符串反转",
        tags=["python", "python-basic", "string"],
        stem="""# 字符串反转

读入一行字符串（不含空格以外的特殊要求），输出它的反转。

- 输入：`abcdef`
- 输出：`fedcba`""",
        solutions=[
            ("切片反转", "s = input()\nprint(s[::-1])\n"),
            ("reversed + join", "s = input()\nprint(''.join(reversed(s)))\n"),
        ],
        inputs=["abcdef", "a", "", "12345", "hello world", "AaBb", "racecar", "x y z", "0123456789", "中文测试", "ab"],
    ),
    Question(
        key="py-uppercase",
        title="大小写反转",
        tags=["python", "python-basic", "string"],
        stem="""# 大小写反转

读入一行字符串，把小写字母变大写、大写字母变小写，其他字符不变，输出结果。

- 输入：`Abc-Def`
- 输出：`aBC-dEF`""",
        solutions=[
            ("swapcase", "print(input().swapcase())\n"),
            ("逐字符判断", "out = []\nfor ch in input():\n    if ch.islower():\n        out.append(ch.upper())\n    elif ch.isupper():\n        out.append(ch.lower())\n    else:\n        out.append(ch)\nprint(''.join(out))\n"),
        ],
        inputs=["Abc-Def", "ABC", "xyz", "123", "HeLLo World!", "aA", "", "Mixed CASE 42", "中文Abc", "Q", "zZ"],
    ),
    Question(
        key="py-palindrome",
        title="判断回文",
        tags=["python", "python-basic", "string"],
        stem="""# 判断回文

读入一行字符串（只含小写字母与数字），若正读反读相同输出 `yes`，否则输出 `no`。

- 输入：`level`
- 输出：`yes`""",
        solutions=[
            ("切片比较", "s = input()\nprint('yes' if s == s[::-1] else 'no')\n"),
            ("双指针", "s = input()\ni, j = 0, len(s) - 1\nok = True\nwhile i < j:\n    if s[i] != s[j]:\n        ok = False\n        break\n    i += 1\n    j -= 1\nprint('yes' if ok else 'no')\n"),
        ],
        inputs=["level", "abc", "a", "", "12321", "abba", "ab", "racecar", "0", "11", "abcba"],
    ),
    Question(
        key="py-count-char",
        title="统计每个字符出现的次数",
        tags=["python", "python-basic", "dict-count"],
        stem="""# 统计每个字符出现的次数

读入一行字符串，按字符首次出现的顺序输出 `字符:次数`，每行一个。

- 输入：`aabbc`
- 输出：
  ```
  a:2
  b:2
  c:1
  ```""",
        solutions=[
            ("字典计数", "counts = {}\nfor ch in input():\n    counts[ch] = counts.get(ch, 0) + 1\nfor ch, n in counts.items():\n    print(f'{ch}:{n}')\n"),
            ("Counter", "from collections import Counter\nfor ch, n in Counter(input()).items():\n    print(f'{ch}:{n}')\n"),
        ],
        inputs=["aabbc", "abc", "a", "", "aa", "abab", "xyzxyz", "111", "hello", "abca", "z"],
    ),
    Question(
        key="py-word-count",
        title="统计单词个数（按空格切分）",
        tags=["python", "python-basic", "string"],
        stem="""# 统计单词个数

读入一行由单个空格分隔的单词，输出单词个数（空行输出 0）。

- 输入：`i love python`
- 输出：`3`""",
        solutions=[
            ("split", "line = input().strip()\nprint(0 if line == '' else len(line.split()))\n"),
            ("循环计数", "line = input().strip()\nif line == '':\n    print(0)\nelse:\n    count = 1\n    for ch in line:\n        if ch == ' ':\n            count += 1\n    print(count)\n"),
        ],
        inputs=["i love python", "hello", "", "a b c d e", "  ", "one two", "python is fun", "x", "aa bb", "1 2 3", "the quick brown fox"],
    ),
    Question(
        key="py-grade-level",
        title="成绩等级判定",
        tags=["python", "python-basic", "control-flow"],
        stem="""# 成绩等级判定

第一行整数 `n`，第二行 `n` 个成绩（0~100 的整数）。逐个输出等级，每行一个：

- 90 及以上 `A`
- 80~89 `B`
- 70~79 `C`
- 60~69 `D`
- 60 以下 `E`

输入示例：
```
3
95 85 40
```
输出示例：
```
A
B
E
```""",
        solutions=[
            ("if/elif 链", "n = int(input())\nfor score in map(int, input().split()):\n    if score >= 90:\n        print('A')\n    elif score >= 80:\n        print('B')\n    elif score >= 70:\n        print('C')\n    elif score >= 60:\n        print('D')\n    else:\n        print('E')\n"),
            ("阈值表", "n = int(input())\nbands = ((90, 'A'), (80, 'B'), (70, 'C'), (60, 'D'))\nfor score in map(int, input().split()):\n    for limit, label in bands:\n        if score >= limit:\n            print(label)\n            break\n    else:\n        print('E')\n"),
        ],
        inputs=["3\n95 85 40", "1\n100", "1\n0", "4\n90 89 60 59", "2\n70 79", "5\n61 62 63 64 65", "1\n88", "2\n0 100", "3\n75 74 76", "1\n59", "6\n91 81 71 61 51 41"],
    ),
    Question(
        key="py-factorial-sum",
        title="阶乘之和（1! 到 n!）",
        tags=["python", "python-basic", "loop"],
        stem="""# 阶乘之和

读入整数 `n`（1 ≤ n ≤ 20），输出 `1! + 2! + ... + n!` 的结果。

- 输入：`5`
- 输出：`153`""",
        solutions=[
            ("逐步累乘累加", "n = int(input())\ntotal = 0\nfact = 1\nfor i in range(1, n + 1):\n    fact *= i\n    total += fact\nprint(total)\n"),
            ("math.factorial", "import math\nn = int(input())\nprint(sum(math.factorial(i) for i in range(1, n + 1)))\n"),
        ],
        inputs=["5", "1", "2", "3", "4", "6", "7", "8", "10", "12", "20"],
    ),
    Question(
        key="py-fibonacci",
        title="斐波那契数列前 n 项",
        tags=["python", "python-basic", "loop"],
        stem="""# 斐波那契数列

读入整数 `n`（1 ≤ n ≤ 30），输出斐波那契数列前 `n` 项，用单个空格分隔，末尾不带空格。

数列定义：`F(1)=1, F(2)=1, F(k)=F(k-1)+F(k-2)`。

- 输入：`5`
- 输出：`1 1 2 3 5`""",
        solutions=[
            ("滚动两变量", "n = int(input())\nvalues = []\na, b = 1, 1\nfor _ in range(n):\n    values.append(a)\n    a, b = b, a + b\nprint(' '.join(str(v) for v in values))\n"),
            ("列表递推", "n = int(input())\nvalues = [1, 1]\nwhile len(values) < n:\n    values.append(values[-1] + values[-2])\nprint(' '.join(str(v) for v in values[:n]))\n"),
        ],
        inputs=["5", "1", "2", "3", "8", "10", "12", "15", "20", "25", "30"],
    ),
    Question(
        key="py-primes-range",
        title="输出区间内的素数",
        tags=["python", "python-basic", "number-theory"],
        stem="""# 输出区间内的素数

读入两个整数 `a b`（`a ≤ b`，1 ≤ a ≤ 1000），按升序输出 `[a, b]` 内的所有素数，用单个空格分隔；若没有素数则输出 `none`。

- 输入：`10 20`
- 输出：`11 13 17 19`""",
        solutions=[
            ("试除法", "import sys\n\ndef is_prime(n):\n    if n < 2:\n        return False\n    d = 2\n    while d * d <= n:\n        if n % d == 0:\n            return False\n        d += 1\n    return True\n\na, b = map(int, input().split())\nresult = [str(n) for n in range(a, b + 1) if is_prime(n)]\nprint(' '.join(result) if result else 'none')\n"),
            ("筛法", "a, b = map(int, input().split())\nlimit = max(b, 2)\nsieve = [True] * (limit + 1)\nsieve[0] = sieve[1] = False\nfor i in range(2, int(limit ** 0.5) + 1):\n    if sieve[i]:\n        for j in range(i * i, limit + 1, i):\n            sieve[j] = False\nresult = [str(n) for n in range(a, b + 1) if sieve[n]]\nprint(' '.join(result) if result else 'none')\n"),
        ],
        inputs=["10 20", "1 10", "2 2", "4 4", "1 1", "50 60", "100 120", "997 1000", "2 30", "14 16", "90 100"],
    ),
    Question(
        key="py-gcd-lcm",
        title="最大公约数与最小公倍数",
        tags=["python", "python-basic", "number-theory"],
        stem="""# 最大公约数与最小公倍数

读入两个正整数 `a b`，先输出最大公约数，再输出最小公倍数，各占一行。

- 输入：`12 18`
- 输出：
  ```
  6
  36
  ```""",
        solutions=[
            ("欧几里得算法", "a, b = map(int, input().split())\n\nx, y = a, b\nwhile y:\n    x, y = y, x % y\ngcd = x\nprint(gcd)\nprint(a // gcd * b)\n"),
            ("math.gcd", "import math\n\na, b = map(int, input().split())\ng = math.gcd(a, b)\nprint(g)\nprint(a * b // g)\n"),
        ],
        inputs=["12 18", "1 1", "7 13", "100 75", "8 12", "0 5", "17 17", "24 36", "6 35", "50 20", "999 333"],
    ),
    Question(
        key="py-caesar",
        title="凯撒密码（字母后移 3 位）",
        tags=["python", "python-basic", "string"],
        stem="""# 凯撒密码

读入一行小写字母与空格，把每个小写字母后移 3 位（`x`→`a`、`y`→`b`、`z`→`c`），空格不变，输出结果。

- 输入：`abc xyz`
- 输出：`def abc`""",
        solutions=[
            ("ord/chr", "out = []\nfor ch in input():\n    if 'a' <= ch <= 'z':\n        out.append(chr((ord(ch) - ord('a') + 3) % 26 + ord('a')))\n    else:\n        out.append(ch)\nprint(''.join(out))\n"),
            ("str.translate", "import string\nshifted = string.ascii_lowercase[3:] + string.ascii_lowercase[:3]\ntable = str.maketrans(string.ascii_lowercase, shifted)\nprint(input().translate(table))\n"),
        ],
        inputs=["abc xyz", "xyz", "", "hello world", "a", "z", "mn", "attack at dawn", "q", "abcdefghijklmnopqrstuvwxyz", "zz yy"],
    ),
    Question(
        key="py-word-frequency",
        title="出现次数最多的单词",
        tags=["python", "python-basic", "dict-count"],
        stem="""# 出现次数最多的单词

读入一行由单个空格分隔的小写单词，输出出现次数最多的那个单词及其次数（`单词 次数`）。
若并列最多，取**最先出现**的那个。

- 输入：`a b a c b a`
- 输出：`a 3`""",
        solutions=[
            ("字典 + 首次顺序", "words = input().split()\ncounts = {}\norder = []\nfor w in words:\n    if w not in counts:\n        counts[w] = 0\n        order.append(w)\n    counts[w] += 1\nbest = order[0]\nfor w in order:\n    if counts[w] > counts[best]:\n        best = w\nprint(best, counts[best])\n"),
            ("max 保留首位", "words = input().split()\ncounts = {}\nfor w in words:\n    counts[w] = counts.get(w, 0) + 1\nbest = max(counts, key=lambda w: counts[w])\nprint(best, counts[best])\n"),
        ],
        inputs=["a b a c b a", "x", "a b c", "aa aa bb", "p q p q", "one", "z y x z", "m m m n", "a b b a", "k", "red blue red green blue red"],
    ),
    Question(
        key="py-json-like-parse",
        title="解析简单的键值对",
        tags=["python", "python-basic", "string"],
        stem="""# 解析简单的键值对

读入一行形如 `k1=v1;k2=v2;...` 的字符串（键值都不含 `;` 和 `=`），按输入顺序输出每行 `键:值`。
若某一段为空则跳过。

- 输入：`a=1;b=2`
- 输出：
  ```
  a:1
  b:2
  ```""",
        solutions=[
            ("split 两次", "line = input()\nfor part in line.split(';'):\n    if not part:\n        continue\n    key, value = part.split('=', 1)\n    print(f'{key}:{value}')\n"),
            ("partition", "for part in input().split(';'):\n    if not part:\n        continue\n    key, _, value = part.partition('=')\n    print(key + ':' + value)\n"),
        ],
        inputs=["a=1;b=2", "x=9", "", ";;a=1;;", "k=v;", "name=tom;age=12", "a=1;b=2;c=3", "only=v", "u=;v=1", "p=q", "m=1;n=1"],
    ),
    Question(
        key="py-regex-numbers",
        title="提取字符串中的数字",
        tags=["python", "python-basic", "regex"],
        stem="""# 提取字符串中的数字

读入一行字符串，按出现顺序输出其中所有连续数字串，用单个空格分隔；没有数字则输出 `none`。

- 输入：`ab12cd345e`
- 输出：`12 345`""",
        solutions=[
            ("re.findall", "import re\nfound = re.findall(r'\\d+', input())\nprint(' '.join(found) if found else 'none')\n"),
            ("逐字符扫描", "s = input()\nnums = []\ncurrent = ''\nfor ch in s:\n    if ch.isdigit():\n        current += ch\n    elif current:\n        nums.append(current)\n        current = ''\nif current:\n    nums.append(current)\nprint(' '.join(nums) if nums else 'none')\n"),
        ],
        inputs=["ab12cd345e", "123", "abc", "1a2b3c", "", "007bond", "x9", "a1b22c333", "42", "no digits here", "9 8 7"],
    ),
    Question(
        key="py-binary",
        title="十进制转二进制",
        tags=["python", "python-basic", "number-base"],
        stem="""# 十进制转二进制

读入一个非负整数 `n`（0 ≤ n ≤ 100000），输出它的二进制表示（不带前缀）。

- 输入：`10`
- 输出：`1010`""",
        solutions=[
            ("bin 去前缀", "n = int(input())\nprint(bin(n)[2:])\n"),
            ("除二取余", "n = int(input())\nif n == 0:\n    print('0')\nelse:\n    digits = []\n    while n:\n        digits.append(str(n % 2))\n        n //= 2\n    print(''.join(reversed(digits)))\n"),
        ],
        inputs=["10", "0", "1", "2", "255", "1024", "100000", "7", "64", "999", "31"],
    ),
    Question(
        key="py-sort-desc",
        title="按数值降序排序",
        tags=["python", "python-basic", "sorting"],
        stem="""# 按数值降序排序

第一行整数 `n`，第二行 `n` 个整数。按数值从大到小输出，用单个空格分隔。

- 输入：
  ```
  4
  3 1 4 2
  ```
- 输出：`4 3 2 1`""",
        solutions=[
            ("sorted + reverse", "n = int(input())\nvalues = list(map(int, input().split()))\nprint(' '.join(str(v) for v in sorted(values, reverse=True)))\n"),
            ("sort 原地", "n = int(input())\nvalues = list(map(int, input().split()))\nvalues.sort(reverse=True)\nprint(' '.join(map(str, values)))\n"),
        ],
        inputs=["4\n3 1 4 2", "1\n5", "3\n-1 -2 -3", "5\n5 5 5 5 5", "2\n10 1", "6\n0 9 8 7 6 5", "4\n100 -100 0 50", "3\n2 2 1", "7\n11 12 13 14 15 16 17", "2\n-5 5", "8\n8 7 6 5 4 3 2 1"],
    ),
]


def run_python(code: str, stdin_text: str) -> str:
    # 空字符串表示"输入了一行空行"，而不是"没有任何输入"：后者会让 input() 抛 EOFError，
    # 学生看到的题目是"读入一行"，所以这里补一个换行符，与实际提交时的一致。
    feed = "\n" if stdin_text == "" else stdin_text
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=feed,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    if result.returncode != 0:
        raise SystemExit(f"[{code[:40]}...] failed: {result.stderr[-400:]}")
    return result.stdout


def build_payload(question: Question) -> dict:
    """把题目规格变成 B3 的草稿载荷；期望输出全部来自本地实跑。"""
    if len(question.inputs) != 11:
        raise SystemExit(f"{question.key}: 需要 11 组输入（1 公开 + 10 隐藏），实际 {len(question.inputs)}")

    reference = question.solutions[0][1]
    expected: list[str] = []
    for stdin_text in question.inputs:
        output = run_python(reference, stdin_text)
        for label, code in question.solutions[1:]:
            if run_python(code, stdin_text) != output:
                raise SystemExit(f"{question.key}: 解法 {label} 与主解法输出不一致（输入 {stdin_text!r}）")
        expected.append(output)

    cases = [
        {
            "name": "公开示例",
            "ordinal": 0,
            "weight": 1,
            "visible_to_student": True,
            "stdin": "\n" if question.inputs[0] == "" else question.inputs[0],
            "args": [],
            "expected_stdout": expected[0],
            "expected_stderr": None,
            "expected_exit_code": 0,
        }
    ]
    for index, stdin_text in enumerate(question.inputs[1:], start=1):
        cases.append(
            {
                "name": f"隐藏用例 {index}",
                "ordinal": index,
                "weight": 1,
                "visible_to_student": False,
                "stdin": "\n" if stdin_text == "" else stdin_text,
                "args": [],
                "expected_stdout": expected[index],
                "expected_stderr": None,
                "expected_exit_code": 0,
            }
        )

    return {
        "title": question.title,
        "stem_markdown": question.stem,
        "language": "python",
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
        "reference_answers": [
            {"label": label, "body": code} for label, code in question.solutions
        ],
        "reference_answer": reference,
        "grading_prompt": grading_prompt(
            question.case_percent, question.ai_percent, question.focus
        ),
        "execution": {"mode": "per_case"},
        "cases": cases,
    }


def call(method: str, path: str, token: str | None = None, payload: dict | None = None):
    """一次 HTTP 调用；网络层故障重试 3 次，HTTP 结论（4xx/5xx）原样返回不重试。

    跨境链路常在 TLS 握手阶段被重置（`UNEXPECTED_EOF_WHILE_READING`），一次重试就能过去；
    而 4xx/5xx 是服务端已经给出的判断，重试它们只会掩盖真实原因。
    """
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


def check_published(token: str, item: dict) -> bool:
    """这道题在服务端是否已是发布态（用公开详情判断，不依赖本地记录）。"""
    question_id = item.get("question_id")
    if not question_id:
        return False
    status, detail = call("GET", f"/api/v1/questions/{question_id}", token)
    return status == 200 and detail.get("status") == "published"


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    bank = [build_payload(question) for question in PY_QUESTIONS]
    print(f"built {len(bank)} payload(s); local solutions agree")

    if "--dump" in sys.argv:
        target = "python-dual-bank.json"
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(bank, handle, ensure_ascii=False, indent=2)
        print("written", target)
    if dry_run:
        return 0

    password = ADMIN[1]
    if not password:
        raise SystemExit("AUTOGRADER_ADMIN_PASSWORD is required")
    status, session = call("POST", "/api/v1/auth/session", payload={"username": ADMIN[0], "password": password})
    if status != 200:
        raise SystemExit(f"sign-in failed: {status} {session}")
    token = session["access_token"]

    _, courses = call("GET", "/api/v1/courses?limit=200", token)
    course_id = next(c["course_id"] for c in courses["items"] if c["title"] == COURSE_TITLE)

    # 已有同名题目就复用：脚本可以反复执行，中断后重跑不会产生第二份题库。
    _, existing_page = call(
        "GET", f"/api/v1/questions?course_id={course_id}&limit=200&published_only=false", token
    )
    by_title = {item["title"]: item for item in existing_page.get("items", [])}

    results = []
    for payload in bank:
        known = by_title.get(payload["title"])
        if known is not None:
            versions = known.get("versions") or []
            version_id = versions[0]["question_version_id"] if versions else None
            updated = None
            if known.get("status") != "published":
                # 草稿可以就地更新（整体替换）：上一次运行可能带着错误的用例内容，
                # 直接沿用旧内容会让"重跑"变成"重试同一个错误"。
                code, body = call(
                    "PATCH", f"/api/v1/questions/{known['question_id']}", token, payload
                )
                updated = code
                if code != 200:
                    print("update failed", payload["title"], code, str(body)[:160])
            results.append(
                {
                    "title": payload["title"],
                    "question_id": known["question_id"],
                    "version_id": version_id,
                    "reused": True,
                    "status": known.get("status"),
                    "update": updated,
                }
            )
            print("reuse", payload["title"], known["question_id"], known.get("status"))
            continue
        code, body = call("POST", f"/api/v1/questions?course_id={course_id}", token, payload)
        if code not in (200, 201):
            results.append({"title": payload["title"], "result": f"create {code}", "detail": str(body)[:200]})
            continue
        question_id = body["question_id"]
        versions = body.get("versions") or []
        version_id = versions[0]["question_version_id"] if versions else None
        if version_id is None:
            _, detail = call("GET", f"/api/v1/questions/{question_id}", token)
            version_id = (detail.get("versions") or [{}])[0].get("question_version_id")
        results.append({"title": payload["title"], "question_id": question_id, "version_id": version_id})
        print("created", payload["title"], question_id)

    with open("python-dual-created.json", "w", encoding="utf-8", newline="\n") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print("created", sum(1 for r in results if r.get("version_id")), "of", len(bank))

    if "--publish" not in sys.argv:
        return 0

    published = []
    for item in results:
        version_id = item.get("version_id")
        if not version_id:
            published.append({"title": item["title"], "result": "skipped (no version)"})
            continue
        # 已经发布过的题不再重跑验证：存证只能记在草稿上，对已发布版本再验会被服务端拒绝，
        # 而"跳过"才是这次重跑的真实语义。判断只看服务端的公开状态，不信本地记录。
        if item.get("status") == "published" or check_published(token, item):
            published.append({"title": item["title"], "result": "published (earlier run)"})
            continue
        code, verification = call(
            "POST",
            "/api/v1/reference-verifications",
            token,
            {"question_version_id": version_id},
        )
        if code not in (200, 201) or verification.get("attestation_id") is None:
            published.append(
                {
                    "title": item["title"],
                    "result": f"verify {code}",
                    "detail": str(verification)[:200],
                }
            )
            continue
        if verification.get("cases_passed") != verification.get("cases_total"):
            published.append(
                {
                    "title": item["title"],
                    "result": "cases not all passed",
                    "detail": str(verification)[:200],
                }
            )
            continue
        code, body = call(
            "POST", f"/api/v1/question-versions/{version_id}/publish", token, {}
        )
        published.append(
            {
                "title": item["title"],
                "result": "published" if code in (200, 201) else f"publish {code}",
                "detail": None if code in (200, 201) else str(body)[:200],
            }
        )
        print(item["title"], "->", published[-1]["result"])

    with open("python-dual-published.json", "w", encoding="utf-8", newline="\n") as handle:
        json.dump(published, handle, ensure_ascii=False, indent=2)
    print(
        "published",
        sum(1 for item in published if item["result"] == "published"),
        "of",
        len(results),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
